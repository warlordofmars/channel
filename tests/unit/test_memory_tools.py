# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the ``remember`` / ``recall`` memory tools (#273)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from channel.agents.memory import derive_actor_id
from channel.agents.memory_ranking import Candidate
from channel.agents.tools import memory_tools as mt
from channel.agents.tools.memory_tools import (
    _RECALL_TOOL_MAX_RESULTS,
    _RECALL_TOOL_TEXT_TRUNCATE,
    _format_recall_result,
    _format_remember_text,
    _rank_tool_candidates,
    build_memory_tools,
)


@pytest.fixture
def fake_metrics(monkeypatch):
    """Patch the EMF counters so tests can assert on the success flag
    without hitting the real (stdout) metrics logger.

    The tools emit the tool-specific ``MemoryTool*`` counters (#400), kept
    separate from the hook counters so the admin dashboard's hook-health
    signal stays isolated — so these patch the ``record_memory_tool_*``
    symbols, not the hook ``record_memory_write_outcome`` /
    ``record_recall_outcome``."""
    write = AsyncMock()
    recall = AsyncMock()
    monkeypatch.setattr(mt, "record_memory_tool_write_outcome", write)
    monkeypatch.setattr(mt, "record_memory_tool_recall_outcome", recall)
    return {"write": write, "recall": recall}


# ---------------------------------------------------------------------------
# _format_remember_text
# ---------------------------------------------------------------------------


def test_format_remember_text_without_tags():
    assert _format_remember_text("likes sage green", None) == "[remember] likes sage green"


def test_format_remember_text_with_tags():
    text = _format_remember_text("prefers dark mode", ["preferences", "ui"])
    assert text == "[remember] prefers dark mode [tags: preferences, ui]"


def test_format_remember_text_ignores_blank_tags():
    """Tags that are empty or whitespace-only after strip add no suffix."""
    assert _format_remember_text("a fact", ["  ", ""]) == "[remember] a fact"


def test_format_remember_text_strips_surrounding_whitespace():
    assert _format_remember_text("  padded  ", None) == "[remember] padded"


# ---------------------------------------------------------------------------
# shared test client
# ---------------------------------------------------------------------------


def _events_client(sessions, events_by_session):
    """Build a MagicMock AgentCore client returning canned sessions/events."""
    client = MagicMock()
    client.list_sessions.return_value = {"sessionSummaries": sessions}

    def _list_events(*, memoryId, actorId, sessionId, maxResults):
        return {"events": events_by_session.get(sessionId, [])}

    client.list_events.side_effect = _list_events
    return client


def _cand(text, date="2026-06-14", role="USER", session_id="s-1", order=0):
    return Candidate(
        session_id=session_id,
        date=date,
        role=role,
        text=text,
        match_text=text.lower(),
        order=order,
        event_index=order,
    )


# ---------------------------------------------------------------------------
# _rank_tool_candidates — the tool's binding of the SHARED ranker (#274)
# ---------------------------------------------------------------------------


def test_rank_tool_candidates_floats_keyword_matches_to_top():
    candidates = [
        _cand("weather is nice", order=0),
        _cand("about billing preferences", order=1),
        _cand("the migration plan", order=2),
    ]
    ranked = _rank_tool_candidates(candidates, "billing preferences")
    # The matching candidate floats above the non-matches; non-matches
    # keep their recency order below.
    assert ranked[0].text == "about billing preferences"
    assert [c.text for c in ranked[1:]] == ["weather is nice", "the migration plan"]


def test_rank_tool_candidates_pads_with_recency_when_nothing_matches():
    """The tool's ``pad=True`` is the half of #274 that did NOT change.

    A deliberate ``recall`` call that matches nothing still gets the most
    recent notes to judge — the exact behaviour the always-on hook now
    refuses (see ``test_recall.py``). Both surfaces run one ranker; this
    flag is the entire difference between them, so it is pinned on both
    sides.
    """
    candidates = [_cand("one", order=0), _cand("two", order=1), _cand("three", order=2)]
    assert _rank_tool_candidates(candidates, "zzzznomatch") == candidates


def test_rank_tool_candidates_recency_when_no_usable_query_tokens():
    """A query of only sub-3-char words yields no tokens → pure recency."""
    candidates = [_cand("one", order=0), _cand("two", order=1), _cand("three", order=2)]
    assert _rank_tool_candidates(candidates, "a of is") == candidates


def test_rank_tool_candidates_truncates_to_max_results():
    candidates = [_cand(f"note {i}", order=i) for i in range(_RECALL_TOOL_MAX_RESULTS + 5)]
    # No usable tokens → recency path, still capped.
    ranked = _rank_tool_candidates(candidates, "")
    assert len(ranked) == _RECALL_TOOL_MAX_RESULTS


# ---------------------------------------------------------------------------
# _format_recall_result
# ---------------------------------------------------------------------------


def test_format_recall_result_renders_lines_and_truncates_long_text():
    long_text = "L" * (_RECALL_TOOL_TEXT_TRUNCATE + 50)
    candidates = [
        _cand("short note", date="2026-06-14", role="USER"),
        _cand(long_text, date="", role="ASSISTANT"),  # empty date → "earlier"
    ]
    result = _format_recall_result(candidates)

    assert "Notes from your past conversations:" in result
    assert "- (2026-06-14) You: short note" in result
    assert "- (earlier) Me: " + "L" * _RECALL_TOOL_TEXT_TRUNCATE + "..." in result
    assert "L" * (_RECALL_TOOL_TEXT_TRUNCATE + 1) not in result


def test_format_recall_result_passes_an_unknown_role_through():
    """An unrecognised AgentCore role renders as itself, not as ``Me``.

    Shares ``memory_ranking.role_label`` with the recall hook, so a wire
    change stays visible on both surfaces rather than being silently
    relabelled on one of them.
    """
    assert "ROBOT: beep" in _format_recall_result([_cand("beep", role="ROBOT")])


# ---------------------------------------------------------------------------
# build_memory_tools + remember / recall
# ---------------------------------------------------------------------------


def test_build_memory_tools_returns_remember_and_recall():
    tools = build_memory_tools("mem-1", "u@x.com", "sess-1", client=MagicMock())
    assert [t.tool_name for t in tools] == ["remember", "recall"]


async def test_remember_writes_create_event_and_counts_success(fake_metrics):
    fake_client = MagicMock()
    remember, _recall = build_memory_tools("mem-1", "u@x.com", "sess-1", client=fake_client)

    result = await remember(content="user prefers sage green", tags=["preferences"])

    assert result == "Saved to memory."
    fake_client.create_event.assert_called_once()
    kwargs = fake_client.create_event.call_args.kwargs
    assert kwargs["memoryId"] == "mem-1"
    # actorId is derived at the factory boundary (email → label + digest).
    assert kwargs["actorId"] == derive_actor_id("u@x.com")
    assert kwargs["sessionId"] == "sess-1"
    assert isinstance(kwargs["eventTimestamp"], datetime)
    conv = kwargs["payload"][0]["conversational"]
    assert conv["role"] == "ASSISTANT"
    assert conv["content"]["text"] == "[remember] user prefers sage green [tags: preferences]"
    fake_metrics["write"].assert_awaited_once_with(success=True)


async def test_remember_empty_content_returns_early_without_writing(fake_metrics):
    fake_client = MagicMock()
    remember, _recall = build_memory_tools("mem-1", "actor", "sess-1", client=fake_client)

    result = await remember(content="   ")

    assert "Nothing to remember" in result
    fake_client.create_event.assert_not_called()
    fake_metrics["write"].assert_not_awaited()


async def test_remember_failure_counts_and_returns_soft_error(fake_metrics):
    fake_client = MagicMock()
    fake_client.create_event.side_effect = RuntimeError("agentcore boom")
    remember, _recall = build_memory_tools("mem-1", "actor", "sess-1", client=fake_client)

    result = await remember(content="something worth keeping")

    assert "Could not save to memory" in result
    fake_metrics["write"].assert_awaited_once_with(success=False)


async def test_recall_returns_formatted_matches(fake_metrics):
    sessions = [
        {"sessionId": "s-1", "createdAt": datetime(2026, 6, 14, tzinfo=timezone.utc)},
    ]
    events_by_session = {
        "s-1": [
            {
                "payload": [
                    {
                        "conversational": {
                            "role": "ASSISTANT",
                            "content": {"text": "[remember] user prefers sage green"},
                        }
                    }
                ]
            },
        ],
    }
    fake_client = _events_client(sessions, events_by_session)
    _remember, recall = build_memory_tools("mem-1", "actor", "sess-1", client=fake_client)

    result = await recall(query="sage")

    assert "Notes from your past conversations:" in result
    assert "user prefers sage green" in result
    fake_metrics["recall"].assert_awaited_once_with(success=True)


async def test_recall_no_candidates_returns_not_found(fake_metrics):
    fake_client = _events_client([], {})
    _remember, recall = build_memory_tools("mem-1", "actor", "sess-1", client=fake_client)

    result = await recall(query="anything")

    assert result == "No matching memories found."
    fake_metrics["recall"].assert_awaited_once_with(success=True)


async def test_recall_failure_counts_and_returns_soft_error(fake_metrics):
    fake_client = MagicMock()
    fake_client.list_sessions.side_effect = RuntimeError("agentcore boom")
    _remember, recall = build_memory_tools("mem-1", "actor", "sess-1", client=fake_client)

    result = await recall(query="whatever")

    assert "Could not search memory" in result
    fake_metrics["recall"].assert_awaited_once_with(success=False)
