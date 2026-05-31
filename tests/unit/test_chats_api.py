# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api/chats router.

Tests use FastAPI's ``TestClient`` and patch ``channel.storage`` at the
module boundary.  ``require_mgmt_user`` is overridden to return a stub
claims dict.
"""

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


def test_chats_router_is_mounted(client: TestClient) -> None:
    response = client.get("/api/chats")
    # 200 (empty list) or 500 (storage not stubbed yet) — but NOT 404.
    assert response.status_code != 404


def test_post_creates_chat_and_returns_metadata(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat

    created: list[Chat] = []

    def fake_create_chat(*, user_id: str, title: str | None, model_default: str) -> Chat:
        chat = Chat(
            chat_id="chat-1",
            user_id=user_id,
            title=title or "New chat",
            created_at="2026-05-30T00:00:00Z",
            last_message_at="2026-05-30T00:00:00Z",
            last_user_preview="",
            model_default=model_default,
            message_count=0,
            archived=False,
        )
        created.append(chat)
        return chat

    monkeypatch.setattr("channel.api.chats.storage.create_chat", fake_create_chat)

    response = client.post("/api/chats", json={"model_default": "canned-stream-v1"})

    assert response.status_code == 201
    body = response.json()
    assert body["chat_id"] == "chat-1"
    assert body["title"] == "New chat"
    assert created[0].user_id == "u-1"


def test_post_chat_uses_default_model_when_unspecified(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_create_chat(**kwargs: Any):
        from channel.models import Chat

        captured.update(kwargs)
        return Chat(
            chat_id="c",
            user_id=kwargs["user_id"],
            title=kwargs["title"] or "New chat",
            created_at="t",
            last_message_at="t",
            model_default=kwargs["model_default"],
        )

    monkeypatch.setattr("channel.api.chats.storage.create_chat", fake_create_chat)
    client.post("/api/chats", json={})
    assert captured["model_default"] == "canned-stream-v1"
