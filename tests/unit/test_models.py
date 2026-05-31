# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for chat domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from channel.models import (
    Chat,
    ChatCreate,
    ChatPatch,
    Message,
    MessageRole,
)


def test_chat_roundtrips_with_required_fields():
    chat = Chat(
        chat_id="abc",
        user_id="u-1",
        title="Hello",
        created_at="2026-05-30T00:00:00Z",
        last_message_at="2026-05-30T00:00:00Z",
        last_user_preview="",
        model_default="canned-stream-v1",
        message_count=0,
        archived=False,
    )
    assert chat.chat_id == "abc"
    assert chat.archived is False


def test_message_rejects_unknown_role():
    with pytest.raises(ValidationError):
        Message(
            chat_id="abc",
            msg_id="m-1",
            role="system",  # not user|assistant
            text="hi",
            created_at="2026-05-30T00:00:00Z",
        )


def test_message_assistant_carries_token_usage():
    msg = Message(
        chat_id="abc",
        msg_id="m-1",
        role=MessageRole.ASSISTANT,
        text="hello",
        model="canned-stream-v1",
        input_tokens=12,
        output_tokens=3,
        created_at="2026-05-30T00:00:00Z",
    )
    assert msg.role == MessageRole.ASSISTANT
    assert msg.input_tokens == 12


def test_chat_create_defaults_title():
    payload = ChatCreate()
    assert payload.title is None  # server will substitute "New chat"


def test_chat_patch_allows_partial_updates():
    patch = ChatPatch(title="Renamed")
    assert patch.title == "Renamed"
    assert patch.archived is None


def test_last_user_preview_caps_at_120_chars():
    long = "x" * 500
    chat = Chat(
        chat_id="abc",
        user_id="u-1",
        title="t",
        created_at="2026-05-30T00:00:00Z",
        last_message_at="2026-05-30T00:00:00Z",
        last_user_preview=long,
        model_default="m",
        message_count=0,
        archived=False,
    )
    assert len(chat.last_user_preview) == 120
