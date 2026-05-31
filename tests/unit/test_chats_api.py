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
from channel.models import Chat, Message, MessageRole


@pytest.fixture
def client() -> TestClient:
    def _stub_user() -> dict[str, Any]:
        return {"sub": "u-1", "role": "user"}

    app.dependency_overrides[require_mgmt_user] = _stub_user
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_chats_router_is_mounted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "channel.api.chats.storage.list_chats_for_user",
        lambda *_a, **_kw: ([], None),
    )
    response = client.get("/api/chats")
    assert response.status_code != 404


def test_post_creates_chat_and_returns_metadata(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    def fake_create_chat(**kwargs: Any) -> Chat:
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


def test_list_returns_chats_for_authenticated_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_list(user_id: str, *, limit: int, cursor: str | None):
        assert user_id == "u-1"
        assert limit == 50
        return (
            [
                Chat(
                    chat_id="c1",
                    user_id=user_id,
                    title="Hi",
                    created_at="2026-05-30T00:00:00Z",
                    last_message_at="2026-05-30T00:00:00Z",
                    model_default="canned-stream-v1",
                )
            ],
            None,
        )

    monkeypatch.setattr("channel.api.chats.storage.list_chats_for_user", fake_list)

    response = client.get("/api/chats")
    body = response.json()
    assert response.status_code == 200
    assert len(body["items"]) == 1
    assert body["items"][0]["chat_id"] == "c1"
    assert body["next_cursor"] is None


def test_list_honors_limit_and_cursor(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_list(user_id: str, *, limit: int, cursor: str | None):
        captured.update({"limit": limit, "cursor": cursor})
        return ([], "next-cursor-token")

    monkeypatch.setattr("channel.api.chats.storage.list_chats_for_user", fake_list)
    response = client.get("/api/chats?limit=5&cursor=abc")
    assert response.status_code == 200
    assert captured == {"limit": 5, "cursor": "abc"}
    assert response.json()["next_cursor"] == "next-cursor-token"


def test_get_chat_returns_chat_and_messages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="2026-05-30T00:00:00Z",
        last_message_at="2026-05-30T00:00:00Z",
        model_default="canned-stream-v1",
    )

    def fake_get(chat_id: str) -> Chat | None:
        return chat if chat_id == "c1" else None

    def fake_msgs(chat_id: str, *, limit: int, cursor: str | None):
        return (
            [
                Message(
                    chat_id="c1",
                    msg_id="m1",
                    role=MessageRole.USER,
                    text="hi",
                    created_at="2026-05-30T00:00:00Z",
                )
            ],
            None,
        )

    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", fake_get)
    monkeypatch.setattr("channel.api.chats.storage.list_messages", fake_msgs)

    response = client.get("/api/chats/c1")
    body = response.json()
    assert response.status_code == 200
    assert body["chat"]["chat_id"] == "c1"
    assert body["messages"][0]["text"] == "hi"
    assert body["next_cursor"] is None


def test_get_chat_returns_404_for_unknown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: None)
    response = client.get("/api/chats/does-not-exist")
    assert response.status_code == 404


def test_get_chat_returns_404_when_owner_mismatches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = Chat(
        chat_id="c1",
        user_id="u-other",
        title="t",
        created_at="2026-05-30T00:00:00Z",
        last_message_at="2026-05-30T00:00:00Z",
        model_default="m",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: other)
    response = client.get("/api/chats/c1")
    assert response.status_code == 404
