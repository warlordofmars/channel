# Phase 7c — AgentCore Memory writes implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Bedrock AgentCore Memory **writes** into the Strands agent loop. Every chat round-trip produces one atomic `CreateEvent` containing the user+assistant pair, with `actorId=jwt_sub` and `sessionId=chat_id`. Failures log + emit EMF + swallow. Recall stays disabled — that's 7d.

**Architecture:** A new `HookProvider` subclass (`AgentCoreMemoryHook`) subscribes to `AfterInvocationEvent`, fires `asyncio.create_task(...)` calling `bedrock-agentcore.CreateEvent`, never blocks the SSE response. A lazy idempotent `get_or_create_memory(env)` bootstrap helper finds-or-creates one Memory resource per environment, caches the ID at module level. A dev-only `GET /api/_debug/memory/events` endpoint surfaces writes for Playwright e2e verification, gated by `STARTER_ENABLE_DEBUG_ENDPOINTS=1`.

**Tech Stack:** Strands Agents 1.41 (`HookProvider`, `AfterInvocationEvent`), boto3 1.42 (`bedrock-agentcore`, `bedrock-agentcore-control`), FastAPI router conditional mount, AWS CDK Python (IAM PolicyStatement + Lambda env vars), CloudWatch EMF (no per-actor dimensions), Playwright async API via `pytest-playwright`.

**Spec:** `docs/superpowers/specs/2026-05-31-phase-7c-agentcore-memory-writes-design.md` — read it before starting Task 1. Risks section #3 (cardinality), #5 (debug-endpoint honor system), and #6 (loss-on-freeze) drove specific implementation choices below; don't undo them.

---

## File map

**Create:**
- `src/channel/agents/memory.py` — `AgentCoreMemoryHook` + `get_or_create_memory` + `_payload_from_messages` (~150 LOC)
- `src/channel/api/_debug.py` — dev-only debug router
- `tests/unit/test_memory.py` — covers `memory.py` (100%)
- `tests/unit/test_debug_api.py` — covers `_debug.py` (mount/unmount, JWT)
- `tests/unit/test_channel_stack.py` — CDK assertion: prod excludes debug env var (create if absent)
- `tests/e2e/test_memory_writes.py` — Playwright async e2e

**Modify:**
- `src/channel/agents/chat_agent.py` — `build_agent` accepts `user_id` + `chat_id`, attaches hook
- `src/channel/api/chats.py` — plumb `jwt_sub` + `chat_id` into `build_agent`
- `src/channel/api/main.py` — conditional mount of `_debug.router`
- `src/channel/metrics.py` — `record_memory_write_outcome(success)`
- `infra/stacks/channel_stack.py` — IAM grants + `STARTER_ENABLE_DEBUG_ENDPOINTS` env var on dev
- `tasks.py` — `inv dev` sets `STARTER_ENABLE_DEBUG_ENDPOINTS=1`
- `tests/unit/test_chat_agent.py` — assert hook is attached with correct args
- `CHANGELOG.md` — new `[Unreleased]` entry
- `CLAUDE.md` — `## Structure` updates + new `## AgentCore Memory` section

---

## Task 1: `_payload_from_messages` pure helper + tests

**Files:**
- Create: `src/channel/agents/memory.py`
- Test: `tests/unit/test_memory.py`

The smallest, purely-functional piece of the integration. Start here so we have a green test before any boto3 mocking.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_memory.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for AgentCore Memory hook + bootstrap helpers."""

from __future__ import annotations

import pytest

from channel.agents.memory import _payload_from_messages


def test_payload_from_messages_pairs_user_and_assistant_text():
    messages = [
        {"role": "user", "content": [{"text": "what's 2+2?"}]},
        {"role": "assistant", "content": [{"text": "4"}]},
    ]

    payload = _payload_from_messages(messages)

    # AgentCore CreateEvent payload shape: list of typed conversational
    # entries. We emit one entry per role.
    assert payload == [
        {"conversational": {"role": "USER", "content": {"text": "what's 2+2?"}}},
        {"conversational": {"role": "ASSISTANT", "content": {"text": "4"}}},
    ]


def test_payload_from_messages_concatenates_multi_block_content():
    # Strands sometimes emits multiple text blocks within one message
    # (e.g. when a tool result is interleaved). We concatenate the text
    # blocks; non-text blocks (toolUse, toolResult) are dropped because
    # AgentCore's payload spec for v1 only accepts text.
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_memory.py -v`
Expected: `ModuleNotFoundError: No module named 'channel.agents.memory'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/channel/agents/memory.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Bedrock AgentCore Memory integration via Strands hooks.

Phase 7c implementation. Phase write-only: every chat round-trip
produces one atomic ``CreateEvent`` containing the user+assistant pair.
Recall (``RetrieveMemoryRecords`` + prompt injection) lands in 7d.

Design rationale: ``docs/superpowers/specs/2026-05-31-phase-7c-agentcore-memory-writes-design.md``.

Strands 1.41 has no native AgentCore Memory adapter (verified in
``docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md``)
so this module talks to AgentCore directly via boto3.
"""

from __future__ import annotations

from typing import Any

_ROLE_MAP: dict[str, str] = {"user": "USER", "assistant": "ASSISTANT"}


def _payload_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Strands message dicts to AgentCore ``CreateEvent`` payload.

    Strands messages have shape ``{"role": str, "content": [{"text": str}, ...]}``.
    AgentCore's payload is a list of typed conversational entries with
    ``role`` upper-cased and ``content.text`` as a single string.

    Multi-block content (text + toolUse interleaved) gets its text blocks
    concatenated. Non-text blocks (toolUse, toolResult) are dropped —
    AgentCore's v1 payload spec only accepts text.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = _ROLE_MAP.get(msg["role"])
        if role is None:
            raise ValueError(f"unsupported role: {msg['role']!r}")
        text = "".join(
            block["text"] for block in msg.get("content", []) if "text" in block
        )
        out.append({"conversational": {"role": role, "content": {"text": text}}})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_memory.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Run coverage check**

Run: `uv run pytest tests/unit/test_memory.py --cov=channel.agents.memory --cov-report=term-missing`
Expected: `src/channel/agents/memory.py     X      0   100%`

- [ ] **Step 6: Commit**

```bash
git add src/channel/agents/memory.py tests/unit/test_memory.py
git commit -m "feat(channel-7c): _payload_from_messages helper for AgentCore writes"
```

---

## Task 2: `get_or_create_memory(env)` bootstrap helper + tests

**Files:**
- Modify: `src/channel/agents/memory.py`
- Test: `tests/unit/test_memory.py`

Lazy + idempotent: list-by-name first, create if absent, cache module-level.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/unit/test_memory.py
from unittest.mock import MagicMock, patch

from channel.agents import memory as memory_module
from channel.agents.memory import get_or_create_memory


@pytest.fixture(autouse=True)
def _reset_memory_id_cache():
    """Module-level cache survives across tests; reset before each."""
    memory_module._memory_id_cache.clear()
    yield
    memory_module._memory_id_cache.clear()


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
    # list_memories called once, not twice — second call is cache hit
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

    # Found existing → never called create
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_memory.py -v`
Expected: `ImportError: cannot import name 'get_or_create_memory'`

- [ ] **Step 3: Implement**

```python
# Append to src/channel/agents/memory.py
import os

import boto3

# Module-level cache, resets on Lambda cold-start. Key = env, value =
# AgentCore-assigned memoryId (which includes an opaque suffix).
_memory_id_cache: dict[str, str] = {}


def get_or_create_memory(env: str) -> str:
    """Return the AgentCore Memory id for ``env``, creating it if absent.

    Lazy + idempotent. First call within a Lambda instance pays a
    ``ListMemories`` RPC (~200ms) to find an existing Memory by name;
    subsequent calls hit the module-level cache. If no match exists,
    falls through to ``CreateMemory``.

    AgentCore appends an opaque suffix to ``memoryId``
    (e.g. ``channel-dev-A1B2C3D4``). Look up by **name**, not by id, or
    re-deploys against an existing Memory will create duplicates.

    Override the default ``channel-{env}`` naming via
    ``STARTER_AGENTCORE_MEMORY_NAME`` — useful for pointing a personal
    dev environment at a pre-existing Memory resource.
    """
    if env in _memory_id_cache:
        return _memory_id_cache[env]

    name = os.environ.get("STARTER_AGENTCORE_MEMORY_NAME") or f"channel-{env}"
    control = boto3.client("bedrock-agentcore-control")

    existing = control.list_memories()
    for mem in existing.get("memorySummaries", []):
        if mem["name"] == name:
            _memory_id_cache[env] = mem["id"]
            return mem["id"]

    created = control.create_memory(
        name=name,
        memoryStrategies=[],
        eventExpiryDuration=90,  # days
    )
    memory_id = created["memory"]["id"]
    _memory_id_cache[env] = memory_id
    return memory_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_memory.py -v`
Expected: 7 tests PASS (3 from Task 1 + 4 new)

- [ ] **Step 5: Coverage**

Run: `uv run pytest tests/unit/test_memory.py --cov=channel.agents.memory --cov-report=term-missing`
Expected: 100%

- [ ] **Step 6: Commit**

```bash
git add src/channel/agents/memory.py tests/unit/test_memory.py
git commit -m "feat(channel-7c): get_or_create_memory bootstrap helper"
```

---

## Task 3: `record_memory_write_outcome` EMF metric helper

**Files:**
- Modify: `src/channel/metrics.py`
- Test: `tests/unit/test_metrics.py`

Plug the EMF surface before the hook needs it. No per-actor dimensions — counter-only (see spec Risk #3).

- [ ] **Step 1: Read existing metrics.py for the EMF emission pattern**

```bash
grep -n "def " src/channel/metrics.py
```

- [ ] **Step 2: Write the failing test**

```python
# Append to tests/unit/test_metrics.py
def test_record_memory_write_outcome_success_emits_success_counter(capsys):
    from channel.metrics import record_memory_write_outcome

    record_memory_write_outcome(success=True)

    out = capsys.readouterr().out
    # EMF is emitted via stdout as a JSON object on its own line. The
    # _AWS block must reference "MemoryWriteSuccesses" with value 1.
    assert '"MemoryWriteSuccesses"' in out
    assert '"MemoryWriteFailures"' not in out


def test_record_memory_write_outcome_failure_emits_failure_counter(capsys):
    from channel.metrics import record_memory_write_outcome

    record_memory_write_outcome(success=False)

    out = capsys.readouterr().out
    assert '"MemoryWriteFailures"' in out
    assert '"MemoryWriteSuccesses"' not in out


def test_record_memory_write_outcome_does_not_emit_actor_dimension(capsys):
    """Per spec Risk #3: cardinality blowup. Counter-only, no dimensions."""
    from channel.metrics import record_memory_write_outcome
    import json

    record_memory_write_outcome(success=True)

    out = capsys.readouterr().out
    emf_line = [line for line in out.splitlines() if '"_aws"' in line][0]
    emf = json.loads(emf_line)
    # Dimensions list must be empty or only contain a single default set
    # — never an actor-scoped dimension.
    metric_defs = emf["_aws"]["CloudWatchMetrics"][0]
    for dim_set in metric_defs["Dimensions"]:
        assert "actor_id" not in dim_set
        assert "user_id" not in dim_set
        assert "ActorId" not in dim_set
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_metrics.py -v -k memory_write`
Expected: `ImportError: cannot import name 'record_memory_write_outcome'`

- [ ] **Step 4: Implement** (match the existing emission style in `metrics.py` — read it before writing)

```python
# Append to src/channel/metrics.py
def record_memory_write_outcome(success: bool) -> None:
    """Emit an EMF counter for an AgentCore Memory write attempt.

    Counter-only — DO NOT add per-actor or per-session dimensions.
    Cardinality scales with active users, which blows up CloudWatch
    metric volume. Per-actor context belongs in structured logs, not
    metric dimensions. See spec Risk #3.
    """
    metric_name = "MemoryWriteSuccesses" if success else "MemoryWriteFailures"
    _emit_emf(
        namespace="Channel/Memory",
        metrics={metric_name: 1},
        unit="Count",
    )
```

(Adapt `_emit_emf` to whatever the existing module exposes — read first.)

- [ ] **Step 5: Run tests + coverage**

Run: `uv run pytest tests/unit/test_metrics.py --cov=channel.metrics --cov-report=term-missing`
Expected: PASS, 100% coverage on `metrics.py` overall.

- [ ] **Step 6: Commit**

```bash
git add src/channel/metrics.py tests/unit/test_metrics.py
git commit -m "feat(channel-7c): record_memory_write_outcome EMF counter"
```

---

## Task 4: `AgentCoreMemoryHook` class + tests

**Files:**
- Modify: `src/channel/agents/memory.py`
- Test: `tests/unit/test_memory.py`

The HookProvider subscription, the fire-and-forget write, and the log+swallow failure path.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/unit/test_memory.py
from unittest.mock import AsyncMock

from channel.agents.memory import AgentCoreMemoryHook


def test_hook_registers_after_invocation_callback():
    """The hook subscribes only to AfterInvocationEvent — write-only phase."""
    hook = AgentCoreMemoryHook(
        memory_id="channel-jc-A1B2",
        actor_id="user-abc",
        session_id="chat-xyz",
    )

    registry = MagicMock()
    hook.register_hooks(registry)

    # Exactly one subscription, to AfterInvocationEvent.
    from strands.hooks.events import AfterInvocationEvent
    assert registry.add_callback.call_count == 1
    args, _ = registry.add_callback.call_args
    assert args[0] is AfterInvocationEvent


@pytest.mark.asyncio
async def test_hook_writes_create_event_on_after_invocation(monkeypatch):
    fake_client = MagicMock()
    fake_client.create_event.return_value = {"event": {"eventId": "evt-1"}}

    hook = AgentCoreMemoryHook(
        memory_id="channel-jc-A1B2",
        actor_id="user-abc",
        session_id="chat-xyz",
        client=fake_client,  # injection seam for tests
    )

    fake_agent = MagicMock()
    fake_agent.messages = [
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
    ]
    fake_event = MagicMock(agent=fake_agent)

    await hook._on_after_invocation_async(fake_event)

    fake_client.create_event.assert_called_once()
    kwargs = fake_client.create_event.call_args.kwargs
    assert kwargs["memoryId"] == "channel-jc-A1B2"
    assert kwargs["actorId"] == "user-abc"
    assert kwargs["sessionId"] == "chat-xyz"
    assert len(kwargs["payload"]) == 2


@pytest.mark.asyncio
async def test_hook_swallows_exceptions_and_emits_failure_metric(monkeypatch, capsys):
    fake_client = MagicMock()
    fake_client.create_event.side_effect = RuntimeError("agentcore down")

    hook = AgentCoreMemoryHook(
        memory_id="m", actor_id="a", session_id="s", client=fake_client,
    )

    fake_agent = MagicMock()
    fake_agent.messages = [
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
    ]
    fake_event = MagicMock(agent=fake_agent)

    # MUST NOT raise — write failures are swallowed.
    await hook._on_after_invocation_async(fake_event)

    out = capsys.readouterr().out
    assert '"MemoryWriteFailures"' in out


def test_hook_after_invocation_schedules_async_write(monkeypatch):
    """The sync callback must fire-and-forget via asyncio.create_task,
    not block the agent loop. Verifies create_task is called."""
    hook = AgentCoreMemoryHook(memory_id="m", actor_id="a", session_id="s")

    fake_event = MagicMock()
    fake_event.agent.messages = [
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
    ]

    created_tasks: list[Any] = []

    def _record_task(coro: Any) -> Any:
        created_tasks.append(coro)
        # Close the coroutine to silence "coroutine was never awaited"
        coro.close()
        return MagicMock()

    monkeypatch.setattr("channel.agents.memory.asyncio.create_task", _record_task)
    hook._on_after_invocation(fake_event)

    assert len(created_tasks) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_memory.py -v -k hook`
Expected: `ImportError: cannot import name 'AgentCoreMemoryHook'`

- [ ] **Step 3: Implement**

```python
# Append to src/channel/agents/memory.py
import asyncio
import logging
from datetime import datetime, timezone

from strands.hooks import HookProvider
from strands.hooks.events import AfterInvocationEvent

from channel.metrics import record_memory_write_outcome

logger = logging.getLogger(__name__)


class AgentCoreMemoryHook(HookProvider):
    """Strands HookProvider that persists each chat turn to AgentCore.

    Subscribes to ``AfterInvocationEvent`` (per turn, fires once). The
    sync callback fires-and-forgets via ``asyncio.create_task`` so the
    SSE response doesn't wait on AgentCore. Write failures are logged
    + EMF-counted + swallowed — memory must not break chats.

    See ``docs/superpowers/specs/2026-05-31-phase-7c-agentcore-memory-writes-design.md``
    for design rationale (esp. §Per-turn write and §Failure mode).
    """

    def __init__(
        self,
        memory_id: str,
        actor_id: str,
        session_id: str,
        client: Any | None = None,
    ) -> None:
        self._memory_id = memory_id
        self._actor_id = actor_id
        self._session_id = session_id
        self._client = client or boto3.client("bedrock-agentcore")

    def register_hooks(self, registry: Any) -> None:
        registry.add_callback(AfterInvocationEvent, self._on_after_invocation)

    def _on_after_invocation(self, event: AfterInvocationEvent) -> None:
        """Sync entry-point — schedule the async write, return immediately."""
        asyncio.create_task(self._on_after_invocation_async(event))

    async def _on_after_invocation_async(self, event: AfterInvocationEvent) -> None:
        """Async write path. Log + swallow on failure."""
        try:
            # Last two messages on the agent are the just-completed
            # user+assistant pair.
            messages = list(event.agent.messages)[-2:]
            payload = _payload_from_messages(messages)
            await asyncio.to_thread(
                self._client.create_event,
                memoryId=self._memory_id,
                actorId=self._actor_id,
                sessionId=self._session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=payload,
            )
            record_memory_write_outcome(success=True)
        except Exception as exc:
            logger.warning(
                "agentcore.create_event_failed",
                extra={
                    "err": str(exc),
                    "actor_id": self._actor_id,
                    "session_id": self._session_id,
                },
            )
            record_memory_write_outcome(success=False)
```

- [ ] **Step 4: Run tests + coverage**

Run: `uv run pytest tests/unit/test_memory.py --cov=channel.agents.memory --cov-report=term-missing`
Expected: All tests PASS, 100% coverage on `memory.py`.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/memory.py tests/unit/test_memory.py
git commit -m "feat(channel-7c): AgentCoreMemoryHook fire-and-forget writes"
```

---

## Task 5: Wire `build_agent` to attach the hook

**Files:**
- Modify: `src/channel/agents/chat_agent.py`
- Modify: `tests/unit/test_chat_agent.py`

- [ ] **Step 1: Read current `build_agent` signature**

```bash
grep -nE "def build_agent|build_agent\(" src/channel/agents/chat_agent.py
```

- [ ] **Step 2: Write the failing test**

```python
# Append to tests/unit/test_chat_agent.py
from unittest.mock import patch, MagicMock


def test_build_agent_attaches_agentcore_memory_hook(monkeypatch):
    """Hook must be constructed with the caller's user_id + chat_id."""
    monkeypatch.setenv("STARTER_ENV", "jc")

    captured: dict[str, Any] = {}

    class FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            captured["agent_kwargs"] = kwargs

    fake_hook = MagicMock()

    def fake_hook_factory(**kwargs: Any) -> Any:
        captured["hook_kwargs"] = kwargs
        return fake_hook

    with patch("channel.agents.chat_agent.Agent", FakeAgent), \
         patch("channel.agents.chat_agent.AgentCoreMemoryHook", fake_hook_factory), \
         patch(
             "channel.agents.chat_agent.get_or_create_memory",
             return_value="channel-jc-A1B2",
         ):
        build_agent(
            model_id="claude-sonnet-4-6",
            user_id="user-abc",
            chat_id="chat-xyz",
        )

    assert captured["hook_kwargs"] == {
        "memory_id": "channel-jc-A1B2",
        "actor_id": "user-abc",
        "session_id": "chat-xyz",
    }
    assert captured["agent_kwargs"]["hooks"] == [fake_hook]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_chat_agent.py::test_build_agent_attaches_agentcore_memory_hook -v`
Expected: `TypeError: build_agent() got an unexpected keyword argument 'user_id'`

- [ ] **Step 4: Update existing `test_build_agent_constructs_strands_agent_with_bedrock_model` to pass the new args**

```python
# Locate in tests/unit/test_chat_agent.py and add user_id, chat_id to the call:
build_agent(
    "claude-sonnet-4-6",
    user_id="test-user",
    chat_id="test-chat",
)
```

- [ ] **Step 5: Implement**

```python
# In src/channel/agents/chat_agent.py
import os

from channel.agents.memory import AgentCoreMemoryHook, get_or_create_memory


def build_agent(
    model_id: str,
    *,
    user_id: str,
    chat_id: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Agent:
    """Construct a Strands Agent wired to Bedrock + AgentCore Memory.

    ``user_id`` becomes the AgentCore ``actorId``; ``chat_id`` becomes
    the AgentCore ``sessionId``. The Memory resource itself is
    discovered (or created) once per Lambda cold-start, keyed off the
    ``STARTER_ENV`` env var.
    """
    bedrock_model = BedrockModel(model_id=resolve_model_id(model_id), ...)
    memory_id = get_or_create_memory(os.environ["STARTER_ENV"])
    memory_hook = AgentCoreMemoryHook(
        memory_id=memory_id,
        actor_id=user_id,
        session_id=chat_id,
    )
    return Agent(
        model=bedrock_model,
        system_prompt=system_prompt,
        hooks=[memory_hook],
        ...
    )
```

(Preserve all existing keyword args from the current signature.)

- [ ] **Step 6: Run tests + coverage**

Run: `uv run pytest tests/unit/test_chat_agent.py -v`
Expected: All tests PASS.
Run: `uv run pytest tests/unit/ --cov=channel.agents --cov-report=term-missing`
Expected: 100% on `agents/` modules.

- [ ] **Step 7: Commit**

```bash
git add src/channel/agents/chat_agent.py tests/unit/test_chat_agent.py
git commit -m "feat(channel-7c): wire AgentCoreMemoryHook into build_agent"
```

---

## Task 6: Plumb `jwt_sub` + `chat_id` into `_stream_bedrock_reply`

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py` (if existing tests break)

The call site already has both values; just pass them through.

- [ ] **Step 1: Locate the call**

```bash
grep -n "build_agent(" src/channel/api/chats.py
```

- [ ] **Step 2: Update the call**

```python
# Before:
agent = build_agent(model_id=...)
# After:
agent = build_agent(
    model_id=...,
    user_id=jwt_sub,    # already in scope from _load_owned_chat caller
    chat_id=chat_id,    # already in scope as the URL path param
)
```

- [ ] **Step 3: Run full unit suite — fix any tests that break**

Run: `uv run pytest tests/unit/ -v`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "feat(channel-7c): plumb actor_id+session_id through stream_bedrock_reply"
```

---

## Task 7: `_debug.py` debug router + tests

**Files:**
- Create: `src/channel/api/_debug.py`
- Test: `tests/unit/test_debug_api.py`

Dev-only `GET /api/_debug/memory/events?chat_id=...&limit=...`. Mounted conditionally by `main.py` in Task 8.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_debug_api.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the dev-only /api/_debug/* router."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from channel.api.main import app
# (mgmt-JWT fixture from existing conftest — adapt to actual import path.)
from tests.unit.conftest import mgmt_jwt_for


def test_debug_endpoint_returns_events_for_caller(monkeypatch):
    monkeypatch.setenv("STARTER_ENABLE_DEBUG_ENDPOINTS", "1")

    fake_client = MagicMock()
    fake_client.list_events.return_value = {
        "events": [
            {
                "eventId": "evt-1",
                "eventTimestamp": "2026-05-31T18:00:00Z",
                "payload": [{"conversational": {"role": "USER", "content": {"text": "hi"}}}],
            },
        ],
    }

    with patch("channel.api._debug.boto3.client", return_value=fake_client), \
         patch("channel.api._debug.get_or_create_memory", return_value="m-1"):
        client = TestClient(app)
        token = mgmt_jwt_for("user-abc")
        resp = client.get(
            "/api/_debug/memory/events",
            params={"chat_id": "chat-xyz", "limit": 5},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["events"]) == 1
    assert data["events"][0]["eventId"] == "evt-1"

    # Caller's jwt_sub scopes the call
    fake_client.list_events.assert_called_once_with(
        memoryId="m-1", actorId="user-abc", sessionId="chat-xyz", maxResults=5,
    )


def test_debug_endpoint_requires_mgmt_jwt(monkeypatch):
    monkeypatch.setenv("STARTER_ENABLE_DEBUG_ENDPOINTS", "1")

    client = TestClient(app)
    resp = client.get("/api/_debug/memory/events", params={"chat_id": "c"})

    assert resp.status_code == 401


def test_debug_endpoint_not_mounted_when_flag_unset(monkeypatch):
    monkeypatch.delenv("STARTER_ENABLE_DEBUG_ENDPOINTS", raising=False)

    # Re-import the app to re-evaluate the mount check
    import importlib
    from channel.api import main
    importlib.reload(main)

    client = TestClient(main.app)
    token = mgmt_jwt_for("user-abc")
    resp = client.get(
        "/api/_debug/memory/events",
        params={"chat_id": "c"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_debug_api.py -v`
Expected: `ImportError` or 404 on the mounted-path test (depending on order).

- [ ] **Step 3: Implement the router**

```python
# src/channel/api/_debug.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Dev-only debug endpoints.

Mounted by ``main.py`` only when ``STARTER_ENABLE_DEBUG_ENDPOINTS=1``.
The check happens at app construction so prod (which never sets the
env var) doesn't register these routes at all — see spec Risk #5.

Current endpoints:

- ``GET /api/_debug/memory/events`` — list AgentCore events for the
  caller (scoped by jwt_sub) and a given chat_id. Used by the Phase 7c
  Playwright e2e to verify writes landed.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from fastapi import APIRouter, Depends, Query

from channel.agents.memory import get_or_create_memory
from channel.api._auth import require_mgmt_jwt

router = APIRouter(prefix="/api/_debug", tags=["debug"])


@router.get("/memory/events")
def list_memory_events(
    chat_id: str = Query(...),
    limit: int = Query(10, ge=1, le=100),
    jwt_claims: dict[str, Any] = Depends(require_mgmt_jwt),
) -> dict[str, Any]:
    """Return the most recent AgentCore events for caller + chat_id."""
    memory_id = get_or_create_memory(os.environ["STARTER_ENV"])
    client = boto3.client("bedrock-agentcore")
    resp = client.list_events(
        memoryId=memory_id,
        actorId=jwt_claims["sub"],
        sessionId=chat_id,
        maxResults=limit,
    )
    return {"events": resp.get("events", [])}
```

- [ ] **Step 4: Run tests + coverage**

Run: `uv run pytest tests/unit/test_debug_api.py --cov=channel.api._debug --cov-report=term-missing`
Expected: PASS, 100%.

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/_debug.py tests/unit/test_debug_api.py
git commit -m "feat(channel-7c): /api/_debug/memory/events debug endpoint"
```

---

## Task 8: Conditional mount in `main.py`

**Files:**
- Modify: `src/channel/api/main.py`

- [ ] **Step 1: Locate the router-include block**

```bash
grep -n "include_router\|app = FastAPI" src/channel/api/main.py
```

- [ ] **Step 2: Add the conditional mount**

```python
# After the other include_router(...) lines:
if os.environ.get("STARTER_ENABLE_DEBUG_ENDPOINTS") == "1":
    from channel.api._debug import router as debug_router
    app.include_router(debug_router)
```

- [ ] **Step 3: Run the debug-api tests (they cover both mount paths)**

Run: `uv run pytest tests/unit/test_debug_api.py -v`
Expected: PASS.

- [ ] **Step 4: Run full unit suite to check for regressions**

Run: `uv run inv test-unit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/main.py
git commit -m "feat(channel-7c): conditionally mount /api/_debug router"
```

---

## Task 9: CDK — IAM grants + env var + CDK assertion test

**Files:**
- Modify: `infra/stacks/channel_stack.py`
- Create: `tests/unit/test_channel_stack.py` (if absent)

- [ ] **Step 1: Write the failing CDK assertion test**

```python
# tests/unit/test_channel_stack.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""CDK template assertions for ChannelStack.

These run as Python unit tests (no AWS calls). They guard against
regressions where env-specific configuration leaks into the wrong env —
specifically, the debug-endpoints flag must never be set on prod.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from infra.stacks.channel_stack import ChannelStack


def test_prod_stack_does_not_set_debug_endpoints_env_var():
    app = cdk.App()
    stack = ChannelStack(app, "ChannelStack-prod", env_name="prod")
    template = assertions.Template.from_stack(stack)

    # Find the API Lambda — there's only one in this stack.
    funcs = template.find_resources("AWS::Lambda::Function")
    api_fns = {
        k: v for k, v in funcs.items() if "ApiFunction" in k
    }
    assert len(api_fns) == 1
    api_fn = next(iter(api_fns.values()))

    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert "STARTER_ENABLE_DEBUG_ENDPOINTS" not in env_vars, (
        "Prod stack must NOT enable debug endpoints. See spec Risk #5."
    )


def test_dev_stack_sets_debug_endpoints_env_var():
    app = cdk.App()
    stack = ChannelStack(app, "ChannelStack-dev", env_name="dev")
    template = assertions.Template.from_stack(stack)

    funcs = template.find_resources("AWS::Lambda::Function")
    api_fns = {k: v for k, v in funcs.items() if "ApiFunction" in k}
    api_fn = next(iter(api_fns.values()))

    env_vars = api_fn["Properties"]["Environment"]["Variables"]
    assert env_vars.get("STARTER_ENABLE_DEBUG_ENDPOINTS") == "1"


def test_lambda_role_can_invoke_agentcore_writes_and_lookups():
    app = cdk.App()
    stack = ChannelStack(app, "ChannelStack-dev", env_name="dev")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::IAM::Policy",
        assertions.Match.object_like({
            "PolicyDocument": {
                "Statement": assertions.Match.array_with([
                    assertions.Match.object_like({
                        "Action": assertions.Match.array_with([
                            "bedrock-agentcore:CreateEvent",
                            "bedrock-agentcore:ListEvents",
                            "bedrock-agentcore-control:ListMemories",
                            "bedrock-agentcore-control:CreateMemory",
                            "bedrock-agentcore-control:GetMemory",
                        ]),
                    }),
                ]),
            },
        }),
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_channel_stack.py -v`
Expected: All 3 tests FAIL (env var absent, IAM action absent).

- [ ] **Step 3: Implement CDK changes**

```python
# In infra/stacks/channel_stack.py — extend the existing Bedrock IAM
# PolicyStatement (or add a new one) with AgentCore actions:

api_role.add_to_policy(
    iam.PolicyStatement(
        actions=[
            "bedrock-agentcore:CreateEvent",
            "bedrock-agentcore:ListEvents",
            "bedrock-agentcore-control:ListMemories",
            "bedrock-agentcore-control:CreateMemory",
            "bedrock-agentcore-control:GetMemory",
        ],
        resources=[
            # Memory ARNs include an opaque suffix AgentCore appends.
            f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:memory/channel-{env_name}*",
            # ListMemories is account-scoped — no resource constraint
            # available for it; we narrow other actions via the suffix
            # pattern above.
        ],
    )
)

# Lambda environment additions:
common_env = {
    ...
    "STARTER_ENV": env_name,  # already there? confirm before adding
}
if env_name in {"dev", "jc"}:  # or whatever non-prod envs apply
    common_env["STARTER_ENABLE_DEBUG_ENDPOINTS"] = "1"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_channel_stack.py -v`
Expected: All PASS.

- [ ] **Step 5: Run synth to verify CloudFormation generation**

Run: `uv run inv synth`
Expected: success.

- [ ] **Step 6: Commit**

```bash
git add infra/stacks/channel_stack.py tests/unit/test_channel_stack.py
git commit -m "feat(channel-7c): CDK IAM grants for AgentCore + debug-flag env var"
```

---

## Task 10: `inv dev` sets the debug-endpoints flag

**Files:**
- Modify: `tasks.py`

- [ ] **Step 1: Locate the `dev_env` block in `tasks.py`**

```bash
grep -n "dev_env\|STARTER_BYPASS_GOOGLE_AUTH" tasks.py
```

- [ ] **Step 2: Add the env var**

```python
# In the dev_env dict in tasks.py:
dev_env = {
    ...
    "STARTER_BYPASS_GOOGLE_AUTH": "1",
    "STARTER_ENABLE_DEBUG_ENDPOINTS": "1",  # phase 7c — Playwright e2e
    "STARTER_ENV": "jc",  # used by get_or_create_memory naming
}
```

- [ ] **Step 3: Commit**

```bash
git add tasks.py
git commit -m "chore(channel-7c): inv dev sets STARTER_ENABLE_DEBUG_ENDPOINTS"
```

---

## Task 11: Playwright e2e — `tests/e2e/test_memory_writes.py`

**Files:**
- Create: `tests/e2e/test_memory_writes.py`

This is the validation contract from spec §Validation Layer 2. The test runs against a live `inv dev` stack + the developer's personal AWS account (Bedrock + AgentCore real).

- [ ] **Step 1: Confirm `tests/e2e/conftest.py` provides `live_admin_token` or equivalent**

```bash
cat tests/e2e/conftest.py
```

- [ ] **Step 2: Write the e2e test**

```python
# tests/e2e/test_memory_writes.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""End-to-end verification that AgentCore Memory writes land.

Phase 7c is write-only — there's no user-visible behavior change, so
this test asserts directly against the dev-only
``GET /api/_debug/memory/events`` endpoint after driving real chats
through the UI.

Requirements:

- ``inv dev`` running in another terminal (DDB Local + FastAPI on
  :8001 against real Bedrock + real AgentCore in the developer's
  personal AWS env, Vite on :5173).
- ``STARTER_ENABLE_DEBUG_ENDPOINTS=1`` (``inv dev`` sets this).
- Personal AWS credentials in env / ``~/.aws/credentials`` with
  Bedrock + AgentCore permissions.

Run::

    uv run inv e2e-local --tests tests/e2e/test_memory_writes.py
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx
import pytest
from playwright.async_api import async_playwright


@pytest.mark.asyncio
async def test_memory_writes_land_per_turn_and_isolate_per_actor_and_session():
    ui_url = os.environ["STARTER_UI_URL"]
    api_url = os.environ.get("STARTER_API_URL", "http://localhost:8001")

    tag = f"e2e-7c-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    user_a = f"{tag}-a@example.com"
    user_b = f"{tag}-b@example.com"

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            chat_a_id, chat_b_id, jwt_a = await _drive_chats_as_user(
                browser, ui_url, user_a, message_count_a=3, message_count_b=1,
            )
            _, _, jwt_b = await _drive_chats_as_user(
                browser, ui_url, user_b, message_count_a=0, message_count_b=0,
                login_only=True,
            )
        finally:
            await browser.close()

    # Layer-2 assertions: drive directly against the debug endpoint.
    events_a = _list_events(api_url, jwt_a, chat_a_id, limit=10)
    assert len(events_a) == 3, f"expected 3 events for chat A, got {len(events_a)}"

    events_b_chat_a_view = _list_events(api_url, jwt_a, chat_b_id, limit=10)
    assert len(events_b_chat_a_view) == 1, (
        f"expected 1 event for chat B (caller A), got {len(events_b_chat_a_view)}"
    )

    # Cross-actor isolation: user B looking at user A's chat sees zero.
    events_a_from_b = _list_events(api_url, jwt_b, chat_a_id, limit=10)
    assert events_a_from_b == [], (
        "user B must not see user A's events — actorId scoping is broken"
    )

    # Cleanup: tag-specific events are short-lived, but we delete them
    # so reruns within the same hour start clean.
    for evt in events_a + events_b_chat_a_view:
        _delete_event(api_url, jwt_a, chat_a_id if evt in events_a else chat_b_id, evt)


async def _drive_chats_as_user(
    browser: Any,
    ui_url: str,
    email: str,
    *,
    message_count_a: int,
    message_count_b: int,
    login_only: bool = False,
) -> tuple[str, str, str]:
    """Open browser, log in via test_email bypass, send N messages in
    each of two chats, return (chat_a_id, chat_b_id, jwt_token).

    When ``login_only`` is True, skip the message-sending and just
    return ("", "", jwt). Used to obtain user-B's JWT for the
    cross-actor isolation assertion.
    """
    context = await browser.new_context()
    page = await context.new_page()

    # Auth bypass — Vite proxy forwards to FastAPI which mints a JWT
    # when STARTER_BYPASS_GOOGLE_AUTH=1.
    await page.goto(f"{ui_url}/auth/login?test_email={email}")
    await page.wait_for_url(f"{ui_url}/app**", timeout=10_000)

    jwt = await page.evaluate("() => window.localStorage.getItem('starter_mgmt_token')")
    assert jwt, "auth bypass did not set starter_mgmt_token"

    if login_only:
        await context.close()
        return ("", "", jwt)

    chat_a_id = await _send_messages(page, message_count_a)
    chat_b_id = await _send_messages(page, message_count_b, new_chat=True)

    await context.close()
    return (chat_a_id, chat_b_id, jwt)


async def _send_messages(page: Any, count: int, *, new_chat: bool = False) -> str:
    """Send N messages in either the current chat or a fresh one.

    Returns the chat_id (parsed from the URL ``/app/c/:id``).
    """
    if new_chat:
        await page.click('button:has-text("+ New chat")')
        await page.wait_for_url("**/app", timeout=5_000)

    for i in range(count):
        await page.fill('textarea[placeholder*="essage" i]', f"e2e message {i}")
        await page.press('textarea[placeholder*="essage" i]', "Enter")
        # Wait for assistant turn to finish streaming.
        await page.wait_for_selector(
            '[data-testid="assistant-turn-idle"]', timeout=60_000,
        )

    url = page.url
    return url.rsplit("/", 1)[-1]


def _list_events(api_url: str, jwt: str, chat_id: str, limit: int) -> list[dict[str, Any]]:
    resp = httpx.get(
        f"{api_url}/api/_debug/memory/events",
        params={"chat_id": chat_id, "limit": limit},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()["events"]


def _delete_event(api_url: str, jwt: str, chat_id: str, event: dict[str, Any]) -> None:
    """Best-effort cleanup — ignore failures (the test already passed)."""
    try:
        httpx.delete(
            f"{api_url}/api/_debug/memory/events/{event['eventId']}",
            params={"chat_id": chat_id},
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=5.0,
        )
    except Exception:
        pass
```

- [ ] **Step 3: Add the corresponding `DELETE` route to `_debug.py` and a test (for the cleanup helper above)**

```python
# In src/channel/api/_debug.py — append:
@router.delete("/memory/events/{event_id}")
def delete_memory_event(
    event_id: str,
    chat_id: str = Query(...),
    jwt_claims: dict[str, Any] = Depends(require_mgmt_jwt),
) -> dict[str, str]:
    memory_id = get_or_create_memory(os.environ["STARTER_ENV"])
    client = boto3.client("bedrock-agentcore")
    client.delete_event(
        memoryId=memory_id,
        actorId=jwt_claims["sub"],
        sessionId=chat_id,
        eventId=event_id,
    )
    return {"status": "deleted"}
```

Add a unit test in `tests/unit/test_debug_api.py` covering it (mocked client + JWT scope assertion).

- [ ] **Step 4: Add `data-testid="assistant-turn-idle"` to Conversation.jsx**

The e2e test uses this selector to detect "assistant finished streaming." Add it where Conversation renders the final state.

```bash
grep -n "msg-actions\|streaming" ui/src/app/Conversation.jsx
```

Place the data attribute on the parent of the actions row that's only rendered when `!t.streaming`. Add a vitest assertion in `Conversation.test.jsx` that the attribute appears on settled assistant turns and is absent on streaming ones.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_memory_writes.py src/channel/api/_debug.py \
        tests/unit/test_debug_api.py ui/src/app/Conversation.jsx \
        ui/src/app/Conversation.test.jsx
git commit -m "feat(channel-7c): Playwright e2e for AgentCore writes + DELETE cleanup"
```

---

## Task 12: Local verification dry-run (BEFORE pre-push, BEFORE PR)

This is the Layer 2 validation. Do NOT skip it — that's exactly the failure mode the user called out in the memory note (`feedback_plans_include_local_verification`).

- [ ] **Step 1: Start the full local stack**

In a separate terminal:
```bash
uv run inv dev
```

Wait until you see "Uvicorn running on http://0.0.0.0:8001" and the Vite "Local: http://localhost:5173/" line.

- [ ] **Step 2: Reset the local DDB table**

```bash
uv run python scripts/reset_dev_table.py
```

Expected: `recreated channel with 4 GSIs (incl. ChatByIdIndex)`

- [ ] **Step 3: Run the Playwright e2e once**

```bash
uv run inv e2e-local --tests tests/e2e/test_memory_writes.py
```

Expected:
- 1 test passes.
- No `AccessDeniedException` in `inv dev` console output.
- `MemoryWriteSuccesses` log lines emitted (visible in `inv dev` console).

- [ ] **Step 4: Manual spot-check via the debug endpoint**

```bash
# Grab a JWT from the test_email bypass:
TOKEN=$(curl -s "http://localhost:8001/auth/login?test_email=spotcheck@example.com" \
  | jq -r .token)

# Send a chat through the UI manually, note its chat_id from the URL.
# Then:
curl -s "http://localhost:8001/api/_debug/memory/events?chat_id=<CHAT_ID>" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Expected: a non-empty events array with conversational payload entries matching what you typed.

- [ ] **Step 5: Repeat the e2e to catch flakes**

```bash
uv run inv e2e-local --tests tests/e2e/test_memory_writes.py --n 3
```

Expected: 3 consecutive passes.

- [ ] **Step 6: Tear down `inv dev`** (Ctrl-C)

---

## Task 13: CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add an `[Unreleased]` `### Added` entry**

```markdown
## [Unreleased]

### Added

- Phase 7c — agent now persists every chat turn to Bedrock AgentCore
  Memory via a Strands `AfterInvocationEvent` hook. Writes are
  fire-and-forget (never block the SSE response), failures swallowed
  with EMF + structured-log surfacing, and recall stays disabled
  (lands in 7d). Dev environments gain
  `GET /api/_debug/memory/events` for inspecting writes.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(channel-7c): CHANGELOG entry for AgentCore writes"
```

---

## Task 14: CLAUDE.md sync

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `## Structure`**

In the `agents/` block, add:
```
│       │   ├── chat_agent.py   # Strands Agent factory (build_agent / resolve_model_id)
│       │   ├── memory.py       # AgentCoreMemoryHook + get_or_create_memory (Phase 7c)
│       │   └── strands_sse.py  # Strands event → SSE byte translator
```

In the `api/` block, add (with the dev-only note):
```
│           ├── _debug.py       # /api/_debug/* — dev-only, gated by STARTER_ENABLE_DEBUG_ENDPOINTS
```

- [ ] **Step 2: Add a new `## AgentCore Memory` section** (placed after `## DynamoDB single table design`):

```markdown
## AgentCore Memory

Phase 7c onward, every chat turn is persisted to a Bedrock AgentCore
Memory resource via a Strands `AfterInvocationEvent` hook
(`src/channel/agents/memory.py`). Recall stays off until Phase 7d.

- **One Memory resource per environment** — named `channel-{env}`,
  discovered or created lazily on Lambda cold-start.
  `STARTER_AGENTCORE_MEMORY_NAME` overrides the name for personal dev
  environments that share a pre-existing Memory.
- **`actorId = jwt_sub`** — one actor per Channel user. Per-workspace
  partitioning (per the "workspaces are the tenancy root" product
  decision) will eventually become `actorId = f"{workspace_id}/{user_id}"`.
- **`sessionId = chat_id`** — one AgentCore session per Channel chat.
- **Failure mode**: log + EMF counter (`MemoryWriteFailures`) +
  swallow. Memory writes must not break chats. Loss-on-Lambda-freeze
  is acceptable (recall in 7d treats missing recall as "no memory").
- **`MemoryWriteSuccesses` / `MemoryWriteFailures`** are CloudWatch
  counters under namespace `Channel/Memory`, with NO per-actor or
  per-session dimensions (cardinality blowup risk).

Dev-only `GET /api/_debug/memory/events?chat_id=...&limit=...`
(`src/channel/api/_debug.py`) surfaces writes for Playwright e2e
verification. Mounted only when `STARTER_ENABLE_DEBUG_ENDPOINTS=1`;
prod never sets the flag (CDK assertion test in
`tests/unit/test_channel_stack.py` guards this).
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(channel-7c): CLAUDE.md sync — agents/memory.py + AgentCore section"
```

---

## Task 15: Pre-push + open PR

- [ ] **Step 1: Run full pre-push gate**

```bash
uv run inv pre-push
```

Expected: all green (lint + typecheck + unit + frontend).

- [ ] **Step 2: Run synth to confirm CDK changes work**

```bash
uv run inv synth
```

Expected: success.

- [ ] **Step 3: File the GitHub issue (required for `Closes #NNN` in PR body)**

```bash
gh issue create \
  --title "feat(channel-7c): AgentCore Memory writes via Strands hooks" \
  --label "status:ready,priority:p1,size:l,enhancement,infra" \
  --body "$(cat <<'EOF'
Implements Phase 7c — agent persists every chat turn to AgentCore
Memory via a Strands HookProvider subclass. Write-only by design;
recall lands in 7d.

Design: `docs/superpowers/specs/2026-05-31-phase-7c-agentcore-memory-writes-design.md`
Plan: `docs/superpowers/plans/2026-05-31-phase-7c-agentcore-memory-writes.md`

## Files to touch

- src/channel/agents/memory.py
- src/channel/api/_debug.py
- src/channel/agents/chat_agent.py
- src/channel/api/chats.py
- src/channel/api/main.py
- src/channel/metrics.py
- infra/stacks/channel_stack.py
- tasks.py
- CHANGELOG.md
- CLAUDE.md
- tests/unit/test_channel_stack.py
- tests/e2e/test_memory_writes.py
- ui/src/app/Conversation.jsx
EOF
)"
```

- [ ] **Step 4: Branch + push + open PR per CLAUDE.md "Opening a PR"**

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD  # must show only this phase's commits

git push -u origin feat/channel-7c-memory-writes:feat/channel-7c-memory-writes

gh pr create --base development \
  --title "feat(channel-7c): AgentCore Memory writes via Strands hooks" \
  --body "$(cat <<'EOF'
## Summary

- Implements Phase 7c — agent persists every chat turn to Bedrock
  AgentCore Memory. Write-only by design; recall stays disabled for
  7d. Strands 1.41 has no AgentCore Memory adapter, so the
  integration is a direct `HookProvider` subclass talking to boto3.
- One Memory resource per environment, `actorId=jwt_sub`,
  `sessionId=chat_id`. Lazy idempotent bootstrap with module-level
  cache. Fire-and-forget writes via `asyncio.create_task` so SSE
  responses never wait.
- Failures swallowed with EMF + structured-log surfacing — chats
  never break on AgentCore outages.
- Dev-only `GET /api/_debug/memory/events` endpoint gated by
  `STARTER_ENABLE_DEBUG_ENDPOINTS=1` for Playwright verification.
  CDK assertion test guards that prod never sets the flag.

Closes #<NNN-from-step-3>

## Test plan

- [x] Layer 1 — unit + integration + CDK assertion green (`inv pre-push`).
- [x] Layer 2 — Playwright e2e (`inv e2e-local --tests tests/e2e/test_memory_writes.py`) passed locally against personal AWS env, 3-run repeat check for flakiness clean.
- [ ] Layer 3 — after merge + dev deploy, verify `MemoryWriteSuccesses` EMF metric in CloudWatch (`Channel/Memory/MemoryWriteSuccesses`) increments on a real chat send at https://channel-dev.warlordofmars.net/app.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"

gh pr merge --auto --squash --delete-branch  # gate for auto-merge on green CI
```

NOTE: this PR is NOT eligible for `agent-safe` — it touches `infra/stacks/channel_stack.py`. The agent runs Copilot review then stops for human merge per CLAUDE.md.

- [ ] **Step 5: Watch CI**

```bash
gh run watch
```

- [ ] **Step 6: Layer 3 — after dev deploy completes, verify EMF metric in CloudWatch**

```bash
aws logs tail /aws/lambda/ChannelStack-dev-Api... \
    --filter-pattern '"MemoryWriteSuccesses"' --since 5m
```

Send a chat at `https://channel-dev.warlordofmars.net/app` first to generate traffic.

---

## Done criteria

- ✅ Phase 7c spec referenced by every implementation file's docstring.
- ✅ `src/channel/agents/memory.py` exists with 100% coverage.
- ✅ `AgentCoreMemoryHook` attached to every `build_agent(...)` call.
- ✅ EMF counters emit on every write attempt (success + failure).
- ✅ CDK IAM grants AgentCore actions on `arn:aws:bedrock-agentcore:*:*:memory/channel-{env}*`.
- ✅ Prod stack assertion test guards `STARTER_ENABLE_DEBUG_ENDPOINTS` from leaking to prod.
- ✅ Playwright e2e passes 3 consecutive runs against a real AWS env.
- ✅ CloudWatch shows `MemoryWriteSuccesses > 0` after dev deploy.
- ✅ CHANGELOG + CLAUDE.md updated.
