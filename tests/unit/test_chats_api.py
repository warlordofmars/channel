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
from channel.models import Chat, Message, MessageRole, Prefs


@pytest.fixture(autouse=True)
def _stub_get_prefs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default ``storage.get_prefs`` to canonical defaults + neutralise
    the follow-ups Haiku call.

    ``_stream_bedrock_reply`` reads prefs once at stream start and (when
    ``suggest_followups`` is on) invokes a Haiku Agent after the
    assistant turn lands (Phase 113). Without these stubs the
    production code would hit boto3 / Bedrock; tests that care about
    follow-up behaviour override these inline.
    """
    monkeypatch.setattr("channel.api.chats.storage.get_prefs", lambda _user_id: Prefs())

    async def _empty_stream(self, prompt: str):  # noqa: ANN001, ANN201
        if False:  # pragma: no cover
            yield None

    class _NoopFollowupsAgent:
        stream_async = _empty_stream

    monkeypatch.setattr("channel.api.chats.build_followups_agent", lambda: _NoopFollowupsAgent())

    # Default the attachments cascade (#174) to a no-op so the existing
    # delete_chat tests that don't care about the cascade keep passing.
    # Tests that exercise cascade behaviour override this inline.
    monkeypatch.setattr(
        "channel.api.chats.storage.delete_chat_attachments",
        lambda **_kwargs: (0, 0),
    )


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
    assert captured["model_default"] == "claude-sonnet-4-6"


def test_list_returns_chats_for_authenticated_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_list(user_id: str, *, limit: int, cursor: str | None, include_archived: bool = False):
        assert user_id == "u-1"
        assert limit == 50
        assert include_archived is False
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

    def fake_list(user_id: str, *, limit: int, cursor: str | None, include_archived: bool = False):
        captured.update({"limit": limit, "cursor": cursor, "include_archived": include_archived})
        return ([], "next-cursor-token")

    monkeypatch.setattr("channel.api.chats.storage.list_chats_for_user", fake_list)
    response = client.get("/api/chats?limit=5&cursor=abc")
    assert response.status_code == 200
    assert captured == {"limit": 5, "cursor": "abc", "include_archived": False}
    assert response.json()["next_cursor"] == "next-cursor-token"


def test_list_defaults_to_excluding_archived(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No query param → ``include_archived=False`` reaches storage."""

    captured: dict[str, Any] = {}

    def fake_list(user_id: str, *, limit: int, cursor: str | None, include_archived: bool = False):
        captured["include_archived"] = include_archived
        return ([], None)

    monkeypatch.setattr("channel.api.chats.storage.list_chats_for_user", fake_list)
    response = client.get("/api/chats")
    assert response.status_code == 200
    assert captured == {"include_archived": False}


def test_list_passes_include_archived_query_param(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``?include_archived=1`` forwards the flag to storage."""

    captured: dict[str, Any] = {}

    def fake_list(user_id: str, *, limit: int, cursor: str | None, include_archived: bool = False):
        captured["include_archived"] = include_archived
        return ([], None)

    monkeypatch.setattr("channel.api.chats.storage.list_chats_for_user", fake_list)
    response = client.get("/api/chats?include_archived=1")
    assert response.status_code == 200
    assert captured == {"include_archived": True}


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


def test_patch_renames_chat(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="Old",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    captured: dict[str, Any] = {}

    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)

    def fake_patch(*, user_id: str, chat: Chat, title, archived) -> None:
        captured.update({"title": title, "archived": archived})

    monkeypatch.setattr("channel.api.chats.storage.patch_chat", fake_patch)

    response = client.patch("/api/chats/c1", json={"title": "New"})
    assert response.status_code == 204
    assert captured == {"title": "New", "archived": None}


def test_patch_archives_chat(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    captured: dict[str, Any] = {}

    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.patch_chat",
        lambda **kwargs: captured.update(kwargs),
    )

    response = client.patch("/api/chats/c1", json={"archived": True})
    assert response.status_code == 204
    assert captured["archived"] is True


def test_patch_returns_404_for_unowned_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: None)
    response = client.patch("/api/chats/x", json={"title": "y"})
    assert response.status_code == 404


def test_post_message_returns_sse_via_strands(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)

    persisted: list[Message] = []

    def fake_put(**kwargs: Any) -> Message:
        msg = Message(
            chat_id=kwargs["chat_id"],
            msg_id=f"m-{len(persisted)}",
            role=kwargs["role"],
            text=kwargs["text"],
            model=kwargs.get("model"),
            input_tokens=kwargs.get("input_tokens"),
            output_tokens=kwargs.get("output_tokens"),
            created_at="t",
        )
        persisted.append(msg)
        return msg

    monkeypatch.setattr("channel.api.chats.storage.put_message", fake_put)
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)
    # _stream_bedrock_reply now loads prior history to seed the agent —
    # stub it to "no prior context".
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages",
        lambda *_a, **_kw: ([], None),
    )

    # Fake Strands Agent that yields a deterministic event sequence.
    async def fake_stream(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Hello"}}}}
        yield {"event": {"contentBlockDelta": {"delta": {"text": " world"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}
        yield {
            "event": {
                "metadata": {
                    "usage": {"inputTokens": 12, "outputTokens": 5},
                }
            }
        }

    class FakeAgent:
        stream_async = fake_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    body = response.text

    assert "user_persisted" in body
    assert '"type": "delta"' in body
    assert "Hello" in body and "world" in body
    assert '"type": "done"' in body
    assert '"stop_reason": "end_turn"' in body
    assert '"input_tokens": 12' in body
    assert '"output_tokens": 5' in body

    assert persisted[1].text == "Hello world"
    assert persisted[1].input_tokens == 12
    assert persisted[1].output_tokens == 5

    assert [m.role for m in persisted] == [MessageRole.USER, MessageRole.ASSISTANT]


def _stub_storage_for_one_turn(
    monkeypatch: pytest.MonkeyPatch, *, prefs_effort: str = "Medium"
) -> Chat:
    """Minimal storage stubs for a single-turn streaming test."""

    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
        # Non-zero message_count keeps the auto-titler block from
        # firing in these tests (it gates on the first round-trip).
        # Follow-ups are gated by ``prefs.suggest_followups`` + env, not
        # by message_count — we don't disable them explicitly here
        # because the FakeAgent's stream is short enough that the
        # follow-ups call would simply be best-effort and harmless.
        message_count=2,
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id=kwargs["chat_id"],
            msg_id="m",
            role=kwargs["role"],
            text=kwargs["text"],
            model=kwargs.get("model"),
            created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages",
        lambda *_a, **_kw: ([], None),
    )
    monkeypatch.setattr(
        "channel.api.chats.storage.get_prefs",
        lambda _user: Prefs(effort=prefs_effort),
    )
    return chat


def _fake_streaming_agent_factory(captured: dict[str, Any]):
    """Return a build_agent stub that records its kwargs and yields a tiny stream."""

    async def fake_stream(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "hi"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}
        yield {"event": {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1}}}}

    class FakeAgent:
        stream_async = fake_stream

    def factory(**kwargs):
        captured["build_agent_kwargs"] = kwargs
        return FakeAgent()

    return factory


def test_post_message_forwards_payload_effort_to_build_agent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``effort`` field on the request body reaches ``build_agent``."""

    _stub_storage_for_one_turn(monkeypatch, prefs_effort="Medium")

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "channel.api.chats.build_agent",
        _fake_streaming_agent_factory(captured),
    )

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "Low"},
    )
    assert response.status_code == 200
    assert captured["build_agent_kwargs"]["effort"] == "Low"


def test_post_message_falls_back_to_prefs_effort_when_payload_omits_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the request has no ``effort``, the saved pref is used."""

    _stub_storage_for_one_turn(monkeypatch, prefs_effort="High")

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "channel.api.chats.build_agent",
        _fake_streaming_agent_factory(captured),
    )

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6"},
    )
    assert response.status_code == 200
    assert captured["build_agent_kwargs"]["effort"] == "High"


def test_regenerate_forwards_payload_effort_to_build_agent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regenerate also plumbs effort through (RegenerateRequest accepts it)."""

    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
        message_count=4,
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.delete_last_assistant_message",
        lambda _cid: None,
    )
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id=kwargs["chat_id"],
            msg_id="m",
            role=kwargs["role"],
            text=kwargs["text"],
            model=kwargs.get("model"),
            created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)
    user_msg = Message(
        chat_id="c1",
        msg_id="u",
        role=MessageRole.USER,
        text="prior question",
        model=None,
        created_at="t",
    )
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages",
        lambda *_a, **_kw: ([user_msg], None),
    )
    monkeypatch.setattr(
        "channel.api.chats.storage.get_prefs",
        lambda _user: Prefs(effort="Medium"),
    )

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "channel.api.chats.build_agent",
        _fake_streaming_agent_factory(captured),
    )

    response = client.post(
        "/api/chats/c1/regenerate",
        json={"model": "claude-sonnet-4-6", "effort": "High"},
    )
    assert response.status_code == 200
    assert captured["build_agent_kwargs"]["effort"] == "High"


# ---------------------------------------------------------------------------
# Phase 7d auto-title
# ---------------------------------------------------------------------------


def _stub_first_round_trip_chat(monkeypatch: pytest.MonkeyPatch) -> Chat:
    """Common shape: fresh chat (message_count=0), all storage mocked."""
    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="New chat",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
        message_count=0,
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id=kwargs["chat_id"],
            msg_id="m",
            role=kwargs["role"],
            text=kwargs["text"],
            model=kwargs.get("model"),
            created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages",
        lambda *_a, **_kw: ([], None),
    )
    return chat


def test_post_message_emits_title_suggested_on_first_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First round-trip (chat.message_count == 0 at entry) must emit a
    title_suggested event AFTER done and BEFORE stream close."""
    _stub_first_round_trip_chat(monkeypatch)

    patched_titles: list[str] = []
    monkeypatch.setattr(
        "channel.api.chats.storage.patch_chat",
        lambda **kwargs: patched_titles.append(kwargs["title"]),
    )

    async def fake_main_stream(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Hi"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_main_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    async def fake_titler_stream(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Quick chat title"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeTitler:
        stream_async = fake_titler_stream

    monkeypatch.setattr("channel.api.chats.build_titler_agent", lambda: FakeTitler())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    body = response.text

    assert '"type": "title_suggested"' in body
    assert '"title": "Quick chat title"' in body
    # Done event MUST appear before title_suggested.
    assert body.find('"type": "title_suggested"') > body.find('"type": "done"')
    assert patched_titles == ["Quick chat title"]


def test_post_message_skips_titler_on_non_first_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chat with message_count > 0 must NOT emit title_suggested."""
    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="Existing title",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
        message_count=4,
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id=kwargs["chat_id"],
            msg_id="m",
            role=kwargs["role"],
            text=kwargs["text"],
            created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages",
        lambda *_a, **_kw: ([], None),
    )

    titler_built: list[bool] = []
    monkeypatch.setattr(
        "channel.api.chats.build_titler_agent",
        lambda: titler_built.append(True) or object(),
    )

    async def fake_stream(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    assert '"type": "title_suggested"' not in response.text
    assert titler_built == []


def test_post_message_titler_failure_is_swallowed_and_stream_completes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Titler error MUST NOT break the user's stream. Done event still
    lands; title_suggested is omitted; chat keeps its default title."""
    _stub_first_round_trip_chat(monkeypatch)

    patched: list[Any] = []
    monkeypatch.setattr(
        "channel.api.chats.storage.patch_chat",
        lambda **kwargs: patched.append(kwargs),
    )

    async def fake_stream(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    class FakeTitler:
        async def stream_async(self, prompt):
            # Yield in an unreachable branch keeps this an async generator
            # while still raising. ``if False: yield`` makes Sonar S1763
            # happy (no syntactically-unreachable statement after a raise).
            if False:  # pragma: no cover
                yield None
            raise RuntimeError("haiku unavailable")

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())
    monkeypatch.setattr("channel.api.chats.build_titler_agent", lambda: FakeTitler())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    body = response.text
    assert '"type": "done"' in body
    assert '"type": "title_suggested"' not in body
    assert patched == []


def test_post_message_respects_auto_title_kill_switch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STARTER_AUTO_TITLE_ENABLED", "0")
    _stub_first_round_trip_chat(monkeypatch)

    titler_built: list[bool] = []
    monkeypatch.setattr(
        "channel.api.chats.build_titler_agent",
        lambda: titler_built.append(True) or object(),
    )

    async def fake_stream(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    assert '"type": "title_suggested"' not in response.text
    assert titler_built == []


def test_post_message_titler_empty_response_swallows_without_patch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Titler returns empty text → record failure, don't patch chat."""
    _stub_first_round_trip_chat(monkeypatch)

    patched: list[Any] = []
    monkeypatch.setattr(
        "channel.api.chats.storage.patch_chat",
        lambda **kwargs: patched.append(kwargs),
    )

    async def fake_stream(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    async def empty_titler(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeTitler:
        stream_async = empty_titler

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())
    monkeypatch.setattr("channel.api.chats.build_titler_agent", lambda: FakeTitler())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    assert '"type": "title_suggested"' not in response.text
    assert patched == []


# ---- Follow-up suggestions (Phase 113) -------------------------------------


def _stub_existing_chat(monkeypatch: pytest.MonkeyPatch) -> Chat:
    """A chat with prior turns — exercises the follow-up block without
    also exercising the (first-round-trip-only) auto-titler block."""
    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="Existing chat",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
        message_count=4,
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id=kwargs["chat_id"],
            msg_id="m-asst",
            role=kwargs["role"],
            text=kwargs["text"],
            model=kwargs.get("model"),
            created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages",
        lambda *_a, **_kw: ([], None),
    )
    return chat


def test_post_message_emits_follow_ups_suggested_when_pref_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """suggest_followups=True → emits a follow_ups_suggested SSE event
    after done, carrying the parsed suggestion list."""
    _stub_existing_chat(monkeypatch)
    monkeypatch.setattr(
        "channel.api.chats.storage.get_prefs",
        lambda _u: Prefs(suggest_followups=True),
    )

    async def fake_main(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Reply"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_main

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    async def fake_followups(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "What about X?\n"}}}}
        yield {"event": {"contentBlockDelta": {"delta": {"text": "How does Y work?\n"}}}}
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Compare Z and W."}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeFollowups:
        stream_async = fake_followups

    monkeypatch.setattr("channel.api.chats.build_followups_agent", lambda: FakeFollowups())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    body = response.text
    assert '"type": "follow_ups_suggested"' in body
    # Three cleaned suggestions.
    assert '"What about X?"' in body
    assert '"How does Y work?"' in body
    assert '"Compare Z and W"' in body
    # Order: done → follow_ups_suggested.
    assert body.find('"type": "follow_ups_suggested"') > body.find('"type": "done"')


def test_post_message_skips_follow_ups_when_pref_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """suggest_followups=False → no SSE event, no agent built."""
    _stub_existing_chat(monkeypatch)
    monkeypatch.setattr(
        "channel.api.chats.storage.get_prefs",
        lambda _u: Prefs(suggest_followups=False),
    )

    async def fake_main(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Reply"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_main

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    built: list[bool] = []
    monkeypatch.setattr(
        "channel.api.chats.build_followups_agent",
        lambda: built.append(True) or object(),
    )

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    assert '"type": "follow_ups_suggested"' not in response.text
    assert built == []


def test_post_message_follow_ups_respects_kill_switch_env(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STARTER_FOLLOWUPS_ENABLED=0 → skip even when pref is on."""
    monkeypatch.setenv("STARTER_FOLLOWUPS_ENABLED", "0")
    _stub_existing_chat(monkeypatch)
    monkeypatch.setattr(
        "channel.api.chats.storage.get_prefs",
        lambda _u: Prefs(suggest_followups=True),
    )

    async def fake_main(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Reply"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_main

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())
    built: list[bool] = []
    monkeypatch.setattr(
        "channel.api.chats.build_followups_agent",
        lambda: built.append(True) or object(),
    )

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    assert '"type": "follow_ups_suggested"' not in response.text
    assert built == []


def test_post_message_follow_ups_failure_is_swallowed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Follow-ups Agent raises → stream still completes, no event emitted."""
    _stub_existing_chat(monkeypatch)
    monkeypatch.setattr(
        "channel.api.chats.storage.get_prefs",
        lambda _u: Prefs(suggest_followups=True),
    )

    async def fake_main(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Reply"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_main

    class FakeFollowups:
        async def stream_async(self, prompt):
            if False:  # pragma: no cover
                yield None
            raise RuntimeError("haiku unavailable")

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())
    monkeypatch.setattr("channel.api.chats.build_followups_agent", lambda: FakeFollowups())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    body = response.text
    assert '"type": "done"' in body
    assert '"type": "follow_ups_suggested"' not in body


def test_post_message_follow_ups_empty_result_records_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Haiku returns whitespace-only → no event emitted (no garbage chips)."""
    _stub_existing_chat(monkeypatch)
    monkeypatch.setattr(
        "channel.api.chats.storage.get_prefs",
        lambda _u: Prefs(suggest_followups=True),
    )

    async def fake_main(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_main

    async def empty_followups(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "   \n   \n"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeFollowups:
        stream_async = empty_followups

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())
    monkeypatch.setattr("channel.api.chats.build_followups_agent", lambda: FakeFollowups())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    assert '"type": "follow_ups_suggested"' not in response.text


def test_post_message_follow_ups_caps_at_three_suggestions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_existing_chat(monkeypatch)
    monkeypatch.setattr(
        "channel.api.chats.storage.get_prefs",
        lambda _u: Prefs(suggest_followups=True),
    )

    async def fake_main(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_main

    async def fake_followups(self, prompt):
        # Five lines — the parser must drop everything past three.
        text = "One\nTwo\nThree\nFour\nFive"
        yield {"event": {"contentBlockDelta": {"delta": {"text": text}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeFollowups:
        stream_async = fake_followups

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())
    monkeypatch.setattr("channel.api.chats.build_followups_agent", lambda: FakeFollowups())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    body = response.text
    assert '"One"' in body
    assert '"Two"' in body
    assert '"Three"' in body
    assert '"Four"' not in body
    assert '"Five"' not in body


def test_parse_followups_strips_numbering_and_bullets():
    from channel.api.chats import _parse_followups

    assert _parse_followups("1. Build a feature\n- Refactor X\n• Test Z\n* Deploy") == [
        "Build a feature",
        "Refactor X",
        "Test Z",
    ]


def test_parse_followups_returns_empty_for_blank_input():
    from channel.api.chats import _parse_followups

    assert _parse_followups("") == []
    assert _parse_followups("   \n\n   ") == []


def test_post_message_follow_ups_salvages_partial_output_on_max_tokens(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MaxTokensReachedException → use whatever lines were emitted so far."""
    from strands.types.exceptions import MaxTokensReachedException

    _stub_existing_chat(monkeypatch)
    monkeypatch.setattr(
        "channel.api.chats.storage.get_prefs",
        lambda _u: Prefs(suggest_followups=True),
    )

    async def fake_main(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_main

    class FakeFollowups:
        async def stream_async(self, prompt):
            yield {"event": {"contentBlockDelta": {"delta": {"text": "Salvage me"}}}}
            raise MaxTokensReachedException("hit cap")

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())
    monkeypatch.setattr("channel.api.chats.build_followups_agent", lambda: FakeFollowups())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    assert '"Salvage me"' in response.text


def test_post_message_seeds_agent_with_prior_chat_history(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each turn must seed Strands with the chat's prior messages so
    the agent doesn't restart from scratch — bug surfaced on Phase 7c
    dev where multi-turn chats lost continuity."""
    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id=kwargs["chat_id"],
            msg_id="m1",
            role=kwargs["role"],
            text=kwargs["text"],
            model=kwargs.get("model"),
            created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)

    prior = [
        Message(chat_id="c1", msg_id="m0", role=MessageRole.USER, text="hi", created_at="t1"),
        Message(
            chat_id="c1",
            msg_id="m1",
            role=MessageRole.ASSISTANT,
            text="hey",
            model="us.anthropic.claude-sonnet-4-6",
            created_at="t2",
        ),
    ]
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages",
        lambda *_a, **_kw: (prior, None),
    )

    captured: dict[str, Any] = {}

    async def fake_stream(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    def fake_build_agent(**kwargs):
        captured["build_kwargs"] = kwargs
        return FakeAgent()

    monkeypatch.setattr("channel.api.chats.build_agent", fake_build_agent)

    resp = client.post(
        "/api/chats/c1/messages",
        json={"message": "third turn", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert resp.status_code == 200

    # build_agent must receive prior history translated to Strands shape.
    prior_messages = captured["build_kwargs"]["prior_messages"]
    assert prior_messages == [
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"text": "hey"}]},
    ]


def test_regenerate_drops_trailing_user_from_seeded_history(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regenerate path: the last user message in storage IS the one
    being re-streamed via ``stream_async``. It must be dropped from
    the seeded history so Strands doesn't see it twice."""
    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.delete_last_assistant_message",
        lambda _: None,
    )
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id=kwargs["chat_id"],
            msg_id="new-a",
            role=kwargs["role"],
            text=kwargs["text"],
            model=kwargs.get("model"),
            created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)

    prior = [
        Message(chat_id="c1", msg_id="m0", role=MessageRole.USER, text="round1-u", created_at="t1"),
        Message(
            chat_id="c1",
            msg_id="m1",
            role=MessageRole.ASSISTANT,
            text="round1-a",
            model="us.anthropic.claude-sonnet-4-6",
            created_at="t2",
        ),
        Message(
            chat_id="c1", msg_id="m2", role=MessageRole.USER, text="regen-this", created_at="t3"
        ),
    ]
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages",
        lambda *_a, **_kw: (prior, None),
    )

    captured: dict[str, Any] = {}

    async def fake_stream(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    def fake_build_agent(**kwargs):
        captured["build_kwargs"] = kwargs
        return FakeAgent()

    monkeypatch.setattr("channel.api.chats.build_agent", fake_build_agent)

    resp = client.post("/api/chats/c1/regenerate", json={})
    assert resp.status_code == 200

    # Trailing user ("regen-this") is excluded — it'll come back in via
    # stream_async. Only the first two turns survive.
    prior_messages = captured["build_kwargs"]["prior_messages"]
    assert prior_messages == [
        {"role": "user", "content": [{"text": "round1-u"}]},
        {"role": "assistant", "content": [{"text": "round1-a"}]},
    ]


def test_post_message_returns_404_for_unowned_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: None)
    response = client.post("/api/chats/x/messages", json={"message": "hi"})
    assert response.status_code == 404


def test_post_message_replays_on_duplicate_idempotency_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "channel.api.chats.storage.get_chat_by_id",
        lambda _: Chat(
            chat_id="c1",
            user_id="u-1",
            title="t",
            created_at="t",
            last_message_at="t",
            model_default="m",
        ),
    )

    stored_result = {
        "user_msg_id": "user-existing",
        "text": "previous reply",
        "done": {
            "msg_id": "asst-existing",
            "seq": 1,
            "model": "canned-stream-v1",
            "input_tokens": 0,
            "output_tokens": 0,
            "stop_reason": "end_turn",
        },
    }

    monkeypatch.setattr(
        "channel.api.chats.storage.reserve_idempotency_key",
        lambda **_: {"result": stored_result},
    )
    # put_message MUST NOT be called on the replay path.
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **_: pytest.fail("replay path must skip put_message"),
    )

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi"},
        headers={"Idempotency-Key": "kkk"},
    )
    assert response.status_code == 200
    assert "previous reply" in response.text
    assert "user-existing" in response.text
    assert "asst-existing" in response.text


def test_post_message_stores_result_on_fresh_idempotency_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id=kwargs["chat_id"],
            msg_id="m1",
            role=kwargs["role"],
            text=kwargs["text"],
            model=kwargs.get("model"),
            created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages",
        lambda *_a, **_kw: ([], None),
    )
    # Fresh key — reserve returns None.
    monkeypatch.setattr("channel.api.chats.storage.reserve_idempotency_key", lambda **_: None)

    # Mock Strands so the stream completes without real Bedrock.
    async def fake_stream(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Hi back"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}
        yield {"event": {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 2}}}}

    class FakeAgent:
        stream_async = fake_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    stored: dict[str, Any] = {}
    monkeypatch.setattr(
        "channel.api.chats.storage.store_idempotency_result",
        lambda **kwargs: stored.update(kwargs),
    )

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6"},
        headers={"Idempotency-Key": "k-fresh"},
    )
    assert response.status_code == 200
    assert "delta" in response.text
    # After the stream completes, store_idempotency_result should have been called.
    assert stored["user_id"] == "u-1"
    assert stored["key"] == "k-fresh"
    # Accumulated assistant text from the Strands stream is persisted verbatim.
    assert stored["payload"]["text"] == "Hi back"
    assert stored["payload"]["user_msg_id"] == "m1"
    assert stored["payload"]["done"]["msg_id"] == "m1"
    assert stored["payload"]["done"]["seq"] == 1
    assert stored["payload"]["done"]["input_tokens"] == 1
    assert stored["payload"]["done"]["output_tokens"] == 2
    assert stored["payload"]["done"]["stop_reason"] == "end_turn"


def test_post_message_rejects_empty_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "channel.api.chats.storage.get_chat_by_id",
        lambda _: Chat(
            chat_id="c1",
            user_id="u-1",
            title="t",
            created_at="t",
            last_message_at="t",
            model_default="m",
        ),
    )
    response = client.post("/api/chats/c1/messages", json={"message": ""})
    assert response.status_code == 422  # Pydantic min_length=1


def test_regenerate_drops_last_assistant_and_restreams(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)

    deleted_calls = []
    monkeypatch.setattr(
        "channel.api.chats.storage.delete_last_assistant_message",
        lambda chat_id: deleted_calls.append(chat_id),
    )
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages",
        lambda *_a, **_kw: (
            [
                Message(
                    chat_id="c1",
                    msg_id="u-1",
                    role=MessageRole.USER,
                    text="redo",
                    created_at="t",
                )
            ],
            None,
        ),
    )

    async def fake_stream(self, prompt):
        assert prompt == "redo"
        yield {"event": {"contentBlockDelta": {"delta": {"text": "again"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    put_calls: list[dict[str, Any]] = []

    def fake_put(**kw: Any) -> Message:
        put_calls.append(kw)
        return Message(
            chat_id=kw["chat_id"],
            msg_id="m-new",
            role=kw["role"],
            text=kw["text"],
            created_at="t",
        )

    monkeypatch.setattr("channel.api.chats.storage.put_message", fake_put)

    update_index_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "channel.api.chats.storage.update_chat_index",
        lambda **kw: update_index_calls.append(kw),
    )

    response = client.post("/api/chats/c1/regenerate", json={})
    assert response.status_code == 200
    assert "again" in response.text
    assert deleted_calls == ["c1"]

    # Regenerate must NOT persist a new user message — only the assistant turn.
    assert [c["role"] for c in put_calls] == [MessageRole.ASSISTANT], (
        "regenerate must not persist a new user message"
    )
    # And must NOT emit a user_persisted SSE event (no temp turn to match).
    assert "user_persisted" not in response.text
    # Chat-index delta_count is 0 (deleted assistant + new assistant cancel out).
    assert update_index_calls[0]["delta_count"] == 0


def test_regenerate_returns_400_when_no_user_messages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "channel.api.chats.storage.get_chat_by_id",
        lambda _: Chat(
            chat_id="c1",
            user_id="u-1",
            title="t",
            created_at="t",
            last_message_at="t",
            model_default="m",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.delete_last_assistant_message", lambda _: None)
    monkeypatch.setattr("channel.api.chats.storage.list_messages", lambda *_a, **_kw: ([], None))

    response = client.post("/api/chats/c1/regenerate", json={})
    assert response.status_code == 400


def test_regenerate_returns_404_for_unowned_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: None)
    response = client.post("/api/chats/x/regenerate", json={})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/chats/{chat_id}
# ---------------------------------------------------------------------------


def test_delete_chat_returns_204_and_wipes_ddb(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: DELETE returns 204 with empty body; subsequent GET returns 404."""
    chat = Chat(
        chat_id="c-del-1",
        user_id="u-1",
        title="to delete",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
    )
    deleted: list[dict[str, Any]] = []

    def fake_get(chat_id: str) -> Chat | None:
        # Return None after deletion to simulate the chat being gone.
        return None if deleted else chat

    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", fake_get)
    monkeypatch.setattr(
        "channel.api.chats.storage.delete_chat",
        lambda **kwargs: deleted.append(kwargs),
    )
    # Stub out AgentCore helpers so no boto3 calls happen.
    monkeypatch.setattr("channel.api.chats._agentcore_client", lambda: None)
    monkeypatch.setattr("channel.api.chats._memory_id_for_env", lambda: "mem-fake")

    async def fake_wipe(actor_id: str, chat_id: str) -> None:
        pass

    monkeypatch.setattr("channel.api.chats._wipe_agentcore_session", fake_wipe)

    resp = client.delete("/api/chats/c-del-1")
    assert resp.status_code == 204
    assert resp.content == b""
    assert deleted[0]["user_id"] == "u-1"
    assert deleted[0]["chat"].chat_id == "c-del-1"

    # Subsequent GET should 404 (chat_id unknown after deletion).
    get_resp = client.get("/api/chats/c-del-1")
    assert get_resp.status_code == 404


def test_delete_chat_404s_on_cross_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE from a different user's JWT returns 404; owner's chat is untouched."""
    from channel.api._auth import require_mgmt_user
    from channel.api.main import app

    chat = Chat(
        chat_id="c-del-2",
        user_id="u-owner",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)

    # Client authenticated as a different user (intruder).
    def _stub_intruder() -> dict[str, Any]:
        return {"sub": "u-intruder", "role": "user"}

    app.dependency_overrides[require_mgmt_user] = _stub_intruder
    try:
        intruder_client = TestClient(app)
        resp = intruder_client.delete("/api/chats/c-del-2")
        assert resp.status_code == 404

        # Owner can still GET the chat.
        def _stub_owner() -> dict[str, Any]:
            return {"sub": "u-owner", "role": "user"}

        app.dependency_overrides[require_mgmt_user] = _stub_owner
        owner_client = TestClient(app)
        monkeypatch.setattr(
            "channel.api.chats.storage.list_messages", lambda *_a, **_kw: ([], None)
        )
        ok = owner_client.get("/api/chats/c-del-2")
        assert ok.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_delete_chat_calls_agentcore_delete_event_per_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AgentCore wipe calls delete_event for each event returned by list_events."""
    from unittest.mock import MagicMock

    chat = Chat(
        chat_id="c-del-3",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr("channel.api.chats.storage.delete_chat", lambda **_: None)

    fake_ac = MagicMock()
    fake_ac.list_events.return_value = {
        "events": [
            {"eventId": "ev-1"},
            {"eventId": "ev-2"},
        ],
    }
    monkeypatch.setattr("channel.api.chats._agentcore_client", lambda: fake_ac)
    monkeypatch.setattr("channel.api.chats._memory_id_for_env", lambda: "channel_test-FAKEMEMID")

    resp = client.delete("/api/chats/c-del-3")
    assert resp.status_code == 204

    assert fake_ac.list_events.called
    assert fake_ac.delete_event.call_count == 2
    deleted_event_ids = {call.kwargs["eventId"] for call in fake_ac.delete_event.call_args_list}
    assert deleted_event_ids == {"ev-1", "ev-2"}


def test_delete_chat_swallows_agentcore_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AgentCore failure must not prevent the 204; metric is recorded as failure."""
    from unittest.mock import AsyncMock, MagicMock

    chat = Chat(
        chat_id="c-del-4",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr("channel.api.chats.storage.delete_chat", lambda **_: None)

    fake_ac = MagicMock()
    fake_ac.list_events.side_effect = RuntimeError("agentcore down")
    monkeypatch.setattr("channel.api.chats._agentcore_client", lambda: fake_ac)
    monkeypatch.setattr("channel.api.chats._memory_id_for_env", lambda: "channel_test-FAKEMEMID")

    record_failure = AsyncMock()
    monkeypatch.setattr(
        "channel.api.chats.record_chat_delete_memory_wipe_outcome",
        record_failure,
    )

    resp = client.delete("/api/chats/c-del-4")
    assert resp.status_code == 204
    record_failure.assert_awaited_once_with(success=False)


def test_agentcore_client_and_memory_id_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Directly exercise _agentcore_client and _memory_id_for_env to cover
    the real-boto3 / real-agentcore code paths (lines patched in endpoint tests)."""
    from unittest.mock import MagicMock, patch

    import channel.api.chats as chats_mod

    fake_boto_client = MagicMock()
    with patch("channel.api.chats.boto3.client", return_value=fake_boto_client) as mock_boto:
        result = chats_mod._agentcore_client()
    mock_boto.assert_called_once_with("bedrock-agentcore")
    assert result is fake_boto_client

    monkeypatch.setenv("STARTER_ENV", "test-env")
    monkeypatch.setattr("channel.api.chats.get_or_create_memory", lambda env: f"mem-{env}")
    mem_id = chats_mod._memory_id_for_env()
    assert mem_id == "mem-test-env"


def test_delete_chat_wipe_paginates_with_next_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_wipe_agentcore_session must page through list_events when nextToken
    is present, covering the ``kwargs['nextToken'] = next_token`` branch."""
    from unittest.mock import MagicMock

    chat = Chat(
        chat_id="c-del-5",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr("channel.api.chats.storage.delete_chat", lambda **_: None)

    # First call returns page 1 with nextToken; second call returns page 2.
    page1 = {"events": [{"eventId": "ev-p1"}], "nextToken": "tok-abc"}
    page2 = {"events": [{"eventId": "ev-p2"}]}
    fake_ac = MagicMock()
    fake_ac.list_events.side_effect = [page1, page2]
    monkeypatch.setattr("channel.api.chats._agentcore_client", lambda: fake_ac)
    monkeypatch.setattr("channel.api.chats._memory_id_for_env", lambda: "mem-fake")

    resp = client.delete("/api/chats/c-del-5")
    assert resp.status_code == 204
    # Two pages → two list_events calls, two delete_event calls.
    assert fake_ac.list_events.call_count == 2
    assert fake_ac.delete_event.call_count == 2
    # Second list_events call must include nextToken.
    second_call_kwargs = fake_ac.list_events.call_args_list[1].kwargs
    assert second_call_kwargs["nextToken"] == "tok-abc"


# ---- _postprocess_title helper ----------------------------------------------


def test_postprocess_title_passes_clean_input_through():
    from channel.api.chats import _postprocess_title

    assert _postprocess_title("Debug pytest fixture") == "Debug pytest fixture"


def test_postprocess_title_strips_colon_prefixed_preamble():
    from channel.api.chats import _postprocess_title

    assert (
        _postprocess_title("Here's a 3-6 word title: Debug pytest fixture")
        == "Debug pytest fixture"
    )


def test_postprocess_title_returns_empty_on_empty_input():
    from channel.api.chats import _postprocess_title

    assert _postprocess_title("") == ""


def test_postprocess_title_caps_at_6_words():
    from channel.api.chats import _postprocess_title

    assert (
        _postprocess_title("One two three four five six seven eight")
        == "One two three four five six"
    )


def test_postprocess_title_strips_wrapping_quotes():
    from channel.api.chats import _postprocess_title

    assert _postprocess_title('"Debug pytest fixture"') == "Debug pytest fixture"


def test_postprocess_title_returns_empty_on_preamble_only_truncation():
    """When the model wrote preamble but ran out of tokens before
    emitting the actual title, the colon-split returns an empty tail.
    The helper MUST surface that as empty (caller treats it as a
    failure outcome) — emitting the preamble itself as the title
    would be worse than 'New chat'."""
    from channel.api.chats import _postprocess_title

    assert _postprocess_title("Here's a 3-6 word title:") == ""


# ---- Titler salvage on MaxTokensReachedException ----------------------------


def test_titler_salvages_partial_output_on_max_tokens(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strands emits deltas BEFORE raising MaxTokensReachedException
    on the cap, so title_chunks already holds usable partial text.
    The salvage path strips the preamble via colon-split and persists
    the recovered title."""
    from strands.types.exceptions import MaxTokensReachedException

    _stub_first_round_trip_chat(monkeypatch)

    patched_titles: list[str] = []
    monkeypatch.setattr(
        "channel.api.chats.storage.patch_chat",
        lambda **kwargs: patched_titles.append(kwargs["title"]),
    )

    async def fake_main_stream(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Hi"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_main_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    async def fake_titler_stream(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Here's a title: "}}}}
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Debug pytest fixture"}}}}
        raise MaxTokensReachedException("token limit reached")

    class FakeTitler:
        stream_async = fake_titler_stream

    monkeypatch.setattr("channel.api.chats.build_titler_agent", lambda: FakeTitler())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    body = response.text
    assert '"type": "title_suggested"' in body
    assert '"title": "Debug pytest fixture"' in body
    assert patched_titles == ["Debug pytest fixture"]


def test_titler_records_failure_when_only_preamble_emitted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If MaxTokensReachedException fires before any post-colon text
    arrives, _postprocess_title returns empty and we MUST NOT emit
    SSE — surfacing the preamble as the title would be worse than
    leaving 'New chat'."""
    from strands.types.exceptions import MaxTokensReachedException

    _stub_first_round_trip_chat(monkeypatch)

    patched: list[Any] = []
    monkeypatch.setattr(
        "channel.api.chats.storage.patch_chat",
        lambda **kwargs: patched.append(kwargs),
    )

    async def fake_main_stream(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_main_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    async def fake_titler_stream(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Here's a 3-6 word title:"}}}}
        raise MaxTokensReachedException("token limit reached")

    class FakeTitler:
        stream_async = fake_titler_stream

    monkeypatch.setattr("channel.api.chats.build_titler_agent", lambda: FakeTitler())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    assert '"type": "title_suggested"' not in response.text
    assert patched == []


def test_titler_outer_except_catches_unrelated_errors(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-MaxTokens errors (network, agent build failure, etc.) still
    hit the outer except Exception and record failure — fail-soft
    contract preserved for arbitrary exceptions."""
    _stub_first_round_trip_chat(monkeypatch)

    patched: list[Any] = []
    monkeypatch.setattr(
        "channel.api.chats.storage.patch_chat",
        lambda **kwargs: patched.append(kwargs),
    )

    async def fake_main_stream(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_main_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    async def fake_titler_stream(self, prompt):
        raise RuntimeError("bedrock down")
        yield  # unreachable; declares this as an async generator

    class FakeTitler:
        stream_async = fake_titler_stream

    monkeypatch.setattr("channel.api.chats.build_titler_agent", lambda: FakeTitler())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    assert '"type": "title_suggested"' not in response.text
    assert patched == []


# ---------------------------------------------------------------------------
# POST /api/chats/{chat_id}/messages/{msg_id}/feedback (issue #146)
# ---------------------------------------------------------------------------


def _stub_owned_chat(
    monkeypatch: pytest.MonkeyPatch, *, chat_id: str = "c1", user_id: str = "u-1"
) -> Chat:
    chat = Chat(
        chat_id=chat_id,
        user_id=user_id,
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    return chat


def test_submit_feedback_returns_204_and_calls_storage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: POST stores up-feedback and returns 204 No Content."""
    from channel.models import Feedback, FeedbackKind

    _stub_owned_chat(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_put(*, chat_id: str, msg_id: str, kind: FeedbackKind, note: str | None) -> Feedback:
        captured.update({"chat_id": chat_id, "msg_id": msg_id, "kind": kind, "note": note})
        return Feedback(kind=kind, note=note, created_at="2026-06-03T00:00:00Z")

    monkeypatch.setattr("channel.api.chats.storage.put_message_feedback", fake_put)

    response = client.post(
        "/api/chats/c1/messages/m1/feedback",
        json={"kind": "up", "note": None},
    )

    assert response.status_code == 204
    assert response.content == b""
    assert captured == {
        "chat_id": "c1",
        "msg_id": "m1",
        "kind": FeedbackKind.UP,
        "note": None,
    }


def test_submit_feedback_persists_down_kind_with_note(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thumbs-down + optional note round-trip through the endpoint."""
    from channel.models import Feedback, FeedbackKind

    _stub_owned_chat(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_put(**kwargs: Any) -> Feedback:
        captured.update(kwargs)
        return Feedback(kind=kwargs["kind"], note=kwargs["note"], created_at="2026-06-03T00:00:00Z")

    monkeypatch.setattr("channel.api.chats.storage.put_message_feedback", fake_put)

    response = client.post(
        "/api/chats/c1/messages/m1/feedback",
        json={"kind": "down", "note": "wrong answer"},
    )

    assert response.status_code == 204
    assert captured["kind"] is FeedbackKind.DOWN
    assert captured["note"] == "wrong answer"


def test_submit_feedback_404s_on_unknown_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller doesn't own the chat (or it doesn't exist) → 404."""
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: None)

    response = client.post(
        "/api/chats/c-missing/messages/m1/feedback",
        json={"kind": "up", "note": None},
    )

    assert response.status_code == 404


def test_submit_feedback_404s_on_cross_user_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller is authenticated but the chat belongs to a different user → 404."""
    chat = Chat(
        chat_id="c-other",
        user_id="u-owner",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)

    def _stub_intruder() -> dict[str, Any]:
        return {"sub": "u-intruder", "role": "user"}

    app.dependency_overrides[require_mgmt_user] = _stub_intruder
    try:
        intruder = TestClient(app)
        resp = intruder.post(
            "/api/chats/c-other/messages/m1/feedback",
            json={"kind": "up", "note": None},
        )
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_submit_feedback_404s_when_message_unknown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Storage returns None (msg_id not in chat) → API surfaces 404."""
    _stub_owned_chat(monkeypatch)
    monkeypatch.setattr("channel.api.chats.storage.put_message_feedback", lambda **_: None)

    response = client.post(
        "/api/chats/c1/messages/m-missing/feedback",
        json={"kind": "up", "note": None},
    )

    assert response.status_code == 404


def test_submit_feedback_422s_on_invalid_kind(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown `kind` values are rejected by pydantic with a 422."""
    _stub_owned_chat(monkeypatch)

    def _must_not_run(**_: Any) -> None:
        raise AssertionError("storage must not be called on validation failure")

    monkeypatch.setattr("channel.api.chats.storage.put_message_feedback", _must_not_run)

    response = client.post(
        "/api/chats/c1/messages/m1/feedback",
        json={"kind": "sideways", "note": None},
    )

    assert response.status_code == 422


def test_submit_feedback_422s_on_missing_kind(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing required `kind` field fails validation."""
    _stub_owned_chat(monkeypatch)

    response = client.post(
        "/api/chats/c1/messages/m1/feedback",
        json={"note": "hi"},
    )

    assert response.status_code == 422


def test_submit_feedback_requires_auth() -> None:
    """Without the auth override, the endpoint requires a mgmt JWT."""
    # Snapshot + restore overrides so a parallel-randomized test order
    # can't see this test's mid-flight clear and leak across cases.
    # The `client` fixture's autouse teardown already clears overrides
    # per-test; this restoration is defence-in-depth for any test that
    # constructs `app` directly without going through that fixture.
    saved_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        unauth = TestClient(app)
        resp = unauth.post(
            "/api/chats/c1/messages/m1/feedback",
            json={"kind": "up", "note": None},
        )
        assert resp.status_code in {401, 403}
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved_overrides)


# ----------------------------------------------------------------
# Attachment cascade on chat delete (#174)
# ----------------------------------------------------------------


def _stub_delete_handler_storage_and_agentcore(monkeypatch: pytest.MonkeyPatch, chat: Chat) -> None:
    """Common stubs for the delete_chat handler: chat lookup, DDB delete,
    and the AgentCore session wipe. Tests layer attachment-specific
    stubs on top."""
    from unittest.mock import MagicMock

    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr("channel.api.chats.storage.delete_chat", lambda **_: None)
    fake_ac = MagicMock()
    fake_ac.list_events.return_value = {"events": []}
    monkeypatch.setattr("channel.api.chats._agentcore_client", lambda: fake_ac)
    monkeypatch.setattr("channel.api.chats._memory_id_for_env", lambda: "channel_test-FAKEMEMID")


def test_delete_chat_invokes_attachment_cascade(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delete_chat handler MUST call storage.delete_chat_attachments
    with the chat id and the authenticated user's JWT sub."""

    chat = Chat(
        chat_id="c-cascade-1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    _stub_delete_handler_storage_and_agentcore(monkeypatch, chat)

    seen: list[dict[str, Any]] = []

    def fake_cascade(**kwargs: Any) -> tuple[int, int]:
        seen.append(kwargs)
        return (3, 0)

    monkeypatch.setattr("channel.api.chats.storage.delete_chat_attachments", fake_cascade)

    resp = client.delete("/api/chats/c-cascade-1")
    assert resp.status_code == 204
    assert seen == [{"chat_id": "c-cascade-1", "user_id": "u-1"}]


def test_delete_chat_emits_attachment_success_metric_when_cascade_clean(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cascade returns (N, 0) → success metric."""

    from unittest.mock import AsyncMock

    chat = Chat(
        chat_id="c-cascade-2",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    _stub_delete_handler_storage_and_agentcore(monkeypatch, chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.delete_chat_attachments",
        lambda **_: (2, 0),
    )
    record = AsyncMock()
    monkeypatch.setattr("channel.api.chats.record_chat_delete_attachment_wipe_outcome", record)

    resp = client.delete("/api/chats/c-cascade-2")
    assert resp.status_code == 204
    record.assert_awaited_once_with(success=True)


def test_delete_chat_emits_attachment_failure_metric_on_partial_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cascade returns (N, M>0) → failure metric. Partial-success counts as
    failure so alarming triggers on any attachment leftover."""

    from unittest.mock import AsyncMock

    chat = Chat(
        chat_id="c-cascade-3",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    _stub_delete_handler_storage_and_agentcore(monkeypatch, chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.delete_chat_attachments",
        lambda **_: (1, 1),
    )
    record = AsyncMock()
    monkeypatch.setattr("channel.api.chats.record_chat_delete_attachment_wipe_outcome", record)

    resp = client.delete("/api/chats/c-cascade-3")
    assert resp.status_code == 204
    record.assert_awaited_once_with(success=False)


def test_delete_chat_swallows_attachment_cascade_exception(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cascade raising must NOT prevent the 204 — DDB is source of truth
    for chat existence; orphan attachments are an out-of-band cleanup
    concern. Failure metric is recorded."""

    from unittest.mock import AsyncMock

    chat = Chat(
        chat_id="c-cascade-4",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    _stub_delete_handler_storage_and_agentcore(monkeypatch, chat)

    def fake_cascade(**_: Any) -> tuple[int, int]:
        raise RuntimeError("ddb query blew up")

    monkeypatch.setattr("channel.api.chats.storage.delete_chat_attachments", fake_cascade)
    record = AsyncMock()
    monkeypatch.setattr("channel.api.chats.record_chat_delete_attachment_wipe_outcome", record)

    resp = client.delete("/api/chats/c-cascade-4")
    assert resp.status_code == 204
    record.assert_awaited_once_with(success=False)
