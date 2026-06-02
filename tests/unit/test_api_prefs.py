# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api/me/prefs router."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from channel.api._auth import require_mgmt_user
from channel.api.main import app
from channel.models import Prefs


@pytest.fixture
def client() -> TestClient:
    def _stub_user() -> dict[str, Any]:
        return {"sub": "u-1", "role": "user"}

    app.dependency_overrides[require_mgmt_user] = _stub_user
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, Prefs]:
    """In-memory prefs store keyed by user_id, patched into channel.storage."""

    state: dict[str, Prefs] = {}

    def _get(user_id: str) -> Prefs:
        return state.get(user_id, Prefs())

    def _put(user_id: str, updates: dict[str, Any]) -> Prefs:
        current = state.get(user_id, Prefs()).model_dump()
        current.update(updates)
        merged = Prefs(**current)  # raises ValidationError on bad keys
        state[user_id] = merged
        return merged

    monkeypatch.setattr("channel.api.prefs.storage.get_prefs", _get)
    monkeypatch.setattr("channel.api.prefs.storage.put_prefs", _put)
    return state


def test_get_my_prefs_returns_defaults(client: TestClient, store: dict[str, Prefs]) -> None:
    r = client.get("/api/me/prefs", headers={"Authorization": "Bearer x"})
    assert r.status_code == 200
    body = r.json()
    assert body["prefs"]["theme"] == "dark"
    assert body["prefs"]["send_on_enter"] is True
    assert body["prefs"]["suggest_followups"] is True
    assert body["prefs"]["show_reasoning"] is False


def test_put_my_prefs_partial_update(client: TestClient, store: dict[str, Prefs]) -> None:
    r = client.put(
        "/api/me/prefs",
        json={"prefs": {"theme": "light"}},
        headers={"Authorization": "Bearer x"},
    )
    assert r.status_code == 204
    r2 = client.get("/api/me/prefs", headers={"Authorization": "Bearer x"})
    assert r2.json()["prefs"]["theme"] == "light"
    # Unchanged fields keep defaults.
    assert r2.json()["prefs"]["accent"] == "42"


def test_put_my_prefs_rejects_unknown_key(client: TestClient, store: dict[str, Prefs]) -> None:
    r = client.put(
        "/api/me/prefs",
        json={"prefs": {"made_up": "x"}},
        headers={"Authorization": "Bearer x"},
    )
    assert r.status_code == 422


def test_get_my_prefs_requires_auth(store: dict[str, Prefs]) -> None:
    # No dependency override — request is unauthenticated.
    client = TestClient(app)
    r = client.get("/api/me/prefs")
    # HTTPBearer returns 403 (not 401) when no Authorization header is
    # supplied; either is "rejected for missing auth" so accept both.
    assert r.status_code in (401, 403)
