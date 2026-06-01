# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for AgentCore Memory recall hook + helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.hooks.events import BeforeInvocationEvent

from channel.agents import recall as recall_module
from channel.agents.recall import (
    _RECALL_EVENT_TEXT_TRUNCATE,
    _RECALL_EVENTS_PER_SESSION,
    AgentCoreRecallHook,
    _format_recall_addendum,
    _iso_date,
)


@pytest.fixture(autouse=True)
def _reset_recall_cache():
    """Module-level cache survives across tests; reset before each."""
    recall_module._recall_cache.clear()
    yield
    recall_module._recall_cache.clear()


def _fake_before_event(user_text: str, system_text: str = "You are Channel.") -> MagicMock:
    """Build a BeforeInvocationEvent stand-in.

    Strands keeps the system prompt as a separate field on the Agent;
    ``event.messages`` is the conversation only (no synthetic system
    message). Pre-Phase 8a's Layer-3 fix mocked messages[0] as the
    system message, which masked the production bug where the addendum
    was appended to the user message instead of the system prompt.
    """
    msgs = [
        {"role": "user", "content": [{"text": user_text}]},
    ]
    fake_agent = MagicMock()
    fake_agent.messages = msgs
    fake_agent.chat_id = "chat-1"
    fake_agent.system_prompt = system_text
    fake_event = MagicMock()
    fake_event.agent = fake_agent
    fake_event.messages = msgs
    return fake_event


def test_format_recall_addendum_returns_empty_string_for_no_records():
    """No records → no addendum. The caller appends only if non-empty."""
    assert _format_recall_addendum([]) == ""


def test_format_recall_addendum_groups_records_by_session_with_date_header():
    # Records here use the normalized YYYY-MM-DD form that _get_or_fetch_records
    # emits via _iso_date; _format_recall_addendum receives pre-normalized data.
    records = [
        {
            "sessionId": "s1",
            "createdAt": "2026-05-31",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": "i love sage green"}}},
                {"conversational": {"role": "ASSISTANT", "content": {"text": "sage is great"}}},
            ],
        },
        {
            "sessionId": "s2",
            "createdAt": "2026-05-31",
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
            "createdAt": "2026-05-31",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": long_text}}},
            ],
        },
    ]
    result = _format_recall_addendum(records)
    # Positive shape: exactly _RECALL_EVENT_TEXT_TRUNCATE chars of A
    # followed by an ellipsis indicator.
    assert "A" * _RECALL_EVENT_TEXT_TRUNCATE + "..." in result
    assert "A" * (_RECALL_EVENT_TEXT_TRUNCATE + 1) not in result


def test_format_recall_addendum_skips_records_with_no_payload_text():
    records = [
        {
            "sessionId": "s1",
            "createdAt": "2026-05-31",
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
            "createdAt": "2026-05-31",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": "should not appear"}}},
            ],
        },
        {
            "sessionId": "s1",
            "createdAt": "2026-05-31",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": "valid"}}},
            ],
        },
    ]
    result = _format_recall_addendum(records)
    assert "should not appear" not in result
    assert "- You: valid" in result


def test_format_recall_addendum_handles_iso_string_createdAt():
    """createdAt as ISO string (the normalized boundary shape)."""
    records = [
        {
            "sessionId": "s1",
            "createdAt": "2026-05-31",  # already a YYYY-MM-DD from _iso_date
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": "hello"}}},
            ],
        },
    ]
    result = _format_recall_addendum(records)
    assert "Earlier conversation (2026-05-31)" in result


def test_iso_date_normalizes_datetime():
    """_iso_date handles datetime, datetime-naive, string, and None inputs."""
    from datetime import datetime, timezone

    # datetime → YYYY-MM-DD
    aware = datetime(2026, 5, 31, 22, 58, 39, tzinfo=timezone.utc)
    assert _iso_date(aware) == "2026-05-31"

    # Naive datetime → YYYY-MM-DD
    naive = datetime(2026, 5, 31, 22, 58, 39)
    assert _iso_date(naive) == "2026-05-31"

    # ISO-string passthrough (truncated to 10 chars)
    assert _iso_date("2026-05-31T22:58:39Z") == "2026-05-31"

    # None → empty string
    assert _iso_date(None) == ""


@pytest.mark.asyncio
async def test_get_or_fetch_records_normalizes_datetime_createdAt():
    """ListSessions returns datetime objects; _get_or_fetch_records must
    normalize them to ISO strings in the aggregated records."""
    from datetime import datetime, timezone

    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [
            {
                "sessionId": "prior-1",
                "createdAt": datetime(2026, 5, 31, 19, 0, 0, tzinfo=timezone.utc),
            },
        ],
    }
    fake_client.list_events.return_value = {
        "events": [
            {
                "sessionId": "prior-1",
                "eventTimestamp": datetime(2026, 5, 31, 19, 0, 30, tzinfo=timezone.utc),
                "payload": [
                    {"conversational": {"role": "USER", "content": {"text": "hi"}}},
                ],
            },
        ],
    }
    hook = AgentCoreRecallHook(
        memory_id="m-1",
        actor_id="a",
        client=fake_client,
    )
    event = _fake_before_event(user_text="anything")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        event.agent.chat_id = "current"
        await hook._on_before_invocation(event)

    sys_text = event.agent.system_prompt
    # Date header rendered correctly from datetime, not "TypeError" or empty.
    assert "Earlier conversation (2026-05-31)" in sys_text
    assert "- You: hi" in sys_text


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
                        {
                            "conversational": {
                                "role": "USER",
                                "content": {"text": "hello from prior-1"},
                            }
                        },
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
                        {
                            "conversational": {
                                "role": "USER",
                                "content": {"text": "hello from prior-2"},
                            }
                        },
                        {"conversational": {"role": "ASSISTANT", "content": {"text": "hi"}}},
                    ],
                },
            ],
        },
    ]
    hook = AgentCoreRecallHook(
        memory_id="m-1",
        actor_id="user_abc",
        client=fake_client,
    )
    event = _fake_before_event(user_text="anything")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        event.agent.chat_id = "current-chat"
        await hook._on_before_invocation(event)

    # ListSessions called once, scoped to actor.
    fake_client.list_sessions.assert_called_once_with(
        memoryId="m-1",
        actorId="user_abc",
    )
    # ListEvents called once per prior session, NOT for the current chat.
    assert fake_client.list_events.call_count == 2
    session_ids_queried = {
        call.kwargs["sessionId"] for call in fake_client.list_events.call_args_list
    }
    assert session_ids_queried == {"prior-1", "prior-2"}
    # System prompt (on the agent, not in event.messages) grew with
    # content from both prior sessions.
    sys_text = event.agent.system_prompt
    assert "hello from prior-1" in sys_text
    assert "hello from prior-2" in sys_text


@pytest.mark.asyncio
async def test_hook_caps_sessions_at_max():
    """8 sessions returned → only the 5 most-recent get ListEvents'd."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [
            {"sessionId": f"s{i}", "createdAt": f"2026-05-{30 - i:02d}T00:00:00Z"} for i in range(8)
        ],
    }
    fake_client.list_events.return_value = {"events": []}
    hook = AgentCoreRecallHook(
        memory_id="m",
        actor_id="a",
        client=fake_client,
    )
    event = _fake_before_event(user_text="anything")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        event.agent.chat_id = "not-in-list"
        await hook._on_before_invocation(event)

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
        memory_id="m",
        actor_id="a",
        client=fake_client,
    )
    event = _fake_before_event(user_text="anything")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        event.agent.chat_id = "not-in-list"
        await hook._on_before_invocation(event)

    call = fake_client.list_events.call_args
    assert call.kwargs["maxResults"] == _RECALL_EVENTS_PER_SESSION


@pytest.mark.asyncio
async def test_hook_emits_no_addendum_when_actor_has_no_prior_sessions():
    """0 prior sessions → no addendum, no ListEvents calls."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {"sessionSummaries": []}
    hook = AgentCoreRecallHook(
        memory_id="m",
        actor_id="a",
        client=fake_client,
    )
    event = _fake_before_event(
        user_text="hello",
        system_text="You are Channel.",
    )

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        event.agent.chat_id = "any"
        await hook._on_before_invocation(event)

    fake_client.list_events.assert_not_called()
    # System prompt unchanged.
    assert event.agent.system_prompt == "You are Channel."


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
        memory_id="m",
        actor_id="a",
        client=fake_client,
    )
    event = _fake_before_event(
        user_text="hello",
        system_text="You are Channel.",
    )

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        event.agent.chat_id = "current"
        await hook._on_before_invocation(event)

    fake_client.list_events.assert_not_called()
    assert event.agent.system_prompt == "You are Channel."


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
            event.agent.chat_id = "chat-1"
            await hook._on_before_invocation(event)

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
            event.agent.chat_id = "chat-1"
            await hook._on_before_invocation(event)

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
        evt_a = _fake_before_event("q1")
        evt_a.agent.chat_id = "A"
        await hook._on_before_invocation(evt_a)
        evt_b = _fake_before_event("q2")
        evt_b.agent.chat_id = "B"
        await hook._on_before_invocation(evt_b)
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
    original_sys = event.agent.system_prompt

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()) as mock_record:
        # MUST NOT raise.
        event.agent.chat_id = "chat-1"
        await hook._on_before_invocation(event)

    # System prompt untouched on failure.
    assert event.agent.system_prompt == original_sys
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
    event.agent.chat_id = "chat-1"
    await hook._on_before_invocation(event)
    fake_client.list_sessions.assert_not_called()


def test_hook_registers_async_callback():
    """Phase 8a Layer-3 fix (#97): the callback is now async so Strands'
    ``invoke_callbacks_async`` awaits it. The prior sync-wrapper +
    fire-and-forget pattern raced against the model invocation —
    Strands built the request payload (capturing
    ``agent.system_prompt``) before the async recall task had mutated
    it, so the addendum landed too late to affect the model's reply.
    """
    import inspect

    hook = AgentCoreRecallHook(memory_id="m", actor_id="a", client=MagicMock())
    registry = MagicMock()
    hook.register_hooks(registry)

    args, _kwargs = registry.add_callback.call_args
    callback = args[1]
    assert inspect.iscoroutinefunction(callback), (
        "Recall hook callback must be an async function so Strands "
        "awaits it before invoking the model. Sync + fire-and-forget "
        "races against the request build — see #97."
    )


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


def test_append_to_system_prompt_appends_to_existing_system_prompt():
    """Mutates ``event.agent.system_prompt`` in place, appending the
    addendum with a blank-line separator."""
    from channel.agents.recall import _append_to_system_prompt

    fake_agent = MagicMock()
    fake_agent.system_prompt = "You are Channel."
    fake_event = MagicMock()
    fake_event.agent = fake_agent

    _append_to_system_prompt(fake_event, "addendum text")

    assert fake_agent.system_prompt == "You are Channel.\n\naddendum text"


def test_append_to_system_prompt_sets_addendum_when_agent_has_no_system_prompt():
    """Defensive: if the agent's ``system_prompt`` is None / empty for
    any reason, the addendum becomes the entire system prompt rather
    than producing a leading-blank-line string."""
    from channel.agents.recall import _append_to_system_prompt

    fake_agent = MagicMock()
    fake_agent.system_prompt = None
    fake_event = MagicMock()
    fake_event.agent = fake_agent

    _append_to_system_prompt(fake_event, "addendum text")
    assert fake_agent.system_prompt == "addendum text"

    fake_agent.system_prompt = ""
    _append_to_system_prompt(fake_event, "addendum text")
    assert fake_agent.system_prompt == "addendum text"
