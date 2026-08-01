# Copyright (c) 2026 John Carter. All rights reserved.
"""Bedrock AgentCore Memory recall via Strands hooks.

Phase 8a implementation. Subscribes to ``BeforeInvocationEvent`` and
injects prior-conversation context (scoped to the caller's ``actorId``)
into the system prompt as a Markdown addendum. Uses ``ListSessions`` +
``ListEvents`` for synchronous recall — the Phase 7d
``SemanticMemoryStrategy``/``RetrieveMemoryRecords`` approach had
hours-long ingestion lag that made it unusable in practice.

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
from datetime import datetime as _dt
from datetime import timezone as _tz
from typing import Any

import boto3
from strands.hooks.events import BeforeInvocationEvent

from channel.agents.memory import _sanitize_actor_id
from channel.metrics import record_recall_outcome

logger = logging.getLogger(__name__)

_RECALL_HEADING = "## What we've talked about before"
_RECALL_CACHE_REFRESH_TURNS: int = 5
_RECALL_ROLE_MAP: dict[str, str] = {"USER": "You", "ASSISTANT": "Me"}

# Phase 8a: ListSessions + ListEvents recall constants.
# Replace the SemanticMemoryStrategy/RetrieveMemoryRecords approach
# which had hours-long ingestion lag (unusable in practice).
_RECALL_MAX_SESSIONS: int = 5
_RECALL_EVENTS_PER_SESSION: int = 2
_RECALL_EVENT_TEXT_TRUNCATE: int = 120

# Sentinel used as the sort-key default when a session has no ``createdAt``.
# Using ``datetime.min`` (tz-aware) ensures datetime objects compare correctly.
_EPOCH = _dt(1970, 1, 1, tzinfo=_tz.utc)


def _iso_date(value: Any) -> str:
    """Return ``YYYY-MM-DD`` for a datetime or string; empty string for None.

    boto3 deserializes AgentCore ``createdAt`` fields as ``datetime.datetime``
    objects.  This helper normalizes them to a plain date string at the API
    boundary so the rest of the module never needs to handle datetimes.
    """
    if value is None:
        return ""
    if hasattr(value, "strftime"):  # datetime.datetime or datetime.date
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


@dataclass
class CacheEntry:
    """Per-(actor, chat) recall cache row. ``age`` increments on cache
    hits; ``records`` is one entry per prior session with shape
    ``{sessionId, createdAt, payload}`` from the most recent
    ``ListSessions`` + ``ListEvents`` fetch."""

    records: list[dict[str, Any]]
    age: int


# Module-level cache keyed by ``(actor_id, chat_id)``. Survives across
# requests within a warm Lambda instance; cold-start invalidates.
_recall_cache: dict[tuple[str, str], CacheEntry] = {}


def _format_recall_addendum(records: list[dict[str, Any]]) -> str:
    """Render aggregated ListEvents output as a Markdown addendum.

    Records are grouped by ``sessionId``; each group gets a header
    derived from ``createdAt`` (date only — time-of-day is noise).
    Within each group, AgentCore ``payload[].conversational``
    messages become turn bullets::

        ## What we've talked about before

        **Earlier conversation (2026-05-31)**
        - You: i love sage green
        - Me: sage is great

    Returns the empty string if no records have usable text.
    Defensive: payload entries missing ``conversational.content.text``
    or with empty text are silently skipped so a malformed AgentCore
    response can't corrupt the system prompt.
    """
    if not records:
        return ""

    # Group by sessionId, preserving the order in ``records`` (which is
    # already most-recent first from ListSessions).
    groups: dict[str, dict[str, Any]] = {}
    for rec in records:
        sid = rec.get("sessionId", "")
        if not sid:
            continue
        # Invariant: _get_or_fetch_records emits exactly one record per
        # sessionId, so setdefault never collides. If upstream is ever
        # refactored to emit multiple records per session, the later
        # createdAt would be silently dropped here.
        group = groups.setdefault(
            sid,
            {"createdAt": rec.get("createdAt", ""), "bullets": []},
        )
        for msg in rec.get("payload", []):
            conv = msg.get("conversational") or {}
            role_raw = conv.get("role", "")
            text = (conv.get("content") or {}).get("text", "")
            if not text:
                continue
            role_label = _RECALL_ROLE_MAP.get(role_raw, role_raw or "?")
            if len(text) > _RECALL_EVENT_TEXT_TRUNCATE:
                text = text[:_RECALL_EVENT_TEXT_TRUNCATE] + "..."
            group["bullets"].append(f"- {role_label}: {text}")

    blocks: list[str] = []
    for group in groups.values():
        if not group["bullets"]:
            continue
        date = group["createdAt"] or "earlier"
        blocks.append(f"**Earlier conversation ({date})**\n" + "\n".join(group["bullets"]))

    if not blocks:
        return ""
    return _RECALL_HEADING + "\n\n" + "\n\n".join(blocks)


def _extract_user_message(event: BeforeInvocationEvent) -> str:
    """Read the most recent user-turn text off the event's messages."""
    # Strands types ``event.messages`` as ``list[Message] | None`` but in
    # practice the agent loop always populates it before the hook fires.
    # Guard defensively anyway.
    messages = event.messages or []
    for msg in reversed(messages):
        if msg.get("role") == "user":
            for block in msg.get("content", []):
                if "text" in block:
                    return str(block["text"])
    return ""


def _append_to_system_prompt(event: BeforeInvocationEvent, addendum: str) -> None:
    """Append the recall block to the agent's system prompt.

    Strands keeps ``system_prompt`` as a separate field on the
    ``Agent`` instance (set at construction time, read by the event
    loop when building each request). ``event.messages`` is the
    *conversation* (prior turns + new user turn); mutating
    ``messages[0]`` would land the addendum inside the user message —
    which is exactly the Phase 8a Layer-3 bug we hit on dev (#95).

    Each turn builds a fresh Agent via ``chat_agent.build_agent``,
    so mutating ``agent.system_prompt`` here is safe — no leak across
    turns. Defensive: if the agent has no system_prompt for any
    reason, set it to the addendum directly.
    """
    agent = event.agent
    current = getattr(agent, "system_prompt", None) or ""
    agent.system_prompt = (current + "\n\n" + addendum) if current else addendum


class AgentCoreRecallHook:
    """Strands ``HookProvider`` that injects AgentCore Memory recall
    into the system prompt before each turn.

    Subscribes to ``BeforeInvocationEvent``; runs ``ListSessions`` +
    per-session ``ListEvents`` scoped to the caller's ``actorId``;
    injects the aggregated conversational history as a Markdown
    system-prompt addendum. Failures are logged + EMF-counted + swallowed
    — recall must not break chats.

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

    def register_hooks(self, registry: Any, **_: Any) -> None:
        # Register the ASYNC callback directly. Strands'
        # ``HookRegistry.invoke_callbacks_async`` detects coroutine
        # functions via ``inspect.iscoroutinefunction`` and ``await``s
        # them, so we get synchronous-completion semantics in an async
        # context. Phase 8a Layer-3 (#97): the prior fire-and-forget
        # pattern raced against the model invocation — Strands would
        # build the request payload (capturing ``agent.system_prompt``)
        # before the recall task had a chance to mutate it. Awaiting
        # the callback guarantees the addendum is in place before the
        # model call begins.
        registry.add_callback(BeforeInvocationEvent, self._on_before_invocation)

    async def _on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        """Async recall path. Log + swallow on failure.

        ``chat_id`` is carried on ``event.agent.chat_id`` (set at
        agent-build time in ``chat_agent.build_agent``). Strands'
        agent doesn't expose chat_id natively; this is a
        Channel-specific attribute.
        """
        if os.environ.get("CHANNEL_RECALL_ENABLED", "1") != "1":
            return
        chat_id = getattr(event.agent, "chat_id", None) or ""

        try:
            records = await self._get_or_fetch_records(
                user_message=_extract_user_message(event),
                chat_id=chat_id,
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
        self,
        *,
        user_message: str,
        chat_id: str,
    ) -> list[dict[str, Any]]:
        """Return cached records when fresh; refetch when stale or missing.

        Phase 8a: synchronous recall via ListSessions + ListEvents per
        session. Excludes the current chat (PR #73 already feeds that
        history into Strands via ``Agent(messages=...)``). Caps at
        ``_RECALL_MAX_SESSIONS`` prior sessions to bound prompt size.

        ``user_message`` is no longer used for retrieval (we don't do
        semantic search anymore) — kept on the signature for cache
        parity and to match the BeforeInvocationEvent contract.

        Returns one record per prior session with shape
        ``{sessionId, createdAt, payload}``, where ``payload`` is the
        concatenation of all that session's events' payloads in arrival
        order. ``_format_recall_addendum`` renders these into Markdown.
        """
        del user_message  # no longer used for retrieval
        key = (self._actor_id, chat_id)
        cache_entry = _recall_cache.get(key)
        if cache_entry is not None and cache_entry.age < _RECALL_CACHE_REFRESH_TURNS:
            cache_entry.age += 1
            return cache_entry.records

        aggregated = await self._fetch_records(chat_id=chat_id)

        # ``age=1`` counts the cold-fetch turn as the 1st served turn —
        # next 4 turns are cache hits (ages 2..5), 6th turn triggers refresh.
        _recall_cache[key] = CacheEntry(records=aggregated, age=1)
        return aggregated

    async def _fetch_records(self, *, chat_id: str) -> list[dict[str, Any]]:
        """Fetch fresh recall records via ``ListSessions`` + per-session
        ``ListEvents`` — the uncached read that ``_get_or_fetch_records``
        wraps with the 5-turn cache.

        Pure read: performs NO caching and mutates no module-level state,
        so it is safe to call outside the live-turn path (the
        ``/api/_debug/recall/inspect`` inspection endpoint, #227) without
        polluting ``_recall_cache`` or perturbing the age counters that a
        real turn relies on.

        Returns one record per prior session with shape
        ``{sessionId, createdAt, payload}`` — see ``_get_or_fetch_records``.
        """
        # Step 1: list this actor's sessions, exclude the current chat.
        sessions_resp = await asyncio.to_thread(
            self._client.list_sessions,
            memoryId=self._memory_id,
            actorId=self._actor_id,
        )
        sessions = sessions_resp.get("sessionSummaries", [])
        prior_sessions = [s for s in sessions if s.get("sessionId") != chat_id]
        # Explicit newest-first ordering; don't rely on AgentCore's default.
        prior_sessions.sort(key=lambda s: s.get("createdAt") or _EPOCH, reverse=True)
        prior_sessions = prior_sessions[:_RECALL_MAX_SESSIONS]

        # Step 2: fetch the last K events from each prior session and
        # flatten all events' payloads into one combined list per session.
        aggregated: list[dict[str, Any]] = []
        for session in prior_sessions:
            events_resp = await asyncio.to_thread(
                self._client.list_events,
                memoryId=self._memory_id,
                actorId=self._actor_id,
                sessionId=session["sessionId"],
                maxResults=_RECALL_EVENTS_PER_SESSION,
            )
            combined_payload: list[dict[str, Any]] = []
            # ListEvents returns newest-first; reverse so combined_payload reads
            # chronologically within each session (oldest event first).
            for ev in reversed(events_resp.get("events", [])):
                combined_payload.extend(ev.get("payload", []))
            if combined_payload:
                aggregated.append(
                    {
                        "sessionId": session["sessionId"],
                        "createdAt": _iso_date(session.get("createdAt")),
                        "payload": combined_payload,
                    }
                )
        return aggregated

    async def preview_addendum(self, *, chat_id: str) -> tuple[str, list[dict[str, Any]]]:
        """Return ``(addendum_text, records)`` the hook WOULD inject for
        ``chat_id`` — computed WITHOUT firing a real turn and WITHOUT
        touching the 5-turn ``_recall_cache``.

        Diagnostic-only surface for ``/api/_debug/recall/inspect`` (#227).
        Reuses the exact fetch + formatting path the live hook runs
        (``_fetch_records`` + ``_format_recall_addendum``) so the preview
        matches what really gets injected. Two deliberate differences from
        the live path, both to make the endpoint a faithful *right now*
        probe rather than a replay of hook state:

        - **Always a fresh fetch** — the block reflects the current
          AgentCore Memory state. On a warm Lambda a live turn that hits
          the cache could inject a block up to ``_RECALL_CACHE_REFRESH_TURNS``
          turns stale; the preview shows the un-cached truth.
        - **Ignores the kill-switch** — the block is computed even when
          ``CHANNEL_RECALL_ENABLED=0`` so it stays inspectable during an
          A/B comparison (the endpoint reports the flag separately).

        Each record's ``sessionId`` is the source chat id (``sessionId ==
        chat_id`` by design — CLAUDE.md §AgentCore Memory), giving the
        caller per-fragment provenance.
        """
        records = await self._fetch_records(chat_id=chat_id)
        return _format_recall_addendum(records), records
