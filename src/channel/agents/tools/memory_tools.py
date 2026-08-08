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

**Since #274 the retrieval half lives in ``agents/memory_ranking``** and
is shared with ``AgentCoreRecallHook`` rather than duplicated. This module
keeps what is specific to a *deliberate* search: the wider result cap, the
200-char render truncation, and ``pad=True`` — a model that explicitly
asked gets the most recent notes to judge even when nothing matched, where
the always-on hook gets nothing. That single flag is the difference
between the two surfaces; everything else about how candidates are found
and ordered is now one implementation.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime, timezone
from typing import Any

from strands import tool

from channel.agents.memory import derive_actor_id
from channel.agents.memory_ranking import (
    Candidate,
    collect_candidates,
    rank_candidates,
    role_label,
)
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

# ``recall`` OUTPUT caps. The read caps that used to live beside them
# (``10`` sessions x ``5`` events) moved to
# ``memory_ranking.POOL_MAX_SESSIONS`` / ``POOL_EVENTS_PER_SESSION`` in
# #274, where the hook now shares them — the pool is the same question
# asked by both surfaces, so it is asked once.
#
# These two are genuinely tool-specific and stay: a deliberate search
# renders more results, at greater length, than an always-on prompt
# addendum can afford. The hook's counterpart is a token budget
# (``recall._RECALL_TOKEN_BUDGET``), not a result count, because it is
# spending prompt rather than tool-result space.
_RECALL_TOOL_MAX_RESULTS = 12
_RECALL_TOOL_TEXT_TRUNCATE = 200


@functools.lru_cache(maxsize=1)
def _default_client() -> Any:
    """Lazy-load the boto3 AgentCore data-plane client.

    Mirrors ``code_exec._get_lambda_client`` /
    ``web_search._resolve_exa_api_key``:
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


def _rank_tool_candidates(candidates: list[Candidate], query: str) -> list[Candidate]:
    """Rank for a **deliberate** search: matches first, recency behind them.

    A thin binding of the shared ranker to this surface's two choices —
    ``_RECALL_TOOL_MAX_RESULTS`` and, load-bearingly, ``pad=True``.

    Padding is right here and wrong for the hook, which is the whole
    asymmetry #274 turned on. The model called ``recall`` on purpose, so
    "nothing matched your words, here are your most recent notes" is a
    useful answer it can judge and discard; the same behaviour in an
    always-on hook is how "morning" used to drag five unrelated chats into
    every system prompt. Same ranker, opposite default.
    """
    return rank_candidates(candidates, query, limit=_RECALL_TOOL_MAX_RESULTS, pad=True)


def _format_recall_result(candidates: list[Candidate]) -> str:
    """Render ranked candidates as tool-result text (the data register)."""
    lines = ["Notes from your past conversations:", ""]
    for candidate in candidates:
        text = candidate.text
        if len(text) > _RECALL_TOOL_TEXT_TRUNCATE:
            text = text[:_RECALL_TOOL_TEXT_TRUNCATE] + "..."
        date = candidate.date or "earlier"
        lines.append(f"- ({date}) {role_label(candidate.role)}: {text}")
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
                collect_candidates, _client(), memory_id, derived_actor
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
        ranked = _rank_tool_candidates(candidates, query)
        if not ranked:
            return "No matching memories found."
        return _format_recall_result(ranked)

    return [remember, recall]
