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
import re
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

os.environ.setdefault("CHANNEL_JWT_SECRET", "test-secret-for-unit-tests")

from channel.agents.memory import _META_PREFIX  # noqa: E402
from channel.agents.memory_ranking import (  # noqa: E402
    POOL_EVENTS_PER_SESSION,
    POOL_MAX_SESSIONS,
)
from channel.agents.recall import _RECALL_EVENT_TEXT_TRUNCATE  # noqa: E402
from channel.agents.tools.memory_tools import _REMEMBER_PREFIX  # noqa: E402
from channel.api import memory as memory_api  # noqa: E402
from channel.api.main import app  # noqa: E402
from channel.auth.tokens import issue_mgmt_jwt  # noqa: E402
from channel.logging_config import fingerprint_id  # noqa: E402
from channel.models import Chat, ChatSummary, Message, MessageRole  # noqa: E402

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
        "max_sessions": POOL_MAX_SESSIONS,
        "events_per_session": POOL_EVENTS_PER_SESSION,
        "text_truncate": _RECALL_EVENT_TEXT_TRUNCATE,
        "ordering": "relevance",
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

    expected = memory_api.derive_actor_id(OWNER)
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


# ══ GET /api/memory/export (#476) ═════════════════════════════════════════
#
# The full account export behind the Privacy page's standing promise. The
# tests that matter most are the completeness ones (a walk that stopped at
# page one would be a silent lie) and the ownership one (this response is a
# downloadable file, so a leak leaks wherever the file goes).

EXPORT_URL = "/api/memory/export"


def _message(text: str, *, role: str = "user", created_at: str = "2026-07-30T12:00:00+00:00"):
    return Message(
        chat_id="chat-a",
        msg_id=f"m-{text}",
        role=MessageRole(role),
        text=text,
        created_at=created_at,
    )


def _export_agentcore(
    pages: list[tuple[list[str], str | None]],
    events: dict[str, list[dict[str, Any]]],
) -> MagicMock:
    """A fake AgentCore whose ``ListSessions`` actually paginates.

    ``pages`` is ``[(session_ids, next_token), ...]``. The un-paginated
    recall-window probe (no ``maxResults``) always gets the first page,
    mirroring ``recall_window_session_ids``, which reads one page by design.
    """
    by_token: dict[str | None, dict[str, Any]] = {}
    previous: str | None = None
    for session_ids, next_token in pages:
        by_token[previous] = {
            "sessionSummaries": [
                {"sessionId": sid, "createdAt": datetime(2026, 7, 30, tzinfo=timezone.utc)}
                for sid in session_ids
            ],
            **({"nextToken": next_token} if next_token else {}),
        }
        previous = next_token

    fake = MagicMock()
    fake.list_sessions.side_effect = lambda **kw: (
        by_token[kw.get("nextToken")] if "maxResults" in kw else by_token[None]
    )
    fake.list_events.side_effect = lambda **kw: {"events": events.get(kw["sessionId"], [])}
    return fake


def _export_call(
    fake: MagicMock,
    owned: dict[str, Chat] | None = None,
    *,
    list_chats: Any = None,
    list_messages: Any = None,
    summaries: dict[str, ChatSummary] | None = None,
    put_audit: Any = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    client_obj: TestClient | None = None,
):
    """Drive ``GET /api/memory/export`` with every AWS seam mocked.

    ``owned`` doubles as the ``get_chat_by_id`` table (the ownership gate)
    and, unless ``list_chats`` overrides it, as the chat rows the DynamoDB
    walk returns — newest first, as ``list_chats_for_user`` orders them.
    """
    owned = owned or {}
    if list_chats is None:
        rows = sorted(owned.values(), key=lambda c: c.created_at, reverse=True)
        list_chats = MagicMock(return_value=(rows, None))
    if list_messages is None:
        list_messages = MagicMock(return_value=([], None))
    with (
        patch.object(memory_api, "_agentcore_client", return_value=fake),
        patch.object(memory_api, "_memory_id_for_env", return_value="mem-1"),
        patch("channel.storage.get_chat_by_id", owned.get),
        patch("channel.storage.list_chats_for_user", list_chats),
        patch("channel.storage.list_messages", list_messages),
        patch("channel.storage.get_chat_summary", (summaries or {}).get),
        patch("channel.storage.put_audit_event", put_audit or MagicMock(return_value={})),
    ):
        return (client_obj or client).get(
            EXPORT_URL, params=params or {}, headers=headers or _headers()
        )


# ── auth ──────────────────────────────────────────────────────────────────


def test_export_requires_a_mgmt_jwt():
    assert client.get(EXPORT_URL).status_code in (401, 403)


def test_export_rejects_a_garbage_bearer_token():
    assert client.get(EXPORT_URL, headers={"Authorization": "Bearer nope"}).status_code == 401


# ── shape ─────────────────────────────────────────────────────────────────


def test_export_returns_memory_records_summaries_and_chats():
    fake = _export_agentcore(
        [(["chat-a"], None)],
        {"chat-a": [_event("e1", ("USER", "i love sage"), ("ASSISTANT", "noted"))]},
    )
    summary = ChatSummary(
        chat_id="chat-a",
        text="earlier: sage green",
        covers_through="MSG#2026-07-30#m1",
        updated_at="2026-07-30T12:00:00+00:00",
    )

    resp = _export_call(
        fake,
        {"chat-a": _chat("chat-a")},
        list_messages=MagicMock(
            return_value=([_message("hello"), _message("hi", role="assistant")], None)
        ),
        summaries={"chat-a": summary},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert [(r["role"], r["kind"], r["chat_id"]) for r in body["memory_records"]] == [
        ("USER", "conversation", "chat-a"),
        ("ASSISTANT", "conversation", "chat-a"),
    ]
    assert all(r["used_in_recall"] for r in body["memory_records"])
    assert body["chat_summaries"] == [
        {
            "chat_id": "chat-a",
            "text": "earlier: sage green",
            "covers_through": "MSG#2026-07-30#m1",
            "updated_at": "2026-07-30T12:00:00+00:00",
        }
    ]
    [chat] = body["chats"]
    assert (chat["chat_id"], chat["title"], chat["archived"]) == ("chat-a", "title-chat-a", False)
    assert [(m["role"], m["text"]) for m in chat["messages"]] == [
        ("user", "hello"),
        ("assistant", "hi"),
    ]


def test_export_manifest_is_self_describing():
    fake = _export_agentcore([([], None)], {})

    manifest = _export_call(fake, {}).json()["manifest"]

    assert manifest["schema_version"] == memory_api._EXPORT_SCHEMA_VERSION
    assert manifest["actor_id"] == memory_api.derive_actor_id(OWNER)
    assert manifest["recall_window"] == {
        "max_sessions": POOL_MAX_SESSIONS,
        "events_per_session": POOL_EVENTS_PER_SESSION,
        "text_truncate": _RECALL_EVENT_TEXT_TRUNCATE,
        "ordering": "relevance",
        "enabled": True,
    }
    # The one sentence that stops a reader mistaking "stored" for "recalled".
    assert "used_in_recall" in manifest["note"]
    assert datetime.fromisoformat(manifest["exported_at"]).tzinfo is not None


def test_export_manifest_never_re_literals_the_recall_caps():
    # The caps must come from recall.py's own constants — a hand-copied
    # number would make the manifest a lie the first time a cap moves.
    fake = _export_agentcore([([], None)], {})

    window = _export_call(fake, {}).json()["manifest"]["recall_window"]

    assert window["max_sessions"] == POOL_MAX_SESSIONS
    assert window["events_per_session"] == POOL_EVENTS_PER_SESSION


def test_export_sets_the_attachment_download_headers():
    fake = _export_agentcore([([], None)], {})

    resp = _export_call(fake, {})

    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment; ")
    assert re.fullmatch(
        r'attachment; filename="channel-export-\d{4}-\d{2}-\d{2}\.json"', disposition
    )
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-type"].startswith("application/json")


def test_export_is_never_cached():
    # Same rule as /api/me/prefs and /api/me/sessions, with more at stake:
    # without no-store Chromium heuristic-caches the response, leaving a
    # whole account's chat history in an on-disk HTTP cache.
    fake = _export_agentcore([([], None)], {})

    assert _export_call(fake, {}).headers["cache-control"] == "no-store"


def test_export_of_an_empty_account_is_valid_not_an_error():
    fake = _export_agentcore([([], None)], {})

    resp = _export_call(fake, {})

    assert resp.status_code == 200
    body = resp.json()
    assert (body["memory_records"], body["chat_summaries"], body["chats"]) == ([], [], [])
    assert body["manifest"]["truncated_chat_ids"] == []
    # Empty is not the same as incomplete — an account with no data has
    # been exported in full.
    assert body["manifest"]["complete"] is True


def test_export_omits_summaries_for_chats_that_have_none():
    fake = _export_agentcore([([], None)], {})

    body = _export_call(fake, {"chat-a": _chat("chat-a")}, summaries={}).json()

    assert body["chat_summaries"] == []
    assert [c["chat_id"] for c in body["chats"]] == ["chat-a"]


# ── completeness (the whole point of an export) ───────────────────────────


def test_export_walks_every_session_page():
    fake = _export_agentcore(
        [(["chat-a"], "tok-2"), (["chat-b"], None)],
        {
            "chat-a": [_event("e1", ("USER", "from page one"))],
            "chat-b": [_event("e1", ("USER", "from page two"))],
        },
    )

    body = _export_call(fake, {"chat-a": _chat("chat-a"), "chat-b": _chat("chat-b")}).json()

    assert [r["text"] for r in body["memory_records"]] == ["from page one", "from page two"]


def test_export_walks_every_chat_page():
    list_chats = MagicMock(
        side_effect=[([_chat("chat-a")], {"SK": "cursor"}), ([_chat("chat-b")], None)]
    )
    fake = _export_agentcore([([], None)], {})

    body = _export_call(fake, {}, list_chats=list_chats).json()

    assert [c["chat_id"] for c in body["chats"]] == ["chat-a", "chat-b"]
    assert list_chats.call_args_list[1].kwargs["cursor"] == {"SK": "cursor"}


def test_export_walks_every_message_page():
    list_messages = MagicMock(
        side_effect=[([_message("first")], {"SK": "cursor"}), ([_message("second")], None)]
    )
    fake = _export_agentcore([([], None)], {})

    body = _export_call(fake, {"chat-a": _chat("chat-a")}, list_messages=list_messages).json()

    assert [m["text"] for m in body["chats"][0]["messages"]] == ["first", "second"]
    assert list_messages.call_args_list[1].kwargs["cursor"] == {"SK": "cursor"}


def test_export_includes_archived_chats():
    # An archived chat is still the user's data; the flag is exported so the
    # distinction survives rather than the rows vanishing.
    archived = _chat("chat-a").model_copy(update={"archived": True})
    list_chats = MagicMock(return_value=([archived], None))
    fake = _export_agentcore([([], None)], {})

    body = _export_call(fake, {}, list_chats=list_chats).json()

    assert body["chats"][0]["archived"] is True
    assert list_chats.call_args.kwargs["include_archived"] is True


def test_export_reports_chats_whose_memory_history_was_truncated():
    fake = _export_agentcore([(["chat-a"], None)], {})
    fake.list_events.side_effect = None
    fake.list_events.return_value = {
        "events": [_event("e1", ("USER", "newest kept"))],
        "nextToken": "older-events-exist",
    }

    body = _export_call(fake, {"chat-a": _chat("chat-a")}).json()

    assert body["manifest"]["truncated_chat_ids"] == ["chat-a"]
    # A truncated chat means the file is not the whole account, and the
    # file has to say so — a log line doesn't travel with a download.
    assert body["manifest"]["complete"] is False


def test_export_manifest_reports_complete_on_a_full_walk():
    fake = _export_agentcore([(["chat-a"], None)], {"chat-a": [_event("e1", ("USER", "hi"))]})

    body = _export_call(
        fake,
        {"chat-a": _chat("chat-a")},
        list_messages=MagicMock(return_value=([_message("hello")], None)),
    ).json()

    assert body["manifest"]["complete"] is True


def test_export_manifest_reports_incomplete_when_a_walk_hits_the_page_cap(
    monkeypatch: pytest.MonkeyPatch,
):
    # A paginator still handing back a token at the ceiling. The cap is
    # shrunk rather than simulating 200 real pages.
    monkeypatch.setattr(memory_api, "_EXPORT_MAX_PAGES", 1)
    fake = _export_agentcore(
        [(["chat-a"], "tok-2"), (["chat-b"], None)],
        {"chat-a": [_event("e1", ("USER", "hi"))]},
    )

    body = _export_call(fake, {"chat-a": _chat("chat-a")}).json()

    assert body["manifest"]["complete"] is False
    # The partial data is still returned — truncating is the lesser evil,
    # claiming completeness is not.
    assert [r["text"] for r in body["memory_records"]] == ["hi"]


def test_export_is_incomplete_when_a_message_walk_hits_the_page_cap(
    monkeypatch: pytest.MonkeyPatch,
):
    # Incompleteness anywhere — including inside one chat's messages —
    # makes the whole file incomplete.
    monkeypatch.setattr(memory_api, "_EXPORT_MAX_PAGES", 1)
    fake = _export_agentcore([([], None)], {})

    body = _export_call(
        fake,
        {"chat-a": _chat("chat-a")},
        list_messages=MagicMock(return_value=([_message("first")], {"SK": "more"})),
    ).json()

    assert body["manifest"]["complete"] is False


# ── scoping ───────────────────────────────────────────────────────────────


def test_export_withholds_sessions_the_caller_does_not_own():
    fake = _export_agentcore(
        [(["mine", "theirs"], None)],
        {
            "mine": [_event("e1", ("USER", "my message"))],
            "theirs": [_event("e1", ("USER", "their secret"))],
        },
    )
    chats = {"mine": _chat("mine"), "theirs": _chat("theirs", user_id=INTRUDER)}

    body = _export_call(fake, chats, list_chats=MagicMock(return_value=([], None))).json()

    assert [r["text"] for r in body["memory_records"]] == ["my message"]
    serialized = json.dumps(body)
    assert "their secret" not in serialized
    assert "theirs" not in serialized


def test_export_withholding_is_logged_as_the_partition_canary(monkeypatch: pytest.MonkeyPatch):
    fake_logger = MagicMock()
    monkeypatch.setattr(memory_api, "logger", fake_logger)
    fake = _export_agentcore([(["theirs"], None)], {"theirs": [_event("e1", ("USER", "secret"))]})

    _export_call(fake, {"theirs": _chat("theirs", user_id=INTRUDER)})

    fmt, user_hash, sessions, records = fake_logger.warning.call_args.args
    assert fmt.startswith("memory.export_sessions_withheld")
    assert (sessions, records) == (1, 1)
    # The raw sub is an email — fingerprinted, never logged raw.
    assert user_hash == fingerprint_id(OWNER)


def test_export_logs_nothing_on_a_clean_partition(monkeypatch: pytest.MonkeyPatch):
    fake_logger = MagicMock()
    monkeypatch.setattr(memory_api, "logger", fake_logger)
    fake = _export_agentcore([(["mine"], None)], {"mine": [_event("e1", ("USER", "hi"))]})

    _export_call(fake, {"mine": _chat("mine")})

    fake_logger.warning.assert_not_called()


def test_export_scopes_to_the_token_claim_alone():
    fake = _export_agentcore([(["mine"], None)], {"mine": [_event("e1", ("USER", "hi"))]})

    _export_call(fake, {"mine": _chat("mine")})

    expected = memory_api.derive_actor_id(OWNER)
    for call in fake.list_sessions.call_args_list:
        assert call.kwargs["actorId"] == expected


def test_export_ignores_a_rogue_actor_parameter():
    # Scope comes from the token claim only ("agents swap tokens to switch
    # context"); there is no actor / user parameter, so a supplied one must
    # be ignored rather than honoured.
    fake = _export_agentcore([(["mine"], None)], {"mine": [_event("e1", ("USER", "hi"))]})

    _export_call(
        fake,
        {"mine": _chat("mine")},
        params={"actor_id": INTRUDER, "user_id": INTRUDER},
    )

    expected = memory_api.derive_actor_id(OWNER)
    for call in fake.list_sessions.call_args_list:
        assert call.kwargs["actorId"] == expected


# ── recall kill-switch ────────────────────────────────────────────────────


def test_export_flags_nothing_as_recalled_while_the_kill_switch_is_off(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("CHANNEL_RECALL_ENABLED", "0")
    fake = _export_agentcore([(["chat-a"], None)], {"chat-a": [_event("e1", ("USER", "hi"))]})

    body = _export_call(fake, {"chat-a": _chat("chat-a")}).json()

    assert body["manifest"]["recall_window"]["enabled"] is False
    assert [r["used_in_recall"] for r in body["memory_records"]] == [False]
    # The window probe is skipped entirely — only the paged drain runs.
    assert all("maxResults" in c.kwargs for c in fake.list_sessions.call_args_list)


# ── audit trail ───────────────────────────────────────────────────────────


def test_export_writes_an_audit_event_with_counts_only():
    put_audit = MagicMock(return_value={})
    fake = _export_agentcore(
        [(["chat-a"], None)], {"chat-a": [_event("e1", ("USER", "secret sauce"))]}
    )
    summary = ChatSummary(
        chat_id="chat-a", text="gist", covers_through="MSG#1", updated_at="2026-07-30T12:00:00Z"
    )

    _export_call(
        fake,
        {"chat-a": _chat("chat-a")},
        list_messages=MagicMock(return_value=([_message("hello")], None)),
        summaries={"chat-a": summary},
        put_audit=put_audit,
    )

    kwargs = put_audit.call_args.kwargs
    assert kwargs["event_type"] == "memory.exported"
    assert kwargs["actor_id"] == OWNER
    assert kwargs["details"] == {
        "memory_records": 1,
        "chat_summaries": 1,
        "chats": 1,
        "messages": 1,
    }
    # An audit row is a trail, not a second copy of the data it records.
    serialized = json.dumps(kwargs["details"])
    assert "secret sauce" not in serialized
    assert "hello" not in serialized
    assert "gist" not in serialized


def test_export_fails_closed_when_the_audit_write_fails():
    # Nothing has been disclosed yet, so the export refuses rather than
    # handing over a whole account with no compliance trail.
    put_audit = MagicMock(side_effect=RuntimeError("ddb down"))
    fake = _export_agentcore([([], None)], {})

    with pytest.raises(RuntimeError, match="ddb down"):
        _export_call(fake, {}, put_audit=put_audit)


# ── metrics ───────────────────────────────────────────────────────────────


def test_export_emits_the_success_counter():
    fake = _export_agentcore([([], None)], {})

    with patch.object(memory_api, "record_memory_export_outcome", new=AsyncMock()) as counter:
        _export_call(fake, {})

    counter.assert_awaited_once_with(success=True)


def test_export_emits_the_failure_counter_and_re_raises():
    fake = _export_agentcore([([], None)], {})
    fake.list_sessions.side_effect = RuntimeError("agentcore down")

    with (
        patch.object(memory_api, "record_memory_export_outcome", new=AsyncMock()) as counter,
        pytest.raises(RuntimeError, match="agentcore down"),
    ):
        _export_call(fake, {})

    counter.assert_awaited_once_with(success=False)


# ── pagination backstop ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paginate_stops_at_the_page_cap_and_says_so(monkeypatch: pytest.MonkeyPatch):
    """A paginator that never terminates must not burn the Lambda timeout.

    Truncating here is the lesser evil, but a silent truncation would be
    exactly the lie this surface exists to stop — hence both the log line
    and the ``drained=False`` the manifest is built from.
    """
    fake_logger = MagicMock()
    monkeypatch.setattr(memory_api, "logger", fake_logger)

    async def _never_ends(token: Any) -> tuple[list[int], str]:
        return [1], "always-more"

    items, drained = await memory_api._paginate(_never_ends, what="sessions")

    assert len(items) == memory_api._EXPORT_MAX_PAGES
    assert drained is False
    fmt, what, pages = fake_logger.warning.call_args.args
    # Neutral event name: the helper is shared with #477's forget walk, so
    # labelling every cap-hit an "export" would make the canary useless.
    assert fmt.startswith("memory.page_cap_hit")
    assert "export" not in fmt
    assert (what, pages) == ("sessions", memory_api._EXPORT_MAX_PAGES)


@pytest.mark.asyncio
async def test_paginate_reports_drained_when_the_walk_finishes():
    async def _one_page(token: Any) -> tuple[list[int], None]:
        return [1, 2], None

    assert await memory_api._paginate(_one_page, what="chats") == ([1, 2], True)


def test_export_route_is_mounted_under_the_api_prefix():
    assert EXPORT_URL in {route.path for route in app.routes}


# ══ A caller with no memory partition (#527) ══════════════════════════════
#
# Both endpoints returned 500 to every user who had signed in but never
# chatted: an AgentCore actor partition is created lazily by the first
# memory WRITE, so those users have none and every read raises
# ``ResourceNotFoundException``.
#
# These tests make the mocked client RAISE. The suite already had
# ``test_export_of_an_empty_account_is_valid_not_an_error``, whose mock
# RETURNS an empty page — and that is exactly why this shipped twice: a
# returning mock cannot tell "no actor" from "empty actor", which is the
# distinction that was broken. Each 200 assertion below is paired with one
# proving a DIFFERENT ``ClientError`` still produces a 500, because an
# over-broad handler would trade a visible failure for a confident, wrong
# "you have no memories" — on an export, a data-integrity claim we cannot
# stand behind.

#: A client that surfaces 5xx as a response instead of re-raising, so the
#: status code a caller would actually receive is what gets asserted.
error_client = TestClient(app, raise_server_exceptions=False)

NO_PARTITION = "ResourceNotFoundException"


def _agentcore_raising(code: str) -> MagicMock:
    """A fake AgentCore where every read raises ``code``.

    ``botocore.errorfactory.ResourceNotFoundException`` is generated from
    the service model at runtime and subclasses ``ClientError`` with this
    response shape, so the base class with the right code drives the same
    branch the vendor exception would.
    """
    error = ClientError({"Error": {"Code": code, "Message": "Actor x not found"}}, "ListSessions")
    fake = MagicMock()
    fake.list_sessions.side_effect = error
    fake.list_events.side_effect = error
    return fake


@pytest.mark.parametrize("params", [{}, {"chat_id": "chat-a"}])
def test_records_for_a_user_with_no_memory_partition_is_empty_not_an_error(
    params: dict[str, Any],
):
    """The bug: a signed-in user who has never chatted got a 500.

    Both shapes are covered because they take different AgentCore paths —
    the unfiltered page enumerates sessions, while ``?chat_id=`` skips
    straight to ``ListEvents`` on a chat the caller owns.
    """
    fake = _agentcore_raising(NO_PARTITION)

    resp = _call(fake, {"chat-a": _chat("chat-a")}, params=params)

    assert resp.status_code == 200
    body = resp.json()
    assert body["groups"] == []
    assert body["summaries"] == []
    assert body["withheld_record_count"] == 0
    assert body["next_cursor"] is None
    # The window is still reported honestly — the caps are real, the caller
    # simply has nothing sitting in them.
    assert body["recall_window"]["max_sessions"] == POOL_MAX_SESSIONS


def test_records_is_still_empty_with_the_recall_kill_switch_off(
    monkeypatch: pytest.MonkeyPatch,
):
    """With recall disabled the recall probe is skipped, so ``ListSessions``
    inside ``list_sessions_page`` is the FIRST AgentCore call — a fix
    confined to the probe would leave this path 500ing."""
    monkeypatch.setenv("CHANNEL_RECALL_ENABLED", "0")
    fake = _agentcore_raising(NO_PARTITION)

    resp = _call(fake, {})

    assert resp.status_code == 200
    assert resp.json()["groups"] == []
    assert resp.json()["recall_window"]["enabled"] is False


@pytest.mark.parametrize("code", ["ThrottlingException", "AccessDeniedException"])
def test_records_still_500s_when_agentcore_fails_for_any_other_reason(code: str):
    """The narrowness that makes the empty answer trustworthy."""
    fake = _agentcore_raising(code)

    with (
        patch.object(memory_api, "_agentcore_client", return_value=fake),
        patch.object(memory_api, "_memory_id_for_env", return_value="mem-1"),
        patch("channel.storage.get_chat_by_id", {}.get),
        patch("channel.storage.get_chat_summary", {}.get),
    ):
        resp = error_client.get(URL, headers=_headers())

    assert resp.status_code == 500


def test_export_for_a_user_with_no_memory_partition_is_empty_not_an_error():
    """The Privacy page's promise, for the account most likely to test it:
    brand new, no chats, straight to "export my data"."""
    fake = _agentcore_raising(NO_PARTITION)

    resp = _export_call(fake, {})

    assert resp.status_code == 200
    body = resp.json()
    assert body["memory_records"] == []
    assert body["chat_summaries"] == []
    assert body["chats"] == []
    # ``complete`` answers "did I get everything there is?", not "is there
    # anything?" — the walks finished and there was nothing to walk.
    assert body["manifest"]["complete"] is True
    assert body["manifest"]["truncated_chat_ids"] == []


def test_export_of_an_empty_partition_still_writes_its_audit_row():
    # An export that discloses nothing is still a whole-account read, and
    # the trail is what makes that claim checkable later.
    put_audit = MagicMock(return_value={})

    _export_call(_agentcore_raising(NO_PARTITION), {}, put_audit=put_audit)

    assert put_audit.call_args.kwargs["event_type"] == "memory.exported"
    assert put_audit.call_args.kwargs["details"]["memory_records"] == 0


@pytest.mark.parametrize("code", ["ThrottlingException", "AccessDeniedException"])
def test_export_still_500s_when_agentcore_fails_for_any_other_reason(code: str):
    """The one that matters most: a downloadable file asserting "you have no
    memories" during an AgentCore outage would be a data-integrity claim
    this endpoint has no basis to make."""
    fake = _agentcore_raising(code)

    with (
        patch.object(memory_api, "_agentcore_client", return_value=fake),
        patch.object(memory_api, "_memory_id_for_env", return_value="mem-1"),
        patch("channel.storage.get_chat_by_id", {}.get),
        patch("channel.storage.list_chats_for_user", MagicMock(return_value=([], None))),
        patch("channel.storage.list_messages", MagicMock(return_value=([], None))),
        patch("channel.storage.get_chat_summary", {}.get),
        patch("channel.storage.put_audit_event", MagicMock(return_value={})),
    ):
        resp = error_client.get(EXPORT_URL, headers=_headers())

    assert resp.status_code == 500


def test_export_counts_a_failed_agentcore_read_as_a_failed_export():
    """The failure counter must not be skipped by the new handler — an
    absorbed missing partition is a success, anything else is not."""
    fake = _agentcore_raising("ThrottlingException")

    with (
        patch.object(memory_api, "record_memory_export_outcome", new=AsyncMock()) as counter,
        pytest.raises(ClientError),
    ):
        _export_call(fake, {})

    counter.assert_awaited_once_with(success=False)


# ══ Forget — DELETE /api/memory/records[/{record_id}] (#477) ══════════════
#
# This is the destructive surface, so the tests that earn their place are
# the ones that prove an attempt FAILS rather than that a filter was
# applied: a cross-user delete must be impossible, and the assertion is
# that ``delete_event`` was never called at all — a test that only checked
# the response code would pass against an implementation that deleted the
# record and then 404'd.
#
# The #527 shape is re-proved here on the delete path. Every "no partition"
# mock RAISES; a mock that returned empty could not tell an absent actor
# from an empty one, which is exactly how that bug shipped twice. Each
# absorbing assertion is paired with one proving a different ``ClientError``
# still produces a 500 — on a forget endpoint, reporting "deleted" when
# nothing was deleted is worse than the error it replaces, because a user
# told their data is gone stops asking.

FORGET_URL = "/api/memory/records"


def _record_id(session_id: str = "chat-a", event_id: str = "e1", index: int = 0) -> str:
    from channel.agents.memory_records import encode_record_id

    return encode_record_id(session_id, event_id, index)


def _deleting_agentcore(
    session_ids: list[str],
    events: dict[str, list[dict[str, Any]]],
    *,
    delete_side_effect: Any = None,
) -> MagicMock:
    fake = _agentcore(session_ids, events)
    if delete_side_effect is not None:
        fake.delete_event.side_effect = delete_side_effect
    return fake


def _forget_call(
    fake: MagicMock,
    chats: dict[str, Chat],
    *,
    path: str = FORGET_URL,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    put_audit: Any = None,
    client_obj: TestClient | None = None,
):
    """Drive either DELETE route with every AWS seam mocked."""
    with (
        patch.object(memory_api, "_agentcore_client", return_value=fake),
        patch.object(memory_api, "_memory_id_for_env", return_value="mem-1"),
        patch("channel.storage.get_chat_by_id", chats.get),
        patch("channel.storage.put_audit_event", put_audit or MagicMock(return_value={})),
    ):
        return (client_obj or client).delete(
            path, params=params or {}, headers=headers or _headers()
        )


# ── auth ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [FORGET_URL, f"{FORGET_URL}/anything"])
def test_forget_requires_a_mgmt_jwt(path: str):
    assert client.delete(path).status_code in (401, 403)


# ── per-record ────────────────────────────────────────────────────────────


def test_forgetting_one_record_deletes_exactly_that_agentcore_event():
    fake = _deleting_agentcore([], {})

    resp = _forget_call(
        fake, {"chat-a": _chat("chat-a")}, path=f"{FORGET_URL}/{_record_id('chat-a', 'e7')}"
    )

    assert resp.status_code == 204
    assert fake.delete_event.call_args.kwargs == {
        "memoryId": "mem-1",
        "actorId": memory_api.derive_actor_id(OWNER),
        "sessionId": "chat-a",
        "eventId": "e7",
    }


def test_forgetting_one_record_leaves_the_dynamodb_chat_and_messages_untouched():
    """Epic #129 decision 9 — the blast radius is the AgentCore event only.

    A click in a settings panel must not punch a hole in visible chat
    history: it would break user/assistant turn pairing and strand the #245
    head summary's ``covers_through`` pointer."""
    fake = _deleting_agentcore([], {})
    writes = {
        name: MagicMock()
        for name in ("delete_chat", "delete_last_assistant_message", "put_chat_summary")
    }

    with (
        patch("channel.storage.delete_chat", writes["delete_chat"]),
        patch(
            "channel.storage.delete_last_assistant_message", writes["delete_last_assistant_message"]
        ),
        patch("channel.storage.put_chat_summary", writes["put_chat_summary"]),
    ):
        resp = _forget_call(
            fake, {"chat-a": _chat("chat-a")}, path=f"{FORGET_URL}/{_record_id('chat-a')}"
        )

    assert resp.status_code == 204
    for name, mock in writes.items():
        assert mock.call_count == 0, f"{name} must not be reached by a memory forget"


def test_a_malformed_record_id_is_a_400_and_never_reaches_agentcore():
    # Forwarding it would come back as a ValidationException — a 500 for
    # what is plainly a client error.
    fake = _deleting_agentcore([], {})

    resp = _forget_call(fake, {"chat-a": _chat("chat-a")}, path=f"{FORGET_URL}/not-base64!!")

    assert resp.status_code == 400
    fake.delete_event.assert_not_called()


def test_another_users_record_cannot_be_forgotten():
    """The load-bearing one: the attempt must FAIL, not merely be filtered.

    404 rather than 403 so a probe cannot distinguish "someone else owns
    this" from "this never existed" — the shape ``_load_owned_chat`` and
    ``_load_owned_session`` already use."""
    fake = _deleting_agentcore([], {})

    resp = _forget_call(
        fake,
        {"chat-a": _chat("chat-a", user_id=OWNER)},
        path=f"{FORGET_URL}/{_record_id('chat-a')}",
        headers=_headers(INTRUDER),
    )

    assert resp.status_code == 404
    fake.delete_event.assert_not_called()


def test_a_record_in_a_session_with_no_chat_row_is_a_404():
    # An orphaned AgentCore session — what a failed
    # ``chats._wipe_agentcore_session`` leaves behind. Every read withholds
    # it, so there is nothing in the panel to press.
    fake = _deleting_agentcore([], {})

    resp = _forget_call(fake, {}, path=f"{FORGET_URL}/{_record_id('orphan')}")

    assert resp.status_code == 404
    fake.delete_event.assert_not_called()


def test_forgetting_a_record_in_a_partition_that_was_never_created_is_a_404_not_a_500():
    """#527 on the delete path: an actor partition is created lazily by the
    first memory WRITE, so a user who has never chatted has none. The mock
    RAISES — one returning a value could not exercise this at all."""
    fake = _deleting_agentcore(
        [],
        {},
        delete_side_effect=ClientError(
            {"Error": {"Code": NO_PARTITION, "Message": "Actor x not found"}}, "DeleteEvent"
        ),
    )

    resp = _forget_call(fake, {"chat-a": _chat("chat-a")}, path=f"{FORGET_URL}/{_record_id()}")

    assert resp.status_code == 404


@pytest.mark.parametrize("code", ["ThrottlingException", "AccessDeniedException"])
def test_forgetting_a_record_still_500s_when_agentcore_fails_for_any_other_reason(code: str):
    """The narrowness that makes the 404 trustworthy. Telling a user their
    record is gone during an AgentCore outage is worse than the error."""
    fake = _deleting_agentcore([], {})
    fake.delete_event.side_effect = ClientError({"Error": {"Code": code}}, "DeleteEvent")

    resp = _forget_call(
        fake,
        {"chat-a": _chat("chat-a")},
        path=f"{FORGET_URL}/{_record_id()}",
        client_obj=error_client,
    )

    assert resp.status_code == 500


def test_a_failed_record_delete_is_counted_as_a_failure():
    fake = _deleting_agentcore([], {})
    fake.delete_event.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException"}}, "DeleteEvent"
    )

    with (
        patch.object(memory_api, "record_memory_record_delete_outcome", new=AsyncMock()) as counter,
        pytest.raises(ClientError),
    ):
        _forget_call(fake, {"chat-a": _chat("chat-a")}, path=f"{FORGET_URL}/{_record_id()}")

    counter.assert_awaited_once_with(success=False)


def test_an_unknown_record_counts_neither_success_nor_failure():
    # A 4xx is a statement about the request; counting it would put a floor
    # under the failure rate that no fix could lower.
    fake = _deleting_agentcore([], {})
    fake.delete_event.side_effect = ClientError({"Error": {"Code": NO_PARTITION}}, "DeleteEvent")

    with patch.object(
        memory_api, "record_memory_record_delete_outcome", new=AsyncMock()
    ) as counter:
        resp = _forget_call(fake, {"chat-a": _chat("chat-a")}, path=f"{FORGET_URL}/{_record_id()}")

    assert resp.status_code == 404
    counter.assert_not_awaited()


def test_a_record_delete_is_counted_and_audited_with_no_record_text():
    put_audit = MagicMock(return_value={})
    fake = _deleting_agentcore([], {})

    with patch.object(
        memory_api, "record_memory_record_delete_outcome", new=AsyncMock()
    ) as counter:
        resp = _forget_call(
            fake,
            {"chat-a": _chat("chat-a")},
            path=f"{FORGET_URL}/{_record_id('chat-a', 'e7')}",
            put_audit=put_audit,
        )

    assert resp.status_code == 204
    counter.assert_awaited_once_with(success=True)
    kwargs = put_audit.call_args.kwargs
    assert kwargs["event_type"] == "memory.record_deleted"
    assert kwargs["actor_id"] == OWNER
    assert kwargs["details"] == {
        "chat_id": "chat-a",
        "record_id": _record_id("chat-a", "e7"),
        "deleted": 1,
    }


def test_a_failed_audit_write_does_not_fail_a_completed_forget():
    """The opposite call from ``/export``'s: by the time this runs the
    record is gone, so failing would report a forget that did happen as one
    that did not — and a user who believes their data survived behaves
    differently from one who knows it is gone."""
    fake = _deleting_agentcore([], {})

    resp = _forget_call(
        fake,
        {"chat-a": _chat("chat-a")},
        path=f"{FORGET_URL}/{_record_id()}",
        put_audit=MagicMock(side_effect=RuntimeError("dynamo down")),
    )

    assert resp.status_code == 204


# ── filter exclusivity ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({}, id="no-filter"),
        pytest.param({"all": "false"}, id="all-false-is-no-filter"),
        pytest.param({"chat_id": "chat-a", "all": "true"}, id="chat-and-all"),
        pytest.param({"chat_id": "chat-a", "since": "2026-07-30T00:00:00Z"}, id="chat-and-since"),
        pytest.param({"since": "2026-07-30T00:00:00Z", "all": "true"}, id="since-and-all"),
    ],
)
def test_a_bulk_forget_needs_exactly_one_filter(params: dict[str, Any]):
    """A bare DELETE must never be a full wipe: the most destructive action
    this API offers is reachable only by naming it."""
    fake = _deleting_agentcore(["chat-a"], {})

    resp = _forget_call(fake, {"chat-a": _chat("chat-a")}, params=params)

    assert resp.status_code == 400
    fake.delete_event.assert_not_called()


@pytest.mark.parametrize("since", ["yesterday", "", "2026-13-45"])
def test_an_unparseable_since_is_a_400_rather_than_widening_to_everything(since: str):
    fake = _deleting_agentcore(["chat-a"], {})

    resp = _forget_call(fake, {"chat-a": _chat("chat-a")}, params={"since": since})

    assert resp.status_code == 400
    fake.delete_event.assert_not_called()


# ── bulk: chat_id ─────────────────────────────────────────────────────────


def test_forgetting_one_chat_deletes_every_event_in_that_session():
    fake = _deleting_agentcore(
        [], {"chat-a": [_event("e1", ("USER", "a")), _event("e2", ("ASSISTANT", "b"))]}
    )

    resp = _forget_call(fake, {"chat-a": _chat("chat-a")}, params={"chat_id": "chat-a"})

    assert resp.status_code == 200
    assert resp.json() == {"deleted": 2, "failed": 0, "complete": True, "withheld_record_count": 0}
    assert [c.kwargs["eventId"] for c in fake.delete_event.call_args_list] == ["e1", "e2"]


def test_forgetting_another_users_chat_is_a_404_and_deletes_nothing():
    fake = _deleting_agentcore([], {"chat-a": [_event("e1", ("USER", "a"))]})

    resp = _forget_call(
        fake,
        {"chat-a": _chat("chat-a", user_id=OWNER)},
        params={"chat_id": "chat-a"},
        headers=_headers(INTRUDER),
    )

    assert resp.status_code == 404
    fake.delete_event.assert_not_called()


def test_a_404_from_an_unowned_chat_is_not_counted_as_a_failed_forget():
    fake = _deleting_agentcore([], {})

    with patch.object(memory_api, "record_memory_bulk_forget_outcome", new=AsyncMock()) as counter:
        resp = _forget_call(
            fake,
            {"chat-a": _chat("chat-a", user_id=OWNER)},
            params={"chat_id": "chat-a"},
            headers=_headers(INTRUDER),
        )

    assert resp.status_code == 404
    counter.assert_not_awaited()


# ── bulk: since ───────────────────────────────────────────────────────────


def test_forgetting_since_a_timestamp_spares_older_records():
    old = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)
    new = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    fake = _deleting_agentcore(
        ["chat-a"],
        {
            "chat-a": [
                {**_event("old", ("USER", "a")), "eventTimestamp": old},
                {**_event("new", ("USER", "b")), "eventTimestamp": new},
            ]
        },
    )

    resp = _forget_call(fake, {"chat-a": _chat("chat-a")}, params={"since": "2026-07-30T00:00:00Z"})

    assert resp.json() == {"deleted": 1, "failed": 0, "complete": True, "withheld_record_count": 0}
    assert [c.kwargs["eventId"] for c in fake.delete_event.call_args_list] == ["new"]


# ── bulk: all ─────────────────────────────────────────────────────────────


def test_forgetting_everything_walks_every_owned_session():
    fake = _deleting_agentcore(
        ["chat-a", "chat-b"],
        {"chat-a": [_event("e1", ("USER", "a"))], "chat-b": [_event("e2", ("USER", "b"))]},
    )

    resp = _forget_call(
        fake, {"chat-a": _chat("chat-a"), "chat-b": _chat("chat-b")}, params={"all": "true"}
    )

    assert resp.json() == {"deleted": 2, "failed": 0, "complete": True, "withheld_record_count": 0}
    assert {c.kwargs["sessionId"] for c in fake.delete_event.call_args_list} == {"chat-a", "chat-b"}


def test_forget_everything_never_touches_a_session_the_caller_does_not_own():
    """The bulk half of the cross-user guard. A foreign session sitting in
    the actor partition — an orphan, or the #474 collision class — is
    skipped, not erased. Asserted as "``delete_event`` was never called for
    it", which is the only assertion an implementation that deleted first
    and filtered afterwards would fail."""
    fake = _deleting_agentcore(
        ["chat-mine", "chat-theirs"],
        {
            "chat-mine": [_event("e1", ("USER", "a"))],
            "chat-theirs": [_event("e2", ("USER", "secret"))],
        },
    )

    resp = _forget_call(
        fake,
        {"chat-mine": _chat("chat-mine"), "chat-theirs": _chat("chat-theirs", user_id=INTRUDER)},
        params={"all": "true"},
    )

    assert resp.json()["deleted"] == 1
    assert {c.kwargs["sessionId"] for c in fake.delete_event.call_args_list} == {"chat-mine"}


def test_forgetting_everything_for_a_user_with_no_memory_partition_is_a_no_op_success():
    """#527 on the most destructive path. The mock RAISES — a mock that
    returned an empty page would pass even against the broken version."""
    fake = _agentcore_raising(NO_PARTITION)

    resp = _forget_call(fake, {}, params={"all": "true"})

    assert resp.status_code == 200
    assert resp.json() == {"deleted": 0, "failed": 0, "complete": True, "withheld_record_count": 0}


@pytest.mark.parametrize("code", ["ThrottlingException", "AccessDeniedException"])
def test_a_bulk_forget_still_500s_when_agentcore_fails_for_any_other_reason(code: str):
    fake = _agentcore_raising(code)

    resp = _forget_call(fake, {}, params={"all": "true"}, client_obj=error_client)

    assert resp.status_code == 500


def test_a_bulk_forget_whose_listing_fails_is_counted_as_a_failure():
    fake = _agentcore_raising("ThrottlingException")

    with (
        patch.object(memory_api, "record_memory_bulk_forget_outcome", new=AsyncMock()) as counter,
        pytest.raises(ClientError),
    ):
        _forget_call(fake, {}, params={"all": "true"})

    counter.assert_awaited_once_with(success=False)


# ── bulk: partial failure ─────────────────────────────────────────────────


def test_one_refused_delete_does_not_abandon_the_batch():
    """Stopping early would forget LESS than the user asked for — the wrong
    direction to fail in."""
    fake = _deleting_agentcore(
        [],
        {"chat-a": [_event("e1", ("USER", "a")), _event("e2", ("USER", "b"))]},
        delete_side_effect=[
            ClientError({"Error": {"Code": "ThrottlingException"}}, "DeleteEvent"),
            {},
        ],
    )

    resp = _forget_call(fake, {"chat-a": _chat("chat-a")}, params={"chat_id": "chat-a"})

    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1, "failed": 1, "complete": True, "withheld_record_count": 0}


def test_a_partially_failed_batch_counts_as_a_failure_even_though_it_answers_200():
    # The counter answers "is forget working?", and a shape that read clean
    # while records survived would answer it wrongly.
    fake = _deleting_agentcore(
        [],
        {"chat-a": [_event("e1", ("USER", "a")), _event("e2", ("USER", "b"))]},
        delete_side_effect=[
            ClientError({"Error": {"Code": "ThrottlingException"}}, "DeleteEvent"),
            {},
        ],
    )

    with patch.object(memory_api, "record_memory_bulk_forget_outcome", new=AsyncMock()) as counter:
        _forget_call(fake, {"chat-a": _chat("chat-a")}, params={"chat_id": "chat-a"})

    counter.assert_awaited_once_with(success=False)


def test_a_batch_that_deleted_nothing_while_something_failed_is_a_500():
    """``{"deleted": 0}`` with a 200 reads as "there was nothing to forget",
    which during an outage is a claim about the user's own data this
    endpoint has no basis to make."""
    fake = _deleting_agentcore(
        [],
        {"chat-a": [_event("e1", ("USER", "a"))]},
        delete_side_effect=ClientError({"Error": {"Code": "ThrottlingException"}}, "DeleteEvent"),
    )

    resp = _forget_call(
        fake, {"chat-a": _chat("chat-a")}, params={"chat_id": "chat-a"}, client_obj=error_client
    )

    assert resp.status_code == 500


def test_an_already_absent_event_counts_as_neither_deleted_nor_failed():
    # Nothing was deleted and nothing broke — the end state the caller
    # wanted already holds.
    fake = _deleting_agentcore(
        [],
        {"chat-a": [_event("e1", ("USER", "a"))]},
        delete_side_effect=ClientError({"Error": {"Code": NO_PARTITION}}, "DeleteEvent"),
    )

    resp = _forget_call(fake, {"chat-a": _chat("chat-a")}, params={"chat_id": "chat-a"})

    assert resp.json() == {"deleted": 0, "failed": 0, "complete": True, "withheld_record_count": 0}


def test_a_bulk_forget_that_hit_the_page_ceiling_says_so_in_the_body(
    monkeypatch: pytest.MonkeyPatch,
):
    """Reported in the response, not only in a log line — a log line is not
    visible to the person who just asked for their data to be gone."""
    from channel.agents import memory_records

    monkeypatch.setattr(memory_records, "MAX_FORGET_EVENT_PAGES", 1)
    fake = MagicMock()
    fake.list_events.return_value = {
        "events": [_event("e1", ("USER", "a"))],
        "nextToken": "more",
    }

    resp = _forget_call(fake, {"chat-a": _chat("chat-a")}, params={"chat_id": "chat-a"})

    assert resp.json() == {"deleted": 1, "failed": 0, "complete": False, "withheld_record_count": 0}


def test_an_incomplete_bulk_forget_counts_as_a_failure(monkeypatch: pytest.MonkeyPatch):
    from channel.agents import memory_records

    monkeypatch.setattr(memory_records, "MAX_FORGET_EVENT_PAGES", 1)
    fake = MagicMock()
    fake.list_events.return_value = {"events": [_event("e1", ("USER", "a"))], "nextToken": "more"}

    with patch.object(memory_api, "record_memory_bulk_forget_outcome", new=AsyncMock()) as counter:
        _forget_call(fake, {"chat-a": _chat("chat-a")}, params={"chat_id": "chat-a"})

    counter.assert_awaited_once_with(success=False)


# ── bulk: audit ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("params", "event_type", "extra"),
    [
        ({"chat_id": "chat-a"}, "memory.bulk_forget", {"filter": "chat_id", "chat_id": "chat-a"}),
        (
            {"since": "2026-01-01T00:00:00Z"},
            "memory.bulk_forget",
            {"filter": "since", "since": "2026-01-01T00:00:00Z"},
        ),
        ({"all": "true"}, "memory.forget_all", {"filter": "all"}),
    ],
)
def test_every_bulk_forget_audits_counts_and_filters_only(
    params: dict[str, Any], event_type: str, extra: dict[str, Any]
):
    """Copying the text a user just asked to forget into a 365-day immutable
    audit partition would defeat the feature (epic #129 decision 3)."""
    put_audit = MagicMock(return_value={})
    secret = "the sauce is sage and brown butter"
    fake = _deleting_agentcore(["chat-a"], {"chat-a": [_event("e1", ("USER", secret))]})

    resp = _forget_call(fake, {"chat-a": _chat("chat-a")}, params=params, put_audit=put_audit)

    assert resp.status_code == 200
    kwargs = put_audit.call_args.kwargs
    assert kwargs["event_type"] == event_type
    assert kwargs["actor_id"] == OWNER
    assert kwargs["details"] == {
        **extra,
        "deleted": 1,
        "failed": 0,
        "complete": True,
        "withheld_record_count": 0,
    }
    assert secret not in json.dumps(kwargs["details"])


def test_a_bulk_forget_whose_audit_write_fails_still_reports_its_counts():
    fake = _deleting_agentcore([], {"chat-a": [_event("e1", ("USER", "a"))]})

    resp = _forget_call(
        fake,
        {"chat-a": _chat("chat-a")},
        params={"chat_id": "chat-a"},
        put_audit=MagicMock(side_effect=RuntimeError("dynamo down")),
    )

    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1


# ── withheld reporting, on every surface, in one unit (#552) ──────────────
#
# ``complete`` on ``/export``'s manifest and on the bulk forget answers only
# "did every walk finish". Neither reflected WITHHELD sessions, so an export
# could claim completeness while omitting records and a forget could report
# finished while something remained. Both now carry
# ``withheld_record_count`` alongside ``complete`` — the same name, unit and
# lower-bound semantics ``/records`` has reported since #475.
#
# Every test below constructs a genuinely WITHHELD session (a foreign or
# orphaned one in the caller's actor partition). A test that only drove the
# page-cap path would have passed before this change and proved nothing —
# which is exactly how the gap survived two PRs.

WITHHELD_EVENTS = {
    "mine": [_event("e1", ("USER", "my message"))],
    # Two payload entries → two records from ONE session, which is what
    # makes "records, not sessions" an assertion rather than a coincidence.
    "theirs": [_event("e2", ("USER", "their secret"), ("ASSISTANT", "their reply"))],
}
WITHHELD_CHATS = {"mine": _chat("mine"), "theirs": _chat("theirs", user_id=INTRUDER)}


def _records_withheld_count(**kw: Any) -> int:
    fake = _agentcore(["mine", "theirs"], WITHHELD_EVENTS)
    return _call(fake, WITHHELD_CHATS, **kw).json()["withheld_record_count"]


def _export_withheld_manifest(**kw: Any) -> dict[str, Any]:
    fake = _export_agentcore([(["mine", "theirs"], None)], WITHHELD_EVENTS)
    body = _export_call(
        fake, WITHHELD_CHATS, list_chats=MagicMock(return_value=([], None)), **kw
    ).json()
    return body["manifest"]


def _forget_withheld_body(**kw: Any) -> dict[str, Any]:
    fake = _deleting_agentcore(["mine", "theirs"], WITHHELD_EVENTS)
    return _forget_call(fake, WITHHELD_CHATS, params={"all": "true"}, **kw).json()


def test_export_manifest_reports_the_records_the_ownership_gate_withheld():
    manifest = _export_withheld_manifest()

    assert manifest["withheld_record_count"] == 2


def test_a_bulk_forget_reports_the_records_the_ownership_gate_declined_to_delete():
    body = _forget_withheld_body()

    assert body["withheld_record_count"] == 2


def test_all_three_surfaces_report_the_same_withheld_count_for_the_same_partition():
    """The reason #552 had to change both endpoints at once.

    One actor partition, one withheld session, three surfaces. If the field
    ever means "sessions" on one and "records" on another — or is absent
    from one — a caller comparing the settings panel against a download
    against a forget receipt gets three different answers about the same
    data. Asserting a shared literal is what makes that drift a test
    failure rather than a support ticket.
    """
    expected = 2

    assert _records_withheld_count() == expected
    assert _export_withheld_manifest()["withheld_record_count"] == expected
    assert _forget_withheld_body()["withheld_record_count"] == expected


def test_withholding_does_not_flip_complete_on_either_endpoint():
    """``complete`` keeps its narrow meaning — "no walk was truncated".

    The rejected alternative was widening it. Two conditions stay
    distinguishable because they call for different responses: a truncated
    walk is a retry, an unverifiable session is an investigation.
    """
    assert _export_withheld_manifest()["complete"] is True
    assert _forget_withheld_body()["complete"] is True


def test_neither_endpoint_names_what_it_withheld():
    """Count only, never identity. Naming an unverifiable session id would
    leak the existence of a row the ownership check could not confirm —
    the conservatism the withholding itself exists to preserve."""
    manifest = _export_withheld_manifest()
    body = _forget_withheld_body()

    for payload in (manifest, body):
        serialized = json.dumps(payload)
        assert "theirs" not in serialized
        assert "their secret" not in serialized
        assert "their reply" not in serialized


def test_a_forget_still_never_deletes_the_session_it_reports_as_withheld():
    """#552 changed REPORTING, not what gets withheld. The blast radius must
    be identical to before: the foreign session is counted and skipped, and
    ``delete_event`` is never called for it."""
    fake = _deleting_agentcore(["mine", "theirs"], WITHHELD_EVENTS)

    resp = _forget_call(fake, WITHHELD_CHATS, params={"all": "true"})

    assert resp.json() == {
        "deleted": 1,
        "failed": 0,
        "complete": True,
        "withheld_record_count": 2,
    }
    assert {c.kwargs["sessionId"] for c in fake.delete_event.call_args_list} == {"mine"}


def test_an_orphaned_session_is_reported_as_withheld_by_export_and_forget():
    """The condition most likely to be seen in production: a session whose
    chat row is gone, which a failed ``chats._wipe_agentcore_session``
    leaves behind. It fails the gate exactly like a foreign one."""
    events = {"orphan": [_event("e1", ("USER", "ghost"))]}

    export = _export_call(
        _export_agentcore([(["orphan"], None)], events),
        {},
        list_chats=MagicMock(return_value=([], None)),
    ).json()
    forget = _forget_call(
        _deleting_agentcore(["orphan"], events), {}, params={"all": "true"}
    ).json()

    assert export["manifest"]["withheld_record_count"] == 1
    assert forget["withheld_record_count"] == 1
    assert export["memory_records"] == []


def test_a_clean_partition_reports_zero_withheld_on_both_endpoints():
    events = {"mine": [_event("e1", ("USER", "hi"))]}
    owned = {"mine": _chat("mine")}

    export = _export_call(_export_agentcore([(["mine"], None)], events), owned).json()
    forget = _forget_call(
        _deleting_agentcore(["mine"], events), owned, params={"all": "true"}
    ).json()

    assert export["manifest"]["withheld_record_count"] == 0
    assert forget["withheld_record_count"] == 0


def test_a_chat_id_forget_reports_zero_withheld_because_nothing_can_be():
    """``_load_owned_chat`` has already passed on the only session in scope,
    so a 404 is the sole alternative — the field is structurally 0, not
    merely observed to be."""
    fake = _deleting_agentcore([], {"chat-a": [_event("e1", ("USER", "a"))]})

    body = _forget_call(fake, {"chat-a": _chat("chat-a")}, params={"chat_id": "chat-a"}).json()

    assert body["withheld_record_count"] == 0
    fake.list_sessions.assert_not_called()


def test_withholding_is_not_counted_as_a_failed_forget():
    """The counter answers "is forget working?", and the gate declining an
    unverifiable session IS forget working. The signals for that condition
    are the ``memory.forget_sessions_withheld`` warning and the response
    field — not the health metric."""
    fake = _deleting_agentcore(["mine", "theirs"], WITHHELD_EVENTS)

    with patch.object(memory_api, "record_memory_bulk_forget_outcome", new=AsyncMock()) as counter:
        _forget_call(fake, WITHHELD_CHATS, params={"all": "true"})

    counter.assert_awaited_once_with(success=True)


def test_a_bulk_forget_audits_the_withheld_count():
    """The compliance trail records what was declined as well as what was
    deleted — a count, never the withheld text (which is not even read into
    a record: ``count_session_records`` returns an int and nothing else)."""
    put_audit = MagicMock(return_value={})
    fake = _deleting_agentcore(["mine", "theirs"], WITHHELD_EVENTS)

    _forget_call(fake, WITHHELD_CHATS, params={"all": "true"}, put_audit=put_audit)

    details = put_audit.call_args.kwargs["details"]
    assert details["withheld_record_count"] == 2
    assert "their secret" not in json.dumps(details)


def test_forget_withholding_logs_the_partition_canary_with_both_units(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_logger = MagicMock()
    monkeypatch.setattr(memory_api, "logger", fake_logger)
    fake = _deleting_agentcore(["mine", "theirs"], WITHHELD_EVENTS)

    _forget_call(fake, WITHHELD_CHATS, params={"all": "true"})

    fmt, user_hash, sessions, records = fake_logger.warning.call_args.args
    # Three distinct event names, deliberately: "we declined to SHOW these",
    # "…to EXPORT these" and "…to DELETE these" are different operations,
    # and one shared name covering all three is what makes a canary useless.
    assert fmt.startswith("memory.forget_sessions_withheld")
    assert (sessions, records) == (1, 2)
    assert user_hash == fingerprint_id(OWNER)


def test_the_withheld_count_is_records_not_sessions():
    """Two withheld sessions holding three records between them read as 3.

    Distinguishes the shipped shape from the cheaper one both endpoints
    could have used — ``len(session_ids) - len(owned)``, which is what they
    already computed for their log lines and which would have read 2.
    """
    events = {
        "theirs-a": [_event("e1", ("USER", "s1"), ("ASSISTANT", "s2"))],
        "theirs-b": [_event("e2", ("USER", "s3"))],
    }
    foreign = {
        "theirs-a": _chat("theirs-a", user_id=INTRUDER),
        "theirs-b": _chat("theirs-b", user_id=INTRUDER),
    }

    export = _export_call(
        _export_agentcore([(["theirs-a", "theirs-b"], None)], events),
        foreign,
        list_chats=MagicMock(return_value=([], None)),
    ).json()
    forget = _forget_call(
        _deleting_agentcore(["theirs-a", "theirs-b"], events), foreign, params={"all": "true"}
    ).json()

    assert export["manifest"]["withheld_record_count"] == 3
    assert forget["withheld_record_count"] == 3


def test_a_withheld_count_costs_nothing_on_a_clean_partition():
    """The per-withheld-session ``ListEvents`` is only paid when the gate
    actually withholds something. ``/export`` declined to pay it at all
    before #552; the reason that trade changed is that the number is now
    part of a claim made to the user, not only an alarm — but it must still
    cost nothing in the normal case, where nothing is withheld."""
    fake = _export_agentcore([(["mine"], None)], {"mine": [_event("e1", ("USER", "hi"))]})

    _export_call(fake, {"mine": _chat("mine")})

    assert {c.kwargs["sessionId"] for c in fake.list_events.call_args_list} == {"mine"}


# ── #552 × #527: a caller with no memory partition ────────────────────────
#
# The regression this repo has shipped TWICE. An actor partition is created
# lazily by the first memory write, so a user who has never chatted has none
# and ``ListSessions`` raises ``ResourceNotFoundException``. The mocks below
# RAISE — a mock returning an empty page cannot tell "no actor" from "empty
# actor", which is precisely why unit tests missed it both times and only a
# smoke test against the deployed environment caught it.


def test_export_of_a_partitionless_user_reports_zero_withheld_not_an_error():
    resp = _export_call(_agentcore_raising(NO_PARTITION), {})

    assert resp.status_code == 200
    manifest = resp.json()["manifest"]
    # Nothing reaches the ownership gate, so nothing can be withheld — and
    # the walks genuinely did finish, so ``complete`` stays true.
    assert manifest["withheld_record_count"] == 0
    assert manifest["complete"] is True


def test_a_forget_for_a_partitionless_user_reports_zero_withheld_not_an_error():
    resp = _forget_call(_agentcore_raising(NO_PARTITION), {}, params={"all": "true"})

    assert resp.status_code == 200
    assert resp.json() == {
        "deleted": 0,
        "failed": 0,
        "complete": True,
        "withheld_record_count": 0,
    }


@pytest.mark.parametrize("code", ["ThrottlingException", "AccessDeniedException"])
def test_neither_endpoint_reports_zero_withheld_when_agentcore_merely_fails(code: str):
    """The pairing that keeps the absorption narrow. Only
    ``ResourceNotFoundException`` may read as "no partition"; a throttle or
    a denial must stay a visible 5xx, because ``withheld_record_count: 0``
    during an outage is a claim about the user's own data neither endpoint
    has any basis to make."""
    fake = _agentcore_raising(code)

    assert _export_call(fake, {}, client_obj=error_client).status_code == 500
    assert (
        _forget_call(fake, {}, params={"all": "true"}, client_obj=error_client).status_code == 500
    )


def test_a_withheld_session_whose_events_are_unreadable_counts_zero_not_a_500():
    """A session listed but whose ``ListEvents`` reports it absent — the
    orphan race, where a chat-delete wipe removes the events between the two
    calls. Zero is the honest answer: no records were withheld from this
    export, because there were none to withhold. The session count in the
    log line is what still records that the gate fired."""
    fake = _export_agentcore([(["orphan"], None)], {})
    fake.list_events.side_effect = ClientError(
        {"Error": {"Code": NO_PARTITION, "Message": "Session not found"}}, "ListEvents"
    )

    resp = _export_call(fake, {}, list_chats=MagicMock(return_value=([], None)))

    assert resp.status_code == 200
    assert resp.json()["manifest"]["withheld_record_count"] == 0
