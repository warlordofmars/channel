# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for AgentCore Memory hook + bootstrap helpers."""

from __future__ import annotations

import hashlib
import itertools
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.hooks.events import AfterInvocationEvent

from channel.agents import memory as memory_module
from channel.agents.memory import (
    AgentCoreMemoryHook,
    _legacy_lossy_actor_id,
    _payload_from_messages,
    derive_actor_id,
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
    "content",
    [
        pytest.param([{"text": ""}], id="empty-string"),
        pytest.param([{"text": "   \n\t"}], id="whitespace-only"),
        pytest.param([], id="no-content"),
        pytest.param([{"toolUse": {"name": "calc"}}], id="text-less-blocks-only"),
    ],
)
def test_payload_from_messages_skips_empty_text_entry(content: list[dict[str, Any]]):
    """#392: an entry whose text is empty / whitespace-only / absent is
    dropped rather than serialized as ``content.text == ""`` (which
    AgentCore's create_event validator rejects with a ParamValidationError)."""
    payload = _payload_from_messages([{"role": "user", "content": content}])

    assert payload == []


def test_payload_from_messages_mixed_drops_only_empty_entry():
    """#392: a mixed turn (one empty entry, one non-empty) drops only the
    empty entry and keeps the non-empty one intact."""
    messages = [
        {"role": "user", "content": [{"text": "   "}]},
        {"role": "assistant", "content": [{"text": "the answer is 4"}]},
    ]

    payload = _payload_from_messages(messages)

    assert payload == [
        {"conversational": {"role": "ASSISTANT", "content": {"text": "the answer is 4"}}},
    ]


def test_payload_from_messages_preserves_internal_whitespace():
    """#392 guard is a whitespace-only *emptiness* check — text that merely
    contains whitespace (leading/internal) is written verbatim, unchanged."""
    messages = [
        {"role": "assistant", "content": [{"text": "  padded reply  "}]},
    ]

    payload = _payload_from_messages(messages)

    assert payload == [
        {"conversational": {"role": "ASSISTANT", "content": {"text": "  padded reply  "}}},
    ]


# --------------------------------------------------------------------------
# actorId derivation (#474)
#
# ``actorId`` is the ONLY partition of the per-environment AgentCore Memory
# resource, so a non-injective derivation is a cross-user disclosure of
# private chat content, not a hygiene problem. These tests pin all four
# acceptance properties: injectivity, stability, charset validity, and the
# single-derivation rule.
# --------------------------------------------------------------------------

# AgentCore's own ``ActorId`` validator (botocore ``bedrock-agentcore``
# service model: ``min=1``, ``max=255``, plus this pattern). Copied here so
# the charset test asserts against the service contract rather than against
# our own regex constants.
_AGENTCORE_ACTOR_ID_PATTERN = re.compile(
    r"[a-zA-Z0-9][a-zA-Z0-9-_/]*(?::[a-zA-Z0-9-_/]+)*[a-zA-Z0-9-_/]*"
)
_AGENTCORE_ACTOR_ID_MAX = 255

# The three collision pairs from #474 — each pair is two DISTINCT users that
# the pre-#474 derivation collapsed onto one ``actorId``.
_COLLISION_PAIRS = [
    ("john.carter@warlordofmars.net", "john@carter.warlordofmars.net"),
    ("a.b@x.com", "a@b.x.com"),
    # Plus-addressing (``user+tag@``) colliding with an ordinary underscore
    # username — the pair that makes this a live bug rather than a theoretical
    # one.
    ("jc+work@x.com", "jc_work@x.com"),
]


def _injectivity_corpus() -> list[str]:
    """Distinct JWT-``sub`` values spanning every shape the derivation sees.

    Deliberately dense in the disallowed characters (``. @ + :``) that the
    old scheme flattened to ``_`` — that is where collisions live. The
    exhaustive length-4 product over a mixed alphabet is what makes this a
    property test rather than three hand-picked examples: it contains every
    arrangement of separator and payload characters at that length, so any
    scheme that loses information about *which* separator appeared *where*
    fails it.
    """
    alphabet = "a.@+_-/"
    corpus = ["".join(t) for t in itertools.product(alphabet, repeat=4)]
    corpus += [pair_member for pair in _COLLISION_PAIRS for pair_member in pair]
    corpus += [
        "",  # empty sub — no label, bare digest
        ".",  # label empties out after the leading-junk strip
        "@@@@@@@@",
        "alice@example.com",
        "alice+tag@example.com",
        "user-abc-123",
        "workspace_1/user_42",  # the future {workspace_id}/{user_id} shape
        "e2e-7c-1234-abc-a@example.com",
        "UPPER@Example.COM",  # case is significant — distinct from the above
        "upper@example.com",
        # Two subs sharing a >48-char prefix: identical labels after
        # truncation, so only the digest can tell them apart.
        "a" * 60 + "-one@example.com",
        "a" * 60 + "-two@example.com",
        "unicode-ñ@example.com",  # non-ASCII collapses in the label
        "unicode-ø@example.com",
    ]
    assert len(corpus) == len(set(corpus)), "corpus itself must hold distinct inputs"
    return corpus


def test_derive_actor_id_is_injective_over_collision_corpus():
    """THE property: distinct subs never share an ``actorId``.

    A collision here means two users share an AgentCore Memory partition —
    one person's chat content surfacing in another's system prompt (#474).
    """
    corpus = _injectivity_corpus()
    derived = [derive_actor_id(s) for s in corpus]

    collisions = {
        value: [s for s in corpus if derive_actor_id(s) == value]
        for value in derived
        if derived.count(value) > 1
    }
    assert collisions == {}, f"actorId collisions: {collisions}"
    assert len(set(derived)) == len(corpus)


def test_legacy_lossy_derivation_collapses_the_documented_pairs():
    """Regression oracle: the pre-#474 scheme really did collide.

    Without this, the injectivity test above could pass against a corpus
    that never exercised the bug. Each pair below is two distinct users
    that the old derivation mapped onto one partition.
    """
    for left, right in _COLLISION_PAIRS:
        assert _legacy_lossy_actor_id(left) == _legacy_lossy_actor_id(right)
        assert derive_actor_id(left) != derive_actor_id(right)

    # And the collapse is broad, not limited to the three known pairs: the
    # generated corpus loses a large fraction of its identities under the
    # old scheme and none under the new one.
    corpus = _injectivity_corpus()
    assert len({_legacy_lossy_actor_id(s) for s in corpus}) < len(corpus)
    assert len({derive_actor_id(s) for s in corpus}) == len(corpus)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Golden vectors. These pin the derivation FOREVER — an actorId is a
        # storage key, so changing any value below orphans that user's stored
        # memories. Treat a failure here as a migration decision, never as a
        # test to update.
        ("alice@example.com", "alice_example_com-ff8d9819fc0e12bf0d24892e45987e24"),
        (
            "e2e-7c-1234-abc-a@example.com",
            "e2e-7c-1234-abc-a_example_com-aa5bc84d0ce2009d7954da5774de2cf8",
        ),
        ("user-abc-123", "user-abc-123-25b4a3ce57a106a35cf1ce9bf9dd33f0"),
        ("workspace_1/user_42", "workspace_1/user_42-18f556327e94918834f52ffa3c3bac14"),
        ("alice+tag@example.com", "alice_tag_example_com-567e25214281f7004cf65c7a16fd5868"),
        # The #474 pairs, now distinct.
        ("jc+work@x.com", "jc_work_x_com-97228b20a199da09d5511eff68cd7a28"),
        ("jc_work@x.com", "jc_work_x_com-4619423e39f32af236b33a96561f0f05"),
        # Empty / label-free inputs degrade to the bare digest rather than to
        # the empty string AgentCore's ``min=1`` would reject.
        ("", "e3b0c44298fc1c149afbf4c8996fb924"),
        (".....", "f85d30e7d95a052f65167f1f1095186d"),
    ],
)
def test_derive_actor_id_golden_vectors(raw: str, expected: str):
    """Also THE cross-process stability check: the right-hand sides are
    literals authored in a different interpreter than the one asserting
    them, so a derivation carrying any per-process input (a salt, the
    builtin ``hash()``'s `PYTHONHASHSEED`, a clock) cannot pass."""
    assert derive_actor_id(raw) == expected


def test_derive_actor_id_suffix_is_the_sha256_of_the_input_bytes():
    """The suffix is exactly the first 32 hex chars of the UTF-8 SHA-256 —
    recomputed here independently of the implementation, so the golden
    vectors above are anchored to a named construction rather than to
    whatever the function happened to emit on the day they were captured."""
    sub = "alice@example.com"
    expected_digest = hashlib.sha256(sub.encode("utf-8")).hexdigest()[:32]

    assert derive_actor_id(sub).endswith(f"-{expected_digest}")


def test_derive_actor_id_output_satisfies_agentcore_validator():
    """Charset + length contract of AgentCore's ``ActorId`` shape."""
    for sub in _injectivity_corpus():
        actor_id = derive_actor_id(sub)
        assert _AGENTCORE_ACTOR_ID_PATTERN.fullmatch(actor_id), actor_id
        assert 1 <= len(actor_id) <= _AGENTCORE_ACTOR_ID_MAX, actor_id


def test_derive_actor_id_keeps_a_readable_label_prefix():
    """The label is what keeps an actor recognisable in the AgentCore
    console — the operability the pre-#474 scheme had and a bare hash
    would have thrown away."""
    assert derive_actor_id("alice@example.com").startswith("alice_example_com-")


def test_derive_actor_id_truncates_a_long_label_without_losing_injectivity():
    """Long subs share a truncated label; only the digest separates them."""
    left = "a" * 60 + "-one@example.com"
    right = "a" * 60 + "-two@example.com"

    assert derive_actor_id(left)[:48] == derive_actor_id(right)[:48]
    assert derive_actor_id(left) != derive_actor_id(right)
    assert len(derive_actor_id(left)) == 48 + 1 + 32


def test_one_shared_derivation_across_every_call_site():
    """``memory.py`` owns the derivation; every consumer imports it.

    Two copies of this logic drifting apart would re-open #474 on
    whichever path lagged, so the single-source rule is asserted rather
    than assumed.
    """
    from channel.agents import recall as recall_module
    from channel.agents.tools import memory_tools as memory_tools_module
    from channel.api import _debug as debug_module
    from channel.api import chats as chats_module
    from channel.api import memory as memory_api_module

    for module in (
        recall_module,
        memory_tools_module,
        chats_module,
        debug_module,
        memory_api_module,
    ):
        assert module.derive_actor_id is derive_actor_id, module.__name__


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
    monkeypatch.setenv("CHANNEL_AGENTCORE_MEMORY_NAME", "preexisting_mem")
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
    assert kwargs["actorId"] == derive_actor_id("user-abc")
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


@pytest.mark.asyncio
async def test_hook_skips_create_event_when_turn_is_all_empty():
    """#392: an all-empty turn (every entry has empty/whitespace-only text)
    must NOT call create_event and must NOT bump the success/failure
    counter — a no-op turn is neither a write success nor a failure."""
    fake_client = MagicMock()

    hook = AgentCoreMemoryHook(
        memory_id="m-1",
        actor_id="user-abc",
        session_id="chat-xyz",
        client=fake_client,
    )

    event = _fake_event_with_messages(
        [
            {"role": "user", "content": [{"text": "   "}]},
            {"role": "assistant", "content": [{"text": ""}]},
        ]
    )

    with patch(
        "channel.agents.memory.record_memory_write_outcome",
        new=AsyncMock(),
    ) as mock_record:
        await hook._on_after_invocation_async(event)

    fake_client.create_event.assert_not_called()
    mock_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_hook_writes_only_non_empty_entry_on_mixed_turn():
    """#392: a mixed turn (one empty entry, one non-empty) still writes —
    create_event fires once with only the non-empty entry in the payload,
    and the write is counted as a success."""
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
            {"role": "user", "content": [{"text": "  "}]},
            {"role": "assistant", "content": [{"text": "hello"}]},
        ]
    )

    with patch(
        "channel.agents.memory.record_memory_write_outcome",
        new=AsyncMock(),
    ) as mock_record:
        await hook._on_after_invocation_async(event)

    fake_client.create_event.assert_called_once()
    payload = fake_client.create_event.call_args.kwargs["payload"]
    assert payload == [
        {"conversational": {"role": "ASSISTANT", "content": {"text": "hello"}}},
    ]
    mock_record.assert_awaited_once_with(success=True)


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
async def test_hook_derives_actor_id_passed_to_create_event():
    """Email-form actor_ids must be derived before reaching AgentCore."""
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
    assert kwargs["actorId"] == derive_actor_id("alice@example.com")
    # The raw sub never reaches AgentCore — it would fail the validator, and
    # (pre-#474) the lossy repair was what let two users share a partition.
    assert kwargs["actorId"] != "alice@example.com"


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
    assert captured["actorId"] == derive_actor_id("user-1")
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


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty-string"),
        pytest.param("   \n\t", id="whitespace-only"),
    ],
)
def test_write_meta_event_skips_empty_body(text: str):
    """#392: a meta event whose body is empty/whitespace-only carries no
    META fact — the guard short-circuits before scheduling any write, so
    create_event is never called and no pending task leaks."""

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def create_event(self, **_: Any) -> dict[str, Any]:
            self.calls += 1
            return {}

    client = FakeClient()
    hook = AgentCoreMemoryHook(
        memory_id="m",
        actor_id="a",
        session_id="s",
        client=client,
    )

    hook.write_meta_event(text)

    assert client.calls == 0
    assert hook._pending_writes == set()


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
