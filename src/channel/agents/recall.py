# Copyright (c) 2026 John Carter. All rights reserved.
"""Bedrock AgentCore Memory recall via Strands hooks.

Phase 7d implementation. Subscribes to ``BeforeInvocationEvent`` and
injects relevant prior-conversation context (scoped to the caller's
``actorId``) into the system prompt as a Markdown addendum.

Design rationale: ``docs/superpowers/specs/2026-05-31-phase-7d-memory-recall-auto-titling-design.md``.

Strands 1.41 has no native AgentCore Memory adapter (verified in the
spike at ``docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md``)
so this module talks to AgentCore directly via boto3 — same pattern as
the 7c write hook in ``agents/memory.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import boto3
from strands.hooks.events import BeforeInvocationEvent

from channel.agents.memory import _sanitize_actor_id
from channel.metrics import record_recall_outcome

logger = logging.getLogger(__name__)

_RECALL_HEADING = "## What I remember about previous conversations"
_RECALL_SCORE_THRESHOLD: float = 0.7
_RECALL_TOP_K: int = 5
_RECALL_CACHE_REFRESH_TURNS: int = 5


@dataclass
class CacheEntry:
    """Per-(actor, chat) recall cache row. ``age`` increments on cache
    hits; ``records`` is the score-filtered ``MemoryRecordSummary`` list
    from the most recent ``RetrieveMemoryRecords`` response."""

    records: list[dict[str, Any]]
    age: int


# Module-level cache keyed by ``(actor_id, chat_id)``. Survives across
# requests within a warm Lambda instance; cold-start invalidates.
_recall_cache: dict[tuple[str, str], CacheEntry] = {}


def _format_recall_addendum(records: list[dict[str, Any]]) -> str:
    """Render ``MemoryRecordSummary`` records as a Markdown addendum.

    Returns the empty string when no records have usable text content.
    Defensive: records missing ``content.text`` or with empty text are
    silently skipped so a malformed AgentCore response can't corrupt
    the system prompt.
    """
    bullets: list[str] = []
    for rec in records:
        text = rec.get("content", {}).get("text") if isinstance(rec, dict) else None
        if text:
            bullets.append(f"- {text}")
    if not bullets:
        return ""
    return _RECALL_HEADING + "\n\n" + "\n".join(bullets)


def _extract_user_message(event: BeforeInvocationEvent) -> str:
    """Read the most recent user-turn text off the event's messages."""
    for msg in reversed(event.messages):
        if msg.get("role") == "user":
            for block in msg.get("content", []):
                if "text" in block:
                    return str(block["text"])
    return ""


def _append_to_system_prompt(event: BeforeInvocationEvent, addendum: str) -> None:
    """Mutate the system message in place to append the recall block.

    Strands' system prompt is always at ``event.messages[0]``; we append
    the addendum to its first text block. Defensive: if the system
    message has no text block (shouldn't happen with our prompt), we
    create one.
    """
    sys_msg = event.messages[0]
    content = sys_msg.setdefault("content", [])
    if content and "text" in content[0]:
        content[0]["text"] = content[0]["text"] + "\n\n" + addendum
    else:
        content.append({"text": addendum})


class AgentCoreRecallHook:
    """Strands ``HookProvider`` that injects AgentCore Memory recall
    into the system prompt before each turn.

    Subscribes to ``BeforeInvocationEvent``; runs ``RetrieveMemoryRecords``
    scoped to the caller's ``actorId``; filters by score; injects the
    survivors as a Markdown system-prompt addendum. Failures are logged
    + EMF-counted + swallowed — recall must not break chats.

    Conforms to the ``HookProvider`` protocol (``strands.hooks.registry``)
    structurally; no explicit base class — Strands uses ``@runtime_checkable``.

    See ``docs/superpowers/specs/2026-05-31-phase-7d-memory-recall-auto-titling-design.md``
    §Recall trigger + caching and §Recall injection format for rationale.
    """

    def __init__(
        self,
        memory_id: str,
        actor_id: str,
        client: Any | None = None,
    ) -> None:
        self._memory_id = memory_id
        # Sanitize at the hook boundary so callers can pass raw JWT
        # sub (email-form or otherwise) — same pattern as the write
        # hook. AgentCore's actorId/namespace regex rejects ``@`` and ``.``.
        self._actor_id = _sanitize_actor_id(actor_id)
        self._client = client if client is not None else boto3.client("bedrock-agentcore")
        # Strong refs to in-flight recall tasks (Sonar python:S7502, same
        # pattern as the 7c write hook).
        self._pending_recalls: set[asyncio.Task[None]] = set()

    def register_hooks(self, registry: Any, **_: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self._on_before_invocation)

    def _on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        """Sync entry point — schedule the async recall, return immediately.

        ``chat_id`` is carried on ``event.agent.chat_id`` (set at
        agent-build time in ``chat_agent.build_agent``). Strands' agent
        doesn't expose chat_id natively; this is a Channel-specific
        attribute.
        """
        chat_id = getattr(event.agent, "chat_id", None) or ""
        task = asyncio.create_task(
            self._on_before_invocation_async(event, chat_id=chat_id)
        )
        self._pending_recalls.add(task)
        task.add_done_callback(self._pending_recalls.discard)

    async def _on_before_invocation_async(
        self, event: BeforeInvocationEvent, *, chat_id: str,
    ) -> None:
        """Async recall path. Log + swallow on failure."""
        if os.environ.get("STARTER_RECALL_ENABLED", "1") != "1":
            return

        try:
            records = await self._get_or_fetch_records(
                user_message=_extract_user_message(event), chat_id=chat_id,
            )
            addendum = _format_recall_addendum(records)
            if addendum:
                _append_to_system_prompt(event, addendum)
            await record_recall_outcome(success=True)
        except Exception as exc:
            logger.warning(
                "agentcore.recall_failed actor_id=%s chat_id=%s",
                self._actor_id,
                chat_id,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )
            await record_recall_outcome(success=False)

    async def _get_or_fetch_records(
        self, *, user_message: str, chat_id: str,
    ) -> list[dict[str, Any]]:
        """Return cached records when fresh; refetch when stale or missing.

        ``age`` counts how many turns the current cache has served,
        including the cold-fetch turn. Refresh when ``age >= 5`` so
        the cache lifetime is exactly 5 turns (cold-fetch + 4 hits,
        then re-fetch on the 6th turn).
        """
        key = (self._actor_id, chat_id)
        cache_entry = _recall_cache.get(key)
        if cache_entry is not None and cache_entry.age < _RECALL_CACHE_REFRESH_TURNS:
            cache_entry.age += 1
            return cache_entry.records

        # AgentCore's RetrieveMemoryRecords takes ``namespace`` (a
        # prefix), NOT ``actorId``. The actor is encoded INTO the
        # namespace per the strategy's namespace template configured at
        # CreateMemory time. The default SemanticMemoryStrategy uses
        # ``/strategies/{memoryStrategyId}/actors/{actorId}`` — we use
        # ``/actors/{actorId}`` as a prefix which matches all strategies
        # for this actor (lets us evolve strategy configuration without
        # changing this query).
        #
        # Records are async — there's a multi-minute lag between
        # CreateEvent and the strategy emitting a queryable record. Early
        # turns in a new chat will see empty recall; subsequent turns
        # surface what's been ingested.
        resp = await asyncio.to_thread(
            self._client.retrieve_memory_records,
            memoryId=self._memory_id,
            namespace=f"/actors/{self._actor_id}",
            searchCriteria={"searchQuery": user_message, "topK": _RECALL_TOP_K},
        )
        records = [
            rec
            for rec in resp.get("memoryRecordSummaries", [])
            if isinstance(rec, dict) and rec.get("score", 0) >= _RECALL_SCORE_THRESHOLD
        ]
        # ``age=1`` counts the cold-fetch turn as the 1st served turn —
        # next 4 turns are cache hits (ages 2..5), 6th turn triggers refresh.
        _recall_cache[key] = CacheEntry(records=records, age=1)
        return records
