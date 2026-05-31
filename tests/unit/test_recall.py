# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for AgentCore Memory recall hook + helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.hooks.events import BeforeInvocationEvent

from channel.agents import recall as recall_module
from channel.agents.recall import (
    _RECALL_TOP_K,
    AgentCoreRecallHook,
    _format_recall_addendum,
)


@pytest.fixture(autouse=True)
def _reset_recall_cache():
    """Module-level cache survives across tests; reset before each."""
    recall_module._recall_cache.clear()
    yield
    recall_module._recall_cache.clear()


def _fake_before_event(user_text: str, system_text: str = "You are Channel.") -> MagicMock:
    """Build a BeforeInvocationEvent stand-in with mutable messages."""
    msgs = [
        {"role": "system", "content": [{"text": system_text}]},
        {"role": "user", "content": [{"text": user_text}]},
    ]
    fake_agent = MagicMock()
    fake_agent.messages = msgs
    fake_agent.chat_id = "chat-1"
    fake_event = MagicMock()
    fake_event.agent = fake_agent
    fake_event.messages = msgs
    return fake_event


def test_format_recall_addendum_returns_empty_string_for_no_records():
    """No records → no addendum. The caller appends only if non-empty."""
    assert _format_recall_addendum([]) == ""


def test_format_recall_addendum_renders_records_as_markdown_bullets():
    records = [
        {"content": {"text": "User builds chess engines"}, "score": 0.92},
        {"content": {"text": "Favourite colour: sage green"}, "score": 0.85},
    ]

    result = _format_recall_addendum(records)

    # Heading + each record on its own bullet.
    assert "## What I remember about previous conversations" in result
    assert "- User builds chess engines" in result
    assert "- Favourite colour: sage green" in result


def test_format_recall_addendum_skips_records_missing_text():
    """Defensive: AgentCore responses without ``content.text`` are dropped
    so a malformed payload can't corrupt the prompt."""
    records = [
        {"content": {"text": "valid record"}, "score": 0.9},
        {"content": {}, "score": 0.85},  # no text key
        {"score": 0.8},  # no content key
        {"content": {"text": ""}, "score": 0.75},  # empty text
    ]

    result = _format_recall_addendum(records)

    assert "- valid record" in result
    # Heading appears once, no empty bullets.
    assert result.count("- ") == 1


# ---------------------------------------------------------------------------
# AgentCoreRecallHook
# ---------------------------------------------------------------------------


def test_hook_registers_before_invocation_callback_only():
    """Recall hook subscribes ONLY to BeforeInvocationEvent — NOT
    AfterInvocationEvent (that's the write hook's job)."""
    hook = AgentCoreRecallHook(
        memory_id="m-1",
        actor_id="alice",
        client=MagicMock(),
    )
    registry = MagicMock()
    hook.register_hooks(registry)

    registry.add_callback.assert_called_once()
    args, _ = registry.add_callback.call_args
    assert args[0] is BeforeInvocationEvent


@pytest.mark.asyncio
async def test_hook_injects_records_into_system_prompt_on_first_turn():
    fake_client = MagicMock()
    fake_client.retrieve_memory_records.return_value = {
        "memoryRecordSummaries": [
            {"content": {"text": "User builds chess engines"}, "score": 0.92},
            {"content": {"text": "Likes sage green"}, "score": 0.81},
        ],
    }
    hook = AgentCoreRecallHook(
        memory_id="m-1",
        actor_id="alice_example_com",
        client=fake_client,
    )
    event = _fake_before_event(user_text="what colour do i like?")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()) as mock_record:
        await hook._on_before_invocation_async(event, chat_id="chat-1")

    sys_text = event.messages[0]["content"][0]["text"]
    assert "## What I remember about previous conversations" in sys_text
    assert "chess engines" in sys_text
    assert "sage green" in sys_text

    fake_client.retrieve_memory_records.assert_called_once()
    kwargs = fake_client.retrieve_memory_records.call_args.kwargs
    assert kwargs["memoryId"] == "m-1"
    # AgentCore takes ``namespace`` (a path prefix encoding the actor),
    # NOT ``actorId``. ``/actors/<actorId>`` is the prefix that matches
    # all strategies' records for this actor.
    assert kwargs["namespace"] == "/actors/alice_example_com"
    assert "actorId" not in kwargs
    assert kwargs["searchCriteria"]["searchQuery"] == "what colour do i like?"
    assert kwargs["searchCriteria"]["topK"] == _RECALL_TOP_K
    mock_record.assert_awaited_once_with(success=True)


@pytest.mark.asyncio
async def test_hook_filters_records_below_score_threshold():
    fake_client = MagicMock()
    fake_client.retrieve_memory_records.return_value = {
        "memoryRecordSummaries": [
            {"content": {"text": "Highly relevant"}, "score": 0.95},
            {"content": {"text": "Borderline"}, "score": 0.65},  # below threshold
            {"content": {"text": "Also high"}, "score": 0.82},
        ],
    }
    hook = AgentCoreRecallHook(
        memory_id="m",
        actor_id="a",
        client=fake_client,
    )
    event = _fake_before_event(user_text="...")
    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(event, chat_id="chat-1")

    sys_text = event.messages[0]["content"][0]["text"]
    assert "Highly relevant" in sys_text
    assert "Also high" in sys_text
    assert "Borderline" not in sys_text


@pytest.mark.asyncio
async def test_hook_reuses_cached_records_for_next_5_turns():
    fake_client = MagicMock()
    fake_client.retrieve_memory_records.return_value = {
        "memoryRecordSummaries": [
            {"content": {"text": "Cached record"}, "score": 0.9},
        ],
    }
    hook = AgentCoreRecallHook(
        memory_id="m",
        actor_id="a",
        client=fake_client,
    )
    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        for _ in range(5):
            event = _fake_before_event(user_text="anything")
            await hook._on_before_invocation_async(event, chat_id="chat-1")

    # Only ONE RPC across 5 turns — turns 2-5 are cache hits.
    fake_client.retrieve_memory_records.assert_called_once()


@pytest.mark.asyncio
async def test_hook_refreshes_cache_after_5_turns():
    fake_client = MagicMock()
    fake_client.retrieve_memory_records.return_value = {
        "memoryRecordSummaries": [{"content": {"text": "x"}, "score": 0.9}],
    }
    hook = AgentCoreRecallHook(
        memory_id="m",
        actor_id="a",
        client=fake_client,
    )
    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        for _ in range(6):  # 1 cold + 4 cached + 1 refresh
            event = _fake_before_event(user_text="anything")
            await hook._on_before_invocation_async(event, chat_id="chat-1")

    assert fake_client.retrieve_memory_records.call_count == 2


@pytest.mark.asyncio
async def test_hook_per_chat_cache_keys():
    """Cache is keyed by ``(actor_id, chat_id)`` — chat A's cache must
    NOT serve chat B."""
    fake_client = MagicMock()
    fake_client.retrieve_memory_records.return_value = {"memoryRecordSummaries": []}
    hook = AgentCoreRecallHook(
        memory_id="m",
        actor_id="a",
        client=fake_client,
    )
    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(
            _fake_before_event("q1"),
            chat_id="A",
        )
        await hook._on_before_invocation_async(
            _fake_before_event("q2"),
            chat_id="B",
        )
    # Two distinct chats → two cold-cache RPCs.
    assert fake_client.retrieve_memory_records.call_count == 2


@pytest.mark.asyncio
async def test_hook_swallows_retrieve_failures_and_emits_failure_metric():
    fake_client = MagicMock()
    fake_client.retrieve_memory_records.side_effect = RuntimeError("agentcore down")
    hook = AgentCoreRecallHook(
        memory_id="m",
        actor_id="a",
        client=fake_client,
    )
    event = _fake_before_event(user_text="...")
    original_sys = event.messages[0]["content"][0]["text"]

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()) as mock_record:
        # MUST NOT raise.
        await hook._on_before_invocation_async(event, chat_id="chat-1")

    # System prompt untouched on failure.
    assert event.messages[0]["content"][0]["text"] == original_sys
    mock_record.assert_awaited_once_with(success=False)


@pytest.mark.asyncio
async def test_hook_short_circuits_when_kill_switch_off(monkeypatch):
    monkeypatch.setenv("STARTER_RECALL_ENABLED", "0")
    fake_client = MagicMock()
    hook = AgentCoreRecallHook(
        memory_id="m",
        actor_id="a",
        client=fake_client,
    )
    event = _fake_before_event(user_text="...")
    await hook._on_before_invocation_async(event, chat_id="chat-1")
    fake_client.retrieve_memory_records.assert_not_called()


def test_hook_before_invocation_fires_and_forgets():
    """Sync callback must fire-and-forget via asyncio.create_task and
    hold a strong ref to the task (Sonar python:S7502 from 7c)."""
    hook = AgentCoreRecallHook(memory_id="m", actor_id="a", client=MagicMock())
    event = _fake_before_event(user_text="anything")

    scheduled: list[Any] = []
    fake_task = MagicMock(name="fake_task")

    def _record(coro: Any) -> Any:
        scheduled.append(coro)
        coro.close()
        return fake_task

    with patch("channel.agents.recall.asyncio.create_task", side_effect=_record):
        hook._on_before_invocation(event)

    assert len(scheduled) == 1
    assert fake_task in hook._pending_recalls
    fake_task.add_done_callback.assert_called_once_with(hook._pending_recalls.discard)


def test_hook_default_client_is_bedrock_agentcore():
    """Sanity: default client is bedrock-agentcore (not control plane)."""
    with patch("channel.agents.recall.boto3.client") as mock_client:
        AgentCoreRecallHook(memory_id="m", actor_id="a")
    mock_client.assert_called_once_with("bedrock-agentcore")


# ---------------------------------------------------------------------------
# Defensive helpers — branches not exercised by hook integration tests
# ---------------------------------------------------------------------------


def test_extract_user_message_returns_empty_when_no_user_turn():
    """Defensive: a malformed event with no user turn returns ``""``
    rather than crashing the hook."""
    from channel.agents.recall import _extract_user_message

    fake_event = MagicMock()
    fake_event.messages = [
        {"role": "system", "content": [{"text": "..."}]},
        {"role": "assistant", "content": [{"text": "..."}]},
    ]
    assert _extract_user_message(fake_event) == ""


def test_append_to_system_prompt_creates_text_block_when_absent():
    """Defensive: when the system message has no text content block,
    create one rather than mutating an unrelated block."""
    from channel.agents.recall import _append_to_system_prompt

    fake_event = MagicMock()
    fake_event.messages = [
        {"role": "system", "content": []},  # no blocks
        {"role": "user", "content": [{"text": "hi"}]},
    ]
    _append_to_system_prompt(fake_event, "addendum text")
    assert fake_event.messages[0]["content"] == [{"text": "addendum text"}]
