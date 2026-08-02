# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for ``GET /api/memory/records`` (#475, epic #129).

Auth is driven through the REAL ``require_mgmt_user`` with real mgmt JWTs
(repo rule: don't mock auth; mock the AWS boundary). AgentCore is mocked at
``channel.api.memory._agentcore_client``; DynamoDB at the
``channel.storage`` seam, so the ownership boundary proven here is the one
production enforces.

This endpoint returns private memory content, so the ownership tests are
the point of this file. They pin the read boundary itself: a session whose
chat the caller does not own is withheld, counted, and never leaks a byte
of its text — asserted without any claim about how ``actorId`` is derived,
since not depending on that derivation is precisely what the gate is for
(#474 was the demonstration; #485 repairs the derivation).
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CHANNEL_JWT_SECRET", "test-secret-for-unit-tests")

from channel.agents.memory import _META_PREFIX  # noqa: E402
from channel.agents.recall import (  # noqa: E402
    _RECALL_EVENT_TEXT_TRUNCATE,
    _RECALL_EVENTS_PER_SESSION,
    _RECALL_MAX_SESSIONS,
)
from channel.agents.tools.memory_tools import _REMEMBER_PREFIX  # noqa: E402
from channel.api import memory as memory_api  # noqa: E402
from channel.api.main import app  # noqa: E402
from channel.auth.tokens import issue_mgmt_jwt  # noqa: E402
from channel.logging_config import fingerprint_id  # noqa: E402
from channel.models import Chat, ChatSummary  # noqa: E402

client = TestClient(app)

OWNER = "owner@test.com"
INTRUDER = "intruder@test.com"
URL = "/api/memory/records"


def _headers(user_id: str = OWNER) -> dict[str, str]:
    token = issue_mgmt_jwt({"user_id": user_id, "email": user_id, "role": "user"})
    return {"Authorization": f"Bearer {token}"}


def _chat(chat_id: str, *, user_id: str = OWNER, created_at: str = "2026-07-30T10:00:00+00:00"):
    return Chat(
        chat_id=chat_id,
        user_id=user_id,
        title=f"title-{chat_id}",
        created_at=created_at,
        last_message_at=created_at,
        model_default="m",
    )


def _event(event_id: str, *entries: tuple[str, str]) -> dict[str, Any]:
    return {
        "eventId": event_id,
        "eventTimestamp": datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
        "payload": [
            {"conversational": {"role": role, "content": {"text": text}}} for role, text in entries
        ],
    }


def _agentcore(
    session_ids: list[str],
    events: dict[str, list[dict[str, Any]]],
    *,
    next_token: str | None = None,
) -> MagicMock:
    fake = MagicMock()
    fake.list_sessions.return_value = {
        "sessionSummaries": [
            {"sessionId": sid, "createdAt": datetime(2026, 7, 30, tzinfo=timezone.utc)}
            for sid in session_ids
        ],
        **({"nextToken": next_token} if next_token else {}),
    }
    fake.list_events.side_effect = lambda **kw: {"events": events.get(kw["sessionId"], [])}
    return fake


def _call(
    fake: MagicMock,
    chats: dict[str, Chat],
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    summaries: dict[str, ChatSummary] | None = None,
):
    with (
        patch.object(memory_api, "_agentcore_client", return_value=fake),
        patch.object(memory_api, "_memory_id_for_env", return_value="mem-1"),
        patch("channel.storage.get_chat_by_id", chats.get),
        patch("channel.storage.get_chat_summary", (summaries or {}).get),
    ):
        return client.get(URL, params=params or {}, headers=headers or _headers())


# ── auth ──────────────────────────────────────────────────────────────────


def test_requires_a_mgmt_jwt():
    assert client.get(URL).status_code in (401, 403)


def test_rejects_a_garbage_bearer_token():
    assert client.get(URL, headers={"Authorization": "Bearer nope"}).status_code == 401


# ── happy path ────────────────────────────────────────────────────────────


def test_returns_grouped_classified_records():
    fake = _agentcore(
        ["chat-a"],
        {
            "chat-a": [
                _event("e2", ("ASSISTANT", f"{_REMEMBER_PREFIX} likes sage")),
                _event("e1", ("USER", "i love sage"), ("ASSISTANT", "noted")),
            ]
        },
    )

    resp = _call(fake, {"chat-a": _chat("chat-a")})

    assert resp.status_code == 200
    body = resp.json()
    assert body["withheld_record_count"] == 0
    assert body["next_cursor"] is None
    [group] = body["groups"]
    assert group["chat_id"] == "chat-a"
    assert group["chat_title"] == "title-chat-a"
    assert group["created_at"] == "2026-07-30"
    assert [(r["role"], r["kind"], r["editable"]) for r in group["records"]] == [
        ("USER", "conversation", False),
        ("ASSISTANT", "conversation", False),
        ("ASSISTANT", "remembered", True),
    ]
    # Every record carries the in-window flag; ids are unique even though
    # the first two share one AgentCore event.
    assert all(r["used_in_recall"] for r in group["records"])
    assert len({r["record_id"] for r in group["records"]}) == 3


def test_meta_records_are_classified_and_not_editable():
    fake = _agentcore(
        ["chat-a"], {"chat-a": [_event("e1", ("ASSISTANT", f"{_META_PREFIX} used web_search"))]}
    )

    body = _call(fake, {"chat-a": _chat("chat-a")}).json()

    [record] = body["groups"][0]["records"]
    assert (record["kind"], record["editable"]) == ("meta", False)


def test_text_is_returned_in_full_not_truncated_to_the_recall_cap():
    long_text = "x" * (_RECALL_EVENT_TEXT_TRUNCATE * 4)
    fake = _agentcore(["chat-a"], {"chat-a": [_event("e1", ("USER", long_text))]})

    body = _call(fake, {"chat-a": _chat("chat-a")}).json()

    assert body["groups"][0]["records"][0]["text"] == long_text


def test_recall_window_reports_the_live_caps():
    fake = _agentcore([], {})

    body = _call(fake, {}).json()

    assert body["recall_window"] == {
        "max_sessions": _RECALL_MAX_SESSIONS,
        "events_per_session": _RECALL_EVENTS_PER_SESSION,
        "text_truncate": _RECALL_EVENT_TEXT_TRUNCATE,
        "ordering": "recency",
        "enabled": True,
    }


def test_recall_window_enabled_reflects_the_kill_switch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHANNEL_RECALL_ENABLED", "0")
    fake = _agentcore([], {})

    assert _call(fake, {}).json()["recall_window"]["enabled"] is False


def test_nothing_is_used_in_recall_while_the_kill_switch_is_off(
    monkeypatch: pytest.MonkeyPatch,
):
    # A disabled hook injects nothing, so no record can be in the window.
    # `recall_window.enabled: false` alongside `used_in_recall: true` would
    # be the response contradicting itself.
    monkeypatch.setenv("CHANNEL_RECALL_ENABLED", "0")
    fake = _agentcore(["chat-a"], {"chat-a": [_event("e1", ("USER", "hi"))]})

    body = _call(fake, {"chat-a": _chat("chat-a")}).json()

    assert body["recall_window"]["enabled"] is False
    assert [r["used_in_recall"] for r in body["groups"][0]["records"]] == [False]
    # The window ListSessions is skipped entirely — only the page call runs.
    assert len(fake.list_sessions.call_args_list) == 1
    assert "maxResults" in fake.list_sessions.call_args_list[0].kwargs


def test_groups_are_newest_chat_first():
    fake = _agentcore(
        ["old", "new"],
        {"old": [_event("e1", ("USER", "o"))], "new": [_event("e1", ("USER", "n"))]},
    )
    chats = {
        "old": _chat("old", created_at="2026-07-01T00:00:00+00:00"),
        "new": _chat("new", created_at="2026-07-31T00:00:00+00:00"),
    }

    body = _call(fake, chats).json()

    assert [g["chat_id"] for g in body["groups"]] == ["new", "old"]


def test_sessions_with_no_usable_events_are_skipped():
    fake = _agentcore(
        ["chat-a", "chat-b"], {"chat-a": [_event("e1", ("USER", "hi"))], "chat-b": []}
    )

    body = _call(fake, {"chat-a": _chat("chat-a"), "chat-b": _chat("chat-b")}).json()

    assert [g["chat_id"] for g in body["groups"]] == ["chat-a"]


# ── summaries (#245) ──────────────────────────────────────────────────────


def test_head_summaries_are_returned_read_only():
    fake = _agentcore(["chat-a"], {"chat-a": [_event("e1", ("USER", "hi"))]})
    summary = ChatSummary(
        chat_id="chat-a",
        text="earlier: sage green",
        covers_through="MSG#2026-07-30#m1",
        updated_at="2026-07-30T12:00:00+00:00",
    )

    body = _call(fake, {"chat-a": _chat("chat-a")}, summaries={"chat-a": summary}).json()

    assert body["summaries"] == [
        {
            "chat_id": "chat-a",
            "chat_title": "title-chat-a",
            "text": "earlier: sage green",
            "covers_through": "MSG#2026-07-30#m1",
            "updated_at": "2026-07-30T12:00:00+00:00",
        }
    ]
    # Derived + regenerating, so deliberately not addressable (decision 4).
    assert "record_id" not in body["summaries"][0]


def test_a_chat_with_only_a_summary_still_surfaces_it():
    # No AgentCore events at all — the group is skipped but the summary,
    # which IS injected into that chat's system prompt every turn, is not.
    fake = _agentcore(["chat-a"], {"chat-a": []})
    summary = ChatSummary(
        chat_id="chat-a", text="gist", covers_through="MSG#1", updated_at="2026-07-30T12:00:00Z"
    )

    body = _call(fake, {"chat-a": _chat("chat-a")}, summaries={"chat-a": summary}).json()

    assert body["groups"] == []
    assert [s["chat_id"] for s in body["summaries"]] == ["chat-a"]


# ── ownership verification (the #474 defence) ─────────────────────────────


def test_records_from_a_session_the_caller_does_not_own_are_withheld():
    # A foreign session sitting in this caller's actor partition — an
    # actor-id collision (#474) or a chat-delete wipe that left an orphan.
    fake = _agentcore(
        ["mine", "theirs"],
        {
            "mine": [_event("e1", ("USER", "my message"))],
            "theirs": [_event("e1", ("USER", "their secret"), ("ASSISTANT", "their reply"))],
        },
    )
    chats = {"mine": _chat("mine"), "theirs": _chat("theirs", user_id=INTRUDER)}

    resp = _call(fake, chats)

    body = resp.json()
    assert [g["chat_id"] for g in body["groups"]] == ["mine"]
    assert body["withheld_record_count"] == 2
    serialized = json.dumps(body)
    assert "their secret" not in serialized
    assert "their reply" not in serialized
    assert "theirs" not in serialized


def test_a_session_whose_chat_row_is_gone_is_withheld_not_shown_untitled():
    # Unverifiable is unverifiable: an orphaned AgentCore session is what a
    # failed chat-delete wipe leaves behind, so surfacing it would show the
    # user data they already asked to delete.
    fake = _agentcore(["orphan"], {"orphan": [_event("e1", ("USER", "ghost"))]})

    body = _call(fake, {}).json()

    assert body["groups"] == []
    assert body["withheld_record_count"] == 1


def test_withholding_is_logged_as_a_474_detector(monkeypatch: pytest.MonkeyPatch):
    """A non-zero withheld count is the live #474 signal, so it must warn.

    The module-level ``logger`` is mocked directly rather than using
    ``caplog`` because the ``channel`` logger sets ``propagate = False``
    once ``configure_logging`` has run in the test session (same
    workaround as ``test_admin_api`` / ``test_chats_api``).
    """
    fake_logger = MagicMock()
    monkeypatch.setattr(memory_api, "logger", fake_logger)
    fake = _agentcore(["theirs"], {"theirs": [_event("e1", ("USER", "secret"))]})

    _call(fake, {"theirs": _chat("theirs", user_id=INTRUDER)})

    fmt, user_hash, sessions, records = fake_logger.warning.call_args.args
    assert fmt.startswith("memory.records_withheld")
    assert (sessions, records) == (1, 1)
    # The raw sub is an email — it must be fingerprinted, never logged.
    assert user_hash != OWNER
    assert user_hash == fingerprint_id(OWNER)


def test_no_withheld_log_line_on_a_clean_page(monkeypatch: pytest.MonkeyPatch):
    fake_logger = MagicMock()
    monkeypatch.setattr(memory_api, "logger", fake_logger)
    fake = _agentcore(["mine"], {"mine": [_event("e1", ("USER", "hi"))]})

    _call(fake, {"mine": _chat("mine")})

    fake_logger.warning.assert_not_called()


def test_endpoint_takes_no_actor_or_user_parameter():
    # Scope comes from the token claim only ("agents swap tokens to switch
    # context"); a rogue query param must be ignored, not honoured.
    fake = _agentcore(["mine"], {"mine": [_event("e1", ("USER", "hi"))]})

    _call(fake, {"mine": _chat("mine")}, params={"actor_id": INTRUDER, "user_id": INTRUDER})

    expected = memory_api._sanitize_actor_id(OWNER)
    for call in fake.list_sessions.call_args_list:
        assert call.kwargs["actorId"] == expected


# ── chat_id filter ────────────────────────────────────────────────────────


def test_chat_id_filter_returns_only_that_chat():
    fake = _agentcore(
        ["chat-a", "chat-b"],
        {"chat-a": [_event("e1", ("USER", "a"))], "chat-b": [_event("e1", ("USER", "b"))]},
    )

    body = _call(
        fake,
        {"chat-a": _chat("chat-a"), "chat-b": _chat("chat-b")},
        params={"chat_id": "chat-a"},
    ).json()

    assert [g["chat_id"] for g in body["groups"]] == ["chat-a"]
    assert body["next_cursor"] is None


def test_chat_id_filter_404s_on_a_chat_the_caller_does_not_own():
    fake = _agentcore(["chat-a"], {})

    resp = _call(fake, {"chat-a": _chat("chat-a", user_id=INTRUDER)}, params={"chat_id": "chat-a"})

    # 404, never 403 — chat existence must not leak.
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Chat not found"


def test_chat_id_filter_404s_on_a_missing_chat():
    fake = _agentcore([], {})

    resp = _call(fake, {}, params={"chat_id": "nope"})

    assert resp.status_code == 404


def test_chat_id_filter_still_flags_the_recall_window():
    fake = _agentcore(["chat-a"], {"chat-a": [_event("e1", ("USER", "hi"))]})

    body = _call(fake, {"chat-a": _chat("chat-a")}, params={"chat_id": "chat-a"}).json()

    assert body["groups"][0]["records"][0]["used_in_recall"] is True


# ── pagination ────────────────────────────────────────────────────────────


def test_next_cursor_is_opaque_and_round_trips():
    fake = _agentcore(
        ["chat-a"], {"chat-a": [_event("e1", ("USER", "hi"))]}, next_token="vendor-tok"
    )

    body = _call(fake, {"chat-a": _chat("chat-a")}).json()

    cursor = body["next_cursor"]
    assert cursor is not None
    assert "vendor-tok" not in cursor  # the vendor token never appears raw

    fake2 = _agentcore(["chat-a"], {"chat-a": [_event("e1", ("USER", "hi"))]})
    _call(fake2, {"chat-a": _chat("chat-a")}, params={"cursor": cursor})

    page_call = next(c for c in fake2.list_sessions.call_args_list if "maxResults" in c.kwargs)
    assert page_call.kwargs["nextToken"] == "vendor-tok"


def test_a_cursor_minted_for_another_caller_is_rejected():
    # The cursor is bound to the actor it was minted for, matching
    # ``chats._decode_cursor``'s chat-scoping: a foreign nextToken is a
    # malformed request, not something to forward to AgentCore.
    fake = _agentcore(
        ["chat-a"], {"chat-a": [_event("e1", ("USER", "hi"))]}, next_token="vendor-tok"
    )
    cursor = _call(fake, {"chat-a": _chat("chat-a")}).json()["next_cursor"]

    other = _agentcore([], {})
    resp = _call(other, {}, params={"cursor": cursor}, headers=_headers(INTRUDER))

    assert resp.status_code == 400
    other.list_sessions.assert_not_called()


def test_group_reports_when_a_chat_has_more_events_than_the_cap():
    fake = MagicMock()
    fake.list_sessions.return_value = {
        "sessionSummaries": [
            {"sessionId": "chat-a", "createdAt": datetime(2026, 7, 30, tzinfo=timezone.utc)}
        ]
    }
    fake.list_events.return_value = {
        "events": [_event("e1", ("USER", "newest kept"))],
        "nextToken": "older-events-exist",
    }

    body = _call(fake, {"chat-a": _chat("chat-a")}).json()

    assert body["groups"][0]["records_truncated"] is True


def test_group_reports_no_truncation_for_a_short_chat():
    fake = _agentcore(["chat-a"], {"chat-a": [_event("e1", ("USER", "hi"))]})

    body = _call(fake, {"chat-a": _chat("chat-a")}).json()

    assert body["groups"][0]["records_truncated"] is False


def test_limit_is_forwarded_as_the_session_page_size():
    fake = _agentcore([], {})

    _call(fake, {}, params={"limit": 25})

    page_call = next(c for c in fake.list_sessions.call_args_list if "maxResults" in c.kwargs)
    assert page_call.kwargs["maxResults"] == 25


@pytest.mark.parametrize("limit", [0, 51, "abc"])
def test_limit_is_validated(limit: Any):
    fake = _agentcore([], {})

    assert _call(fake, {}, params={"limit": limit}).status_code == 422


@pytest.mark.parametrize(
    "cursor",
    [
        "!!!not base64!!!",
        base64.urlsafe_b64encode(b"[1,2,3]").decode(),  # not an envelope dict
        base64.urlsafe_b64encode(b'{"t": 7}').decode(),  # token isn't a string
        base64.urlsafe_b64encode(b'{"t": ""}').decode(),  # empty token
        base64.urlsafe_b64encode(b"\xff\xfe").decode(),  # not utf-8
        base64.urlsafe_b64encode(b'{"t": "tok"}').decode(),  # no actor binding
        base64.urlsafe_b64encode(b'{"t": "tok", "a": "deadbeefdeadbeef"}').decode(),  # wrong actor
    ],
)
def test_malformed_cursor_is_a_400_not_a_500(cursor: str):
    fake = _agentcore([], {})

    resp = _call(fake, {}, params={"cursor": cursor})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid cursor"


def test_malformed_cursor_is_rejected_before_any_agentcore_call():
    fake = _agentcore([], {})

    _call(fake, {}, params={"cursor": "!!!"})

    fake.list_sessions.assert_not_called()


# ── module helpers ────────────────────────────────────────────────────────


def test_agentcore_client_is_lazily_constructed():
    with patch.object(memory_api.boto3, "client", return_value="sentinel") as mk:
        assert memory_api._agentcore_client() == "sentinel"
    mk.assert_called_once_with("bedrock-agentcore")


def test_memory_id_resolves_from_the_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHANNEL_ENV", "unit")
    with patch.object(memory_api, "get_or_create_memory", return_value="mem-9") as mk:
        assert memory_api._memory_id_for_env() == "mem-9"
    mk.assert_called_once_with("unit")


def test_memory_id_falls_back_when_env_is_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CHANNEL_ENV", raising=False)
    with patch.object(memory_api, "get_or_create_memory", return_value="mem-9") as mk:
        memory_api._memory_id_for_env()
    mk.assert_called_once_with("unknown")


def test_router_is_mounted_under_the_api_prefix():
    assert "/api/memory/records" in {route.path for route in app.routes}
