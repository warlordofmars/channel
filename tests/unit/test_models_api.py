# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api/models router."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from channel.api._auth import require_mgmt_user
from channel.api.main import app


@pytest.fixture
def client() -> TestClient:
    def _stub_user() -> dict[str, Any]:
        return {"sub": "u-1", "role": "user"}

    app.dependency_overrides[require_mgmt_user] = _stub_user
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_models_returns_allowlist(client: TestClient) -> None:
    response = client.get("/api/models")
    assert response.status_code == 200
    body = response.json()
    assert "models" in body
    ids = {m["id"] for m in body["models"]}
    assert ids == {"claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-6"}
    sonnet = next(m for m in body["models"] if m["id"] == "claude-sonnet-4-6")
    assert sonnet["family"] == "anthropic"
    assert sonnet["supports_streaming"] is True


def test_list_models_requires_auth(client: TestClient) -> None:
    # Remove the override and retry — no Authorization header should yield 401/403.
    app.dependency_overrides.clear()
    response = client.get("/api/models")
    assert response.status_code in (401, 403)
