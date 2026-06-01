# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for AgentCore Memory recall hook + helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.hooks.events import BeforeInvocationEvent

from channel.agents import recall as recall_module
from channel.agents.recall import (
    _RECALL_EVENTS_PER_SESSION,
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


def test_format_recall_addendum_groups_records_by_session_with_date_header():
    records = [
        {
            "sessionId": "s1",
            "createdAt": "2026-05-31T19:00:00Z",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": "i love sage green"}}},
                {"conversational": {"role": "ASSISTANT", "content": {"text": "sage is great"}}},
            ],
        },
        {
            "sessionId": "s2",
            "createdAt": "2026-05-31T18:00:00Z",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": "building Nightfall"}}},
                {"conversational": {"role": "ASSISTANT", "content": {"text": "cool engine name"}}},
            ],
        },
    ]
    result = _format_recall_addendum(records)

    assert "## What we've talked about before" in result
    # Date header per session.
    assert "Earlier conversation (2026-05-31)" in result
    # Two session blocks.
    assert result.count("Earlier conversation") == 2
    # Role mapping.
    assert "- You: i love sage green" in result
    assert "- Me: sage is great" in result
    assert "- You: building Nightfall" in result
    assert "- Me: cool engine name" in result


def test_format_recall_addendum_truncates_long_text():
    long_text = "A" * 500
    records = [
        {
            "sessionId": "s1",
            "createdAt": "2026-05-31T00:00:00Z",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": long_text}}},
            ],
        },
    ]
    result = _format_recall_addendum(records)
    # Each event text capped at _RECALL_EVENT_TEXT_TRUNCATE chars
    # (plus a trailing ellipsis indicator).
    assert "A" * 500 not in result
    assert "..." in result


def test_format_recall_addendum_skips_records_with_no_payload_text():
    records = [
        {
            "sessionId": "s1",
            "createdAt": "2026-05-31T00:00:00Z",
            "payload": [
                {"conversational": {"role": "USER", "content": {}}},  # no text
                {"conversational": {"role": "ASSISTANT", "content": {"text": ""}}},
            ],
        },
    ]
    # All payload entries unusable → no bullets → session block dropped.
    result = _format_recall_addendum(records)
    assert result == ""


def test_format_recall_addendum_skips_records_missing_session_id():
    """Defensive: records with no ``sessionId`` key are silently dropped."""
    records = [
        # No sessionId — should be skipped entirely.
        {
            "createdAt": "2026-05-31T00:00:00Z",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": "should not appear"}}},
            ],
        },
        {
            "sessionId": "s1",
            "createdAt": "2026-05-31T01:00:00Z",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": "valid"}}},
            ],
        },
    ]
    result = _format_recall_addendum(records)
    assert "should not appear" not in result
    assert "- You: valid" in result


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
async def test_hook_lists_sessions_and_events_excluding_current_chat():
    """Recall iterates this actor's sessions, drops the current chat,
    fetches the last 2 events per session, returns the aggregate."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [
            {"sessionId": "current-chat", "createdAt": "2026-05-31T20:00:00Z"},
            {"sessionId": "prior-1", "createdAt": "2026-05-31T19:00:00Z"},
            {"sessionId": "prior-2", "createdAt": "2026-05-31T18:00:00Z"},
        ],
    }
    fake_client.list_events.side_effect = [
        {
            "events": [
                {
                    "sessionId": "prior-1",
                    "eventTimestamp": "2026-05-31T19:00:30Z",
                    "payload": [
                        {"conversational": {"role": "USER", "content": {"text": "hello from prior-1"}}},
                        {"conversational": {"role": "ASSISTANT", "content": {"text": "hi"}}},
                    ],
                },
            ],
        },
        {
            "events": [
                {
                    "sessionId": "prior-2",
                    "eventTimestamp": "2026-05-31T18:00:30Z",
                    "payload": [
                        {"conversational": {"role": "USER", "content": {"text": "hello from prior-2"}}},
                        {"conversational": {"role": "ASSISTANT", "content": {"text": "hi"}}},
                    ],
                },
            ],
        },
    ]
    hook = AgentCoreRecallHook(
        memory_id="m-1", actor_id="user_abc", client=fake_client,
    )
    event = _fake_before_event(user_text="anything")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(event, chat_id="current-chat")

    # ListSessions called once, scoped to actor.
    fake_client.list_sessions.assert_called_once_with(
        memoryId="m-1", actorId="user_abc",
    )
    # ListEvents called once per prior session, NOT for the current chat.
    assert fake_client.list_events.call_count == 2
    session_ids_queried = {
        call.kwargs["sessionId"] for call in fake_client.list_events.call_args_list
    }
    assert session_ids_queried == {"prior-1", "prior-2"}
    # System prompt grew with content from both prior sessions.
    sys_text = event.messages[0]["content"][0]["text"]
    assert "hello from prior-1" in sys_text
    assert "hello from prior-2" in sys_text


@pytest.mark.asyncio
async def test_hook_caps_sessions_at_max():
    """8 sessions returned → only the 5 most-recent get ListEvents'd."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [
            {"sessionId": f"s{i}", "createdAt": f"2026-05-{30 - i:02d}T00:00:00Z"}
            for i in range(8)
        ],
    }
    fake_client.list_events.return_value = {"events": []}
    hook = AgentCoreRecallHook(
        memory_id="m", actor_id="a", client=fake_client,
    )
    event = _fake_before_event(user_text="anything")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(event, chat_id="not-in-list")

    assert fake_client.list_events.call_count == 5  # _RECALL_MAX_SESSIONS


@pytest.mark.asyncio
async def test_hook_caps_events_per_session():
    """ListEvents is called with maxResults=_RECALL_EVENTS_PER_SESSION."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [{"sessionId": "s1", "createdAt": "2026-05-31T00:00:00Z"}],
    }
    fake_client.list_events.return_value = {"events": []}
    hook = AgentCoreRecallHook(
        memory_id="m", actor_id="a", client=fake_client,
    )
    event = _fake_before_event(user_text="anything")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(event, chat_id="not-in-list")

    call = fake_client.list_events.call_args
    assert call.kwargs["maxResults"] == _RECALL_EVENTS_PER_SESSION


@pytest.mark.asyncio
async def test_hook_emits_no_addendum_when_actor_has_no_prior_sessions():
    """0 prior sessions → no addendum, no ListEvents calls."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {"sessionSummaries": []}
    hook = AgentCoreRecallHook(
        memory_id="m", actor_id="a", client=fake_client,
    )
    event = _fake_before_event(
        user_text="hello", system_text="You are Channel.",
    )

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(event, chat_id="any")

    fake_client.list_events.assert_not_called()
    # System prompt unchanged.
    assert event.messages[0]["content"][0]["text"] == "You are Channel."


@pytest.mark.asyncio
async def test_hook_emits_no_addendum_when_only_session_is_current_chat():
    """1 session = the current chat → exclusion leaves 0 candidates,
    no addendum, no ListEvents calls."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [
            {"sessionId": "current", "createdAt": "2026-05-31T00:00:00Z"},
        ],
    }
    hook = AgentCoreRecallHook(
        memory_id="m", actor_id="a", client=fake_client,
    )
    event = _fake_before_event(
        user_text="hello", system_text="You are Channel.",
    )

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(event, chat_id="current")

    fake_client.list_events.assert_not_called()
    assert event.messages[0]["content"][0]["text"] == "You are Channel."


@pytest.mark.asyncio
async def test_hook_reuses_cached_records_for_next_5_turns():
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {"sessionSummaries": []}
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
    fake_client.list_sessions.assert_called_once()


@pytest.mark.asyncio
async def test_hook_refreshes_cache_after_5_turns():
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {"sessionSummaries": []}
    hook = AgentCoreRecallHook(
        memory_id="m",
        actor_id="a",
        client=fake_client,
    )
    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        for _ in range(6):  # 1 cold + 4 cached + 1 refresh
            event = _fake_before_event(user_text="anything")
            await hook._on_before_invocation_async(event, chat_id="chat-1")

    assert fake_client.list_sessions.call_count == 2


@pytest.mark.asyncio
async def test_hook_per_chat_cache_keys():
    """Cache is keyed by ``(actor_id, chat_id)`` — chat A's cache must
    NOT serve chat B."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {"sessionSummaries": []}
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
    assert fake_client.list_sessions.call_count == 2


@pytest.mark.asyncio
async def test_hook_swallows_list_failures_and_emits_failure_metric():
    fake_client = MagicMock()
    fake_client.list_sessions.side_effect = RuntimeError("agentcore down")
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
    fake_client.list_sessions.assert_not_called()


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


def test_append_to_system_prompt_is_noop_when_messages_empty():
    """Defensive: Strands types ``event.messages`` as ``list[Message] | None``.
    If None or empty, the helper short-circuits rather than indexing."""
    from channel.agents.recall import _append_to_system_prompt

    fake_event_none = MagicMock()
    fake_event_none.messages = None
    _append_to_system_prompt(fake_event_none, "anything")  # MUST NOT raise

    fake_event_empty = MagicMock()
    fake_event_empty.messages = []
    _append_to_system_prompt(fake_event_empty, "anything")  # MUST NOT raise
