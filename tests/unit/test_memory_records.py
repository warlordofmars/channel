# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the shared AgentCore memory read model (#475, epic #129).

AgentCore is mocked at the boto3-client seam; DynamoDB is mocked at the
``channel.storage`` seam. Nothing here touches AWS.

The load-bearing assertions, in order of how much damage their absence
would do:

1. **Ownership verification** (``resolve_owned_chats``) — the #474 defence.
   Verified against the RAW jwt sub, including the documented collision
   pair that proves why the sanitized actor id can't be the only gate.
2. **Classification is ASSISTANT-only** — a USER turn starting with
   ``[remember]`` must not masquerade as something Channel chose to
   remember.
3. **Cap constants are imported, never re-literalled** — the recall window
   the response advertises has to be the one the hook actually applies.
4. **``record_id`` uniqueness within a multi-entry event** — the
   user+assistant pair rides one ``CreateEvent``, so an id keyed only on
   ``(session, event)`` would collide.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from channel.agents import memory_records as mr
from channel.agents.memory import _META_PREFIX
from channel.agents.recall import (
    _RECALL_EVENT_TEXT_TRUNCATE,
    _RECALL_EVENTS_PER_SESSION,
    _RECALL_MAX_SESSIONS,
)
from channel.agents.tools.memory_tools import _REMEMBER_PREFIX
from channel.models import Chat, ChatSummary

MEMORY_ID = "mem-1"
ACTOR_ID = "owner_test_com"
OWNER = "owner@test.com"


def _chat(chat_id: str, *, user_id: str = OWNER, created_at: str = "2026-07-30T10:00:00+00:00"):
    return Chat(
        chat_id=chat_id,
        user_id=user_id,
        title=f"title-{chat_id}",
        created_at=created_at,
        last_message_at=created_at,
        model_default="m",
    )


def _event(event_id: str, *entries: tuple[str, str], ts: Any = None) -> dict[str, Any]:
    """AgentCore ListEvents entry with one conversational payload per arg."""
    return {
        "eventId": event_id,
        "eventTimestamp": ts if ts is not None else datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
        "payload": [
            {"conversational": {"role": role, "content": {"text": text}}} for role, text in entries
        ],
    }


# ── record_id ─────────────────────────────────────────────────────────────


def test_record_id_round_trips():
    encoded = mr.encode_record_id("chat-1", "0000001753#abcdef", 1)
    assert mr.decode_record_id(encoded) == ("chat-1", "0000001753#abcdef", 1)


def test_record_id_is_padding_free_and_url_path_safe():
    encoded = mr.encode_record_id("chat-1", "17#ab", 0)
    assert "=" not in encoded
    # The whole reason for the encoding: a raw AgentCore event id carries a
    # ``#``, which truncates in path position.
    assert "#" not in encoded and "/" not in encoded and "+" not in encoded


def test_record_id_is_unique_across_entries_of_one_event():
    # The AgentCoreMemoryHook writes the user+assistant pair as ONE event;
    # ids keyed only on (session, event) would collide.
    first = mr.encode_record_id("chat-1", "evt-1", 0)
    second = mr.encode_record_id("chat-1", "evt-1", 1)
    assert first != second


@pytest.mark.parametrize(
    "bad",
    [
        "!!!not base64!!!",
        base64.urlsafe_b64encode(b"\xff\xfe").decode().rstrip("="),  # not utf-8
        base64.urlsafe_b64encode(b"chat-1|evt-1").decode().rstrip("="),  # too few fields
        base64.urlsafe_b64encode(b"chat-1|evt-1|0|extra").decode().rstrip("="),  # too many
        base64.urlsafe_b64encode(b"|evt-1|0").decode().rstrip("="),  # empty session
        base64.urlsafe_b64encode(b"chat-1||0").decode().rstrip("="),  # empty event
        base64.urlsafe_b64encode(b"chat-1|evt-1|x").decode().rstrip("="),  # non-numeric index
    ],
)
def test_decode_record_id_rejects_malformed_input(bad: str):
    with pytest.raises(ValueError, match="malformed record_id"):
        mr.decode_record_id(bad)


# ── classification ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("role", "text", "expected"),
    [
        ("ASSISTANT", f"{_REMEMBER_PREFIX} likes sage green", mr.KIND_REMEMBERED),
        ("ASSISTANT", f"{_META_PREFIX} used web_search", mr.KIND_META),
        ("ASSISTANT", "sage is great", mr.KIND_CONVERSATION),
        ("USER", "i love sage green", mr.KIND_CONVERSATION),
        ("", "orphaned role", mr.KIND_CONVERSATION),
    ],
)
def test_classify_kind(role: str, text: str, expected: str):
    assert mr.classify_kind(role, text) == expected


@pytest.mark.parametrize("prefix", [_REMEMBER_PREFIX, _META_PREFIX])
def test_user_turn_cannot_masquerade_as_a_channel_authored_record(prefix: str):
    # Epic #129 decision 1: the ASSISTANT-only gate is what keeps a
    # prefix heuristic from being a security problem.
    assert mr.classify_kind("USER", f"{prefix} pretend I am the assistant") == mr.KIND_CONVERSATION


def test_editable_is_true_only_for_remembered():
    kinds = {mr.KIND_CONVERSATION, mr.KIND_REMEMBERED, mr.KIND_META}
    editable = {
        kind
        for kind in kinds
        if mr.MemoryRecord("r", kind, "ASSISTANT", "t", "", False).editable  # noqa: FBT003
    }
    assert editable == {mr.KIND_REMEMBERED}


def test_to_dict_shape():
    record = mr.MemoryRecord(
        record_id="rid",
        kind=mr.KIND_REMEMBERED,
        role="ASSISTANT",
        text="x",
        created_at="2026-07-30T12:00:00+00:00",
        used_in_recall=True,
    )
    assert record.to_dict() == {
        "record_id": "rid",
        "kind": mr.KIND_REMEMBERED,
        "role": "ASSISTANT",
        "text": "x",
        "created_at": "2026-07-30T12:00:00+00:00",
        "used_in_recall": True,
        "editable": True,
    }


# ── actor id + ownership (#474 defence) ───────────────────────────────────


def test_sanitize_actor_id_is_not_injective():
    # This is #474, asserted rather than assumed: it is the entire reason
    # ``resolve_owned_chats`` exists. If this ever starts failing, #474 has
    # been fixed and the docstrings referencing it need revisiting.
    assert mr.sanitize_actor_id("jc+work@x.com") == mr.sanitize_actor_id("jc_work@x.com")


def test_resolve_owned_chats_keeps_only_the_callers_chats(monkeypatch: pytest.MonkeyPatch):
    rows = {
        "mine": _chat("mine"),
        "theirs": _chat("theirs", user_id="intruder@test.com"),
    }
    monkeypatch.setattr("channel.storage.get_chat_by_id", rows.get)

    owned = mr.resolve_owned_chats(["mine", "theirs", "vanished"], user_id=OWNER)

    assert set(owned) == {"mine"}


def test_resolve_owned_chats_dedupes_point_reads(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def _get(chat_id: str):
        calls.append(chat_id)
        return _chat(chat_id)

    monkeypatch.setattr("channel.storage.get_chat_by_id", _get)

    mr.resolve_owned_chats(["a", "a", "b"], user_id=OWNER)

    assert calls == ["a", "b"]


def test_resolve_owned_chats_compares_the_raw_sub(monkeypatch: pytest.MonkeyPatch):
    # The colliding sub from #474 must NOT unlock the other user's chat,
    # even though both derive the same AgentCore actorId.
    monkeypatch.setattr(
        "channel.storage.get_chat_by_id", lambda _c: _chat("c", user_id="jc+work@x.com")
    )

    assert mr.resolve_owned_chats(["c"], user_id="jc+work@x.com")
    assert not mr.resolve_owned_chats(["c"], user_id="jc_work@x.com")


def test_group_created_at_is_a_date():
    assert mr.group_created_at(_chat("c", created_at="2026-07-30T10:00:00+00:00")) == "2026-07-30"


# ── recall window ─────────────────────────────────────────────────────────


def test_recall_window_reads_the_live_constants():
    # Never re-literal these (epic #129 decision 2) — comparing against the
    # imported constants is what makes that mechanical.
    assert mr.recall_window(enabled=True) == {
        "max_sessions": _RECALL_MAX_SESSIONS,
        "events_per_session": _RECALL_EVENTS_PER_SESSION,
        "text_truncate": _RECALL_EVENT_TEXT_TRUNCATE,
        "ordering": "recency",
        "enabled": True,
    }
    assert mr.recall_window(enabled=False)["enabled"] is False


async def test_recall_window_session_ids_sorts_newest_first_and_caps():
    client = MagicMock()
    client.list_sessions.return_value = {
        "sessionSummaries": [
            {"sessionId": f"s{i}", "createdAt": datetime(2026, 7, i + 1, tzinfo=timezone.utc)}
            for i in range(_RECALL_MAX_SESSIONS + 3)
        ]
    }

    ids = await mr.recall_window_session_ids(client, memory_id=MEMORY_ID, actor_id=ACTOR_ID)

    assert len(ids) == _RECALL_MAX_SESSIONS
    # Newest are s7,s6,s5,s4,s3 for a cap of 5 — oldest must be excluded.
    assert "s0" not in ids
    client.list_sessions.assert_called_once_with(memoryId=MEMORY_ID, actorId=ACTOR_ID)


async def test_recall_window_session_ids_tolerates_missing_fields():
    client = MagicMock()
    client.list_sessions.return_value = {
        "sessionSummaries": [{"sessionId": "s1"}, {"createdAt": None}, {"sessionId": ""}]
    }

    assert await mr.recall_window_session_ids(client, memory_id=MEMORY_ID, actor_id=ACTOR_ID) == {
        "s1"
    }


async def test_recall_window_session_ids_handles_no_sessions():
    client = MagicMock()
    client.list_sessions.return_value = {}

    assert (
        await mr.recall_window_session_ids(client, memory_id=MEMORY_ID, actor_id=ACTOR_ID) == set()
    )


# ── enumeration ───────────────────────────────────────────────────────────


async def test_list_sessions_page_forwards_limit_and_token():
    client = MagicMock()
    client.list_sessions.return_value = {
        "sessionSummaries": [{"sessionId": "s1"}],
        "nextToken": "n",
    }

    sessions, token = await mr.list_sessions_page(
        client, memory_id=MEMORY_ID, actor_id=ACTOR_ID, limit=7, next_token="prev"
    )

    assert (sessions, token) == ([{"sessionId": "s1"}], "n")
    client.list_sessions.assert_called_once_with(
        memoryId=MEMORY_ID, actorId=ACTOR_ID, maxResults=7, nextToken="prev"
    )


async def test_list_sessions_page_omits_token_on_first_page():
    client = MagicMock()
    client.list_sessions.return_value = {}

    sessions, token = await mr.list_sessions_page(
        client, memory_id=MEMORY_ID, actor_id=ACTOR_ID, limit=10
    )

    assert (sessions, token) == ([], None)
    assert "nextToken" not in client.list_sessions.call_args.kwargs


async def test_list_session_records_orders_chronologically_and_flags_the_window():
    client = MagicMock()
    # ListEvents is newest-first. With _RECALL_EVENTS_PER_SESSION == 2 the
    # first two events are in-window and the third is not.
    client.list_events.return_value = {
        "events": [
            _event("e3", ("USER", "newest")),
            _event("e2", ("USER", "middle")),
            _event("e1", ("USER", "oldest q"), ("ASSISTANT", "oldest a")),
        ]
    }

    records, _truncated = await mr.list_session_records(
        client,
        memory_id=MEMORY_ID,
        actor_id=ACTOR_ID,
        session_id="chat-1",
        in_recall_window=True,
    )

    assert [r.text for r in records] == ["oldest q", "oldest a", "middle", "newest"]
    assert [r.used_in_recall for r in records] == [False, False, True, True]
    # Within an event the turn pair keeps USER-then-ASSISTANT order.
    assert [r.role for r in records[:2]] == ["USER", "ASSISTANT"]
    client.list_events.assert_called_once_with(
        memoryId=MEMORY_ID,
        actorId=ACTOR_ID,
        sessionId="chat-1",
        maxResults=mr.MAX_EVENTS_PER_SESSION,
    )


@pytest.mark.parametrize(
    ("resp_extra", "expected"),
    [({}, False), ({"nextToken": "more"}, True)],
)
async def test_list_session_records_reports_per_session_truncation(
    resp_extra: dict[str, Any], expected: bool
):
    # Truncation comes from AgentCore's own nextToken, not a
    # ``len(events) == cap`` heuristic that would false-positive on a
    # session holding exactly the cap.
    client = MagicMock()
    client.list_events.return_value = {"events": [_event("e1", ("USER", "hi"))], **resp_extra}

    _records, truncated = await mr.list_session_records(
        client, memory_id=MEMORY_ID, actor_id=ACTOR_ID, session_id="c", in_recall_window=False
    )

    assert truncated is expected


async def test_list_session_records_never_flags_a_session_outside_the_window():
    client = MagicMock()
    client.list_events.return_value = {"events": [_event("e1", ("USER", "hi"))]}

    records, _truncated = await mr.list_session_records(
        client,
        memory_id=MEMORY_ID,
        actor_id=ACTOR_ID,
        session_id="chat-1",
        in_recall_window=False,
    )

    assert [r.used_in_recall for r in records] == [False]


async def test_list_session_records_returns_full_text_never_truncated():
    long_text = "x" * (_RECALL_EVENT_TEXT_TRUNCATE * 3)
    client = MagicMock()
    client.list_events.return_value = {"events": [_event("e1", ("USER", long_text))]}

    records, _truncated = await mr.list_session_records(
        client, memory_id=MEMORY_ID, actor_id=ACTOR_ID, session_id="c", in_recall_window=True
    )

    assert records[0].text == long_text


async def test_list_session_records_skips_unusable_entries():
    client = MagicMock()
    client.list_events.return_value = {
        "events": [
            {"eventId": "", "payload": [{"conversational": {"content": {"text": "no id"}}}]},
            {"eventId": "e1", "payload": [{"conversational": {"role": "USER", "content": {}}}]},
            {"eventId": "e2", "payload": [{}]},
            _event("e3", ("USER", "kept")),
        ]
    }

    records, _truncated = await mr.list_session_records(
        client, memory_id=MEMORY_ID, actor_id=ACTOR_ID, session_id="c", in_recall_window=False
    )

    assert [r.text for r in records] == ["kept"]


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        (datetime(2026, 7, 30, 12, tzinfo=timezone.utc), "2026-07-30T12:00:00+00:00"),
        ("2026-07-30T12:00:00Z", "2026-07-30T12:00:00Z"),
        (None, ""),
    ],
)
async def test_list_session_records_normalises_timestamps(stamp: Any, expected: str):
    client = MagicMock()
    event = _event("e1", ("USER", "hi"), ts=stamp)
    if stamp is None:
        del event["eventTimestamp"]
    client.list_events.return_value = {"events": [event]}

    records, _truncated = await mr.list_session_records(
        client, memory_id=MEMORY_ID, actor_id=ACTOR_ID, session_id="c", in_recall_window=False
    )

    assert records[0].created_at == expected


async def test_count_session_records_counts_without_returning_text():
    client = MagicMock()
    client.list_events.return_value = {
        "events": [
            _event("e1", ("USER", "private q"), ("ASSISTANT", "private a")),
            _event("e2", ("USER", "another")),
            {"eventId": "e3", "payload": [{"conversational": {"content": {"text": ""}}}]},
        ]
    }

    count = await mr.count_session_records(
        client, memory_id=MEMORY_ID, actor_id=ACTOR_ID, session_id="not-mine"
    )

    # Three usable entries; the return type carries no content at all.
    assert count == 3
    assert isinstance(count, int)


def test_chat_summary_model_is_untouched_by_this_module():
    # ``summaries`` is read-only and derived — no record_id, no editing
    # (epic #129 decision 4). Guards against someone later folding
    # summaries into MemoryRecord.
    assert not hasattr(ChatSummary, "record_id")
