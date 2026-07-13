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
    _sanitize_actor_id,
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


def test_payload_from_messages_invariant_drops_tool_use_blocks():
    """INVARIANT: Tool payloads (toolUse / toolResult blocks) never
    persist to AgentCore Memory. The invariant is about block DICT KEYS
    in the produced payload, not about free-text content — so a
    recursive structural walk is used rather than a ``json.dumps``
    substring check (which would false-positive on legitimate
    conversational text that happens to mention 'toolUse' /
    'toolResult'). The fixture emits both block types interleaved with
    text and the walker asserts neither key leaks at any depth."""
    messages = [
        {
            "role": "assistant",
            "content": [
                {"text": "Let me check. "},
                {"toolUse": {"name": "calc", "input": {"x": 2}}},
                {"text": "The answer is 4."},
            ],
        },
        {
            "role": "user",
            "content": [
                {"toolResult": {"name": "calc", "output": {"y": 4}}},
                {"text": "Thanks."},
            ],
        },
    ]

    payload = _payload_from_messages(messages)

    # 1. Text concatenation still works
    assert payload[0]["conversational"]["content"]["text"] == "Let me check. The answer is 4."
    assert payload[1]["conversational"]["content"]["text"] == "Thanks."

    # 2. Invariant: NO toolUse or toolResult key appears anywhere in payload
    _assert_no_block_keys(payload)


def _assert_no_block_keys(
    node: Any, *, forbidden: tuple[str, ...] = ("toolUse", "toolResult")
) -> None:
    """Recursively assert that no dict key in ``node`` equals any of the
    forbidden strings. Traverses dicts and lists; leaf values (strings,
    ints, etc.) are not inspected — the invariant is about block keys
    in the payload structure, not text content."""
    if isinstance(node, dict):
        for key, value in node.items():
            assert key not in forbidden, f"forbidden key {key!r} present in payload"
            _assert_no_block_keys(value, forbidden=forbidden)
    elif isinstance(node, list):
        for item in node:
            _assert_no_block_keys(item, forbidden=forbidden)


def test_payload_from_messages_raises_on_unknown_role():
    with pytest.raises(ValueError, match="unsupported role"):
        _payload_from_messages([{"role": "system", "content": [{"text": "..."}]}])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Email-form JWT subs (the auth-bypass + Google OAuth shape).
        ("alice@example.com", "alice_example_com"),
        ("e2e-7c-1234-abc-a@example.com", "e2e-7c-1234-abc-a_example_com"),
        # Already-safe inputs pass through unchanged.
        ("user-abc-123", "user-abc-123"),
        ("workspace_1/user_42", "workspace_1/user_42"),
        # Sub-second edge case: ``+`` plus-addressing.
        ("alice+tag@example.com", "alice_tag_example_com"),
    ],
)
def test_sanitize_actor_id_replaces_disallowed_chars(raw: str, expected: str):
    assert _sanitize_actor_id(raw) == expected


def test_get_or_create_memory_returns_cached_value_on_second_call():
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {
        "memories": [{"id": "channel_jc-A1B2C3D4"}],
    }

    with patch("channel.agents.memory.boto3.client", return_value=fake_control):
        first = get_or_create_memory("jc")
        second = get_or_create_memory("jc")

    assert first == "channel_jc-A1B2C3D4"
    assert second == "channel_jc-A1B2C3D4"
    fake_control.list_memories.assert_called_once()


def test_get_or_create_memory_finds_existing_by_name_prefix():
    """The Memory id is ``{name}-{8-char-suffix}``. Match by prefix."""
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {
        "memories": [
            {"id": "channel_prod-X1Y2Z3W4"},
            {"id": "channel_jc-A1B2C3D4"},
            {"id": "other_app-Q9R8S7T6"},
        ],
    }

    with patch("channel.agents.memory.boto3.client", return_value=fake_control):
        assert get_or_create_memory("jc") == "channel_jc-A1B2C3D4"

    fake_control.create_memory.assert_not_called()


def test_get_or_create_memory_does_not_match_unrelated_prefix():
    """``channel_jc`` should NOT match ``channel_jcsmith-…`` (different name)."""
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {
        "memories": [{"id": "channel_jcsmith-AAAA1111"}],
    }
    fake_control.create_memory.return_value = {"memory": {"id": "channel_jc-NEW9NEW9"}}
    fake_control.get_memory.return_value = {"memory": {"status": "ACTIVE"}}

    with patch("channel.agents.memory.boto3.client", return_value=fake_control):
        assert get_or_create_memory("jc") == "channel_jc-NEW9NEW9"

    fake_control.create_memory.assert_called_once()


def test_get_or_create_memory_creates_when_absent():
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {"memories": []}
    fake_control.create_memory.return_value = {
        "memory": {"id": "channel_dev-NEW1NEW1"},
    }
    # Phase 7d: bootstrap polls get_memory until status=ACTIVE.
    fake_control.get_memory.return_value = {
        "memory": {"status": "ACTIVE"},
    }

    with patch("channel.agents.memory.boto3.client", return_value=fake_control):
        assert get_or_create_memory("dev") == "channel_dev-NEW1NEW1"

    fake_control.create_memory.assert_called_once()
    kwargs = fake_control.create_memory.call_args.kwargs
    assert kwargs["name"] == "channel_dev"
    # Phase 8a: SemanticMemoryStrategy backed out — recall reads raw
    # events via ListSessions + ListEvents instead. Regression guard:
    # ensure the strategy is NOT reintroduced accidentally.
    assert kwargs["memoryStrategies"] == [], (
        "Phase 8a backed out SemanticMemoryStrategy; recall reads raw "
        "events via ListSessions + ListEvents instead."
    )


def test_get_or_create_memory_polls_until_active(monkeypatch):
    """Phase 7d: newly-created memories enter CREATING; poll until ACTIVE
    before returning so the first chat doesn't lose events to validation
    errors."""
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {"memories": []}
    fake_control.create_memory.return_value = {
        "memory": {"id": "channel_dev-NEW1NEW1"},
    }
    # First two get_memory calls return CREATING, third returns ACTIVE.
    fake_control.get_memory.side_effect = [
        {"memory": {"status": "CREATING"}},
        {"memory": {"status": "CREATING"}},
        {"memory": {"status": "ACTIVE"}},
    ]
    # Skip the sleep so the test is fast.
    monkeypatch.setattr("channel.agents.memory.time.sleep", lambda _s: None)

    with patch("channel.agents.memory.boto3.client", return_value=fake_control):
        assert get_or_create_memory("dev") == "channel_dev-NEW1NEW1"

    assert fake_control.get_memory.call_count == 3


def test_get_or_create_memory_raises_when_polling_times_out(monkeypatch):
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {"memories": []}
    fake_control.create_memory.return_value = {
        "memory": {"id": "channel_dev-STUCK"},
    }
    fake_control.get_memory.return_value = {"memory": {"status": "CREATING"}}
    # Force the timeout to fire on the next monotonic call.
    times = iter([0.0, 1e9])  # second call exceeds deadline
    monkeypatch.setattr("channel.agents.memory.time.monotonic", lambda: next(times))
    monkeypatch.setattr("channel.agents.memory.time.sleep", lambda _s: None)

    with (
        patch("channel.agents.memory.boto3.client", return_value=fake_control),
        pytest.raises(TimeoutError, match="did not reach ACTIVE"),
    ):
        get_or_create_memory("dev")


def test_get_or_create_memory_raises_when_terminal_status(monkeypatch):
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {"memories": []}
    fake_control.create_memory.return_value = {
        "memory": {"id": "channel_dev-BAD"},
    }
    fake_control.get_memory.return_value = {"memory": {"status": "FAILED"}}
    monkeypatch.setattr("channel.agents.memory.time.sleep", lambda _s: None)

    with (
        patch("channel.agents.memory.boto3.client", return_value=fake_control),
        pytest.raises(RuntimeError, match="terminal status 'FAILED'"),
    ):
        get_or_create_memory("dev")


def test_get_or_create_memory_respects_override_env_var(monkeypatch):
    monkeypatch.setenv("STARTER_AGENTCORE_MEMORY_NAME", "preexisting_mem")
    fake_control = MagicMock()
    fake_control.list_memories.return_value = {
        "memories": [{"id": "preexisting_mem-Q1R2S3T4"}],
    }

    with patch("channel.agents.memory.boto3.client", return_value=fake_control):
        assert get_or_create_memory("jc") == "preexisting_mem-Q1R2S3T4"


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
        client=MagicMock(),  # Avoid real boto3.client (no AWS region in CI).
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
    not block the agent loop. The task must also be held in
    ``_pending_writes`` so the GC can't reclaim it mid-flight
    (Sonar python:S7502)."""
    hook = AgentCoreMemoryHook(
        memory_id="m",
        actor_id="a",
        session_id="s",
        client=MagicMock(),  # Avoid real boto3.client (no AWS region in CI).
    )

    event = _fake_event_with_messages(
        [
            {"role": "user", "content": [{"text": "hi"}]},
            {"role": "assistant", "content": [{"text": "hello"}]},
        ]
    )

    scheduled: list[Any] = []
    fake_task = MagicMock(name="fake_task")

    def _record(coro: Any) -> Any:
        scheduled.append(coro)
        coro.close()  # silence "coroutine was never awaited"
        return fake_task

    with patch("channel.agents.memory.asyncio.create_task", side_effect=_record):
        hook._on_after_invocation(event)

    assert len(scheduled) == 1
    # Strong reference held + completion handler wired to drop it.
    assert fake_task in hook._pending_writes
    fake_task.add_done_callback.assert_called_once_with(
        hook._pending_writes.discard,
    )


def test_hook_default_client_is_bedrock_agentcore():
    """Sanity: the default client is bedrock-agentcore (not control plane)."""
    with patch("channel.agents.memory.boto3.client") as mock_client:
        AgentCoreMemoryHook(memory_id="m", actor_id="a", session_id="s")

    mock_client.assert_called_once_with("bedrock-agentcore")


@pytest.mark.asyncio
async def test_hook_sanitizes_actor_id_passed_to_create_event():
    """Email-form actor_ids must be sanitized before reaching AgentCore."""
    fake_client = MagicMock()
    hook = AgentCoreMemoryHook(
        memory_id="m-1",
        actor_id="alice@example.com",  # contains @ + . — AgentCore rejects
        session_id="chat-xyz",
        client=fake_client,
    )

    event = _fake_event_with_messages(
        [
            {"role": "user", "content": [{"text": "hi"}]},
            {"role": "assistant", "content": [{"text": "hello"}]},
        ]
    )

    with patch("channel.agents.memory.record_memory_write_outcome", new=AsyncMock()):
        await hook._on_after_invocation_async(event)

    kwargs = fake_client.create_event.call_args.kwargs
    assert kwargs["actorId"] == "alice_example_com"


@pytest.mark.asyncio
async def test_write_meta_event_calls_create_event_with_meta_prefix():
    """[meta] synthetic ASSISTANT messages flow through CreateEvent
    via the existing boto3 client. The '[meta]' prefix tags the event
    for the recall hook to render distinctly.

    ``write_meta_event`` itself is a sync fire-and-forget entry point
    (mirrors ``_on_after_invocation``); the boto3 call runs in a
    thread via ``asyncio.to_thread`` so it doesn't block the event
    loop during ``agent.stream_async``. Drain the pending task with
    ``asyncio.sleep(0)`` before asserting.
    """
    import asyncio as _asyncio

    captured: dict[str, Any] = {}

    class FakeClient:
        def create_event(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {}

    hook = AgentCoreMemoryHook(
        memory_id="mem-x",
        actor_id="user-1",
        session_id="chat-1",
        client=FakeClient(),
    )
    hook.write_meta_event("used current_time to get current UTC time")

    # Drain the create_task'd write — to_thread completes on a worker
    # thread, so yield until the pending set drains.
    while hook._pending_writes:
        await _asyncio.sleep(0)

    assert captured["memoryId"] == "mem-x"
    assert captured["actorId"] == "user-1"
    assert captured["sessionId"] == "chat-1"
    # The synthetic ASSISTANT message text starts with [meta]
    payload = captured["payload"]
    assert payload[0]["conversational"]["role"] == "ASSISTANT"
    assert payload[0]["conversational"]["content"]["text"].startswith("[meta] ")


@pytest.mark.asyncio
async def test_write_meta_event_swallows_client_exceptions():
    """Fail-soft contract: write_meta_event must not raise even when the
    boto3 client blows up. Tool result is already in the chain; failure
    to record the META fact must not break the user-visible reply.

    The sync entry point schedules an ``asyncio.create_task``; the
    exception is raised inside the async coroutine and caught there,
    so the sync call must return cleanly AND the task must complete
    without leaking the exception back to the test runner."""
    import asyncio as _asyncio

    class BoomClient:
        def create_event(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("agentcore down")

    hook = AgentCoreMemoryHook(
        memory_id="m",
        actor_id="a",
        session_id="s",
        client=BoomClient(),
    )

    # MUST NOT raise from the sync entry point.
    hook.write_meta_event("used calc with x=2")

    # Drain the pending task; the exception should be caught inside
    # the async helper, not propagated here.
    while hook._pending_writes:
        await _asyncio.sleep(0)


def test_payload_from_messages_never_leaks_asset_content():
    """#326 asset-content memory guard (epic #321 derived decision):
    asset CONTENT must never reach AgentCore Memory.

    Pins the three producer surfaces against the serializer:

    1. Code-exec image payloads (base64 in ``toolResult`` content) —
       dropped by the toolResult strip.
    2. Upload payloads (``document`` / ``image`` content blocks carrying
       raw bytes on the send path) — dropped by the text-only join.
    3. Fenced-code extraction — runs post-stream on the settled text and
       never mutates the agent's message list; the message list fed to
       the serializer here is byte-identical before and after the
       producers module is exercised (extraction is a pure read).

    Only conversational text (including the attachment header label,
    which is metadata, and the assistant's own prose) may survive.
    """
    import json as _json

    from channel.agents import asset_producers as _ap

    image_b64_marker = "aW1hZ2UtYnl0ZXMtbWFya2Vy"  # "image-bytes-marker"
    messages = [
        {
            "role": "user",
            "content": [
                {"text": "[attachment_1: plot.png, 0.1MB, image]"},
                {"image": {"format": "png", "source": {"bytes": b"raw-upload-bytes"}}},
                {"document": {"format": "pdf", "name": "spec", "source": {"bytes": b"pdf"}}},
                {"text": "please chart this"},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "t-1",
                        "status": "success",
                        "content": [
                            {
                                "text": _json.dumps(
                                    {
                                        "stdout": "ok",
                                        "stderr": "",
                                        "exit_code": 0,
                                        "duration_ms": 5,
                                        "truncated": False,
                                        "timed_out": False,
                                        "images": [{"mime": "image/png", "b64": image_b64_marker}],
                                    }
                                )
                            }
                        ],
                    }
                },
                {"text": "Here is the chart."},
            ],
        },
    ]
    snapshot = repr(messages)

    # Exercise the extraction producer on the assistant text the way the
    # post-stream slot does — it must not mutate the message list.
    fences = _ap.extract_code_fences("Here is the chart.")
    assert fences == []
    assert repr(messages) == snapshot

    payload = _payload_from_messages(messages)

    # Text-only survivors: the attachment label + prose.
    assert payload[0]["conversational"]["content"]["text"] == (
        "[attachment_1: plot.png, 0.1MB, image]please chart this"
    )
    assert payload[1]["conversational"]["content"]["text"] == "Here is the chart."

    # No asset payload bytes / base64 anywhere in the serialized event.
    serialized = _json.dumps(payload)
    assert image_b64_marker not in serialized
    assert "raw-upload-bytes" not in serialized

    # No asset-bearing block keys at any depth.
    _assert_no_block_keys(
        payload, forbidden=("toolUse", "toolResult", "document", "image", "images")
    )
