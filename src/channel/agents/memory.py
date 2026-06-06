# Copyright (c) 2026 John Carter. All rights reserved.
"""Bedrock AgentCore Memory integration via Strands hooks.

Phase 7c — write-only. Every chat round-trip produces one atomic
``CreateEvent`` containing the user+assistant pair. Recall
(``RetrieveMemoryRecords`` + prompt injection) lands in 7d.

Design rationale: ``docs/superpowers/specs/2026-05-31-phase-7c-agentcore-memory-writes-design.md``.

Strands 1.41 has no native AgentCore Memory adapter (verified in
``docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md``)
so this module talks to AgentCore directly via boto3.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, cast

import boto3
from strands.hooks.events import AfterInvocationEvent

from channel.metrics import record_memory_write_outcome

logger = logging.getLogger(__name__)

_ROLE_MAP: dict[str, str] = {"user": "USER", "assistant": "ASSISTANT"}

# AgentCore validates ``actorId`` against
# ``[a-zA-Z0-9][a-zA-Z0-9-_/]*(?::[a-zA-Z0-9-_/]+)*[a-zA-Z0-9-_/]*`` —
# letters, digits, hyphens, underscores, slashes, colons. Our JWT
# ``sub`` is the user's email (Google OAuth path) or arbitrary string
# (other paths); the email form contains ``@`` and ``.`` which fail
# the regex. Sanitize by replacing each disallowed char with ``_``.
_ACTOR_ID_DISALLOWED_RE = re.compile(r"[^a-zA-Z0-9_/-]")


def _sanitize_actor_id(jwt_sub: str) -> str:
    """Convert ``jwt_sub`` to a valid AgentCore ``actorId``.

    Replaces disallowed characters (anything outside
    ``[a-zA-Z0-9_/-]``) with ``_``. Stable: the same ``jwt_sub``
    always produces the same actorId. **Theoretically collision-prone**
    for inputs differing only in disallowed chars (``a@b.com`` and
    ``a.b@com`` both map to ``a_b_com``); revisit if user volume grows
    or weird email shapes appear.
    """
    return _ACTOR_ID_DISALLOWED_RE.sub("_", jwt_sub)


# Module-level cache keyed by env. Resets on Lambda cold-start; value is
# the AgentCore-assigned memoryId (which includes an opaque suffix).
_memory_id_cache: dict[str, str] = {}


def get_or_create_memory(env: str) -> str:
    """Return the AgentCore Memory id for ``env``, creating it if absent.

    Lazy + idempotent. First call within a Lambda instance pays a
    ``ListMemories`` RPC (~200ms) to find an existing Memory by name;
    subsequent calls hit the module-level cache. If no match exists,
    falls through to ``CreateMemory``.

    AgentCore appends an opaque suffix to ``memoryId`` (e.g.
    ``channel_dev-A1B2C3D4``). Look up by **name**, not by id, or
    re-deploys against an existing Memory will create duplicates.

    Override the default ``channel_{env}`` naming via
    ``STARTER_AGENTCORE_MEMORY_NAME`` — useful for pointing a personal
    dev environment at a pre-existing Memory resource. AgentCore
    requires names to match ``[a-zA-Z][a-zA-Z0-9_]{0,47}`` — letters,
    digits, underscores only; hyphens are rejected at the service
    layer.
    """
    if env in _memory_id_cache:
        return _memory_id_cache[env]

    name = os.environ.get("STARTER_AGENTCORE_MEMORY_NAME") or f"channel_{env}"
    control = boto3.client("bedrock-agentcore-control")

    # AgentCore's ``ListMemories`` returns ``memories`` (NOT
    # ``memorySummaries``), and each entry exposes ``id`` and ``arn``
    # but NOT a separate ``name`` field. The id is ``{name}-{8-char-suffix}``
    # where the suffix is AgentCore-appended at creation time. Match by
    # prefix to find an existing Memory provisioned under our naming
    # convention.
    existing = control.list_memories()
    for mem in existing.get("memories", []):
        mem_id = mem["id"]
        if mem_id == name or mem_id.startswith(f"{name}-"):
            _memory_id_cache[env] = mem_id
            return mem_id

    # Phase 8a: SemanticMemoryStrategy retired — its ingestion lag (hours
    # in real use) made RetrieveMemoryRecords unusable. Recall now reads
    # raw events via ListSessions + ListEvents (see agents/recall.py).
    # Strategy-less memories provision faster (no strategy resources to
    # spin up) and cost less.
    created = control.create_memory(
        name=name,
        memoryStrategies=[],
        eventExpiryDuration=90,
    )
    memory_id = created["memory"]["id"]

    # Phase 7d: newly-created memories are in ``CREATING`` status for
    # ~30s. CreateEvent and RetrieveMemoryRecords both reject calls
    # against a non-ACTIVE memory with a ValidationException. Block
    # ``get_or_create_memory`` until the memory is ready so the first
    # chat after a cold start doesn't lose its events / produce empty
    # recall. Subsequent requests use the cached id without polling.
    _wait_for_memory_active(control, memory_id)
    _memory_id_cache[env] = memory_id
    return memory_id


def _wait_for_memory_active(
    control: Any,
    memory_id: str,
    *,
    timeout_seconds: int = 360,
    poll_seconds: int = 5,
) -> None:
    """Poll ``GetMemory`` until status is ``ACTIVE`` or timeout."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        resp = control.get_memory(memoryId=memory_id)
        status = resp.get("memory", {}).get("status")
        if status == "ACTIVE":
            return
        if status in ("FAILED", "DELETING", "DELETED"):
            raise RuntimeError(f"AgentCore Memory {memory_id} reached terminal status {status!r}")
        time.sleep(poll_seconds)
    raise TimeoutError(
        f"AgentCore Memory {memory_id} did not reach ACTIVE within {timeout_seconds}s"
    )


class AgentCoreMemoryHook:
    """Strands ``HookProvider`` that persists each chat turn to AgentCore.

    Subscribes to ``AfterInvocationEvent`` (fires once per turn). The
    sync callback fires-and-forgets via ``asyncio.create_task`` so the
    SSE response doesn't wait on AgentCore. Write failures are logged
    + EMF-counted + swallowed — memory must not break chats.

    Conforms to the ``HookProvider`` protocol (``strands.hooks.registry``)
    structurally; no explicit base class — Strands uses
    ``@runtime_checkable``.

    See ``docs/superpowers/specs/2026-05-31-phase-7c-agentcore-memory-writes-design.md``
    §Per-turn write and §Failure mode for rationale.
    """

    def __init__(
        self,
        memory_id: str,
        actor_id: str,
        session_id: str,
        client: Any | None = None,
    ) -> None:
        self._memory_id = memory_id
        # actor_id is sanitized at the hook boundary so callers can pass
        # the raw JWT sub (email-form or otherwise) without worrying
        # about AgentCore's regex constraints.
        self._actor_id = _sanitize_actor_id(actor_id)
        self._session_id = session_id
        self._client = client if client is not None else boto3.client("bedrock-agentcore")
        # Strong refs to in-flight writes — prevents Python's GC from
        # collecting the task before AgentCore replies. See Sonar
        # python:S7502 and asyncio.create_task() docs. Shared by the
        # per-turn write path and ``write_meta_event``'s async dispatch.
        self._pending_writes: set[asyncio.Task[None]] = set()

    def register_hooks(self, registry: Any, **_: Any) -> None:
        registry.add_callback(AfterInvocationEvent, self._on_after_invocation)

    def _on_after_invocation(self, event: AfterInvocationEvent) -> None:
        """Sync entry point — schedule the async write, return immediately.

        The task reference is held in ``_pending_writes`` (instance set) +
        discarded on completion via ``add_done_callback``. Without the
        strong reference, Python's GC can reclaim the task before it
        finishes — see asyncio docs (Sonar python:S7502).
        """
        task = asyncio.create_task(self._on_after_invocation_async(event))
        self._pending_writes.add(task)
        task.add_done_callback(self._pending_writes.discard)

    async def _on_after_invocation_async(self, event: AfterInvocationEvent) -> None:
        """Async write path. Log + swallow on failure."""
        try:
            # Last two messages on the agent are the just-completed
            # user+assistant pair. Strands types these as ``Message``
            # (a TypedDict); ``_payload_from_messages`` reads them as
            # plain dicts. ``cast`` keeps mypy happy without forcing
            # consumers of this private helper to import Strands types.
            messages = cast(list[dict[str, Any]], list(event.agent.messages)[-2:])
            payload = _payload_from_messages(messages)
            await asyncio.to_thread(
                self._client.create_event,
                memoryId=self._memory_id,
                actorId=self._actor_id,
                sessionId=self._session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=payload,
            )
            await record_memory_write_outcome(success=True)
        except Exception as exc:
            # ``error_type`` + ``error_message`` are in the JSON formatter's
            # allowed-extras list (channel.logging_config._JsonFormatter);
            # ``actor_id`` / ``session_id`` aren't, so they're folded into
            # the message text. ``exc_info=True`` makes the formatter emit
            # ``stack_trace`` so we can actually diagnose AgentCore
            # validation failures instead of seeing a bare warning line.
            logger.warning(
                "agentcore.create_event_failed actor_id=%s session_id=%s",
                self._actor_id,
                self._session_id,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )
            await record_memory_write_outcome(success=False)

    def write_meta_event(self, text: str) -> None:
        """Sync entry point — schedule the async ``CreateEvent``, return immediately.

        Called by ``ToolCallTelemetryHook`` after each tool call (epic
        #128 decision 6). One event per tool call, not per chain. The
        recall hook (``recall.py``) tags ``[meta]``-prefixed events and
        renders them differently in the system-prompt addendum.

        **Non-blocking.** Mirrors ``_on_after_invocation`` — boto3's
        ``create_event`` is sync I/O; calling it directly from this
        method would block the event loop during ``agent.stream_async``
        and stall SSE streaming for the duration of the AgentCore round
        trip. We dispatch via ``asyncio.create_task`` +
        ``asyncio.to_thread`` so the tool result returns to the model
        immediately and the META write happens off the event loop.

        Fail-soft: errors log + swallow but never raise — the tool
        result is already in the chain; failure to record the META
        fact must not break the user-visible reply. No EMF counter is
        emitted here (cf. ``_on_after_invocation_async`` which does
        bump ``MemoryWriteFailures``) — META writes are best-effort
        side-channel telemetry, not the primary memory write path.
        """
        task = asyncio.create_task(self._write_meta_event_async(text))
        self._pending_writes.add(task)
        task.add_done_callback(self._pending_writes.discard)

    async def _write_meta_event_async(self, text: str) -> None:
        """Async write path for ``write_meta_event``. Log + swallow on failure."""
        try:
            await asyncio.to_thread(
                self._client.create_event,
                memoryId=self._memory_id,
                actorId=self._actor_id,
                sessionId=self._session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=[
                    {
                        "conversational": {
                            "role": "ASSISTANT",
                            "content": {"text": f"[meta] {text}"},
                        }
                    }
                ],
            )
        except Exception as exc:
            logger.warning(
                "agentcore.write_meta_event_failed actor_id=%s session_id=%s",
                self._actor_id,
                self._session_id,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )


def _payload_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Strands message dicts to AgentCore ``CreateEvent`` payload.

    INVARIANT (epic #128 decision 7, 2026-06-03): Tool payloads —
    ``toolUse`` and ``toolResult`` content blocks — never persist to
    AgentCore Memory. Only conclusion-shaped text survives. Tool-use
    META facts are recorded separately via the AfterToolCallEvent hook
    (see ``src/channel/agents/tool_hooks.py``). Verified explicitly by
    ``tests/unit/test_memory.py::test_payload_from_messages_invariant_drops_tool_use_blocks``.

    Strands messages have shape
    ``{"role": str, "content": [{"text": str}, ...]}``. AgentCore's
    payload is a list of typed conversational entries with role
    upper-cased and ``content.text`` as a single string.

    Multi-block content (text + toolUse interleaved) gets its text
    blocks concatenated; non-text blocks are dropped per the invariant.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = _ROLE_MAP.get(msg["role"])
        if role is None:
            raise ValueError(f"unsupported role: {msg['role']!r}")
        text = "".join(block["text"] for block in msg.get("content", []) if "text" in block)
        out.append({"conversational": {"role": role, "content": {"text": text}}})
    return out
