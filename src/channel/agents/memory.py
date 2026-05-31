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
from datetime import datetime, timezone
from typing import Any

import boto3
from strands.hooks.events import AfterInvocationEvent

from channel.metrics import record_memory_write_outcome

logger = logging.getLogger(__name__)

_ROLE_MAP: dict[str, str] = {"user": "USER", "assistant": "ASSISTANT"}

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
    ``channel-dev-A1B2C3D4``). Look up by **name**, not by id, or
    re-deploys against an existing Memory will create duplicates.

    Override the default ``channel-{env}`` naming via
    ``STARTER_AGENTCORE_MEMORY_NAME`` — useful for pointing a personal
    dev environment at a pre-existing Memory resource.
    """
    if env in _memory_id_cache:
        return _memory_id_cache[env]

    name = os.environ.get("STARTER_AGENTCORE_MEMORY_NAME") or f"channel-{env}"
    control = boto3.client("bedrock-agentcore-control")

    existing = control.list_memories()
    for mem in existing.get("memorySummaries", []):
        if mem["name"] == name:
            _memory_id_cache[env] = mem["id"]
            return mem["id"]

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
        self._actor_id = actor_id
        self._session_id = session_id
        self._client = client if client is not None else boto3.client("bedrock-agentcore")

    def register_hooks(self, registry: Any, **_: Any) -> None:
        registry.add_callback(AfterInvocationEvent, self._on_after_invocation)

    def _on_after_invocation(self, event: AfterInvocationEvent) -> None:
        """Sync entry point — schedule the async write, return immediately."""
        asyncio.create_task(self._on_after_invocation_async(event))

    async def _on_after_invocation_async(self, event: AfterInvocationEvent) -> None:
        """Async write path. Log + swallow on failure."""
        try:
            # Last two messages on the agent are the just-completed
            # user+assistant pair.
            messages = list(event.agent.messages)[-2:]
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
            logger.warning(
                "agentcore.create_event_failed",
                extra={
                    "err": str(exc),
                    "actor_id": self._actor_id,
                    "session_id": self._session_id,
                },
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
        text = "".join(
            block["text"] for block in msg.get("content", []) if "text" in block
        )
        out.append({"conversational": {"role": role, "content": {"text": text}}})
    return out
