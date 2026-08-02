# Copyright (c) 2026 John Carter. All rights reserved.
"""``remember`` / ``recall`` — agent-driven persistent memory tools (#273).

Two native Strands ``@tool`` callables that give the model deliberate
control over its own long-term memory, complementing the always-on
``AgentCoreRecallHook`` (``src/channel/agents/recall.py``). Where the
hook *injects* recent prior-chat context into the system prompt every
turn, these tools let the model *choose* what to persist and when to
search.

Both are thin wrappers over the AgentCore Memory plumbing already wired
for Phase 7c/8a — ``remember`` uses the same ``CreateEvent`` path as
``AgentCoreMemoryHook`` (``agents/memory.py``); ``recall`` uses the same
``ListSessions`` + ``ListEvents`` reads as ``AgentCoreRecallHook``
(``agents/recall.py``). No parallel memory system, no new infra.

Trust posture (issue #273 / #299): both tools operate on the actor's
**own, token-scoped** memories — trusted data, not the cross-owner Hive
pool. ``recall`` returns its findings as the tool's **result**, which
Channel already treats as untrusted *data* in the tool-result register.
Recall output is NOT spliced into the system-prompt instruction register
(that is the recall hook's job, and the exact seam #299/#95 flag).
Keeping recall output in the data register is what keeps #273 v1 off the
confused-deputy surface #299 guards, and is own-data only — no
cross-owner content ever reaches these tools.

Scoping follows the existing convention: ``actorId =
derive_actor_id(jwt.sub)`` (issue #273 Q3 — follow, don't pre-empt
the future ``{workspace_id}/{user_id}`` scheme).

**Known v1 limitation:** the ``SemanticMemoryStrategy`` was retired in
Phase 8a (ingestion lag), so ``recall`` is keyword/recency over the
actor's own events, not vector search. Candidate notes are surfaced and
the model does the final relevance judgment. A semantic index is a v2
optimization, not a v1 blocker.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
from datetime import datetime, timezone
from typing import Any

from strands import tool

from channel.agents.memory import derive_actor_id
from channel.agents.recall import _iso_date
from channel.metrics import (
    record_memory_tool_recall_outcome,
    record_memory_tool_write_outcome,
)

logger = logging.getLogger(__name__)

# ``remember`` writes a note-shaped ASSISTANT event tagged with this
# prefix, paralleling the ``[meta]`` convention in ``agents/memory.py``
# so deliberate memories are distinguishable from raw turn events and
# tool-use META facts when read back by ``recall`` or the recall hook.
_REMEMBER_PREFIX = "[remember]"

# ``recall`` read caps. Deliberate search is allowed a wider net than
# the always-on recall hook (which caps at 5 sessions / 2 events per
# ``agents/recall.py``) because the model asked for it explicitly.
_RECALL_TOOL_MAX_SESSIONS = 10
_RECALL_TOOL_EVENTS_PER_SESSION = 5
_RECALL_TOOL_MAX_RESULTS = 12
_RECALL_TOOL_TEXT_TRUNCATE = 200

_RECALL_ROLE_MAP: dict[str, str] = {"USER": "You", "ASSISTANT": "Me"}

# Sort-key sentinel for sessions missing ``createdAt`` (mirrors recall.py).
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Minimum token length considered for keyword matching in ``recall`` —
# drops noise words like "a" / "of" / "is" without a full stop-word list.
_RECALL_MIN_TOKEN_LEN = 3
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@functools.lru_cache(maxsize=1)
def _default_client() -> Any:
    """Lazy-load the boto3 AgentCore data-plane client.

    Mirrors ``code_exec._get_lambda_client`` / ``web_search._get_exa_search``:
    deferring the boto3 client construction until a memory tool is
    actually invoked keeps it off the cold-start path for turns that
    don't touch memory. Unit tests inject a mock client via
    ``build_memory_tools(client=...)`` and never reach this.
    """
    import boto3  # noqa: PLC0415  # pragma: no cover

    return boto3.client("bedrock-agentcore")  # pragma: no cover


def _format_remember_text(content: str, tags: list[str] | None) -> str:
    """Build the ``[remember]``-tagged event text, encoding tags inline.

    AgentCore's conversational payload has no native tag field, so tags
    ride inline as ``[tags: a, b]`` — which also makes them
    keyword-searchable by ``recall`` in v1.
    """
    text = f"{_REMEMBER_PREFIX} {content.strip()}"
    if tags:
        cleaned = [t.strip() for t in tags if t and t.strip()]
        if cleaned:
            text += f" [tags: {', '.join(cleaned)}]"
    return text


def _collect_candidates(client: Any, memory_id: str, actor_id: str) -> list[dict[str, str]]:
    """ListSessions + ListEvents across the actor → flat candidate notes.

    Same read plumbing as ``AgentCoreRecallHook._get_or_fetch_records``
    but flattened for tool-result rendering, and it does NOT exclude the
    current session — the model is searching deliberately and wants
    everything it has stored.

    Returns candidates newest-session-first, each shaped
    ``{"date": str, "role": str, "text": str}``. Runs blocking boto3
    calls; callers dispatch it via ``asyncio.to_thread``.
    """
    sessions_resp = client.list_sessions(memoryId=memory_id, actorId=actor_id)
    sessions = list(sessions_resp.get("sessionSummaries", []))
    # Explicit newest-first ordering; don't rely on AgentCore's default.
    sessions.sort(key=lambda s: s.get("createdAt") or _EPOCH, reverse=True)
    sessions = sessions[:_RECALL_TOOL_MAX_SESSIONS]

    candidates: list[dict[str, str]] = []
    for session in sessions:
        sid = session.get("sessionId")
        if not sid:
            continue
        events_resp = client.list_events(
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=sid,
            maxResults=_RECALL_TOOL_EVENTS_PER_SESSION,
        )
        date = _iso_date(session.get("createdAt"))
        # ListEvents returns newest-first; reverse for chronological read.
        for ev in reversed(events_resp.get("events", [])):
            for msg in ev.get("payload", []):
                conv = msg.get("conversational") or {}
                text = (conv.get("content") or {}).get("text", "")
                if not text:
                    continue
                role = _RECALL_ROLE_MAP.get(conv.get("role", ""), conv.get("role") or "?")
                candidates.append({"date": date, "role": role, "text": text})
    return candidates


def _rank_candidates(candidates: list[dict[str, str]], query: str) -> list[dict[str, str]]:
    """Float keyword matches to the top, fill with recency, cap results.

    v1 keyword/recency ranking (no vector search). Query tokens are
    lowercased alphanumeric runs of length >= ``_RECALL_MIN_TOKEN_LEN``.
    A candidate matches if any token is a substring of its lowercased
    text. Matches keep their (recency) order and precede non-matches;
    the combined list is truncated to ``_RECALL_TOOL_MAX_RESULTS`` so the
    model always gets a bounded set to judge. With no usable query
    tokens, pure recency applies.
    """
    tokens = {t for t in _TOKEN_RE.findall(query.lower()) if len(t) >= _RECALL_MIN_TOKEN_LEN}
    if not tokens:
        return candidates[:_RECALL_TOOL_MAX_RESULTS]
    matched: list[dict[str, str]] = []
    unmatched: list[dict[str, str]] = []
    for candidate in candidates:
        text = candidate["text"].lower()
        if any(tok in text for tok in tokens):
            matched.append(candidate)
        else:
            unmatched.append(candidate)
    return (matched + unmatched)[:_RECALL_TOOL_MAX_RESULTS]


def _format_recall_result(candidates: list[dict[str, str]]) -> str:
    """Render ranked candidates as tool-result text (the data register)."""
    lines = ["Notes from your past conversations:", ""]
    for candidate in candidates:
        text = candidate["text"]
        if len(text) > _RECALL_TOOL_TEXT_TRUNCATE:
            text = text[:_RECALL_TOOL_TEXT_TRUNCATE] + "..."
        date = candidate["date"] or "earlier"
        lines.append(f"- ({date}) {candidate['role']}: {text}")
    return "\n".join(lines)


def build_memory_tools(
    memory_id: str,
    actor_id: str,
    session_id: str,
    *,
    client: Any | None = None,
) -> list[Any]:
    """Return the ``[remember, recall]`` tool pair bound to per-request context.

    The stateless module-level ``@tool`` pattern used by ``clock`` /
    ``web_search`` can't carry ``memory_id`` / ``actor_id`` /
    ``session_id``, so these tools are built by a factory closure per
    turn. ``actor_id`` is derived at the boundary (same as the memory
    hooks) so callers can pass the raw JWT ``sub`` (email-form or
    otherwise). ``client`` is injectable for unit tests; production
    resolves the boto3 AgentCore client lazily on first invocation.
    """
    derived_actor = derive_actor_id(actor_id)

    def _client() -> Any:
        return client if client is not None else _default_client()

    @tool
    async def remember(content: str, tags: list[str] | None = None) -> str:
        """Save a durable fact, decision, or preference to your long-term memory.

        Call this when the user tells you something worth keeping across
        future conversations — a stable preference, a decision you both
        reached, an important project fact, or a correction you should
        not repeat. Do NOT call it for small talk or anything already
        obvious from the current conversation.

        Args:
            content: The thing to remember, phrased so it stands on its
                own in a future conversation (include enough context to
                be self-explanatory).
            tags: Optional short keywords that make the memory easier to
                find later with ``recall`` (e.g. ["preferences", "billing"]).
        """
        if not content.strip():
            return "Nothing to remember — the content was empty."
        text = _format_remember_text(content, tags)
        try:
            await asyncio.to_thread(
                _client().create_event,
                memoryId=memory_id,
                actorId=derived_actor,
                sessionId=session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=[{"conversational": {"role": "ASSISTANT", "content": {"text": text}}}],
            )
        except Exception as exc:
            logger.warning(
                "memory_tool.remember_failed actor_id=%s session_id=%s",
                derived_actor,
                session_id,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )
            await record_memory_tool_write_outcome(success=False)
            return "Could not save to memory right now."
        await record_memory_tool_write_outcome(success=True)
        return "Saved to memory."

    @tool
    async def recall(query: str) -> str:
        """Search your own memory of past conversations for relevant notes.

        Call this when the user refers to something from a previous
        conversation that isn't in the current chat — a past decision,
        an earlier preference, "what did we say about X", or "when did I
        first mention Y". Returns candidate notes from your own prior
        sessions; judge their relevance yourself and weave in only what
        genuinely fits.

        Args:
            query: What to look for, in a few words (e.g. "billing
                preferences" or "the database migration decision").
        """
        try:
            candidates = await asyncio.to_thread(
                _collect_candidates, _client(), memory_id, derived_actor
            )
        except Exception as exc:
            logger.warning(
                "memory_tool.recall_failed actor_id=%s",
                derived_actor,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )
            await record_memory_tool_recall_outcome(success=False)
            return "Could not search memory right now."
        await record_memory_tool_recall_outcome(success=True)
        ranked = _rank_candidates(candidates, query)
        if not ranked:
            return "No matching memories found."
        return _format_recall_result(ranked)

    return [remember, recall]
