# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the ``remember`` / ``recall`` memory tools (#273)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from channel.agents.tools import memory_tools as mt
from channel.agents.tools.memory_tools import (
    _RECALL_TOOL_MAX_RESULTS,
    _RECALL_TOOL_TEXT_TRUNCATE,
    _collect_candidates,
    _format_recall_result,
    _format_remember_text,
    _rank_candidates,
    build_memory_tools,
)


@pytest.fixture
def fake_metrics(monkeypatch):
    """Patch the EMF counters so tests can assert on the success flag
    without hitting the real (stdout) metrics logger."""
    write = AsyncMock()
    recall = AsyncMock()
    monkeypatch.setattr(mt, "record_memory_write_outcome", write)
    monkeypatch.setattr(mt, "record_recall_outcome", recall)
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
# _collect_candidates
# ---------------------------------------------------------------------------


def _events_client(sessions, events_by_session):
    """Build a MagicMock AgentCore client returning canned sessions/events."""
    client = MagicMock()
    client.list_sessions.return_value = {"sessionSummaries": sessions}

    def _list_events(*, memoryId, actorId, sessionId, maxResults):
        return {"events": events_by_session.get(sessionId, [])}

    client.list_events.side_effect = _list_events
    return client


def test_collect_candidates_orders_sessions_and_reverses_events():
    """Newest session first; events (ListEvents returns newest-first) are
    reversed to chronological; sessions without an id are skipped;
    payload entries without text are skipped; unknown roles fall through
    to the raw role label."""
    sessions = [
        {"sessionId": "s-old", "createdAt": datetime(2026, 6, 10, tzinfo=timezone.utc)},
        {"sessionId": "s-new", "createdAt": datetime(2026, 6, 14, tzinfo=timezone.utc)},
        {"createdAt": datetime(2026, 6, 12, tzinfo=timezone.utc)},  # no sessionId → skip
    ]
    events_by_session = {
        "s-new": [
            # ListEvents newest-first: this is the most recent event.
            {"payload": [{"conversational": {"role": "ASSISTANT", "content": {"text": "second"}}}]},
            {
                "payload": [
                    {"conversational": {"role": "USER", "content": {"text": "first"}}},
                    {"conversational": {"role": "ROBOT", "content": {"text": "odd role"}}},
                    {"conversational": {"role": "USER", "content": {}}},  # no text → skip
                ]
            },
        ],
        "s-old": [
            {"payload": [{"conversational": {"role": "USER", "content": {"text": "old chat"}}}]},
        ],
    }
    client = _events_client(sessions, events_by_session)

    candidates = _collect_candidates(client, "mem-1", "actor-1")

    assert candidates == [
        {"date": "2026-06-14", "role": "You", "text": "first"},
        {"date": "2026-06-14", "role": "ROBOT", "text": "odd role"},
        {"date": "2026-06-14", "role": "Me", "text": "second"},
        {"date": "2026-06-10", "role": "You", "text": "old chat"},
    ]
    # Read scoping is passed through to AgentCore verbatim.
    client.list_sessions.assert_called_once_with(memoryId="mem-1", actorId="actor-1")


def test_collect_candidates_empty_when_no_sessions():
    client = _events_client([], {})
    assert _collect_candidates(client, "mem-1", "actor-1") == []


# ---------------------------------------------------------------------------
# _rank_candidates
# ---------------------------------------------------------------------------


def _cand(text, date="2026-06-14", role="You"):
    return {"date": date, "role": role, "text": text}


def test_rank_candidates_floats_keyword_matches_to_top():
    candidates = [
        _cand("weather is nice"),
        _cand("about billing preferences"),
        _cand("the migration plan"),
    ]
    ranked = _rank_candidates(candidates, "billing preferences")
    # The matching candidate floats above the non-matches; non-matches
    # keep their recency order below.
    assert ranked[0]["text"] == "about billing preferences"
    assert ranked[1:] == [_cand("weather is nice"), _cand("the migration plan")]


def test_rank_candidates_recency_when_no_usable_query_tokens():
    """A query of only sub-3-char words yields no tokens → pure recency."""
    candidates = [_cand("one"), _cand("two"), _cand("three")]
    assert _rank_candidates(candidates, "a of is") == candidates


def test_rank_candidates_truncates_to_max_results():
    candidates = [_cand(f"note {i}") for i in range(_RECALL_TOOL_MAX_RESULTS + 5)]
    # No usable tokens → recency path, still capped.
    ranked = _rank_candidates(candidates, "")
    assert len(ranked) == _RECALL_TOOL_MAX_RESULTS


# ---------------------------------------------------------------------------
# _format_recall_result
# ---------------------------------------------------------------------------


def test_format_recall_result_renders_lines_and_truncates_long_text():
    long_text = "L" * (_RECALL_TOOL_TEXT_TRUNCATE + 50)
    candidates = [
        {"date": "2026-06-14", "role": "You", "text": "short note"},
        {"date": "", "role": "Me", "text": long_text},  # empty date → "earlier"
    ]
    result = _format_recall_result(candidates)

    assert "Notes from your past conversations:" in result
    assert "- (2026-06-14) You: short note" in result
    assert "- (earlier) Me: " + "L" * _RECALL_TOOL_TEXT_TRUNCATE + "..." in result
    assert "L" * (_RECALL_TOOL_TEXT_TRUNCATE + 1) not in result


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
    # actorId is sanitized at the factory boundary (email → underscores).
    assert kwargs["actorId"] == "u_x_com"
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
