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
    assert captured["model_default"] == "claude-sonnet-4-6"


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
