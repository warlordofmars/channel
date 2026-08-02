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
import hashlib
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

# Tool-use META facts are written as synthetic ASSISTANT events tagged
# with this prefix (see :meth:`AgentCoreMemoryHook.write_meta_event`).
# Module-level so downstream readers — ``agents/memory_records.py``'s
# provenance classifier (#475) — import it rather than re-literalling
# ``"[meta]"``; three copies of the same magic string would drift.
# Parallels ``_REMEMBER_PREFIX`` in ``agents/tools/memory_tools.py``.
_META_PREFIX = "[meta]"

# AgentCore validates ``actorId`` against
# ``[a-zA-Z0-9][a-zA-Z0-9-_/]*(?::[a-zA-Z0-9-_/]+)*[a-zA-Z0-9-_/]*`` with
# ``min=1``/``max=255`` (botocore ``bedrock-agentcore`` ``ActorId`` shape) —
# letters, digits, hyphens, underscores, slashes, colons, and a
# LEADING character that must be alphanumeric. Our JWT ``sub`` is the
# user's email (Google OAuth path) or an arbitrary string (other
# paths); the email form contains ``@`` and ``.`` which fail the regex.
_ACTOR_ID_DISALLOWED_RE = re.compile(r"[^a-zA-Z0-9_/-]")
# The leading char must be ``[a-zA-Z0-9]``; strip any run of non-alphanumerics
# the substitution above may have left at the front (``.foo`` → ``_foo``).
_ACTOR_ID_LEADING_NON_ALNUM_RE = re.compile(r"^[^a-zA-Z0-9]+")

# Readability budget for the human-legible label prefix. Lossy by
# design — the digest below is what carries identity.
_ACTOR_ID_LABEL_MAX_CHARS = 48
# 32 hex chars = 128 bits of SHA-256. Birthday-bound ~2**64 distinct
# subs before a collision becomes probable; the realistic user set is
# many orders of magnitude below that, and JWT subs are issued by the
# IdP rather than chosen by the caller, so there is no grinding attack
# against the truncation. Fixed-width, which is what makes the suffix
# unambiguously recoverable (see the injectivity argument below).
_ACTOR_ID_DIGEST_HEX_CHARS = 32


def derive_actor_id(jwt_sub: str) -> str:
    """Derive a stable, **injective** AgentCore ``actorId`` from ``jwt_sub``.

    THE single derivation for the whole codebase — the recall hook, the
    memory tools, the chat-delete session wipe, and the debug endpoints
    all import this function rather than re-deriving. One AgentCore
    Memory resource serves an entire environment and ``actorId`` is its
    ONLY partition, so two users mapping to one ``actorId`` means two
    users sharing a memory store: A's turns get written under B's actor
    and ``AgentCoreRecallHook`` replays them into B's system prompt.
    Injectivity is therefore a cross-user isolation boundary, not
    hygiene (issue #474).

    Shape: ``{label}-{sha256(jwt_sub)[:32]}``, or the bare digest when
    the label is empty.

    - **The digest carries identity.** ``label`` is a lossy, purely
      cosmetic slug so an operator can recognise an actor in the
      AgentCore console; distinct subs may share a label, never a
      digest.
    - **Injective.** The digest is fixed-width and always occupies the
      final 32 characters, so the split point is unambiguous: equal
      outputs imply equal digests, and equal digests imply equal inputs
      up to SHA-256 collision resistance. A bare-digest output (exactly
      32 chars, no label) can never equal a labelled one (≥ 34 chars).
    - **Stable forever.** Pure function of the input bytes — no salt, no
      clock, no per-process state. Pinned by golden vectors in
      ``tests/unit/test_memory.py``.
    - **Charset-safe.** Label chars are drawn from ``[a-zA-Z0-9_/-]``
      with an alphanumeric lead; the digest is lowercase hex. Max length
      is ``48 + 1 + 32 = 81`` — comfortably inside AgentCore's 255.
      An empty ``jwt_sub`` still yields a valid (bare-digest) id rather
      than the empty string AgentCore's ``min=1`` would reject.

    Why hash-suffix rather than the alternatives: a bare
    ``sha256`` (issue #474's option 1) is injective but makes every
    actor opaque in the console, and base32/base64url of the raw sub
    (option 2) is reversible but equally unreadable and unbounded in
    length. Keeping a readable prefix preserves exactly the operability
    the previous scheme had — the sub is already legible in AgentCore
    today, so this is no new disclosure — while the suffix supplies the
    injectivity it lacked. Recovering the sub from an id is a
    hash-and-compare against known subs, not a decode, which is
    sufficient for the one real ops question ("whose partition is
    this?").

    Forward-compatible with per-workspace partitioning ("workspaces are
    the tenancy root"): ``derive_actor_id(f"{workspace_id}/{user_id}")``
    keeps the whole composite key inside the digest.
    """
    digest = hashlib.sha256(jwt_sub.encode("utf-8")).hexdigest()[:_ACTOR_ID_DIGEST_HEX_CHARS]
    label = _ACTOR_ID_LEADING_NON_ALNUM_RE.sub(
        "",
        _ACTOR_ID_DISALLOWED_RE.sub("_", jwt_sub)[:_ACTOR_ID_LABEL_MAX_CHARS],
    )
    return f"{label}-{digest}" if label else digest


def _legacy_lossy_actor_id(jwt_sub: str) -> str:
    """The pre-#474 derivation: every disallowed char replaced by ``_``.

    **Not called by any read or write path** — ``derive_actor_id`` is.
    Retained deliberately, for two reasons:

    1. It is the regression oracle for the injectivity property test:
       the test asserts this function collapses the known collision
       pairs (``jc+work@x.com`` / ``jc_work@x.com``,
       ``a.b@x.com`` / ``a@b.x.com``) and that ``derive_actor_id`` does
       not. Keeping the broken mapping executable is what stops the
       fix from silently regressing into a comment.
    2. It keeps the abandoned partitions *addressable*. Events written
       before #474 live under these ids, and the migration decision was
       to strand them rather than dual-read (a fallback read of a lossy
       partition is the very cross-user disclosure the fix exists to
       close). An offline cleanup or copy-forward tool needs to compute
       the old id from a sub; this is that computation. See the PR for
       #474 and CLAUDE.md §AgentCore Memory.
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
    ``CHANNEL_AGENTCORE_MEMORY_NAME`` — useful for pointing a personal
    dev environment at a pre-existing Memory resource. AgentCore
    requires names to match ``[a-zA-Z][a-zA-Z0-9_]{0,47}`` — letters,
    digits, underscores only; hyphens are rejected at the service
    layer.
    """
    if env in _memory_id_cache:
        return _memory_id_cache[env]

    name = os.environ.get("CHANNEL_AGENTCORE_MEMORY_NAME") or f"channel_{env}"
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
        # actor_id is derived at the hook boundary so callers can pass
        # the raw JWT sub (email-form or otherwise) without worrying
        # about AgentCore's regex constraints — or about the injectivity
        # of the mapping (#474).
        self._actor_id = derive_actor_id(actor_id)
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
            if not payload:
                # An all-empty turn (every entry had empty/whitespace-only
                # text) leaves nothing to persist. Skip ``create_event``
                # entirely — a no-op turn is neither a write success nor a
                # write failure, so do NOT bump
                # ``MemoryWriteSuccesses`` / ``MemoryWriteFailures``.
                # Debug-log so an investigation can still see it without
                # warning-level noise. See #392.
                logger.debug(
                    "memory.skip_empty_event actor_id=%s session_id=%s",
                    self._actor_id,
                    self._session_id,
                )
                return
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
        ``[meta] `` prefix tags the event as a tool-use META fact — a
        recognizable marker for future filtering or condensing in the
        recall hook or other consumers. The current recall hook does
        not special-case the prefix; it reads ``[meta]``-tagged events
        as normal conversational text.

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
        # Guard the empty-body case: a ``[meta]``-prefixed event whose
        # body ``text`` is empty/whitespace-only carries no META fact and
        # would only add noise, so skip it before scheduling any write.
        # (The ``[meta] `` prefix means the wire payload wouldn't itself
        # be zero-length, but an empty body is still a no-op worth
        # dropping.) Mirrors the primary-path guard in
        # ``_on_after_invocation_async``. See #392.
        if not text.strip():
            logger.debug(
                "memory.skip_empty_meta_event actor_id=%s session_id=%s",
                self._actor_id,
                self._session_id,
            )
            return
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
                            "content": {"text": f"{_META_PREFIX} {text}"},
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

    This same strip is the #326 asset-content memory guard (epic #321
    derived decision): code-exec image payloads ride ``toolResult``
    blocks (dropped here), upload payloads ride ``document`` /
    ``image`` blocks (no ``"text"`` key — dropped by the text-only
    join below), and fenced-code extraction runs post-stream on the
    settled assistant text without ever mutating the agent's message
    list. Pinned by
    ``tests/unit/test_memory.py::test_payload_from_messages_never_leaks_asset_content``.

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
        # Skip zero-length / whitespace-only entries. AgentCore's
        # ``create_event`` validator rejects an empty ``content.text``
        # (``ParamValidationError: Invalid length for parameter
        # payload[0]...text, value: 0``), so a turn whose text content is
        # empty has nothing eligible to persist — dropping it here lets
        # the caller skip ``create_event`` for an all-empty turn instead
        # of manufacturing a doomed write. See #392.
        if not text.strip():
            continue
        out.append({"conversational": {"role": role, "content": {"text": text}}})
    return out
