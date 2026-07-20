# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the dev-only /api/_debug/* router.

The router is conditionally mounted at module-import time based on
``CHANNEL_ENABLE_DEBUG_ENDPOINTS``. Tests that need to flip the mount
state call ``importlib.reload(channel.api.main)`` to re-evaluate the
mount check, then clean up by reloading again with the env var back
to its original state.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from channel.api import main as main_module
from channel.api._auth import require_mgmt_user


def _stub_user() -> dict[str, Any]:
    return {"sub": "u-abc", "role": "user"}


@pytest.fixture
def mounted_app(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App with the debug router mounted (env flag set)."""
    monkeypatch.setenv("CHANNEL_ENABLE_DEBUG_ENDPOINTS", "1")
    monkeypatch.setenv("CHANNEL_ENV", "test")
    importlib.reload(main_module)
    main_module.app.dependency_overrides[require_mgmt_user] = _stub_user

    yield TestClient(main_module.app)

    main_module.app.dependency_overrides.clear()
    monkeypatch.delenv("CHANNEL_ENABLE_DEBUG_ENDPOINTS", raising=False)
    importlib.reload(main_module)


def test_get_memory_events_returns_scoped_results(mounted_app: TestClient):
    fake_client = MagicMock()
    fake_client.list_events.return_value = {
        "events": [{"eventId": "evt-1", "eventTimestamp": "2026-05-31T18:00:00Z"}],
    }

    with (
        patch("channel.api._debug.boto3.client", return_value=fake_client),
        patch(
            "channel.api._debug.get_or_create_memory",
            return_value="m-1",
        ),
    ):
        resp = mounted_app.get(
            "/api/_debug/memory/events",
            params={"chat_id": "chat-xyz", "limit": 5},
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "events": [{"eventId": "evt-1", "eventTimestamp": "2026-05-31T18:00:00Z"}],
    }
    fake_client.list_events.assert_called_once_with(
        memoryId="m-1",
        actorId="u-abc",
        sessionId="chat-xyz",
        maxResults=5,
    )


def test_get_memory_events_returns_empty_when_no_events(mounted_app: TestClient):
    fake_client = MagicMock()
    fake_client.list_events.return_value = {}

    with (
        patch("channel.api._debug.boto3.client", return_value=fake_client),
        patch(
            "channel.api._debug.get_or_create_memory",
            return_value="m-1",
        ),
    ):
        resp = mounted_app.get(
            "/api/_debug/memory/events",
            params={"chat_id": "chat-empty"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"events": []}


def test_delete_memory_event_calls_agentcore_delete(mounted_app: TestClient):
    fake_client = MagicMock()

    with (
        patch("channel.api._debug.boto3.client", return_value=fake_client),
        patch(
            "channel.api._debug.get_or_create_memory",
            return_value="m-1",
        ),
    ):
        resp = mounted_app.delete(
            "/api/_debug/memory/events",
            params={"chat_id": "chat-xyz", "event_id": "evt-1"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    fake_client.delete_event.assert_called_once_with(
        memoryId="m-1",
        actorId="u-abc",
        sessionId="chat-xyz",
        eventId="evt-1",
    )


def test_debug_router_not_mounted_when_env_flag_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CHANNEL_ENABLE_DEBUG_ENDPOINTS", raising=False)
    importlib.reload(main_module)
    main_module.app.dependency_overrides[require_mgmt_user] = _stub_user

    try:
        client = TestClient(main_module.app)
        resp = client.get(
            "/api/_debug/memory/events",
            params={"chat_id": "c"},
        )
        assert resp.status_code == 404, "debug routes must not exist when flag unset"
    finally:
        main_module.app.dependency_overrides.clear()


def test_debug_endpoint_requires_mgmt_jwt(monkeypatch: pytest.MonkeyPatch):
    """No dependency override → 403/401 from HTTPBearer enforcement."""
    monkeypatch.setenv("CHANNEL_ENABLE_DEBUG_ENDPOINTS", "1")
    monkeypatch.setenv("CHANNEL_ENV", "test")
    importlib.reload(main_module)

    try:
        client = TestClient(main_module.app)
        resp = client.get("/api/_debug/memory/events", params={"chat_id": "c"})
        # FastAPI's HTTPBearer returns 403 when no Authorization header is sent.
        assert resp.status_code in (401, 403)
    finally:
        monkeypatch.delenv("CHANNEL_ENABLE_DEBUG_ENDPOINTS", raising=False)
        importlib.reload(main_module)


# ---------------------------------------------------------------------------
# GET /api/_debug/recall/inspect — recall inspection instrument (#227)
# ---------------------------------------------------------------------------


def _recall_fake_client() -> MagicMock:
    """A fake bedrock-agentcore client with one prior session (``prior-1``)
    alongside the current chat, so the excluded-current-chat path is
    exercised by the happy-path tests."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [
            {"sessionId": "current-chat", "createdAt": "2026-06-07T20:00:00Z"},
            {"sessionId": "prior-1", "createdAt": "2026-06-01T10:00:00Z"},
        ],
    }
    fake_client.list_events.return_value = {
        "events": [
            {
                "sessionId": "prior-1",
                "payload": [
                    {"conversational": {"role": "USER", "content": {"text": "i love sage green"}}},
                    {"conversational": {"role": "ASSISTANT", "content": {"text": "sage is great"}}},
                ],
            },
        ],
    }
    return fake_client


def test_inspect_recall_returns_block_provenance_and_token_count(mounted_app: TestClient):
    fake_client = _recall_fake_client()

    with (
        patch("channel.api._debug.boto3.client", return_value=fake_client),
        patch("channel.api._debug.get_or_create_memory", return_value="m-1"),
    ):
        resp = mounted_app.get(
            "/api/_debug/recall/inspect",
            params={"chat_id": "current-chat"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["chat_id"] == "current-chat"
    assert body["actor_id"] == "u-abc"
    assert body["recall_enabled"] is True
    # Block reuses the hook's real formatting (heading + turn bullets).
    assert "## What we've talked about before" in body["block"]
    assert "- You: i love sage green" in body["block"]
    # Per-fragment provenance: source chat id + date + turn count.
    assert body["fragments"] == [
        {"session_id": "prior-1", "created_at": "2026-06-01", "turn_count": 2},
    ]
    assert body["session_count"] == 1
    assert body["char_count"] == len(body["block"])
    assert body["token_estimate"] == (len(body["block"]) + 3) // 4
    # Current chat is excluded from the ListEvents fan-out.
    fake_client.list_events.assert_called_once_with(
        memoryId="m-1",
        actorId="u-abc",
        sessionId="prior-1",
        maxResults=2,
    )


def test_inspect_recall_empty_when_no_prior_sessions(mounted_app: TestClient):
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [{"sessionId": "current-chat", "createdAt": "2026-06-07T20:00:00Z"}],
    }

    with (
        patch("channel.api._debug.boto3.client", return_value=fake_client),
        patch("channel.api._debug.get_or_create_memory", return_value="m-1"),
    ):
        resp = mounted_app.get(
            "/api/_debug/recall/inspect",
            params={"chat_id": "current-chat"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["block"] == ""
    assert body["fragments"] == []
    assert body["session_count"] == 0
    assert body["char_count"] == 0
    assert body["token_estimate"] == 0
    fake_client.list_events.assert_not_called()


def test_inspect_recall_reports_kill_switch_but_still_builds_block(
    mounted_app: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """With ``CHANNEL_RECALL_ENABLED=0`` a live turn injects nothing, but
    the inspector still computes the block (so it stays usable during an
    A/B run) and reports the flag so the investigator knows the live state."""
    monkeypatch.setenv("CHANNEL_RECALL_ENABLED", "0")
    fake_client = _recall_fake_client()

    with (
        patch("channel.api._debug.boto3.client", return_value=fake_client),
        patch("channel.api._debug.get_or_create_memory", return_value="m-1"),
    ):
        resp = mounted_app.get(
            "/api/_debug/recall/inspect",
            params={"chat_id": "current-chat"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["recall_enabled"] is False
    assert "- You: i love sage green" in body["block"]


def test_inspect_recall_not_mounted_when_env_flag_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CHANNEL_ENABLE_DEBUG_ENDPOINTS", raising=False)
    importlib.reload(main_module)
    main_module.app.dependency_overrides[require_mgmt_user] = _stub_user

    try:
        client = TestClient(main_module.app)
        resp = client.get("/api/_debug/recall/inspect", params={"chat_id": "c"})
        assert resp.status_code == 404, "recall inspect route must not exist when flag unset"
    finally:
        main_module.app.dependency_overrides.clear()


def test_inspect_recall_requires_mgmt_jwt(monkeypatch: pytest.MonkeyPatch):
    """No dependency override → 403/401 from HTTPBearer enforcement."""
    monkeypatch.setenv("CHANNEL_ENABLE_DEBUG_ENDPOINTS", "1")
    monkeypatch.setenv("CHANNEL_ENV", "test")
    importlib.reload(main_module)

    try:
        client = TestClient(main_module.app)
        resp = client.get("/api/_debug/recall/inspect", params={"chat_id": "c"})
        assert resp.status_code in (401, 403)
    finally:
        monkeypatch.delenv("CHANNEL_ENABLE_DEBUG_ENDPOINTS", raising=False)
        importlib.reload(main_module)


@pytest.mark.parametrize(
    "text,expected",
    [("", 0), ("a", 1), ("abcd", 1), ("abcde", 2), ("a" * 120, 30)],
)
def test_estimate_tokens_ceil_division(text: str, expected: int):
    from channel.api._debug import _estimate_tokens

    assert _estimate_tokens(text) == expected
