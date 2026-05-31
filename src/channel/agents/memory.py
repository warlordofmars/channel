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

    created = control.create_memory(
        name=name,
        memoryStrategies=[],
        eventExpiryDuration=90,
    )
    memory_id = created["memory"]["id"]
    _memory_id_cache[env] = memory_id
    return memory_id


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
        # python:S7502 and asyncio.create_task() docs.
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


def _payload_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Strands message dicts to AgentCore ``CreateEvent`` payload.

    Strands messages have shape
    ``{"role": str, "content": [{"text": str}, ...]}``. AgentCore's
    payload is a list of typed conversational entries with role
    upper-cased and ``content.text`` as a single string.

    Multi-block content (text + toolUse interleaved) gets its text
    blocks concatenated. Non-text blocks (toolUse, toolResult) are
    dropped — AgentCore's v1 payload spec only accepts text.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = _ROLE_MAP.get(msg["role"])
        if role is None:
            raise ValueError(f"unsupported role: {msg['role']!r}")
        text = "".join(block["text"] for block in msg.get("content", []) if "text" in block)
        out.append({"conversational": {"role": role, "content": {"text": text}}})
    return out
