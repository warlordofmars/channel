# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the dev-only /api/_debug/* router.

The router is conditionally mounted at module-import time based on
``STARTER_ENABLE_DEBUG_ENDPOINTS``. Tests that need to flip the mount
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
    monkeypatch.setenv("STARTER_ENABLE_DEBUG_ENDPOINTS", "1")
    monkeypatch.setenv("STARTER_ENV", "test")
    importlib.reload(main_module)
    main_module.app.dependency_overrides[require_mgmt_user] = _stub_user

    yield TestClient(main_module.app)

    main_module.app.dependency_overrides.clear()
    monkeypatch.delenv("STARTER_ENABLE_DEBUG_ENDPOINTS", raising=False)
    importlib.reload(main_module)


def test_get_memory_events_returns_scoped_results(mounted_app: TestClient):
    fake_client = MagicMock()
    fake_client.list_events.return_value = {
        "events": [{"eventId": "evt-1", "eventTimestamp": "2026-05-31T18:00:00Z"}],
    }

    with patch("channel.api._debug.boto3.client", return_value=fake_client), patch(
        "channel.api._debug.get_or_create_memory", return_value="m-1",
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

    with patch("channel.api._debug.boto3.client", return_value=fake_client), patch(
        "channel.api._debug.get_or_create_memory", return_value="m-1",
    ):
        resp = mounted_app.get(
            "/api/_debug/memory/events", params={"chat_id": "chat-empty"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"events": []}


def test_delete_memory_event_calls_agentcore_delete(mounted_app: TestClient):
    fake_client = MagicMock()

    with patch("channel.api._debug.boto3.client", return_value=fake_client), patch(
        "channel.api._debug.get_or_create_memory", return_value="m-1",
    ):
        resp = mounted_app.delete(
            "/api/_debug/memory/events/evt-1",
            params={"chat_id": "chat-xyz"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    fake_client.delete_event.assert_called_once_with(
        memoryId="m-1", actorId="u-abc", sessionId="chat-xyz", eventId="evt-1",
    )


def test_debug_router_not_mounted_when_env_flag_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("STARTER_ENABLE_DEBUG_ENDPOINTS", raising=False)
    importlib.reload(main_module)
    main_module.app.dependency_overrides[require_mgmt_user] = _stub_user

    try:
        client = TestClient(main_module.app)
        resp = client.get(
            "/api/_debug/memory/events", params={"chat_id": "c"},
        )
        assert resp.status_code == 404, "debug routes must not exist when flag unset"
    finally:
        main_module.app.dependency_overrides.clear()


def test_debug_endpoint_requires_mgmt_jwt(monkeypatch: pytest.MonkeyPatch):
    """No dependency override → 403/401 from HTTPBearer enforcement."""
    monkeypatch.setenv("STARTER_ENABLE_DEBUG_ENDPOINTS", "1")
    monkeypatch.setenv("STARTER_ENV", "test")
    importlib.reload(main_module)

    try:
        client = TestClient(main_module.app)
        resp = client.get("/api/_debug/memory/events", params={"chat_id": "c"})
        # FastAPI's HTTPBearer returns 403 when no Authorization header is sent.
        assert resp.status_code in (401, 403)
    finally:
        monkeypatch.delenv("STARTER_ENABLE_DEBUG_ENDPOINTS", raising=False)
        importlib.reload(main_module)
