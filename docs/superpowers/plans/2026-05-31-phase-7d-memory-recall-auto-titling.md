# Phase 7d — Memory recall + auto-titling implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out the AgentCore Memory loop. Phase 7c shipped writes; this phase adds (1) recall via a `BeforeInvocationEvent` hook that injects `RetrieveMemoryRecords` results as a system-prompt addendum, and (2) auto-titling via a one-shot Haiku Strands `Agent` invoked after the first round-trip, emitted inline in the existing SSE stream as a new `title_suggested` event.

**Architecture:** Two new pieces wired into the existing chat agent loop. A `HookProvider` subclass (`AgentCoreRecallHook`) subscribes to `BeforeInvocationEvent`, queries AgentCore semantic search (scoped by `actorId`, score≥0.7, topK=5), and mutates `event.messages[0]` with a Markdown addendum. A module-level `(actor_id, chat_id) → CacheEntry` cache reduces RPC volume to ~one query per 5 turns. A separate `build_titler_agent()` constructs a Haiku-backed `Agent` with NO memory hooks and `max_tokens=20`; `_stream_bedrock_reply` invokes it after the assistant `done` event when `message_count == 0` at function entry, persists via `storage.patch_chat`, and emits a new `sse_title_suggested(chat_id, title)` frame before stream close. Both features fail-soft (log + EMF + swallow) and ship behind `STARTER_RECALL_ENABLED` / `STARTER_AUTO_TITLE_ENABLED` env-var kill-switches.

**Tech Stack:** Strands Agents 1.41 (`HookProvider`, `BeforeInvocationEvent`, second-Agent pattern), boto3 1.42 (`bedrock-agentcore.RetrieveMemoryRecords`), FastAPI streaming response with multi-event SSE, AWS CDK Python (IAM extension), CloudWatch EMF (no per-actor dimensions), Playwright async API.

**Spec:** `docs/superpowers/specs/2026-05-31-phase-7d-memory-recall-auto-titling-design.md` — read it before starting Task 1. Risks §1 (eventual consistency), §3 (recall noise), and §5 (title hallucination) drove specific test choices below; don't undo them.

---

## File map

**Create:**
- `src/channel/agents/recall.py` — `AgentCoreRecallHook` + helpers + cache (~150 LOC)
- `tests/unit/test_recall.py` — covers `recall.py` (100%)
- `tests/e2e/test_memory_recall_and_titling.py` — Playwright e2e

**Modify:**
- `src/channel/agents/chat_agent.py` — `build_agent` attaches recall hook; new `build_titler_agent()`
- `src/channel/api/chats.py` — capture first-round-trip flag; auto-title block after `done` event; emit `title_suggested`
- `src/channel/agents/strands_sse.py` — `sse_title_suggested(chat_id, title)`
- `src/channel/metrics.py` — `record_recall_outcome` + `record_auto_title_outcome`
- `infra/stacks/channel_stack.py` — IAM adds `bedrock-agentcore:RetrieveMemoryRecords`
- `tasks.py` — `inv dev` sets both kill-switches explicit-on
- `tests/unit/test_chat_agent.py` — both-hooks-attached + titler shape
- `tests/unit/test_chats_api.py` — title_suggested SSE flow + idempotency + disable
- `tests/unit/test_strands_sse.py` — title_suggested frame shape
- `tests/unit/test_channel_stack.py` — RetrieveMemoryRecords IAM
- `ui/src/lib/sseParser.js` + `.test.js` — title_suggested event type
- `ui/src/hooks/useChatStream.js` + `.test.js` — onTitleSuggested callback
- `ui/src/hooks/ChatsContext.jsx` + `.test.jsx` — renameChat action
- `ui/src/app/Conversation.jsx` + `.test.jsx` — wire callback to context
- `CHANGELOG.md`, `CLAUDE.md`

---

## Task 1: `_format_recall_addendum` helper + tests

**Files:**
- Create: `src/channel/agents/recall.py`
- Test: `tests/unit/test_recall.py`

The smallest, purely-functional piece of the recall integration. Start here so we have a green test before any boto3 mocking.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_recall.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for AgentCore Memory recall hook + helpers."""

from __future__ import annotations

import pytest

from channel.agents.recall import _format_recall_addendum


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
        {"content": {}, "score": 0.85},                  # no text key
        {"score": 0.8},                                  # no content key
        {"content": {"text": ""}, "score": 0.75},        # empty text
    ]

    result = _format_recall_addendum(records)

    assert "- valid record" in result
    # Heading appears once, no empty bullets.
    assert result.count("- ") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_recall.py -v`
Expected: `ModuleNotFoundError: No module named 'channel.agents.recall'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/channel/agents/recall.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Bedrock AgentCore Memory recall via Strands hooks.

Phase 7d implementation. Subscribes to ``BeforeInvocationEvent`` and
injects relevant prior-conversation context (scoped to the caller's
``actorId``) into the system prompt as a Markdown addendum.

Design rationale: ``docs/superpowers/specs/2026-05-31-phase-7d-memory-recall-auto-titling-design.md``.

Strands 1.41 has no native AgentCore Memory adapter (verified in the
spike at ``docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md``)
so this module talks to AgentCore directly via boto3 — same pattern as
the 7c write hook in ``agents/memory.py``.
"""

from __future__ import annotations

from typing import Any

_RECALL_HEADING = "## What I remember about previous conversations"


def _format_recall_addendum(records: list[dict[str, Any]]) -> str:
    """Render ``MemoryRecordSummary`` records as a Markdown addendum.

    Returns the empty string when no records have usable text content.
    Defensive: records missing ``content.text`` or with empty text are
    silently skipped so a malformed AgentCore response can't corrupt
    the system prompt.
    """
    bullets: list[str] = []
    for rec in records:
        text = rec.get("content", {}).get("text") if isinstance(rec, dict) else None
        if text:
            bullets.append(f"- {text}")
    if not bullets:
        return ""
    return _RECALL_HEADING + "\n\n" + "\n".join(bullets)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_recall.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Coverage check**

Run: `uv run pytest tests/unit/test_recall.py --cov=channel.agents.recall --cov-report=term-missing`
Expected: 100% on `recall.py`

- [ ] **Step 6: Commit**

```bash
git add src/channel/agents/recall.py tests/unit/test_recall.py
git commit -m "feat(channel-7d): _format_recall_addendum helper for system-prompt injection"
```

---

## Task 2: `record_recall_outcome` + `record_auto_title_outcome` EMF metrics

**Files:**
- Modify: `src/channel/metrics.py`
- Modify: `tests/unit/test_metrics.py`

Plug the EMF surface BEFORE the hook needs it. Same locked-signature pattern as `record_memory_write_outcome` so a future caller can't slip a per-actor dimension through (Risk #6).

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/unit/test_metrics.py
@pytest.mark.asyncio
async def test_record_recall_outcome_success_emits_success_counter():
    from channel.metrics import record_recall_outcome
    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_recall_outcome(success=True)
    mock_emit.assert_awaited_once_with("RecallSuccesses")


@pytest.mark.asyncio
async def test_record_recall_outcome_failure_emits_failure_counter():
    from channel.metrics import record_recall_outcome
    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_recall_outcome(success=False)
    mock_emit.assert_awaited_once_with("RecallFailures")


def test_record_recall_outcome_signature_locks_out_dimensions():
    from channel.metrics import record_recall_outcome
    sig = inspect.signature(record_recall_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    assert sig.parameters["success"].annotation == "bool"


@pytest.mark.asyncio
async def test_record_auto_title_outcome_success_emits_success_counter():
    from channel.metrics import record_auto_title_outcome
    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_auto_title_outcome(success=True)
    mock_emit.assert_awaited_once_with("AutoTitleSuccesses")


@pytest.mark.asyncio
async def test_record_auto_title_outcome_failure_emits_failure_counter():
    from channel.metrics import record_auto_title_outcome
    with patch("channel.metrics.emit_metric", new=AsyncMock()) as mock_emit:
        await record_auto_title_outcome(success=False)
    mock_emit.assert_awaited_once_with("AutoTitleFailures")


def test_record_auto_title_outcome_signature_locks_out_dimensions():
    from channel.metrics import record_auto_title_outcome
    sig = inspect.signature(record_auto_title_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    assert sig.parameters["success"].annotation == "bool"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_metrics.py -v -k "recall or auto_title"`
Expected: `ImportError: cannot import name 'record_recall_outcome'`

- [ ] **Step 3: Implement**

```python
# Append to src/channel/metrics.py
async def record_recall_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one RetrieveMemoryRecords attempt.

    Counter-only — same Risk #3-from-7c rationale: per-actor dimensions
    blow up CloudWatch metric volume. Signature accepts only ``success``
    so a future caller cannot accidentally add an ``actor_id`` kwarg.
    """
    metric = "RecallSuccesses" if success else "RecallFailures"
    await emit_metric(metric)


async def record_auto_title_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one auto-title attempt.

    Counter-only — same Risk #3-from-7c rationale.
    """
    metric = "AutoTitleSuccesses" if success else "AutoTitleFailures"
    await emit_metric(metric)
```

- [ ] **Step 4: Run tests + coverage**

Run: `uv run pytest tests/unit/test_metrics.py --cov=channel.metrics --cov-report=term-missing`
Expected: all PASS, 100% coverage on `metrics.py`

- [ ] **Step 5: Commit**

```bash
git add src/channel/metrics.py tests/unit/test_metrics.py
git commit -m "feat(channel-7d): record_recall_outcome + record_auto_title_outcome EMF counters"
```

---

## Task 3: `AgentCoreRecallHook` class + cache + boto3 call

**Files:**
- Modify: `src/channel/agents/recall.py`
- Modify: `tests/unit/test_recall.py`

The HookProvider subscription, the `RetrieveMemoryRecords` boto3 call, the per-`(actor_id, chat_id)` cache, the score-threshold filter + topK cap, and the log+EMF+swallow failure path.

- [ ] **Step 1: Read existing Strands hook surface**

Quick recall: `BeforeInvocationEvent` is defined in
`strands.hooks.events`, the event's `.agent` exposes `messages`, and the
hook's `register_hooks(registry)` calls `registry.add_callback(EventType, handler)`.
The hook callback can mutate `event.messages` per the spike.

```bash
grep -nE "BeforeInvocationEvent|class HookEvent" .venv/lib/python3.12/site-packages/strands/hooks/events.py | head
```

- [ ] **Step 2: Write the failing tests**

```python
# Append to tests/unit/test_recall.py
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from strands.hooks.events import BeforeInvocationEvent

from channel.agents import recall as recall_module
from channel.agents.recall import (
    _RECALL_SCORE_THRESHOLD,
    _RECALL_TOP_K,
    AgentCoreRecallHook,
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
    fake_event = MagicMock()
    fake_event.agent = fake_agent
    fake_event.messages = msgs
    return fake_event


def test_hook_registers_before_invocation_callback_only():
    """Recall hook subscribes ONLY to BeforeInvocationEvent — NOT
    AfterInvocationEvent (that's the write hook's job)."""
    hook = AgentCoreRecallHook(
        memory_id="m-1", actor_id="alice", client=MagicMock(),
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
        memory_id="m-1", actor_id="alice_example_com", client=fake_client,
    )
    event = _fake_before_event(user_text="what colour do i like?")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()) as mock_record:
        await hook._on_before_invocation_async(event, chat_id="chat-1")

    # The system prompt grew an addendum mentioning the recalled bullets.
    sys_text = event.messages[0]["content"][0]["text"]
    assert "## What I remember about previous conversations" in sys_text
    assert "chess engines" in sys_text
    assert "sage green" in sys_text

    fake_client.retrieve_memory_records.assert_called_once()
    kwargs = fake_client.retrieve_memory_records.call_args.kwargs
    assert kwargs["memoryId"] == "m-1"
    assert kwargs["actorId"] == "alice_example_com"
    assert kwargs["searchCriteria"]["searchQuery"] == "what colour do i like?"
    assert kwargs["searchCriteria"]["topK"] == _RECALL_TOP_K
    mock_record.assert_awaited_once_with(success=True)


@pytest.mark.asyncio
async def test_hook_filters_records_below_score_threshold():
    fake_client = MagicMock()
    fake_client.retrieve_memory_records.return_value = {
        "memoryRecordSummaries": [
            {"content": {"text": "Highly relevant"}, "score": 0.95},
            {"content": {"text": "Borderline"}, "score": 0.65},   # below threshold
            {"content": {"text": "Also high"}, "score": 0.82},
        ],
    }
    hook = AgentCoreRecallHook(
        memory_id="m", actor_id="a", client=fake_client,
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
        memory_id="m", actor_id="a", client=fake_client,
    )
    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        for _ in range(5):
            event = _fake_before_event(user_text="anything")
            await hook._on_before_invocation_async(event, chat_id="chat-1")

    # Only ONE RPC across 5 turns — the cache hit on turns 2-5.
    fake_client.retrieve_memory_records.assert_called_once()


@pytest.mark.asyncio
async def test_hook_refreshes_cache_after_5_turns():
    fake_client = MagicMock()
    fake_client.retrieve_memory_records.return_value = {
        "memoryRecordSummaries": [{"content": {"text": "x"}, "score": 0.9}],
    }
    hook = AgentCoreRecallHook(
        memory_id="m", actor_id="a", client=fake_client,
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
        memory_id="m", actor_id="a", client=fake_client,
    )
    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(_fake_before_event("q1"), chat_id="A")
        await hook._on_before_invocation_async(_fake_before_event("q2"), chat_id="B")
    # Two distinct chats → two cold-cache RPCs.
    assert fake_client.retrieve_memory_records.call_count == 2


@pytest.mark.asyncio
async def test_hook_swallows_retrieve_failures_and_emits_failure_metric():
    fake_client = MagicMock()
    fake_client.retrieve_memory_records.side_effect = RuntimeError("agentcore down")
    hook = AgentCoreRecallHook(
        memory_id="m", actor_id="a", client=fake_client,
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
        memory_id="m", actor_id="a", client=fake_client,
    )
    event = _fake_before_event(user_text="...")
    await hook._on_before_invocation_async(event, chat_id="chat-1")
    fake_client.retrieve_memory_records.assert_not_called()


def test_hook_after_invocation_schedules_async_recall(monkeypatch):
    """The sync callback must fire-and-forget via asyncio.create_task,
    not block the agent loop (Sonar python:S7502 same pattern as 7c)."""
    hook = AgentCoreRecallHook(memory_id="m", actor_id="a", client=MagicMock())
    event = _fake_before_event(user_text="anything")
    event.agent.chat_id = "chat-1"  # the hook reads chat_id off the agent

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
```

Note: the hook needs `chat_id` from somewhere on the BeforeInvocationEvent. Strands doesn't carry chat_id natively, so we attach it to the `agent` object when building the agent in Task 4 (set `agent.chat_id = chat_id` as a custom attribute). The sync callback reads it from `event.agent.chat_id`. Tests above use that contract.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_recall.py -v`
Expected: `ImportError: cannot import name 'AgentCoreRecallHook'`

- [ ] **Step 4: Implement**

```python
# Append to src/channel/agents/recall.py
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import boto3
from strands.hooks.events import BeforeInvocationEvent

from channel.metrics import record_recall_outcome

logger = logging.getLogger(__name__)

_RECALL_SCORE_THRESHOLD: float = 0.7
_RECALL_TOP_K: int = 5
_RECALL_CACHE_REFRESH_TURNS: int = 5


@dataclass
class CacheEntry:
    """Per-(actor, chat) recall cache row. ``age`` increments on cache
    hits; ``records`` is the score-filtered ``MemoryRecordSummary`` list
    from the most recent ``RetrieveMemoryRecords`` response."""
    records: list[dict[str, Any]]
    age: int


# Module-level cache keyed by ``(actor_id, chat_id)``. Survives across
# requests within a warm Lambda instance; cold-start invalidates.
_recall_cache: dict[tuple[str, str], CacheEntry] = {}


class AgentCoreRecallHook:
    """Strands ``HookProvider`` that injects AgentCore Memory recall
    into the system prompt before each turn.

    Subscribes to ``BeforeInvocationEvent``; runs ``RetrieveMemoryRecords``
    scoped to the caller's ``actorId``; filters by score; injects the
    survivors as a Markdown system-prompt addendum. Failures are logged
    + EMF-counted + swallowed — recall must not break chats.

    Conforms to the ``HookProvider`` protocol (``strands.hooks.registry``)
    structurally; no explicit base class — Strands uses ``@runtime_checkable``.

    See ``docs/superpowers/specs/2026-05-31-phase-7d-memory-recall-auto-titling-design.md``
    §Recall trigger + caching and §Recall injection format for rationale.
    """

    def __init__(
        self,
        memory_id: str,
        actor_id: str,
        client: Any | None = None,
    ) -> None:
        self._memory_id = memory_id
        self._actor_id = actor_id
        self._client = client if client is not None else boto3.client("bedrock-agentcore")
        self._pending_recalls: set[asyncio.Task[None]] = set()

    def register_hooks(self, registry: Any, **_: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self._on_before_invocation)

    def _on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        """Sync entry point — schedule the async recall, return immediately.

        chat_id is carried on ``event.agent.chat_id`` (set at agent-build
        time in chat_agent.build_agent). Strands' agent doesn't expose
        chat_id natively; this is a Channel-specific attribute.
        """
        chat_id = getattr(event.agent, "chat_id", None) or ""
        task = asyncio.create_task(
            self._on_before_invocation_async(event, chat_id=chat_id)
        )
        self._pending_recalls.add(task)
        task.add_done_callback(self._pending_recalls.discard)

    async def _on_before_invocation_async(
        self, event: BeforeInvocationEvent, *, chat_id: str,
    ) -> None:
        """Async recall path. Log + swallow on failure."""
        if os.environ.get("STARTER_RECALL_ENABLED", "1") != "1":
            return

        try:
            records = await self._get_or_fetch_records(
                user_message=_extract_user_message(event), chat_id=chat_id,
            )
            addendum = _format_recall_addendum(records)
            if addendum:
                _append_to_system_prompt(event, addendum)
            await record_recall_outcome(success=True)
        except Exception as exc:
            logger.warning(
                "agentcore.recall_failed actor_id=%s chat_id=%s",
                self._actor_id,
                chat_id,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )
            await record_recall_outcome(success=False)

    async def _get_or_fetch_records(
        self, *, user_message: str, chat_id: str,
    ) -> list[dict[str, Any]]:
        """Return cached records when fresh; refetch when stale or missing."""
        key = (self._actor_id, chat_id)
        cache_entry = _recall_cache.get(key)
        if cache_entry is not None and cache_entry.age < _RECALL_CACHE_REFRESH_TURNS:
            cache_entry.age += 1
            return cache_entry.records

        resp = await asyncio.to_thread(
            self._client.retrieve_memory_records,
            memoryId=self._memory_id,
            actorId=self._actor_id,
            searchCriteria={"searchQuery": user_message, "topK": _RECALL_TOP_K},
        )
        records = [
            rec
            for rec in resp.get("memoryRecordSummaries", [])
            if isinstance(rec, dict) and rec.get("score", 0) >= _RECALL_SCORE_THRESHOLD
        ]
        _recall_cache[key] = CacheEntry(records=records, age=0)
        return records


def _extract_user_message(event: BeforeInvocationEvent) -> str:
    """Read the most recent user-turn text off the event's messages."""
    for msg in reversed(event.messages):
        if msg.get("role") == "user":
            for block in msg.get("content", []):
                if "text" in block:
                    return str(block["text"])
    return ""


def _append_to_system_prompt(event: BeforeInvocationEvent, addendum: str) -> None:
    """Mutate the system message in place to append the recall block.

    Strands' system prompt is always at ``event.messages[0]``; we append
    the addendum to its first text block. Defensive: if the system
    message has no text block (shouldn't happen with our prompt), we
    create one.
    """
    sys_msg = event.messages[0]
    content = sys_msg.setdefault("content", [])
    if content and "text" in content[0]:
        content[0]["text"] = content[0]["text"] + "\n\n" + addendum
    else:
        content.append({"text": addendum})
```

- [ ] **Step 5: Run tests + coverage**

Run: `uv run pytest tests/unit/test_recall.py --cov=channel.agents.recall --cov-report=term-missing`
Expected: all PASS, 100%

- [ ] **Step 6: Commit**

```bash
git add src/channel/agents/recall.py tests/unit/test_recall.py
git commit -m "feat(channel-7d): AgentCoreRecallHook with score-filtered cached injection"
```

---

## Task 4: Wire recall hook into `build_agent`

**Files:**
- Modify: `src/channel/agents/chat_agent.py`
- Modify: `tests/unit/test_chat_agent.py`

The build path attaches BOTH hooks. We also attach `chat_id` to the agent object so the recall hook's sync callback can read it off `event.agent.chat_id`.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/unit/test_chat_agent.py
def test_build_agent_attaches_recall_hook_before_write_hook(monkeypatch):
    """Recall hook MUST come first so BeforeInvocationEvent fires before
    the write hook's AfterInvocationEvent registration matters."""
    captured: dict[str, object] = {}
    fake_recall = MagicMock(name="recall_hook")
    fake_write = MagicMock(name="write_hook")

    def recall_factory(**kwargs):
        captured["recall_kwargs"] = kwargs
        return fake_recall

    def write_factory(**kwargs):
        captured["write_kwargs"] = kwargs
        return fake_write

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", lambda **_: object())
    monkeypatch.setattr("channel.agents.chat_agent.Agent", FakeAgent)
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreRecallHook", recall_factory)
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreMemoryHook", write_factory)
    monkeypatch.setattr(
        "channel.agents.chat_agent.get_or_create_memory",
        lambda env: f"channel_{env}_MEMID",
    )

    build_agent(
        model_id="claude-sonnet-4-6", user_id="user-abc", chat_id="chat-xyz",
    )

    # Both hooks instantiated with the same memory id + actor id.
    assert captured["recall_kwargs"] == {
        "memory_id": "channel_test_MEMID",
        "actor_id": "user-abc",
    }
    # Order: recall (Before) first, write (After) second.
    assert captured["agent_kwargs"]["hooks"] == [fake_recall, fake_write]


def test_build_agent_attaches_chat_id_to_agent_instance(monkeypatch):
    """The recall hook reads chat_id off ``event.agent.chat_id``; set
    it at construction time."""
    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", lambda **_: object())
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreMemoryHook", lambda **_: object())
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreRecallHook", lambda **_: object())
    monkeypatch.setattr(
        "channel.agents.chat_agent.get_or_create_memory", lambda env: "m",
    )

    class FakeAgent:
        def __init__(self, **kwargs):
            self._kwargs = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.Agent", FakeAgent)

    agent = build_agent(
        model_id="claude-sonnet-4-6", user_id="u", chat_id="chat-zzz",
    )
    assert agent.chat_id == "chat-zzz"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_chat_agent.py -v -k "recall or chat_id_to_agent"`
Expected: `AttributeError: 'FakeAgent' object has no attribute 'chat_id'` or similar.

- [ ] **Step 3: Implement**

```python
# In src/channel/agents/chat_agent.py — extend imports:
from channel.agents.recall import AgentCoreRecallHook

# Replace build_agent's hook construction + return:
def build_agent(
    *,
    model_id: str,
    user_id: str,
    chat_id: str,
    prior_messages: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Agent:
    ...  # bedrock, memory_hook unchanged
    memory_id = get_or_create_memory(os.environ["STARTER_ENV"])
    recall_hook = AgentCoreRecallHook(memory_id=memory_id, actor_id=user_id)
    memory_hook = AgentCoreMemoryHook(
        memory_id=memory_id, actor_id=user_id, session_id=chat_id,
    )
    agent = Agent(
        model=bedrock,
        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        hooks=[recall_hook, memory_hook],  # Before first, After second
        messages=cast(Messages, prior_messages or []),
    )
    agent.chat_id = chat_id  # carried into BeforeInvocationEvent.agent
    return agent
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_chat_agent.py -v`
Expected: All tests PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/chat_agent.py tests/unit/test_chat_agent.py
git commit -m "feat(channel-7d): attach recall hook + chat_id to agent in build_agent"
```

---

## Task 5: `build_titler_agent()` factory

**Files:**
- Modify: `src/channel/agents/chat_agent.py`
- Modify: `tests/unit/test_chat_agent.py`

A separate small Strands Agent dedicated to the auto-title call. NO memory hooks (we don't want titling events landing in AgentCore Memory). Haiku model, tight token cap.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/unit/test_chat_agent.py
def test_build_titler_agent_uses_haiku_with_no_hooks(monkeypatch):
    captured: dict[str, object] = {}

    class FakeBedrockModel:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", FakeBedrockModel)
    monkeypatch.setattr("channel.agents.chat_agent.Agent", FakeAgent)

    from channel.agents.chat_agent import build_titler_agent
    build_titler_agent()

    # Haiku id, max_tokens=20 (tight title cap), no memory hooks attached.
    assert "haiku" in captured["model_kwargs"]["model_id"].lower()
    assert captured["model_kwargs"]["max_tokens"] == 20
    assert captured["agent_kwargs"].get("hooks", []) == []


def test_build_titler_agent_respects_starter_titler_model_override(monkeypatch):
    monkeypatch.setenv("STARTER_TITLER_MODEL", "claude-sonnet-4-6")
    captured: dict[str, object] = {}

    class FakeBedrockModel:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", FakeBedrockModel)
    monkeypatch.setattr("channel.agents.chat_agent.Agent", lambda **_: object())

    from channel.agents.chat_agent import build_titler_agent
    build_titler_agent()
    assert captured["model_kwargs"]["model_id"] == "us.anthropic.claude-sonnet-4-6"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_chat_agent.py -v -k titler`
Expected: `ImportError: cannot import name 'build_titler_agent'`

- [ ] **Step 3: Implement**

```python
# Append to src/channel/agents/chat_agent.py
_DEFAULT_TITLER_MODEL = "claude-haiku-4-5"
_TITLER_MAX_TOKENS = 20

_TITLER_SYSTEM_PROMPT = (
    "Summarise the following exchange in 3-6 words, sentence case, no "
    "quotes, no trailing punctuation. The summary becomes the chat's "
    "title in the sidebar."
)


def build_titler_agent() -> Agent:
    """Build a one-shot Strands Agent for auto-title generation.

    Cheap model (Haiku by default; ``STARTER_TITLER_MODEL`` overrides),
    tight max_tokens, NO memory hooks — we don't want titling events
    polluting AgentCore Memory. Caller invokes ``stream_async`` with the
    user/assistant pair and reads the accumulated text.
    """
    titler_model_id = os.environ.get("STARTER_TITLER_MODEL", _DEFAULT_TITLER_MODEL)
    bedrock = BedrockModel(
        model_id=resolve_model_id(titler_model_id),
        max_tokens=_TITLER_MAX_TOKENS,
    )
    return Agent(
        model=bedrock,
        system_prompt=_TITLER_SYSTEM_PROMPT,
        hooks=[],
    )
```

- [ ] **Step 4: Run tests + coverage**

Run: `uv run pytest tests/unit/test_chat_agent.py --cov=channel.agents.chat_agent --cov-report=term-missing`
Expected: PASS, 100%.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/chat_agent.py tests/unit/test_chat_agent.py
git commit -m "feat(channel-7d): build_titler_agent — one-shot Haiku Agent with no hooks"
```

---

## Task 6: `sse_title_suggested` event helper + tests

**Files:**
- Modify: `src/channel/agents/strands_sse.py`
- Modify: `tests/unit/test_strands_sse.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/unit/test_strands_sse.py
def test_sse_title_suggested_emits_event_and_data_frames():
    from channel.agents.strands_sse import sse_title_suggested

    frame = sse_title_suggested(chat_id="chat-1", title="Pytest fixture debug")
    # SSE spec: event-line, data-line, blank line terminator.
    assert b"event: title_suggested" in frame
    assert b"chat-1" in frame
    assert b"Pytest fixture debug" in frame
    assert frame.endswith(b"\n\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_strands_sse.py::test_sse_title_suggested_emits_event_and_data_frames -v`
Expected: `ImportError: cannot import name 'sse_title_suggested'`

- [ ] **Step 3: Implement** (match the existing `sse_done` / `sse_delta` style)

```python
# Append to src/channel/agents/strands_sse.py
def sse_title_suggested(chat_id: str, title: str) -> bytes:
    """Emit a ``title_suggested`` SSE frame.

    Phase 7d event. The SPA's useChatStream forwards the title to
    ChatsContext.renameChat which updates the sidebar.
    """
    payload = json.dumps({"chat_id": chat_id, "title": title})
    return f"event: title_suggested\ndata: {payload}\n\n".encode()
```

- [ ] **Step 4: Run tests + coverage**

Run: `uv run pytest tests/unit/test_strands_sse.py --cov=channel.agents.strands_sse --cov-report=term-missing`
Expected: PASS, 100%.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/strands_sse.py tests/unit/test_strands_sse.py
git commit -m "feat(channel-7d): sse_title_suggested event helper"
```

---

## Task 7: Wire auto-title block into `_stream_bedrock_reply`

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

After the `done` SSE event, when `was_first_round_trip and STARTER_AUTO_TITLE_ENABLED == "1"`, invoke the titler, persist, emit `title_suggested`. Failure logs + swallows.

- [ ] **Step 1: Read existing `_stream_bedrock_reply` shape**

```bash
grep -nE "def _stream_bedrock_reply|sse_done|message_count|was_first" src/channel/api/chats.py | head -10
```

- [ ] **Step 2: Write the failing tests**

```python
# Append to tests/unit/test_chats_api.py
def test_post_message_emits_title_suggested_on_first_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First round-trip (chat.message_count == 0 at entry) must emit a
    title_suggested event AFTER done and BEFORE stream close."""
    chat = Chat(
        chat_id="c1", user_id="u-1", title="New chat",
        created_at="t", last_message_at="t", model_default="claude-sonnet-4-6",
        message_count=0,
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id=kwargs["chat_id"], msg_id="m", role=kwargs["role"],
            text=kwargs["text"], model=kwargs.get("model"), created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages", lambda *_a, **_kw: ([], None),
    )

    patched_titles: list[str] = []

    def fake_patch_chat(**kwargs):
        patched_titles.append(kwargs["title"])

    monkeypatch.setattr("channel.api.chats.storage.patch_chat", fake_patch_chat)

    # Main agent stream — minimal done event.
    async def fake_stream(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Hi"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    # Titler — yields a deterministic title.
    async def fake_titler_stream(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Quick chat title"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeTitler:
        stream_async = fake_titler_stream

    monkeypatch.setattr("channel.api.chats.build_titler_agent", lambda: FakeTitler())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    body = response.text

    assert "event: title_suggested" in body
    assert '"title": "Quick chat title"' in body
    # Order: done event MUST appear before title_suggested.
    assert body.find("event: title_suggested") > body.find('"type": "done"')
    assert patched_titles == ["Quick chat title"]


def test_post_message_skips_titler_on_non_first_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chat with message_count > 0 must NOT emit title_suggested."""
    chat = Chat(
        chat_id="c1", user_id="u-1", title="Existing title",
        created_at="t", last_message_at="t", model_default="claude-sonnet-4-6",
        message_count=4,
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id=kwargs["chat_id"], msg_id="m", role=kwargs["role"],
            text=kwargs["text"], created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages", lambda *_a, **_kw: ([], None),
    )

    titler_called = []
    monkeypatch.setattr(
        "channel.api.chats.build_titler_agent",
        lambda: titler_called.append(True) or object(),
    )

    async def fake_stream(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    assert "event: title_suggested" not in response.text
    assert titler_called == []


def test_post_message_titler_failure_is_swallowed_and_stream_completes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Titler error MUST NOT break the user's stream. Done event still
    lands; title_suggested is omitted; chat keeps its default title."""
    chat = Chat(
        chat_id="c1", user_id="u-1", title="New chat",
        created_at="t", last_message_at="t", model_default="claude-sonnet-4-6",
        message_count=0,
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id=kwargs["chat_id"], msg_id="m", role=kwargs["role"],
            text=kwargs["text"], created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages", lambda *_a, **_kw: ([], None),
    )
    patched = []
    monkeypatch.setattr(
        "channel.api.chats.storage.patch_chat",
        lambda **kwargs: patched.append(kwargs),
    )

    async def fake_stream(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    async def fake_titler_stream(self, prompt):
        raise RuntimeError("haiku unavailable")
        yield  # unreachable; satisfies generator typing

    class FakeTitler:
        stream_async = fake_titler_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())
    monkeypatch.setattr("channel.api.chats.build_titler_agent", lambda: FakeTitler())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    body = response.text
    assert '"type": "done"' in body
    assert "event: title_suggested" not in body
    assert patched == []


def test_post_message_respects_auto_title_kill_switch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = Chat(
        chat_id="c1", user_id="u-1", title="t",
        created_at="t", last_message_at="t", model_default="claude-sonnet-4-6",
        message_count=0,
    )
    monkeypatch.setenv("STARTER_AUTO_TITLE_ENABLED", "0")
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kwargs: Message(
            chat_id="c1", msg_id="m", role=kwargs["role"], text=kwargs["text"], created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages", lambda *_a, **_kw: ([], None),
    )
    titler_built = []
    monkeypatch.setattr(
        "channel.api.chats.build_titler_agent",
        lambda: titler_built.append(True) or object(),
    )

    async def fake_stream(self, prompt):
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    assert "event: title_suggested" not in response.text
    assert titler_built == []
```

- [ ] **Step 3: Implement** in `_stream_bedrock_reply`

```python
# In src/channel/api/chats.py — extend imports:
import os
from channel.agents.chat_agent import build_agent, build_titler_agent, resolve_model_id
from channel.agents.strands_sse import (
    sse_delta, sse_done, sse_title_suggested, sse_user_persisted, translate_event,
)
from channel.metrics import record_auto_title_outcome

# Inside _stream_bedrock_reply, capture the flag at the START:
async def _stream_bedrock_reply(...):
    state = state if state is not None else {}
    resolved_model = resolve_model_id(model)
    state["resolved_model"] = resolved_model
    was_first_round_trip = chat.message_count == 0
    ...

# After yielding sse_done(...) but BEFORE the function ends, add:
    yield sse_done(...)  # existing

    if (
        was_first_round_trip
        and os.environ.get("STARTER_AUTO_TITLE_ENABLED", "1") == "1"
    ):
        try:
            titler = build_titler_agent()
            titler_prompt = (
                f"User: {user_message}\n"
                f"Assistant: {assistant_text[:500]}"
            )
            title_accum: list[str] = []
            async for event in titler.stream_async(titler_prompt):
                kind, payload = translate_event(event)
                if kind == "delta":
                    title_accum.append(payload)
            title = "".join(title_accum).strip().strip('"').strip(".")
            if title:
                storage.patch_chat(
                    user_id=claims["sub"],
                    chat=chat,
                    title=title,
                    archived=None,
                )
                yield sse_title_suggested(chat_id=chat.chat_id, title=title)
                await record_auto_title_outcome(success=True)
            else:
                await record_auto_title_outcome(success=False)
        except Exception as exc:
            logger.warning(
                "auto_title_failed chat_id=%s",
                chat.chat_id,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )
            await record_auto_title_outcome(success=False)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_chats_api.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "feat(channel-7d): auto-title invocation + title_suggested SSE in stream"
```

---

## Task 8: CDK IAM grant + assertion test

**Files:**
- Modify: `infra/stacks/channel_stack.py`
- Modify: `tests/unit/test_channel_stack.py`

Add `bedrock-agentcore:RetrieveMemoryRecords` to the existing AgentCore action list.

- [ ] **Step 1: Write the failing assertion**

```python
# In tests/unit/test_channel_stack.py — extend the required-actions set
# in test_lambda_role_grants_agentcore_write_and_lookup_actions:
    required = {
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:ListEvents",
        "bedrock-agentcore:CreateMemory",
        "bedrock-agentcore:GetMemory",
        "bedrock-agentcore:ListMemories",
        "bedrock-agentcore:RetrieveMemoryRecords",   # NEW Phase 7d
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_channel_stack.py -v`
Expected: `AssertionError: AgentCore IAM actions missing from synth: {'bedrock-agentcore:RetrieveMemoryRecords'}`

- [ ] **Step 3: Implement** — add to the action list in `channel_stack.py`:

```python
api_role.add_to_policy(
    iam.PolicyStatement(
        actions=[
            # ... existing 7c actions ...
            "bedrock-agentcore:RetrieveMemoryRecords",  # Phase 7d recall
        ],
        resources=[agentcore_memory_arn],
    )
)
```

- [ ] **Step 4: Run tests + synth**

Run: `uv run pytest tests/unit/test_channel_stack.py -v`
Expected: PASS.
Run: `uv run inv synth`
Expected: success.

- [ ] **Step 5: Commit**

```bash
git add infra/stacks/channel_stack.py tests/unit/test_channel_stack.py
git commit -m "feat(channel-7d): CDK IAM grants RetrieveMemoryRecords"
```

---

## Task 9: `inv dev` sets recall + auto-title kill-switches explicitly

**Files:**
- Modify: `tasks.py`

Default is enabled (both env vars default to `"1"`). Explicit-on in `inv dev` makes local-dev behaviour match prod-default and makes the kill-switch discoverable.

- [ ] **Step 1: Add to `dev_env` block**

```python
# In tasks.py — extend the dev_env dict:
"STARTER_RECALL_ENABLED": "1",
"STARTER_AUTO_TITLE_ENABLED": "1",
```

- [ ] **Step 2: Commit**

```bash
git add tasks.py
git commit -m "chore(channel-7d): inv dev sets recall + auto-title kill-switches"
```

---

## Task 10: SPA — `sseParser` recognises `title_suggested` event

**Files:**
- Modify: `ui/src/lib/sseParser.js`
- Modify: `ui/src/lib/sseParser.test.js`

- [ ] **Step 1: Find the existing recognised-events allowlist**

```bash
grep -nE "delta|user_persisted|done" ui/src/lib/sseParser.js
```

- [ ] **Step 2: Write the failing tests**

```js
// Append to ui/src/lib/sseParser.test.js
it("parses title_suggested events", () => {
  const sse = makeSseDecoder();
  const events = sse.feed(
    `event: title_suggested\ndata: {"chat_id":"c1","title":"Hi"}\n\n`,
  );
  expect(events).toEqual([
    { type: "title_suggested", chat_id: "c1", title: "Hi" },
  ]);
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd ui && npx vitest run src/lib/sseParser.test.js`
Expected: assertion failure on the unknown event type.

- [ ] **Step 4: Add `title_suggested` to the parser's case-list**

In `ui/src/lib/sseParser.js`, alongside the existing case branches for
`delta`, `done`, `user_persisted`:

```js
case "title_suggested":
  return { type: "title_suggested", chat_id: payload.chat_id, title: payload.title };
```

- [ ] **Step 5: Verify**

Run: `cd ui && npx vitest run src/lib/sseParser.test.js`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/Projects/channel/.claude/worktrees/feat+channel-mvp-phase-7b
git add ui/src/lib/sseParser.js ui/src/lib/sseParser.test.js
git commit -m "feat(channel-7d): sseParser recognises title_suggested events"
```

---

## Task 11: SPA — `useChatStream` forwards `title_suggested` via callback

**Files:**
- Modify: `ui/src/hooks/useChatStream.js`
- Modify: `ui/src/hooks/useChatStream.test.js`

Add an `onTitleSuggested?: (title: string) => void` callback param (default no-op). When the parser emits a `title_suggested` event in `readSse`, fire the callback.

- [ ] **Step 1: Write the failing test**

```js
// Append to useChatStream.test.js
it("forwards title_suggested events to onTitleSuggested callback", async () => {
  const onTitleSuggested = vi.fn();
  api.streamMessage.mockResolvedValueOnce(
    mockSseResponse([
      sseFrame("delta", { text: "Hi" }),
      sseFrame("done", { msg_id: "a1", model: "x", input_tokens: 1, output_tokens: 1 }),
      sseFrame("title_suggested", { chat_id: "c1", title: "New title" }),
    ]),
  );
  const { result } = renderHook(() => useChatStream("c1", { onTitleSuggested }));
  act(() => result.current.send({ message: "hi", model: "claude-sonnet-4-6" }));
  await waitFor(() => {
    expect(onTitleSuggested).toHaveBeenCalledWith("New title");
  });
});
```

(Adapt to the existing test harness — likely `mockSseResponse` or
similar helper. Read `ui/src/hooks/useChatStream.test.js` for the
real shape.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && npx vitest run src/hooks/useChatStream.test.js -t "title_suggested"`
Expected: `useChatStream is not a function` or callback never invoked.

- [ ] **Step 3: Implement**

```js
// In useChatStream.js — update the signature:
export function useChatStream(chatId, { onTitleSuggested } = {}) {
  ...
  // Inside readSse's event loop, add:
  } else if (event.type === "title_suggested") {
    onTitleSuggested?.(event.title);
  }
  ...
}
```

- [ ] **Step 4: Verify**

Run: `cd ui && npx vitest run src/hooks/useChatStream.test.js`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/john/Projects/channel/.claude/worktrees/feat+channel-mvp-phase-7b
git add ui/src/hooks/useChatStream.js ui/src/hooks/useChatStream.test.js
git commit -m "feat(channel-7d): useChatStream forwards title_suggested via callback"
```

---

## Task 12: SPA — `ChatsContext.renameChat` + Conversation wiring

**Files:**
- Modify: `ui/src/hooks/ChatsContext.jsx` + `.test.jsx`
- Modify: `ui/src/hooks/useChatList.js` (likely where `setChats` lives)
- Modify: `ui/src/app/Conversation.jsx` + `.test.jsx`

- [ ] **Step 1: Find current `useChatList` shape**

```bash
grep -nE "setChats|renameChat|chats\.map" ui/src/hooks/useChatList.js
```

- [ ] **Step 2: Add `renameChat` action**

In `useChatList.js`:

```js
const renameChat = useCallback((chat_id, title) => {
  setChats((prev) => prev.map((c) => (c.chat_id === chat_id ? { ...c, title } : c)));
}, []);
```

Expose `renameChat` via the returned object and through `ChatsContext`.

- [ ] **Step 3: Update Conversation.jsx**

```jsx
const chats = useChats();
const { turns, send, regenerate, status } = useChatStream(chatId, {
  onTitleSuggested: (title) => chats.renameChat(chatId, title),
});
```

- [ ] **Step 4: Write the failing tests**

```js
// In ChatsContext.test.jsx or useChatList.test.js
it("renameChat updates the matching chat's title", () => {
  const { result } = renderHook(() => useChatList(), { wrapper });
  act(() => result.current.setChatsForTest([
    { chat_id: "c1", title: "old A" },
    { chat_id: "c2", title: "old B" },
  ]));
  act(() => result.current.renameChat("c1", "new A"));
  expect(result.current.chats).toEqual([
    { chat_id: "c1", title: "new A" },
    { chat_id: "c2", title: "old B" },
  ]);
});

it("renameChat is a no-op for unknown chat_id", () => {
  const { result } = renderHook(() => useChatList(), { wrapper });
  act(() => result.current.setChatsForTest([{ chat_id: "c1", title: "x" }]));
  act(() => result.current.renameChat("missing", "y"));
  expect(result.current.chats[0].title).toBe("x");
});
```

```jsx
// In Conversation.test.jsx
it("renames the chat when useChatStream emits title_suggested", () => {
  const renameChat = vi.fn();
  vi.mocked(useChats).mockReturnValue({ renameChat, ... });
  const stream = mockStream();
  renderAt("/app/c/c-123");
  // Get the onTitleSuggested callback that Conversation passed in:
  const callConfig = useChatStreamModule.useChatStream.mock.calls[0][1];
  callConfig.onTitleSuggested("Lovely title");
  expect(renameChat).toHaveBeenCalledWith("c-123", "Lovely title");
});
```

- [ ] **Step 5: Run tests**

Run: `cd ui && npx vitest run` (full suite — make sure nothing else broke).
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/Projects/channel/.claude/worktrees/feat+channel-mvp-phase-7b
git add ui/src/hooks/useChatList.js ui/src/hooks/ChatsContext.jsx \
        ui/src/hooks/ChatsContext.test.jsx ui/src/hooks/useChatList.test.js \
        ui/src/app/Conversation.jsx ui/src/app/Conversation.test.jsx
git commit -m "feat(channel-7d): ChatsContext.renameChat + Conversation auto-title wiring"
```

---

## Task 13: Playwright e2e — recall + auto-title

**Files:**
- Create: `tests/e2e/test_memory_recall_and_titling.py`

Implements Layer 2 of the validation contract. Drives real Bedrock + real AgentCore in the developer's `jc` env.

- [ ] **Step 1: Write the test**

```python
# tests/e2e/test_memory_recall_and_titling.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""End-to-end verification of Phase 7d recall + auto-titling.

Drives chats through the UI and asserts (1) cross-session recall
surfaces in chat B based on writes from chat A, and (2) auto-title
fires within 5s of the first assistant reply.

Requirements (same as 7c):

- ``inv dev`` running in another terminal.
- ``STARTER_RECALL_ENABLED=1`` AND ``STARTER_AUTO_TITLE_ENABLED=1``
  (``inv dev`` sets both).
- Personal AWS credentials with Bedrock + AgentCore permissions.
"""

from __future__ import annotations

import asyncio
import contextlib
import html as html_lib
import os
import re
import time
import uuid
from typing import Any

import httpx
import pytest
from playwright.async_api import Browser, Page, async_playwright

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_cross_session_recall_surfaces_in_new_chat() -> None:
    """Plant facts in chat A; verify chat B's agent recalls them."""
    ui_url = os.environ.get("STARTER_UI_URL")
    api_url = os.environ.get("STARTER_API_URL", "http://localhost:8001")
    if not ui_url:
        pytest.skip("STARTER_UI_URL not set — run via `inv e2e-local`")

    tag = f"e2e-7d-recall-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            # Chat A — plant two facts.
            chat_a_id, _ = await _drive_chat(
                browser, ui_url, jwt,
                messages=[
                    "I'm building a chess engine called Nightfall.",
                    "My favourite colour is sage green.",
                ],
            )
            # AgentCore eventual consistency window after CreateEvent.
            await asyncio.sleep(5)
            events = _list_events(api_url, jwt, chat_a_id, limit=10)
            assert len(events) == 2, f"expected 2 events for chat A, got {len(events)}"

            # Chat B — query recall.
            chat_b_id, b_replies = await _drive_chat(
                browser, ui_url, jwt,
                messages=[
                    "What was the project I'm working on?",
                    "And the colour I like?",
                ],
            )
        finally:
            await browser.close()

    # Deterministic-keyword assertions.
    assert any("nightfall" in r.lower() for r in b_replies), (
        f"chat B should mention 'Nightfall' (recall worked); got: {b_replies!r}"
    )
    assert any("sage" in r.lower() for r in b_replies), (
        f"chat B should mention 'sage' (recall worked); got: {b_replies!r}"
    )


@pytest.mark.asyncio
async def test_auto_title_fires_on_first_round_trip_only() -> None:
    """First assistant reply triggers title; second turn doesn't change it."""
    ui_url = os.environ.get("STARTER_UI_URL")
    api_url = os.environ.get("STARTER_API_URL", "http://localhost:8001")
    if not ui_url:
        pytest.skip("STARTER_UI_URL not set — run via `inv e2e-local`")

    tag = f"e2e-7d-title-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(f"{ui_url}/app")
            await page.evaluate(
                "(t) => window.localStorage.setItem('starter_mgmt_token', t)", jwt,
            )
            await page.goto(f"{ui_url}/app")
            await page.wait_for_url(lambda url: "/app" in url, timeout=10_000)

            # First message + first reply → title should land.
            await _send_one_message(
                page, "Help me debug a flaky pytest fixture that uses tmp_path.",
            )
            await page.wait_for_url(lambda url: "/app/c/" in url, timeout=15_000)
            chat_id = page.url.rsplit("/", 1)[-1]
            await _wait_for_assistant_idle(page, n=1, timeout_ms=90_000)

            # Poll the sidebar for a title change (away from "New chat").
            title_after_first = await _wait_for_sidebar_title_change(
                page, chat_id, default_text="New chat", timeout_ms=8_000,
            )
            assert title_after_first != "New chat"
            keywords = {"pytest", "fixture", "tmp_path", "debug"}
            lower = title_after_first.lower()
            assert any(k in lower for k in keywords), (
                f"title should mention a substantive keyword from the message; got: {title_after_first!r}"
            )
            # 3-6 words.
            word_count = len(title_after_first.split())
            assert 3 <= word_count <= 6, f"title word count {word_count} outside 3-6 range"

            # Verify persistence via API.
            resp = httpx.get(
                f"{api_url}/api/chats/{chat_id}",
                headers={"Authorization": f"Bearer {jwt}"},
                timeout=10.0,
            )
            assert resp.status_code == 200
            persisted_title = resp.json()["chat"]["title"]
            assert persisted_title == title_after_first

            # Second message + reply → title MUST NOT change.
            await _send_one_message(page, "What does tmp_path do?")
            await _wait_for_assistant_idle(page, n=2, timeout_ms=90_000)
            # Give the (non-existent) titler 5s to misfire.
            await asyncio.sleep(5)
            current_title = await _sidebar_title(page, chat_id)
            assert current_title == title_after_first, (
                "title changed after second turn — auto-title idempotency broken"
            )
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Helpers (mostly copied from tests/e2e/test_memory_writes.py)
# ---------------------------------------------------------------------------

def _mint_jwt_via_bypass(api_url: str, email: str) -> str:
    resp = httpx.get(
        f"{api_url}/auth/login", params={"test_email": email},
        follow_redirects=False, timeout=15.0,
    )
    if resp.status_code in (301, 302, 307, 308):
        pytest.skip("Google OAuth redirect — STARTER_BYPASS_GOOGLE_AUTH not enabled")
    resp.raise_for_status()
    m = re.search(
        r"localStorage\.setItem\('starter_mgmt_token',\s*'([^']+)'\)", resp.text,
    )
    if not m:
        pytest.fail("Could not extract mgmt token from bypass login response")
    return html_lib.unescape(m.group(1))


async def _drive_chat(
    browser: Browser, ui_url: str, jwt: str, *, messages: list[str],
) -> tuple[str, list[str]]:
    """Run N messages in one chat. Return (chat_id, list of assistant texts)."""
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(f"{ui_url}/app")
    await page.evaluate(
        "(t) => window.localStorage.setItem('starter_mgmt_token', t)", jwt,
    )
    await page.goto(f"{ui_url}/app")
    await page.wait_for_url(lambda url: "/app" in url, timeout=10_000)

    chat_id: str | None = None
    replies: list[str] = []
    for i, msg in enumerate(messages):
        await _send_one_message(page, msg)
        if chat_id is None:
            await page.wait_for_url(lambda url: "/app/c/" in url, timeout=15_000)
            chat_id = page.url.rsplit("/", 1)[-1]
        await _wait_for_assistant_idle(page, n=i + 1, timeout_ms=90_000)
        # Grab the latest assistant turn text.
        turns = await page.locator('[data-testid="assistant-turn-idle"] .msg').all_text_contents()
        if turns:
            replies.append(turns[-1])

    assert chat_id is not None
    await context.close()
    return chat_id, replies


async def _send_one_message(page: Page, text: str) -> None:
    textarea = page.locator("textarea").first
    await textarea.fill(text)
    await textarea.press("Enter")


async def _wait_for_assistant_idle(page: Page, *, n: int, timeout_ms: int) -> None:
    await page.wait_for_function(
        '(want) => document.querySelectorAll(\'[data-testid="assistant-turn-idle"]\').length >= want',
        arg=n, timeout=timeout_ms,
    )


async def _wait_for_sidebar_title_change(
    page: Page, chat_id: str, *, default_text: str, timeout_ms: int,
) -> str:
    """Poll the sidebar row for chat_id until its text differs from default."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        # Sidebar's chat rows have class `recent` per Sidebar.jsx.
        # Find the row whose data-chat-id matches OR fall back to URL match.
        rows = await page.locator(".recent").all_text_contents()
        for txt in rows:
            if txt.strip() and txt.strip() != default_text:
                return txt.strip()
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"sidebar title for chat {chat_id} stayed {default_text!r} for {timeout_ms}ms",
    )


async def _sidebar_title(page: Page, chat_id: str) -> str:
    rows = await page.locator(".recent").all_text_contents()
    return rows[0].strip() if rows else ""


def _list_events(
    api_url: str, jwt: str, chat_id: str, limit: int,
) -> list[dict[str, Any]]:
    resp = httpx.get(
        f"{api_url}/api/_debug/memory/events",
        params={"chat_id": chat_id, "limit": limit},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()["events"]
```

Notes for the implementer:

- If `_wait_for_sidebar_title_change` is flaky due to the chat row not
  carrying `data-chat-id`, add a `data-chat-id={chat.chat_id}` attribute
  to the `.recent` button in `Sidebar.jsx` and update the locator.
- The `_drive_chat` helper relies on the `data-testid="assistant-turn-idle"`
  attribute from Phase 7c. It's already in `Conversation.jsx`.

- [ ] **Step 2: Verify syntax with a no-op pytest collect**

Run: `uv run pytest tests/e2e/test_memory_recall_and_titling.py --collect-only`
Expected: tests collected, no syntax errors.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_memory_recall_and_titling.py
git commit -m "feat(channel-7d): Playwright e2e for cross-session recall + auto-title"
```

---

## Task 14: Local verification dry-run (BEFORE pre-push, BEFORE PR)

Layer 2 of the validation contract. **Do not skip** — the user's
memory note (`feedback_plans_include_local_verification`) was filed
specifically to ensure this step happens.

- [ ] **Step 1: Start the full local stack**

In a separate terminal:
```bash
uv run inv dev
```

Wait for `Uvicorn running on http://0.0.0.0:8001` and Vite's `Local: http://localhost:5173/`.

- [ ] **Step 2: Reset the local DDB table**

```bash
uv run python scripts/reset_dev_table.py
```

Expected: `recreated channel with 4 GSIs (incl. ChatByIdIndex)`.

- [ ] **Step 3: Run the Playwright e2e**

```bash
uv run inv e2e-local --tests tests/e2e/test_memory_recall_and_titling.py
```

Expected:
- 2 tests pass.
- `inv dev` console shows `RecallSuccesses` and `AutoTitleSuccesses`
  log lines.
- No `agentcore.recall_failed` or `auto_title_failed` warnings.

- [ ] **Step 4: Repeat 3 times to catch flakes**

```bash
uv run inv e2e-local --tests tests/e2e/test_memory_recall_and_titling.py -n 3
```

Expected: 3 consecutive passes.

- [ ] **Step 5: Sanity-check the AgentCore record via CLI**

```bash
aws bedrock-agentcore list-actors --memory-id channel_jc-<SUFFIX> --region us-east-1
```

Expected: includes the `e2e-7d-recall-...` actor IDs from the test
run.

- [ ] **Step 6: Manual spot-check via the UI**

Open `http://localhost:5173/app`, sign in with a test email, send
"I love Italian food and I'm allergic to peanuts." Wait. Send a new
chat: "What allergies should I avoid in restaurants?" — agent should
mention peanuts.

- [ ] **Step 7: Tear down `inv dev`** (Ctrl-C)

---

## Task 15: CHANGELOG + CLAUDE.md sync

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add `[Unreleased]` `### Added` entry**

```markdown
- Phase 7d — agent now recalls relevant context from the user's
  PRIOR chats via Bedrock AgentCore Memory, and auto-titles new
  chats in 3-6 words after the first reply. Recall fires per turn
  with a 5-turn cache; injection is a Markdown addendum on the
  system prompt. Auto-title uses a Haiku one-shot Strands Agent.
  Both features fail-soft and ship behind
  `STARTER_RECALL_ENABLED` / `STARTER_AUTO_TITLE_ENABLED`
  kill-switches.
```

- [ ] **Step 2: Update `## Structure` block in CLAUDE.md**

In the `agents/` block:

```
│       │   ├── chat_agent.py   # Strands Agent factory + build_titler_agent
│       │   ├── memory.py       # AgentCoreMemoryHook (Phase 7c writes)
│       │   ├── recall.py       # AgentCoreRecallHook + cache (Phase 7d)
│       │   └── strands_sse.py  # Strands event → SSE translator
```

- [ ] **Step 3: Extend the `## AgentCore Memory` section**

Add a new subsection after the existing 7c content:

```markdown
### Recall (Phase 7d)

- **`AgentCoreRecallHook`** (`src/channel/agents/recall.py`)
  subscribes to `BeforeInvocationEvent`. Per turn, calls
  `RetrieveMemoryRecords` scoped to `actorId` (cross-session same
  user, NOT cross-actor), filters records to `score >= 0.7`, caps
  at top 5, and appends a Markdown addendum (`## What I remember
  about previous conversations\n\n- ...`) to the end of the
  system prompt.
- **5-turn cache** — keyed by `(actor_id, chat_id)`, module-level.
  Cold-start invalidates. Reduces RPC volume to ~one
  `RetrieveMemoryRecords` per 5 turns.
- **Kill-switch** — `STARTER_RECALL_ENABLED=0` short-circuits the
  hook to a no-op. Default `"1"`.

### Auto-titling (Phase 7d)

- After the first assistant `done` event lands (and
  `chat.message_count == 0` at function entry), `_stream_bedrock_reply`
  invokes a small Haiku Strands `Agent` (from
  `build_titler_agent()`) with NO memory hooks and `max_tokens=20`.
- Persists the title via `storage.patch_chat`, then emits a new
  `sse_title_suggested(chat_id, title)` SSE frame before stream
  close. The SPA's `useChatStream` forwards via
  `onTitleSuggested` to `ChatsContext.renameChat`.
- **Kill-switch** — `STARTER_AUTO_TITLE_ENABLED=0` skips the
  titler block. Default `"1"`.
- **Model override** — `STARTER_TITLER_MODEL` (default
  `claude-haiku-4-5`).
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs(channel-7d): CHANGELOG + CLAUDE.md sync — recall + auto-title"
```

---

## Task 16: Pre-push + file issue + open PR

- [ ] **Step 1: Run full pre-push gate**

```bash
uv run inv pre-push
```

Expected: all green.

- [ ] **Step 2: Synth confirms CDK**

```bash
uv run inv synth
```

Expected: success.

- [ ] **Step 3: File the issue**

```bash
gh issue create \
  --title "feat(channel-7d): memory recall + auto-titling" \
  --label "status:ready,priority:p1,size:l,enhancement,infra" \
  --body "$(cat <<'EOF'
Implements Phase 7d — agent recalls relevant context from the
user's prior chats AND auto-titles new chats in 3-6 words.

Design: docs/superpowers/specs/2026-05-31-phase-7d-memory-recall-auto-titling-design.md
Plan: docs/superpowers/plans/2026-05-31-phase-7d-memory-recall-auto-titling.md

Layer-2 validation: 3 consecutive Playwright e2e runs against a
personal AWS environment with real Bedrock + real AgentCore — all
green.

## Files to touch

- src/channel/agents/recall.py
- src/channel/agents/chat_agent.py
- src/channel/api/chats.py
- src/channel/agents/strands_sse.py
- src/channel/metrics.py
- infra/stacks/channel_stack.py
- tasks.py
- CHANGELOG.md
- CLAUDE.md
- tests/unit/test_channel_stack.py
- tests/unit/test_strands_sse.py
- tests/e2e/test_memory_recall_and_titling.py
- ui/src/lib/sseParser.js
- ui/src/hooks/useChatStream.js
- ui/src/hooks/ChatsContext.jsx
- ui/src/hooks/useChatList.js
- ui/src/app/Conversation.jsx
- docs/superpowers/specs/2026-05-31-phase-7d-memory-recall-auto-titling-design.md
- docs/superpowers/plans/2026-05-31-phase-7d-memory-recall-auto-titling.md
EOF
)"
```

- [ ] **Step 4: Branch + push + open PR**

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD

git push -u origin feat/channel-7d-memory-recall-auto-title:feat/channel-7d-memory-recall-auto-title

gh pr create --base development \
  --title "feat(channel-7d): memory recall + auto-titling" \
  --body "..."
```

NOTE: NOT `agent-safe` — touches `infra/stacks/channel_stack.py`.
Agent runs Copilot review then stops for human merge per CLAUDE.md.

- [ ] **Step 5: Watch CI**

```bash
gh run watch
```

- [ ] **Step 6: Layer 3 verification — after dev deploys**

```bash
# Generate traffic.
# Open https://channel-dev.warlordofmars.net/app, run a fresh chat.

# Check EMF metrics in CloudWatch logs.
aws logs tail /aws/lambda/ChannelStack-dev-Api... \
  --filter-pattern '"RecallSuccesses"' --since 5m
aws logs tail /aws/lambda/ChannelStack-dev-Api... \
  --filter-pattern '"AutoTitleSuccesses"' --since 5m
```

Expected: both filters return non-empty log lines from the new
traffic.

---

## Done criteria

- ✅ Spec referenced by every implementation file's docstring.
- ✅ `recall.py` exists with 100% coverage.
- ✅ Recall hook attached to every `build_agent` call; chat_id
  carried on `agent.chat_id`.
- ✅ EMF counters emit for both recall + auto-title outcomes.
- ✅ CDK IAM grants `RetrieveMemoryRecords` (`channel_stack.py`).
- ✅ CDK assertion test extended with the new IAM action.
- ✅ Auto-title fires only on first round-trip (idempotent).
- ✅ SPA's sidebar updates via `ChatsContext.renameChat`.
- ✅ Playwright e2e passes 3 consecutive runs against a real AWS env.
- ✅ CloudWatch shows `RecallSuccesses > 0` and
  `AutoTitleSuccesses > 0` after dev deploy.
- ✅ CHANGELOG + CLAUDE.md updated.
