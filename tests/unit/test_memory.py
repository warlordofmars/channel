# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for AgentCore Memory hook + bootstrap helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from channel.agents import memory as memory_module
from channel.agents.memory import _payload_from_messages, get_or_create_memory


@pytest.fixture(autouse=True)
def _reset_memory_id_cache():
    """Module-level cache survives across tests; reset before each."""
    memory_module._memory_id_cache.clear()
    yield
    memory_module._memory_id_cache.clear()


def test_payload_from_messages_pairs_user_and_assistant_text():
    messages = [
        {"role": "user", "content": [{"text": "what's 2+2?"}]},
        {"role": "assistant", "content": [{"text": "4"}]},
    ]

    payload = _payload_from_messages(messages)

    assert payload == [
        {"conversational": {"role": "USER", "content": {"text": "what's 2+2?"}}},
        {"conversational": {"role": "ASSISTANT", "content": {"text": "4"}}},
    ]


def test_payload_from_messages_concatenates_multi_block_content():
    # Strands sometimes emits multiple text blocks within one message
    # (e.g. when a tool result is interleaved). Concatenate text blocks;
    # non-text blocks (toolUse, toolResult) are dropped — AgentCore's
    # v1 payload spec only accepts text.
    messages = [
        {
            "role": "assistant",
            "content": [
                {"text": "Let me check. "},
                {"toolUse": {"name": "calc", "input": {"x": 2}}},
                {"text": "The answer is 4."},
            ],
        },
    ]

    payload = _payload_from_messages(messages)

    assert payload == [
        {
            "conversational": {
                "role": "ASSISTANT",
                "content": {"text": "Let me check. The answer is 4."},
            },
        },
    ]


def test_payload_from_messages_raises_on_unknown_role():
    with pytest.raises(ValueError, match="unsupported role"):
        _payload_from_messages([{"role": "system", "content": [{"text": "..."}]}])


def test_get_or_create_memory_returns_cached_value_on_second_call():
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {
        "memorySummaries": [{"name": "channel-jc", "id": "channel-jc-A1B2"}]
    }

    with patch("channel.agents.memory.boto3.client", return_value=fake_control):
        first = get_or_create_memory("jc")
        second = get_or_create_memory("jc")

    assert first == "channel-jc-A1B2"
    assert second == "channel-jc-A1B2"
    fake_control.list_memories.assert_called_once()


def test_get_or_create_memory_finds_existing_by_name():
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {
        "memorySummaries": [
            {"name": "channel-prod", "id": "channel-prod-X1Y2"},
            {"name": "channel-jc", "id": "channel-jc-A1B2"},
            {"name": "other-app", "id": "other-Z3"},
        ]
    }

    with patch("channel.agents.memory.boto3.client", return_value=fake_control):
        assert get_or_create_memory("jc") == "channel-jc-A1B2"

    fake_control.create_memory.assert_not_called()


def test_get_or_create_memory_creates_when_absent():
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {"memorySummaries": []}
    fake_control.create_memory.return_value = {
        "memory": {"id": "channel-dev-NEW1", "name": "channel-dev"}
    }

    with patch("channel.agents.memory.boto3.client", return_value=fake_control):
        assert get_or_create_memory("dev") == "channel-dev-NEW1"

    fake_control.create_memory.assert_called_once()
    kwargs = fake_control.create_memory.call_args.kwargs
    assert kwargs["name"] == "channel-dev"


def test_get_or_create_memory_respects_override_env_var(monkeypatch):
    monkeypatch.setenv("STARTER_AGENTCORE_MEMORY_NAME", "preexisting-mem")
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {
        "memorySummaries": [{"name": "preexisting-mem", "id": "preexisting-mem-Q1"}]
    }

    with patch("channel.agents.memory.boto3.client", return_value=fake_control):
        assert get_or_create_memory("jc") == "preexisting-mem-Q1"
