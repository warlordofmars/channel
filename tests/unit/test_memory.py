# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for AgentCore Memory hook + bootstrap helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.hooks.events import AfterInvocationEvent

from channel.agents import memory as memory_module
from channel.agents.memory import (
    AgentCoreMemoryHook,
    _payload_from_messages,
    get_or_create_memory,
)


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


# ---------------------------------------------------------------------------
# AgentCoreMemoryHook
# ---------------------------------------------------------------------------


def _fake_event_with_messages(messages: list[dict[str, Any]]) -> MagicMock:
    fake_agent = MagicMock()
    fake_agent.messages = messages
    fake_event = MagicMock()
    fake_event.agent = fake_agent
    return fake_event


def test_hook_registers_after_invocation_callback():
    hook = AgentCoreMemoryHook(
        memory_id="m-1",
        actor_id="user-abc",
        session_id="chat-xyz",
    )

    registry = MagicMock()
    hook.register_hooks(registry)

    registry.add_callback.assert_called_once()
    args, _ = registry.add_callback.call_args
    assert args[0] is AfterInvocationEvent


@pytest.mark.asyncio
async def test_hook_writes_create_event_on_after_invocation():
    fake_client = MagicMock()
    fake_client.create_event.return_value = {"event": {"eventId": "evt-1"}}

    hook = AgentCoreMemoryHook(
        memory_id="m-1",
        actor_id="user-abc",
        session_id="chat-xyz",
        client=fake_client,
    )

    event = _fake_event_with_messages(
        [
            {"role": "user", "content": [{"text": "hi"}]},
            {"role": "assistant", "content": [{"text": "hello"}]},
        ]
    )

    with patch(
        "channel.agents.memory.record_memory_write_outcome",
        new=AsyncMock(),
    ) as mock_record:
        await hook._on_after_invocation_async(event)

    fake_client.create_event.assert_called_once()
    kwargs = fake_client.create_event.call_args.kwargs
    assert kwargs["memoryId"] == "m-1"
    assert kwargs["actorId"] == "user-abc"
    assert kwargs["sessionId"] == "chat-xyz"
    assert len(kwargs["payload"]) == 2
    assert kwargs["payload"][0]["conversational"]["content"]["text"] == "hi"
    mock_record.assert_awaited_once_with(success=True)


@pytest.mark.asyncio
async def test_hook_swallows_exceptions_and_emits_failure_metric():
    fake_client = MagicMock()
    fake_client.create_event.side_effect = RuntimeError("agentcore down")

    hook = AgentCoreMemoryHook(
        memory_id="m-1",
        actor_id="a",
        session_id="s",
        client=fake_client,
    )

    event = _fake_event_with_messages(
        [
            {"role": "user", "content": [{"text": "hi"}]},
            {"role": "assistant", "content": [{"text": "hello"}]},
        ]
    )

    with patch(
        "channel.agents.memory.record_memory_write_outcome",
        new=AsyncMock(),
    ) as mock_record:
        # MUST NOT raise — write failures are swallowed.
        await hook._on_after_invocation_async(event)

    mock_record.assert_awaited_once_with(success=False)


def test_hook_after_invocation_fires_and_forgets():
    """The sync callback must fire-and-forget via asyncio.create_task,
    not block the agent loop."""
    hook = AgentCoreMemoryHook(memory_id="m", actor_id="a", session_id="s")

    event = _fake_event_with_messages(
        [
            {"role": "user", "content": [{"text": "hi"}]},
            {"role": "assistant", "content": [{"text": "hello"}]},
        ]
    )

    scheduled: list[Any] = []

    def _record(coro: Any) -> Any:
        scheduled.append(coro)
        coro.close()  # silence "coroutine was never awaited"
        return MagicMock()

    with patch("channel.agents.memory.asyncio.create_task", side_effect=_record):
        hook._on_after_invocation(event)

    assert len(scheduled) == 1


def test_hook_default_client_is_bedrock_agentcore():
    """Sanity: the default client is bedrock-agentcore (not control plane)."""
    with patch("channel.agents.memory.boto3.client") as mock_client:
        AgentCoreMemoryHook(memory_id="m", actor_id="a", session_id="s")

    mock_client.assert_called_once_with("bedrock-agentcore")
