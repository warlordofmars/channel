# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for chat persistence against DynamoDB Local."""

from __future__ import annotations

import pytest

from channel import storage
from channel.models import MessageRole


@pytest.mark.usefixtures("starter_table")
def test_create_then_get_then_list() -> None:
    chat = storage.create_chat(
        user_id="u-itest",
        title=None,
        model_default="canned-stream-v1",
    )

    fetched = storage.get_chat_by_id(chat.chat_id)
    assert fetched is not None
    assert fetched.user_id == "u-itest"

    chats, _ = storage.list_chats_for_user("u-itest", limit=10, cursor=None)
    assert any(c.chat_id == chat.chat_id for c in chats)


@pytest.mark.usefixtures("starter_table")
def test_message_round_trip() -> None:
    chat = storage.create_chat(
        user_id="u-itest-msgs",
        title=None,
        model_default="canned-stream-v1",
    )
    storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.USER,
        text="hi",
        model=None,
    )
    storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.ASSISTANT,
        text="hello",
        model="canned-stream-v1",
        input_tokens=0,
        output_tokens=0,
    )
    storage.update_chat_index(
        user_id=chat.user_id,
        chat=chat,
        last_user_preview="hi",
        delta_count=2,
        last_message_at="2026-05-30T00:00:00Z",
    )

    messages, _ = storage.list_messages(chat.chat_id, limit=10, cursor=None)
    assert [m.text for m in messages] == ["hi", "hello"]

    refreshed = storage.get_chat_by_id(chat.chat_id)
    assert refreshed is not None
    assert refreshed.message_count == 2
    assert refreshed.last_user_preview == "hi"


@pytest.mark.usefixtures("starter_table")
def test_idempotency_key_conditional_check() -> None:
    """Real ConditionalCheckFailedException path — uncovered by the unit suite."""

    first = storage.reserve_idempotency_key(user_id="u-idem", key="iter-1")
    assert first is None

    second = storage.reserve_idempotency_key(user_id="u-idem", key="iter-1")
    assert second is not None
    assert second["PK"] == "IDEMP#u-idem"


@pytest.mark.usefixtures("starter_table")
def test_head_summary_row_round_trips_and_hides_from_message_queries() -> None:
    """#245 against real DynamoDB: the SUMMARY row shares the chat
    partition but must never surface through a ``begins_with("MSG#")``
    read, and the open-range scan must exclude both boundaries."""

    chat = storage.create_chat(
        user_id="u-itest-summary",
        title=None,
        model_default="canned-stream-v1",
    )
    msgs = [
        storage.put_message(
            chat_id=chat.chat_id,
            role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
            text=f"turn-{i}",
            model=None,
        )
        for i in range(6)
    ]

    assert storage.get_chat_summary(chat.chat_id) is None

    storage.put_chat_summary(
        chat_id=chat.chat_id,
        text="They settled on OKLCH tokens.",
        covers_through=storage.message_sk(msgs[1]),
    )
    loaded = storage.get_chat_summary(chat.chat_id)
    assert loaded is not None
    assert loaded.text == "They settled on OKLCH tokens."
    assert loaded.covers_through == storage.message_sk(msgs[1])

    # The SUMMARY row is invisible to every message read path.
    listed, _ = storage.list_messages(chat.chat_id, limit=50, cursor=None)
    assert [m.text for m in listed] == [f"turn-{i}" for i in range(6)]
    assert [m.text for m in storage.list_recent_messages(chat.chat_id, limit=50)] == [
        f"turn-{i}" for i in range(6)
    ]

    # Open range: turn-1 (already summarised) and turn-4 (still in the
    # loaded window) are both excluded.
    unsummarized = storage.list_messages_unsummarized(
        chat.chat_id,
        after_sk=storage.message_sk(msgs[1]),
        before_sk=storage.message_sk(msgs[4]),
        limit=50,
    )
    assert [m.text for m in unsummarized] == ["turn-2", "turn-3"]


@pytest.mark.usefixtures("starter_table")
def test_delete_chat_wipes_the_head_summary_row() -> None:
    chat = storage.create_chat(
        user_id="u-itest-summary-del",
        title=None,
        model_default="canned-stream-v1",
    )
    msg = storage.put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="hi", model=None)
    storage.put_chat_summary(
        chat_id=chat.chat_id,
        text="gist",
        covers_through=storage.message_sk(msg),
    )
    assert storage.get_chat_summary(chat.chat_id) is not None

    storage.delete_chat(user_id=chat.user_id, chat=chat)

    assert storage.get_chat_summary(chat.chat_id) is None
    listed, _ = storage.list_messages(chat.chat_id, limit=50, cursor=None)
    assert listed == []
