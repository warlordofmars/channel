# Tool-use chassis (#181) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Strands tool-use substrate for Channel — Strands wiring + SSE protocol + hooks + memory invariant + EMF + SPA step list — under the three-PR split mandated by policy P4 in the strategy spec.

**Architecture:** A `tools=[...]` parameter on `build_agent()` plus a new `tool_hooks.py` module with three hook handlers (`BeforeModelCallEvent` addendum, `BeforeToolCallEvent` guard, `AfterToolCallEvent` telemetry). Per-chain state lives on the Agent instance (`agent.chain_state`) mirroring the existing `agent.chat_id` monkey-patch. Mid-chain interrupt uses a module-level `_CANCEL_SIGNALS` registry keyed by `chat_id`, set from `chats.py` when the SSE client disconnects. Four new SSE event types stream the plan to the SPA. EMF counters mirror the existing `record_*_outcome` pattern. The chassis ships with one trivial tool (`current_time`) behind `STARTER_CLOCK_TOOL_ENABLED` to give an end-to-end smoke-test path.

**Tech Stack:** Strands 1.41.0 (`BedrockModel`, hooks API, `ToolProvider`), Python 3.13 (FastAPI + DynamoDB single table), React 18 (Vite), vitest + pytest, AWS EMF via `aws-embedded-metrics`.

**Strategy spec:** `docs/superpowers/specs/2026-06-06-epic-128-tool-use-sequencing.md`
**Issue:** [#181](https://github.com/warlordofmars/channel/issues/181)
**Decisions comment:** [#128 / 2026-06-03](https://github.com/warlordofmars/channel/issues/128#issuecomment-4617554628)
**ADR:** [ADR-0009](../../adr/0009-unified-context-budget.md) (unified context budget)

---

## File map

**New files:**
- `src/channel/agents/tool_hooks.py` — three hook classes + `ChainState` dataclass + cancel-signal registry
- `src/channel/agents/tools/__init__.py` — package marker
- `src/channel/agents/tools/clock.py` — `current_time` tool (smoke-test)
- `tests/unit/test_tool_hooks.py` — hook unit tests
- `tests/unit/test_tools_clock.py` — clock tool test
- `tests/unit/agents/test_strands_sse_tool_events.py` — SSE tool-event translation tests
- `docs/superpowers/specs/2026-06-06-tool-use-chassis-design.md` — chassis design doc (phenomenology preamble + hook architecture)

**Modified files:**
- `src/channel/agents/chat_agent.py` — add `tools` param to `build_agent()`, attach tool hooks, attach `ChainState`
- `src/channel/agents/memory.py:230-249` — lift `_payload_from_messages` invariant into the docstring
- `src/channel/agents/strands_sse.py` — extend `translate_event()` for tool events; four new emitters
- `src/channel/api/chats.py` — dispatch new event kinds; wire cancel-signal on SSE disconnect
- `src/channel/metrics.py` — `record_tool_call_outcome(success: bool)`
- `tests/unit/test_memory.py:43-68` — harden `test_payload_from_messages_concatenates_multi_block_content` to assert the invariant explicitly
- `tests/unit/test_metrics.py` — `_signature_locks_out_dimensions` test for `record_tool_call_outcome`
- `tests/unit/test_chat_agent.py` — smoke test that `build_agent(tools=[...])` attaches hooks
- `ui/src/hooks/useChatStream.js` — four new event handlers, attach tool-step state to in-flight assistant message
- `ui/src/app/Conversation.jsx` — render collapsible step list
- `ui/src/app/Conversation.test.jsx` — test renders tool steps + chain-cap distinct path
- `ui/src/hooks/useChatStream.test.js` — test event-handler attachments
- `infra/stacks/channel_stack.py` — set `STARTER_CLOCK_TOOL_ENABLED` env var (default `0` in prod, `1` in jc/dev)
- `CHANGELOG.md` — `[Unreleased]` entries

---

## PR-1: Backend chassis

Scope: `chat_agent.py` `tools` param, `tool_hooks.py`, memory invariant, EMF, `current_time` tool. **No SSE protocol changes, no SPA changes.** Hooks exercised by unit tests; the smoke path runs in PR-3.

### Task 1: EMF counter for tool calls

**Files:**
- Modify: `src/channel/metrics.py:117` (append to end)
- Test: `tests/unit/test_metrics.py`

- [ ] **Step 1: Write the failing test** at the bottom of `tests/unit/test_metrics.py`

```python
def test_record_tool_call_outcome_emits_correct_metric_name(mock_emit_metric):
    asyncio.run(record_tool_call_outcome(success=True))
    mock_emit_metric.assert_called_once_with("ToolCallSuccesses")

    mock_emit_metric.reset_mock()
    asyncio.run(record_tool_call_outcome(success=False))
    mock_emit_metric.assert_called_once_with("ToolCallFailures")


def test_record_tool_call_outcome_signature_locks_out_dimensions():
    """The signature must reject per-tool / per-actor dimensions to keep
    CloudWatch metric cardinality bounded."""
    sig = inspect.signature(record_tool_call_outcome)
    assert list(sig.parameters.keys()) == ["success"]
    assert sig.parameters["success"].annotation is bool
```

Imports needed at top of test file (check existing imports first; add only missing ones): `import inspect`, `from channel.metrics import record_tool_call_outcome`. `mock_emit_metric` fixture already exists in this test module — reuse it.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_metrics.py -k tool_call -v`
Expected: ImportError on `record_tool_call_outcome` (function doesn't exist).

- [ ] **Step 3: Implement the counter**

Append to `src/channel/metrics.py`:

```python
async def record_tool_call_outcome(success: bool) -> None:
    """Emit a CloudWatch counter for one tool-call attempt (epic #128).

    Counter-only — same cardinality-risk rationale as
    :func:`record_memory_write_outcome`. No per-tool or per-actor
    dimensions. Per-tool failure breakdowns belong in structured logs,
    which CloudWatch Logs Insights can query without dimension blowup.
    """
    metric = "ToolCallSuccesses" if success else "ToolCallFailures"
    await emit_metric(metric)
```

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/unit/test_metrics.py -k tool_call -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/metrics.py tests/unit/test_metrics.py
git commit -m "feat(metrics): add ToolCallSuccesses/Failures counters (#181)"
```

### Task 2: Memory invariant docstring + hardened test

**Files:**
- Modify: `src/channel/agents/memory.py:230-249`
- Modify: `tests/unit/test_memory.py:43-68`

- [ ] **Step 1: Harden the test first** — replace `test_payload_from_messages_concatenates_multi_block_content` with explicit invariant assertion

Replace lines 43-68 of `tests/unit/test_memory.py` with:

```python
def test_payload_from_messages_invariant_drops_tool_use_blocks():
    """INVARIANT: Tool payloads (toolUse / toolResult blocks) never
    persist to AgentCore Memory. Verified by a fixture that emits both
    block types interleaved with text and asserting neither leaks into
    the produced payload."""
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
    serialised = json.dumps(payload)
    assert "toolUse" not in serialised
    assert "toolResult" not in serialised
```

Add `import json` at top of `tests/unit/test_memory.py` if not present.

- [ ] **Step 2: Run to verify the test still passes** (it's a stricter version of the existing test)

Run: `uv run pytest tests/unit/test_memory.py -k invariant_drops -v`
Expected: 1 passed.

- [ ] **Step 3: Lift the invariant into the docstring** — replace the docstring at `src/channel/agents/memory.py:230`

Replace the docstring of `_payload_from_messages` with:

```python
def _payload_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Strands message dicts to AgentCore ``CreateEvent`` payload.

    INVARIANT (epic #128 decision 7, 2026-06-03): Tool payloads —
    ``toolUse`` and ``toolResult`` content blocks — never persist to
    AgentCore Memory. Only conclusion-shaped text survives. Tool-use
    META facts are recorded separately via the AfterToolCallEvent hook
    (see ``src/channel/agents/tool_hooks.py``). Verified explicitly by
    ``tests/unit/test_memory.py::test_payload_from_messages_invariant_drops_tool_use_blocks``.

    Strands messages have shape
    ``{"role": str, "content": [{"text": str}, ...]}``. AgentCore's
    payload is a list of typed conversational entries with role
    upper-cased and ``content.text`` as a single string.

    Multi-block content (text + toolUse interleaved) gets its text
    blocks concatenated; non-text blocks are dropped per the invariant.
    """
```

- [ ] **Step 4: Re-run the test**

Run: `uv run pytest tests/unit/test_memory.py -v`
Expected: all tests pass.

- [ ] **Step 5: Add `write_meta_event` method to `AgentCoreMemoryHook`** — the telemetry hook (Task 7) needs a way to record `[meta] used <tool> ...` synthetic ASSISTANT messages via the existing `CreateEvent` path. This step adds that capability without changing the existing main `_on_after_invocation` write.

Write the failing test first. Append to `tests/unit/test_memory.py`:

```python
def test_write_meta_event_calls_create_event_with_meta_prefix(monkeypatch):
    """[meta] synthetic ASSISTANT messages flow through CreateEvent
    via the existing boto3 client. The 'meta_' prefix tags the event
    for the recall hook to render distinctly."""
    captured = {}

    class FakeClient:
        def create_event(self, **kwargs):
            captured.update(kwargs)
            return {}

    hook = AgentCoreMemoryHook(memory_id="mem-x", actor_id="user-1", session_id="chat-1")
    hook._client = FakeClient()  # bypass real boto3
    hook.write_meta_event("used current_time to get current UTC time")

    assert captured["memoryId"] == "mem-x"
    assert captured["actorId"] == "user-1"
    assert captured["sessionId"] == "chat-1"
    # The synthetic ASSISTANT message text starts with [meta]
    payload = captured["payload"]
    assert payload[0]["conversational"]["role"] == "ASSISTANT"
    assert payload[0]["conversational"]["content"]["text"].startswith("[meta] ")
```

Implement on `AgentCoreMemoryHook` (in `src/channel/agents/memory.py`, after `_on_after_invocation_async`):

```python
def write_meta_event(self, text: str) -> None:
    """Write a [meta]-prefixed synthetic ASSISTANT message via CreateEvent.

    Called by ToolCallTelemetryHook after each tool call (epic #128
    decision 6). One event per tool call, not per chain. The recall
    hook (``recall.py``) tags ``[meta]``-prefixed events and renders
    them differently in the system-prompt addendum.

    Fail-soft: errors log + count but never raise — tool result is
    already in the chain; failure to record the META fact must not
    break the user-visible reply.
    """
    try:
        self._client.create_event(
            memoryId=self._memory_id,
            actorId=self._actor_id,
            sessionId=self._session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[
                {
                    "conversational": {
                        "role": "ASSISTANT",
                        "content": {"text": f"[meta] {text}"},
                    }
                }
            ],
        )
    except Exception:
        logger.exception(
            "agentcore.write_meta_event_failed actor_id=%s session_id=%s",
            self._actor_id,
            self._session_id,
        )
```

(Confirm the exact `create_event` kwarg shape against the existing `_on_after_invocation_async` call — Strands' AgentCore boto API requires the same envelope as the main write path.)

Run: `uv run pytest tests/unit/test_memory.py -k meta_event -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add src/channel/agents/memory.py tests/unit/test_memory.py
git commit -m "docs(memory): lift invariant + add write_meta_event for tool META writes (#181)"
```

### Task 3: `tool_hooks.py` scaffolding — `ChainState` dataclass + cancel registry

**Files:**
- Create: `src/channel/agents/tool_hooks.py`
- Create: `tests/unit/test_tool_hooks.py`

- [ ] **Step 1: Write the failing test** for `ChainState` and cancel-signal registry

Create `tests/unit/test_tool_hooks.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for tool_hooks chain state + cancel registry."""

from __future__ import annotations

import time

import pytest

from channel.agents.tool_hooks import (
    ChainState,
    clear_cancel_signal,
    is_cancel_requested,
    set_cancel_signal,
)


def test_chain_state_defaults():
    state = ChainState()
    assert state.tool_calls_used == 0
    assert state.tool_calls_max == 8
    assert state.wall_clock_budget_sec == 120
    assert state.started_at > 0


def test_chain_state_increment():
    state = ChainState()
    state.increment()
    state.increment()
    assert state.tool_calls_used == 2


def test_chain_state_chain_cap_exhausted():
    state = ChainState(tool_calls_max=2)
    assert not state.is_chain_cap_exhausted()
    state.increment()
    state.increment()
    assert state.is_chain_cap_exhausted()


def test_chain_state_wall_clock_exhausted_uses_real_time(monkeypatch):
    state = ChainState(wall_clock_budget_sec=10)
    assert not state.is_wall_clock_exhausted()
    # Simulate 11 seconds elapsed
    state.started_at = time.monotonic() - 11
    assert state.is_wall_clock_exhausted()


def test_cancel_signal_round_trip():
    set_cancel_signal("chat-abc")
    assert is_cancel_requested("chat-abc") is True
    clear_cancel_signal("chat-abc")
    assert is_cancel_requested("chat-abc") is False


def test_cancel_signal_unknown_chat_is_false():
    assert is_cancel_requested("never-set") is False


def test_clear_cancel_signal_is_idempotent():
    clear_cancel_signal("never-set")  # must not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_tool_hooks.py -v`
Expected: ImportError (module doesn't exist).

- [ ] **Step 3: Create the skeleton module**

Create `src/channel/agents/tool_hooks.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Tool-use hooks for the Channel chat agent (epic #128 / #181).

Three hook handlers — see the design doc at
``docs/superpowers/specs/2026-06-06-tool-use-chassis-design.md`` for the
phenomenology preamble and the rationale behind each.

* ``ModelVisibilityAddendumHook`` — ``BeforeModelCallEvent``. Renders
  the per-chain "tool calls used: N of M" line into a system-prompt
  addendum so the model can economize. Per policy P1 the
  ``RemainingBudget`` line is OMITTED under the stub — it is added by
  the #131 swap-in PR for the first time.

* ``ToolCallGuardHook`` — ``BeforeToolCallEvent``. Enforces the
  chain-length cap, the soft wall-clock budget, and the cancel-signal
  flag (mid-chain interrupt set by ``chats.py`` on SSE disconnect).

* ``ToolCallTelemetryHook`` — ``AfterToolCallEvent``. Increments the
  per-chain counter, fires the ``[meta] used <tool> ...`` synthetic
  ASSISTANT message via ``AgentCoreMemoryHook``'s ``CreateEvent`` path,
  and emits ``ToolCallSuccesses`` / ``ToolCallFailures`` EMF counters.

The cancel-signal registry is a module-level dict keyed by ``chat_id``;
``chats.py`` calls ``set_cancel_signal(chat_id)`` from the
``finally:`` block when the SSE client disconnects.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ChainState:
    """Per-chain mutable state. Lives on the Strands ``Agent`` instance
    as ``agent.chain_state`` (parallel to ``agent.chat_id``)."""

    tool_calls_used: int = 0
    tool_calls_max: int = 8
    wall_clock_budget_sec: int = 120
    started_at: float = field(default_factory=time.monotonic)

    def increment(self) -> None:
        self.tool_calls_used += 1

    def is_chain_cap_exhausted(self) -> bool:
        return self.tool_calls_used >= self.tool_calls_max

    def is_wall_clock_exhausted(self) -> bool:
        return (time.monotonic() - self.started_at) >= self.wall_clock_budget_sec


# Module-level cancel-signal registry. Keyed by chat_id (UUID). The
# chats.py SSE generator sets the signal in its finally: block when the
# client disconnects; the next BeforeToolCallEvent reads it and calls
# event.cancel_tool(). Cold start clears all signals (no persistence
# needed — a request that hasn't reached its first tool call by the
# time the Lambda restarts is already dead).
_CANCEL_SIGNALS: set[str] = set()


def set_cancel_signal(chat_id: str) -> None:
    _CANCEL_SIGNALS.add(chat_id)


def clear_cancel_signal(chat_id: str) -> None:
    _CANCEL_SIGNALS.discard(chat_id)


def is_cancel_requested(chat_id: str) -> bool:
    return chat_id in _CANCEL_SIGNALS
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_tool_hooks.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/tool_hooks.py tests/unit/test_tool_hooks.py
git commit -m "feat(agents): tool_hooks scaffolding — ChainState + cancel registry (#181)"
```

### Task 4: `current_time` smoke-test tool

**Files:**
- Create: `src/channel/agents/tools/__init__.py`
- Create: `src/channel/agents/tools/clock.py`
- Create: `tests/unit/test_tools_clock.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_tools_clock.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the current_time smoke-test tool (#181)."""

from __future__ import annotations

import re

from channel.agents.tools.clock import current_time


def test_current_time_returns_iso8601_utc_string():
    result = current_time()
    # Expect format like "2026-06-06T12:34:56Z"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", result)


def test_current_time_is_a_strands_tool():
    """Strands' @tool decorator attaches metadata. The chassis registers
    this via build_agent(tools=[current_time]); Strands introspects the
    function's docstring + signature to build the tool schema."""
    # @tool decorator from strands attaches a `tool_spec` attribute
    # (or similar — the exact attribute may be `_tool_spec`,
    # `__strands_tool__`, etc; check strands version's actual API).
    assert hasattr(current_time, "tool_spec") or hasattr(current_time, "_tool_spec")
```

If you're unsure of the exact attribute name in your installed Strands version, run a quick probe first:
`uv run python -c "from strands import tool; help(tool)" | head -40`

Adjust the assertion to match the actual attribute. If neither `tool_spec` nor `_tool_spec` is right, replace the second test with a behavioral check: `result = current_time()` returns the expected string shape.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_tools_clock.py -v`
Expected: ImportError.

- [ ] **Step 3: Create the package + tool**

Create `src/channel/agents/tools/__init__.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Channel-specific Strands tool implementations.

This package is the home for tools registered in
``build_agent(tools=[...])``. The ``clock.current_time`` tool is the
chassis exit test (epic #128 / #181); production tools land under
``web_search`` (#182) and ``code_exec`` (#183).
"""
```

Create `src/channel/agents/tools/clock.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""``current_time`` — chassis smoke-test tool (epic #128 / #181).

Returns the current UTC time as an ISO-8601 string. The chassis
registers this tool when ``STARTER_CLOCK_TOOL_ENABLED=1``; default off
in prod per strategy spec policy P2 ("smoke-test, not a product
feature"). It exists only so the chassis has a real Strands tool to
exercise every hook + every SSE event + every SPA renderer end-to-end.

If a future feature wants real time-aware tools, that's a separate
design pass — do not flip the prod feature flag here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from strands import tool


@tool
def current_time() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Use when the user asks "what time is it" or needs the current
    timestamp for relative-time reasoning.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

- [ ] **Step 4: Verify tests pass**

Run: `uv run pytest tests/unit/test_tools_clock.py -v`
Expected: 2 passed (adjust the second assertion if the Strands probe in Step 1 revealed a different attribute name).

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/tools/ tests/unit/test_tools_clock.py
git commit -m "feat(agents): current_time smoke-test tool (#181)"
```

### Task 5: Hook handlers — `ModelVisibilityAddendumHook`

**Files:**
- Modify: `src/channel/agents/tool_hooks.py`
- Modify: `tests/unit/test_tool_hooks.py`

Read Strands' hook API once before writing the handler:
`uv run python -c "from strands.hooks.events import BeforeModelCallEvent; help(BeforeModelCallEvent)"`

Note the exact event-callback signature and what fields are on `BeforeModelCallEvent` (it has `event.messages`, `event.agent`, etc.). The handler signature shape in Strands 1.41.0 is documented in `strands/hooks/events.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_tool_hooks.py`:

```python
from unittest.mock import MagicMock

from channel.agents.tool_hooks import ModelVisibilityAddendumHook


def test_addendum_hook_appends_chain_progress_to_system_prompt():
    """The addendum surfaces `tool calls used: N of M` so the model can
    economize. Per policy P1 the budget line is OMITTED under stub —
    only chain-progress renders for v1."""
    hook = ModelVisibilityAddendumHook()
    state = ChainState()
    state.increment()
    state.increment()  # 2 used
    state.increment()  # 3 used

    agent = MagicMock()
    agent.chain_state = state
    agent.system_prompt = "You are Channel."

    event = MagicMock()
    event.agent = agent

    hook.on_before_model_call(event)

    # The hook mutates agent.system_prompt by appending the addendum.
    assert "tool calls used: 3 of 8" in agent.system_prompt
    # Budget line is OMITTED under stub.
    assert "Remaining context budget" not in agent.system_prompt
    # Original prompt preserved.
    assert agent.system_prompt.startswith("You are Channel.")


def test_addendum_hook_idempotent_within_chain():
    """Re-running the hook in the same chain shouldn't compound the
    addendum (the system prompt would grow per turn). The hook either
    replaces a previously-injected addendum or detects + skips."""
    hook = ModelVisibilityAddendumHook()
    state = ChainState()
    state.increment()
    agent = MagicMock()
    agent.chain_state = state
    agent.system_prompt = "You are Channel."

    event = MagicMock()
    event.agent = agent

    hook.on_before_model_call(event)
    hook.on_before_model_call(event)

    # Addendum present exactly once
    assert agent.system_prompt.count("tool calls used:") == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_tool_hooks.py -k addendum -v`
Expected: ImportError on `ModelVisibilityAddendumHook`.

- [ ] **Step 3: Implement the hook**

Append to `src/channel/agents/tool_hooks.py`:

```python
# Sentinel that marks the addendum block. Used to detect + replace on
# re-entry so the prompt doesn't grow per turn.
_ADDENDUM_OPEN = "\n\n<tool-use-budget>"
_ADDENDUM_CLOSE = "</tool-use-budget>"


def _strip_existing_addendum(prompt: str) -> str:
    idx = prompt.find(_ADDENDUM_OPEN)
    if idx == -1:
        return prompt
    end = prompt.find(_ADDENDUM_CLOSE, idx)
    if end == -1:
        return prompt
    return prompt[:idx] + prompt[end + len(_ADDENDUM_CLOSE):]


class ModelVisibilityAddendumHook:
    """``BeforeModelCallEvent`` — renders the chain-progress addendum
    into the system prompt so the model can economize.

    Policy P1 (epic #128 strategy spec): the ``RemainingBudget`` line
    is OMITTED under the v1 stub. The #131 swap-in PR adds it for the
    first time; until then, only ``tool calls used: N of M`` renders.
    """

    def on_before_model_call(self, event):  # type: ignore[no-untyped-def]
        agent = event.agent
        state = getattr(agent, "chain_state", None)
        if state is None:
            return  # chassis not yet wired through this call path
        base = _strip_existing_addendum(agent.system_prompt or "")
        addendum = (
            f"{_ADDENDUM_OPEN}\n"
            f"tool calls used: {state.tool_calls_used} of {state.tool_calls_max}\n"
            f"{_ADDENDUM_CLOSE}"
        )
        agent.system_prompt = base + addendum
```

Register the hook in `__all__` or just expose at module level.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_tool_hooks.py -k addendum -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/tool_hooks.py tests/unit/test_tool_hooks.py
git commit -m "feat(agents): ModelVisibilityAddendumHook surfaces chain progress (#181)"
```

### Task 6: Hook handlers — `ToolCallGuardHook`

**Files:**
- Modify: `src/channel/agents/tool_hooks.py`
- Modify: `tests/unit/test_tool_hooks.py`

Read the Strands API for `BeforeToolCallEvent.cancel_tool()`:
`uv run python -c "from strands.hooks.events import BeforeToolCallEvent; help(BeforeToolCallEvent)"`

Confirm the signature shape — `cancel_tool(reason: str)` or a property assignment, etc. The implementation below is written against the documented "interruptible" event; adjust if the actual API differs.

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_tool_hooks.py`:

```python
from channel.agents.tool_hooks import ToolCallGuardHook


def test_guard_hook_blocks_on_chain_cap():
    hook = ToolCallGuardHook()
    state = ChainState(tool_calls_max=2)
    state.increment()
    state.increment()  # used 2 of 2

    agent = MagicMock()
    agent.chain_state = state
    agent.chat_id = "chat-1"

    event = MagicMock()
    event.agent = agent

    hook.on_before_tool_call(event)

    # The guard called cancel_tool with the chain_cap error_type
    event.cancel_tool.assert_called_once()
    call_kwargs = event.cancel_tool.call_args.kwargs
    # Either positional or keyword — handle both. The "chain_cap"
    # error_type is the contract with sse_tool_error consumers.
    args_and_kwargs = list(event.cancel_tool.call_args.args) + list(call_kwargs.values())
    assert any("chain_cap" in str(a) for a in args_and_kwargs)


def test_guard_hook_blocks_on_cancel_signal():
    hook = ToolCallGuardHook()
    state = ChainState()
    agent = MagicMock()
    agent.chain_state = state
    agent.chat_id = "chat-cancelled"

    set_cancel_signal("chat-cancelled")
    try:
        event = MagicMock()
        event.agent = agent
        hook.on_before_tool_call(event)
        event.cancel_tool.assert_called_once()
    finally:
        clear_cancel_signal("chat-cancelled")


def test_guard_hook_blocks_on_wall_clock_exhaustion():
    hook = ToolCallGuardHook()
    state = ChainState(wall_clock_budget_sec=10)
    state.started_at = time.monotonic() - 11  # simulate elapsed

    agent = MagicMock()
    agent.chain_state = state
    agent.chat_id = "chat-slow"

    event = MagicMock()
    event.agent = agent
    hook.on_before_tool_call(event)
    event.cancel_tool.assert_called_once()


def test_guard_hook_allows_under_caps_with_no_cancel():
    hook = ToolCallGuardHook()
    state = ChainState()
    state.increment()  # used 1 of 8

    agent = MagicMock()
    agent.chain_state = state
    agent.chat_id = "chat-ok"

    event = MagicMock()
    event.agent = agent
    hook.on_before_tool_call(event)
    event.cancel_tool.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_tool_hooks.py -k guard -v`
Expected: ImportError on `ToolCallGuardHook`.

- [ ] **Step 3: Implement the guard hook**

Append to `src/channel/agents/tool_hooks.py`:

```python
class ToolCallGuardHook:
    """``BeforeToolCallEvent`` — enforces chain-length cap, wall-clock
    budget, and the cancel signal.

    Order matters for the error_type strings consumed by
    ``sse_tool_error`` (chain_cap is most specific; cancelled wins over
    timeout if both are true at the same time)."""

    def on_before_tool_call(self, event):  # type: ignore[no-untyped-def]
        agent = event.agent
        state = getattr(agent, "chain_state", None)
        if state is None:
            return  # chassis not wired; let the call through
        chat_id = getattr(agent, "chat_id", None)

        if chat_id and is_cancel_requested(chat_id):
            event.cancel_tool(reason="cancelled")
            return

        if state.is_chain_cap_exhausted():
            event.cancel_tool(reason="chain_cap")
            return

        if state.is_wall_clock_exhausted():
            event.cancel_tool(reason="wall_clock")
            return
        # RemainingBudget gate stub: always allow under v1. The #131
        # swap-in PR adds the real check here.
```

Adjust the `event.cancel_tool(reason="...")` shape to match the actual Strands API as probed.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_tool_hooks.py -k guard -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/tool_hooks.py tests/unit/test_tool_hooks.py
git commit -m "feat(agents): ToolCallGuardHook enforces chain-cap + interrupt + wall-clock (#181)"
```

### Task 7: Hook handlers — `ToolCallTelemetryHook`

**Files:**
- Modify: `src/channel/agents/tool_hooks.py`
- Modify: `tests/unit/test_tool_hooks.py`

Read the Strands API for `AfterToolCallEvent`:
`uv run python -c "from strands.hooks.events import AfterToolCallEvent; help(AfterToolCallEvent)"`

Confirm what fields surface the tool result + error state (the event likely has `event.tool_use`, `event.result`, `event.error`).

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_tool_hooks.py`:

```python
from channel.agents.tool_hooks import ToolCallTelemetryHook


@pytest.mark.asyncio
async def test_telemetry_hook_increments_counter_on_success(monkeypatch):
    emitted = []

    async def fake_record(success):
        emitted.append(success)

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_tool_call_outcome",
        fake_record,
    )

    hook = ToolCallTelemetryHook()
    state = ChainState()
    agent = MagicMock()
    agent.chain_state = state

    event = MagicMock()
    event.agent = agent
    event.error = None  # success path
    event.tool_use = {"name": "current_time"}

    await hook.on_after_tool_call(event)

    assert state.tool_calls_used == 1
    assert emitted == [True]


@pytest.mark.asyncio
async def test_telemetry_hook_records_failure_when_event_has_error(monkeypatch):
    emitted = []

    async def fake_record(success):
        emitted.append(success)

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_tool_call_outcome",
        fake_record,
    )

    hook = ToolCallTelemetryHook()
    state = ChainState()
    agent = MagicMock()
    agent.chain_state = state

    event = MagicMock()
    event.agent = agent
    event.error = RuntimeError("upstream 5xx")
    event.tool_use = {"name": "current_time"}

    await hook.on_after_tool_call(event)

    # Counter still increments — the chain budget is spent regardless.
    assert state.tool_calls_used == 1
    assert emitted == [False]
```

The exact shape of `event.error` and how Strands signals tool failure may differ; adjust after probing.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_tool_hooks.py -k telemetry -v`
Expected: ImportError.

- [ ] **Step 3: Implement the telemetry hook**

Append to `src/channel/agents/tool_hooks.py`:

```python
from channel.metrics import record_tool_call_outcome


class ToolCallTelemetryHook:
    """``AfterToolCallEvent`` — increments the per-chain counter, fires
    the [meta] synthetic ASSISTANT message via the supplied
    ``memory_writer`` callable, and emits EMF.

    ``memory_writer`` is wired in ``build_agent()`` to
    ``AgentCoreMemoryHook.write_meta_event``. The callable arg lets the
    hook be unit-tested without AgentCore Memory plumbing.
    """

    def __init__(self, memory_writer=None):
        # memory_writer: Callable[[str], None] | None — synchronous, fail-soft.
        self._memory_writer = memory_writer

    async def on_after_tool_call(self, event):  # type: ignore[no-untyped-def]
        agent = event.agent
        state = getattr(agent, "chain_state", None)
        if state is not None:
            state.increment()
        success = getattr(event, "error", None) is None
        await record_tool_call_outcome(success=success)

        if self._memory_writer is not None:
            tool_name = getattr(event, "tool_use", {}).get("name") or "unknown"
            verb = "used" if success else "tried"
            self._memory_writer(f"{verb} {tool_name}")
```

Add a test asserting the memory_writer is called:

```python
@pytest.mark.asyncio
async def test_telemetry_hook_calls_memory_writer_with_tool_name(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_tool_call_outcome",
        lambda **_: _noop_async(),
    )

    hook = ToolCallTelemetryHook(memory_writer=lambda text: calls.append(text))
    agent = MagicMock()
    agent.chain_state = ChainState()

    event = MagicMock()
    event.agent = agent
    event.error = None
    event.tool_use = {"name": "current_time"}

    await hook.on_after_tool_call(event)
    assert calls == ["used current_time"]


async def _noop_async():
    return None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_tool_hooks.py -k telemetry -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/tool_hooks.py tests/unit/test_tool_hooks.py
git commit -m "feat(agents): ToolCallTelemetryHook — counter + EMF on tool result (#181)"
```

### Task 8: Wire `tools=[]` into `build_agent()`; attach `ChainState`

**Files:**
- Modify: `src/channel/agents/chat_agent.py:140-196`
- Modify: `tests/unit/test_chat_agent.py`

- [ ] **Step 1: Write the failing test** in `tests/unit/test_chat_agent.py`

```python
def test_build_agent_accepts_tools_and_attaches_chain_state(monkeypatch):
    monkeypatch.setenv("STARTER_ENV", "test")
    monkeypatch.setattr(
        "channel.agents.chat_agent.get_or_create_memory",
        lambda env: "mem-test",
    )

    from channel.agents.tools.clock import current_time
    agent = build_agent(
        model_id="claude-sonnet-4-6",
        user_id="user-1",
        chat_id="chat-1",
        tools=[current_time],
    )

    # ChainState attached
    from channel.agents.tool_hooks import ChainState
    assert isinstance(agent.chain_state, ChainState)
    # Tools landed in the agent (exact attribute depends on Strands —
    # may be agent.tool_names, agent.tool_registry, etc. Confirm by
    # printing agent.__dict__ after construction during writing.)
    assert hasattr(agent, "tool_registry") or hasattr(agent, "tool_names")


def test_build_agent_works_without_tools(monkeypatch):
    """Default empty tool list — preserves existing behavior."""
    monkeypatch.setenv("STARTER_ENV", "test")
    monkeypatch.setattr(
        "channel.agents.chat_agent.get_or_create_memory",
        lambda env: "mem-test",
    )

    agent = build_agent(
        model_id="claude-sonnet-4-6",
        user_id="user-1",
        chat_id="chat-1",
    )
    from channel.agents.tool_hooks import ChainState
    assert isinstance(agent.chain_state, ChainState)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_chat_agent.py -k tools_and_attaches -v`
Expected: TypeError on unexpected `tools` keyword.

- [ ] **Step 3: Modify `build_agent()`**

In `src/channel/agents/chat_agent.py`:

Add imports at the top with the existing imports:
```python
from channel.agents.tool_hooks import (
    ChainState,
    ModelVisibilityAddendumHook,
    ToolCallGuardHook,
    ToolCallTelemetryHook,
)
```

Change the `build_agent` signature (line 140) to:
```python
def build_agent(
    *,
    model_id: str,
    user_id: str,
    chat_id: str,
    prior_messages: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = None,
    tools: list[Any] | None = None,
) -> Agent:
```

Update the docstring to document `tools` (one paragraph).

Update the `Agent(...)` construction (line 182-191) to:
```python
    addendum_hook = ModelVisibilityAddendumHook()
    guard_hook = ToolCallGuardHook()
    # Telemetry hook records [meta] used <tool> ... via memory_hook's
    # CreateEvent path (epic #128 decision 6).
    telemetry_hook = ToolCallTelemetryHook(memory_writer=memory_hook.write_meta_event)
    agent = Agent(
        model=bedrock,
        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        # Order matters: addendum first (mutates system prompt),
        # recall + write hooks for memory, guard + telemetry for tools.
        hooks=[
            addendum_hook,
            recall_hook,
            memory_hook,
            guard_hook,
            telemetry_hook,
        ],
        messages=cast(Messages, prior_messages or []),
        tools=tools or [],
    )
    agent.chat_id = chat_id  # type: ignore[attr-defined]
    agent.chain_state = ChainState()  # type: ignore[attr-defined]
    return agent
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_chat_agent.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/chat_agent.py tests/unit/test_chat_agent.py
git commit -m "feat(agents): wire tools=[] into build_agent + attach ChainState (#181)"
```

### Task 9: Register `current_time` conditionally in the chat route

**Files:**
- Modify: `src/channel/api/chats.py` — in the function that calls `build_agent(...)`, look around line 580 (the function that streams the chat response). The exact location is just before the `build_agent(...)` call.
- Modify: `tests/unit/test_chats_routes.py` (or wherever the chat route test lives — `grep -rn "build_agent" tests/unit/` to find).

- [ ] **Step 1: Probe the test location**

Run: `grep -rn "build_agent" tests/unit/ src/channel/api/ | head -20`

Identify the function in `chats.py` that calls `build_agent(model_id=..., user_id=..., chat_id=...)`. This is the chat-stream entry point.

- [ ] **Step 2: Write the failing test**

Add a test asserting that when `STARTER_CLOCK_TOOL_ENABLED=1`, the route passes `tools=[current_time]` to `build_agent`; when `0` or unset, `tools=[]`.

Sketch:

```python
def test_chats_route_enables_clock_tool_when_flag_on(monkeypatch):
    """STARTER_CLOCK_TOOL_ENABLED=1 → current_time registered."""
    monkeypatch.setenv("STARTER_CLOCK_TOOL_ENABLED", "1")
    captured = {}

    def fake_build_agent(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr("channel.api.chats.build_agent", fake_build_agent)
    # ... invoke the stream entry function with minimal fixtures ...
    assert len(captured["tools"]) == 1
    assert captured["tools"][0].__name__ == "current_time"


def test_chats_route_omits_clock_tool_when_flag_off(monkeypatch):
    monkeypatch.delenv("STARTER_CLOCK_TOOL_ENABLED", raising=False)
    # ... same shape ...
    assert captured["tools"] == []
```

- [ ] **Step 3: Implement in `chats.py`**

Add at the top of `chats.py` with the other imports:
```python
from channel.agents.tools.clock import current_time
```

Just before the `build_agent(...)` call:
```python
tool_registry: list[Any] = []
if os.environ.get("STARTER_CLOCK_TOOL_ENABLED") == "1":
    tool_registry.append(current_time)
```

Pass `tools=tool_registry` to `build_agent(...)`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_chats_routes.py -k clock_tool -v`
Expected: 2 passed.

- [ ] **Step 5: CDK env-var wiring**

In `infra/stacks/channel_stack.py`, find where the API Lambda is created (`channel_api_lambda` or similar — `grep -n "Lambda\|Function(" infra/stacks/channel_stack.py | head`). Add to the Lambda's environment:

```python
"STARTER_CLOCK_TOOL_ENABLED": "1" if env_name in {"jc", "dev"} else "0",
```

- [ ] **Step 6: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_routes.py infra/stacks/channel_stack.py
git commit -m "feat(api): register current_time tool behind STARTER_CLOCK_TOOL_ENABLED (#181)"
```

### Task 10: PR-1 — open PR, run pre-push, wait for CI

- [ ] **Step 1: Run pre-push**

Run: `uv run inv pre-push`
Expected: all green. If anything fails, fix before continuing.

- [ ] **Step 2: Verify the agent-safe scope check**

`#181` does NOT carry the `agent-safe` label (per the issue body) — so the autonomous merge path doesn't apply. Stop after CI passes; the human drives the merge.

- [ ] **Step 3: Push and open PR**

Per CLAUDE.md push discipline (W1–W7), use the explicit-refspec first-push form:

```bash
git push -u origin <branch>:<branch>
gh pr create --base development --title "feat(agents): tool-use chassis PR-1 (backend, #181)" --body "$(cat <<'EOF'
## Summary

PR-1 of three (per strategy spec policy P4) for #181 — the backend chassis:
- `tools=[...]` param on `build_agent()`
- `tool_hooks.py` with `ChainState` + cancel registry + three hook classes
- `ToolCallSuccesses` / `ToolCallFailures` EMF counters
- Memory invariant lifted into `_payload_from_messages` docstring + hardened test
- `current_time` smoke-test tool behind `STARTER_CLOCK_TOOL_ENABLED` (default off in prod)

**No SSE protocol changes, no SPA changes** — those are PR-2 and PR-3.

Closes part of #181 (PR-2 / PR-3 to follow).

Strategy spec: `docs/superpowers/specs/2026-06-06-epic-128-tool-use-sequencing.md`

## Test plan

- [x] Unit tests for hooks, ChainState, cancel registry, EMF counter, memory invariant
- [x] `uv run inv pre-push` passes
- [ ] CI green
EOF
)"
```

- [ ] **Step 4: Watch CI**

Run: `gh run watch` — fix any failures.

---

## PR-2: SSE protocol

Scope: 4 new SSE emitters + `translate_event()` extension for Strands' tool events + dispatcher wiring in `chats.py`. **No SPA changes yet** — the SPA still ignores the new event types.

### Task 11: Four new SSE emitters

**Files:**
- Modify: `src/channel/agents/strands_sse.py` — append new emitters at the bottom
- Modify: `tests/unit/agents/test_strands_sse.py` (or wherever the existing SSE tests live — `grep -rn "sse_delta\|sse_done" tests/unit/`)

- [ ] **Step 1: Write failing tests**

Append to the existing SSE test file:

```python
import json
from channel.agents.strands_sse import (
    sse_tool_started,
    sse_tool_progress,
    sse_tool_finished,
    sse_tool_error,
)


def test_sse_tool_started_shape():
    raw = sse_tool_started(
        tool_name="current_time",
        tool_use_id="tu-1",
        args_preview="(no args)",
    ).decode()
    assert raw.startswith("data: ")
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert payload == {
        "type": "tool_started",
        "tool_name": "current_time",
        "tool_use_id": "tu-1",
        "args_preview": "(no args)",
    }


def test_sse_tool_started_truncates_args_preview_at_200():
    raw = sse_tool_started(
        tool_name="x", tool_use_id="t", args_preview="A" * 500
    ).decode()
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert len(payload["args_preview"]) == 200


def test_sse_tool_progress_shape():
    raw = sse_tool_progress(tool_use_id="t1", status_text="searching...").decode()
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert payload == {
        "type": "tool_progress",
        "tool_use_id": "t1",
        "status_text": "searching...",
    }


def test_sse_tool_finished_truncates_summary_at_500():
    raw = sse_tool_finished(tool_use_id="t1", summary="X" * 1000).decode()
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert len(payload["summary"]) == 500


def test_sse_tool_error_shape():
    raw = sse_tool_error(
        tool_use_id="t1",
        error_type="chain_cap",
        partial_result_count=3,
    ).decode()
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert payload == {
        "type": "tool_error",
        "tool_use_id": "t1",
        "error_type": "chain_cap",
        "partial_result_count": 3,
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/agents/ -k tool_ -v`
Expected: ImportError.

- [ ] **Step 3: Implement the emitters**

Append to `src/channel/agents/strands_sse.py`:

```python
_ARGS_PREVIEW_MAX = 200
_SUMMARY_MAX = 500


def sse_tool_started(*, tool_name: str, tool_use_id: str, args_preview: str) -> bytes:
    """Emit a ``tool_started`` SSE event (epic #128 / #181).

    Marks the start of one tool call inside an assistant turn. The SPA
    appends a collapsible step row to the active message.
    """
    return _sse(
        {
            "type": "tool_started",
            "tool_name": tool_name,
            "tool_use_id": tool_use_id,
            "args_preview": args_preview[:_ARGS_PREVIEW_MAX],
        }
    )


def sse_tool_progress(*, tool_use_id: str, status_text: str) -> bytes:
    """Emit a ``tool_progress`` SSE event (epic #128 / #181).

    Model-driven status text for the in-flight tool call. Driven by
    Strands' ``ToolStreamEvent`` yields.
    """
    return _sse(
        {
            "type": "tool_progress",
            "tool_use_id": tool_use_id,
            "status_text": status_text,
        }
    )


def sse_tool_finished(*, tool_use_id: str, summary: str) -> bytes:
    """Emit a ``tool_finished`` SSE event (epic #128 / #181).

    Marks tool completion. ``summary`` is a short human-readable summary
    of the result — never the raw payload. The payload stays out of
    SSE; it goes through the model's regular reply path.
    """
    return _sse(
        {
            "type": "tool_finished",
            "tool_use_id": tool_use_id,
            "summary": summary[:_SUMMARY_MAX],
        }
    )


def sse_tool_error(
    *, tool_use_id: str, error_type: str, partial_result_count: int
) -> bytes:
    """Emit a ``tool_error`` SSE event (epic #128 / #181).

    ``error_type`` is a free-form short string the SPA can branch on:
    ``"timeout"``, ``"rate_limit"``, ``"upstream_5xx"``, ``"chain_cap"``,
    ``"cancelled"``, ``"wall_clock"``. Partial > none: a chain that
    fires 3 of 5 steps and fails on 4 still surfaces
    ``partial_result_count=3`` so the SPA renders "I have 3 of 5
    results; here's what I found" rather than a silent abort.
    """
    return _sse(
        {
            "type": "tool_error",
            "tool_use_id": tool_use_id,
            "error_type": error_type,
            "partial_result_count": partial_result_count,
        }
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/agents/ -k tool_ -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/strands_sse.py tests/unit/agents/test_strands_sse.py
git commit -m "feat(sse): four new tool-event emitters (#181)"
```

### Task 12: Extend `translate_event()` for Strands tool events

**Files:**
- Modify: `src/channel/agents/strands_sse.py:19-59` (`translate_event` body)
- Create: `tests/unit/agents/test_strands_sse_tool_events.py`

Read Strands' event shapes once:
`uv run python -c "from strands.types import _events; print([x for x in dir(_events) if 'Tool' in x])"`

Identify the event shape for `ToolUseStreamEvent`, `ToolStreamEvent`, `ToolResultEvent`, `ToolCancelEvent`, `ToolInterruptEvent`. Inspect their fields (e.g. `tool_use.toolUseId`, `tool_use.name`, etc.).

- [ ] **Step 1: Write failing tests** in the new file

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""translate_event() tool-event translation tests (#181)."""

from channel.agents.strands_sse import translate_event


def test_translate_event_tool_use_start():
    # The exact shape depends on how Strands surfaces ToolUseStreamEvent
    # through the event_loop event stream. Probe with a real
    # stream_async call against a stub tool to capture the dict shape,
    # then write the test against that shape. Documented Strands path:
    # strands/types/_events.py::ToolUseStreamEvent.
    event = {
        "event": {
            "toolUseStart": {
                "toolUseId": "tu-1",
                "name": "current_time",
                "input": {},
            }
        }
    }
    kind, payload = translate_event(event)
    assert kind == "tool_started"
    assert payload["tool_use_id"] == "tu-1"
    assert payload["tool_name"] == "current_time"


def test_translate_event_tool_result():
    event = {
        "event": {
            "toolResult": {
                "toolUseId": "tu-1",
                "status": "success",
                "content": [{"text": "2026-06-06T12:34:56Z"}],
            }
        }
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    assert payload["tool_use_id"] == "tu-1"
    assert "2026-06-06" in payload["summary"]


def test_translate_event_tool_cancel():
    event = {
        "event": {
            "toolCancel": {
                "toolUseId": "tu-1",
                "reason": "chain_cap",
            }
        }
    }
    kind, payload = translate_event(event)
    assert kind == "tool_error"
    assert payload["error_type"] == "chain_cap"
```

These tests are written against assumed Strands event shapes. **Before implementing, run an empirical probe**: write a one-shot script that registers `current_time` with a real `Agent`, calls `stream_async`, and prints every `event` dict. Use those exact shapes in the test fixtures + implementation.

Run the probe:
```bash
uv run python -c "
import asyncio, os
os.environ.setdefault('STARTER_ENV', 'jc')
from channel.agents.chat_agent import build_agent
from channel.agents.tools.clock import current_time

async def main():
    a = build_agent(model_id='claude-haiku-4-5', user_id='probe', chat_id='probe', tools=[current_time])
    async for e in a.stream_async('what time is it?'):
        print(repr(e)[:200])
asyncio.run(main())
"
```

Update the test fixtures + the `translate_event` extension to match what the probe prints.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/agents/test_strands_sse_tool_events.py -v`
Expected: assertion failures (`translate_event` returns `("skip", None)` for tool events).

- [ ] **Step 3: Extend `translate_event()`**

In `src/channel/agents/strands_sse.py`, augment the body of `translate_event` to dispatch on the new tool event keys observed in the probe. Sketch (adjust to actual shapes):

```python
    if "toolUseStart" in inner:
        tu = inner["toolUseStart"]
        return (
            "tool_started",
            {
                "tool_name": tu.get("name", ""),
                "tool_use_id": tu.get("toolUseId", ""),
                "args_preview": json.dumps(tu.get("input", {}))[:200],
            },
        )

    if "toolResult" in inner:
        tr = inner["toolResult"]
        summary = "".join(
            block.get("text", "") for block in tr.get("content", []) if "text" in block
        )
        return (
            "tool_finished",
            {"tool_use_id": tr.get("toolUseId", ""), "summary": summary},
        )

    if "toolCancel" in inner:
        tc = inner["toolCancel"]
        return (
            "tool_error",
            {
                "tool_use_id": tc.get("toolUseId", ""),
                "error_type": tc.get("reason", "unknown"),
                "partial_result_count": 0,
            },
        )
```

Update the `translate_event` docstring to document the new kinds.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/agents/test_strands_sse_tool_events.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/strands_sse.py tests/unit/agents/test_strands_sse_tool_events.py
git commit -m "feat(sse): translate_event handles Strands tool events (#181)"
```

### Task 13: Wire dispatcher in `chats.py`; set cancel signal on disconnect

**Files:**
- Modify: `src/channel/api/chats.py:591-600` (main stream loop)
- Modify: `src/channel/api/chats.py` — `finally:` block of the stream generator

- [ ] **Step 1: Update the dispatcher**

In the existing dispatch around line 591-600, add branches for the four new kinds. Each branch emits the matching SSE bytes:

```python
    async for event in agent.stream_async(user_payload):
        kind, payload = translate_event(event)
        if kind == "delta":
            accumulated.append(payload)
            yield sse_delta(payload)
        elif kind == "stop":
            stop_reason = payload["stop_reason"]
        elif kind == "usage":
            input_tokens = payload["input_tokens"]
            output_tokens = payload["output_tokens"]
        elif kind == "tool_started":
            yield sse_tool_started(**payload)
        elif kind == "tool_finished":
            yield sse_tool_finished(**payload)
        elif kind == "tool_error":
            yield sse_tool_error(**payload)
        elif kind == "tool_progress":
            yield sse_tool_progress(**payload)
```

Add the imports at the top of the file (alongside the existing `sse_*` imports):
```python
from channel.agents.strands_sse import (
    sse_tool_error,
    sse_tool_finished,
    sse_tool_progress,
    sse_tool_started,
    ...
)
```

- [ ] **Step 2: Wire the cancel-signal on disconnect**

Find the stream generator's `finally:` block (or wrap the existing `async for event in agent.stream_async(...)` in `try/finally`). On exit (whether normal or `GeneratorExit` / disconnect), call:

```python
from channel.agents.tool_hooks import clear_cancel_signal, set_cancel_signal
# ...
try:
    async for event in agent.stream_async(user_payload):
        # ... existing dispatch ...
except (asyncio.CancelledError, GeneratorExit):
    # SSE client disconnected — signal in-flight tool calls to cancel
    set_cancel_signal(chat.chat_id)
    raise
finally:
    # Ensure the signal is cleared at the end of every turn so a
    # subsequent turn for the same chat doesn't see stale cancel state.
    clear_cancel_signal(chat.chat_id)
```

The exact placement depends on chats.py's current structure — read around lines 580-700 first. The `try/finally` wraps the agent.stream_async loop; the `except` is what triggers the cancel signal on disconnect.

- [ ] **Step 3: Test the dispatcher** — integration test

Add to `tests/integration/test_chats_routes.py` (or wherever stream-shape tests live):

```python
async def test_stream_emits_tool_started_when_translate_returns_kind(monkeypatch):
    """If translate_event returns kind=tool_started, the stream must
    emit sse_tool_started bytes."""
    # Mock agent.stream_async to yield one event that translate_event
    # classifies as tool_started; assert the bytes appear in the output.
    # (Full fixture left to the implementer; pattern is identical to
    # existing delta/done stream tests in this file.)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/integration/test_chats_routes.py -k tool_started -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/chats.py tests/integration/test_chats_routes.py
git commit -m "feat(api): dispatch tool SSE events + cancel-signal on disconnect (#181)"
```

### Task 14: PR-2 — open PR, run pre-push, wait for CI

- [ ] **Step 1: Pre-push + push + PR**

Run: `uv run inv pre-push`
Expected: green.

Push using the explicit-refspec subsequent-push form per CLAUDE.md W6 (this branch already has a remote tracker from PR-1; rebase on `origin/development` if PR-1 has merged in the meantime).

Open PR:
```bash
gh pr create --base development --title "feat(sse): tool-use chassis PR-2 (SSE protocol, #181)" --body "$(cat <<'EOF'
## Summary

PR-2 of three for #181 — the SSE protocol layer:
- Four new emitters in `strands_sse.py` (`sse_tool_started` / `sse_tool_progress` / `sse_tool_finished` / `sse_tool_error`)
- `translate_event()` extended to handle Strands' tool events
- `chats.py` dispatcher emits the new SSE types
- Cancel-signal wiring on SSE client disconnect

**SPA still ignores the new event types** — that's PR-3.

Part of #181 (PR-3 follows).

## Test plan

- [x] Unit tests for emitters, translate_event tool branches
- [x] Integration test for stream dispatcher emitting new events
- [x] `uv run inv pre-push`
- [ ] CI green
EOF
)"
```

- [ ] **Step 2: Watch CI**

Run: `gh run watch`

---

## PR-3: SPA step list + smoke test

Scope: SPA `useChatStream` event handlers + collapsible step list in `Conversation.jsx` + the `ToolResultBlock` seed component + chain_cap distinct rendering + an end-to-end smoke test exercising `current_time`.

### Task 15: SSE event handlers in `useChatStream.js`

**Files:**
- Modify: `ui/src/hooks/useChatStream.js:111-148` (the event.type dispatch chain)
- Modify: `ui/src/hooks/useChatStream.test.js`

- [ ] **Step 1: Write the failing test**

Sketch (the existing test file has the harness for SSE simulation; copy that pattern):

```javascript
it("attaches a tool step to the active assistant message on tool_started", async () => {
  const { result } = renderHook(...);

  await act(async () => {
    await pumpSse(result, [
      { type: "user_persisted", msg_id: "u1", seq: 0 },
      { type: "delta", text: "Let me check the time. " },
      {
        type: "tool_started",
        tool_name: "current_time",
        tool_use_id: "tu-1",
        args_preview: "{}",
      },
    ]);
  });

  const lastAssistant = result.current.messages.at(-1);
  expect(lastAssistant.toolSteps).toHaveLength(1);
  expect(lastAssistant.toolSteps[0]).toMatchObject({
    toolUseId: "tu-1",
    toolName: "current_time",
    status: "running",
    argsPreview: "{}",
  });
});

it("marks the matching step as finished on tool_finished", async () => {
  // ... pumpSse with tool_started then tool_finished ...
  // assert toolSteps[0].status === "finished" && summary set
});

it("marks the matching step as error on tool_error", async () => {
  // ... pumpSse with tool_started then tool_error chain_cap ...
  // assert toolSteps[0].status === "error" && errorType === "chain_cap"
});
```

The exact `pumpSse` helper and renderHook fixture follow the existing test file's pattern.

- [ ] **Step 2: Run to verify failure**

Run: `cd ui && npm test -- useChatStream`
Expected: failure on missing `toolSteps`.

- [ ] **Step 3: Implement the handlers**

In `useChatStream.js`, after the existing `follow_ups_suggested` branch (line ~148):

```javascript
} else if (event.type === "tool_started") {
  // Attach a new tool step to the active assistant message.
  setMessages((msgs) => {
    const next = [...msgs];
    const idx = next.findIndex((m) => m.streaming);
    if (idx === -1) return next;
    const target = next[idx];
    const steps = [...(target.toolSteps || [])];
    steps.push({
      toolUseId: event.tool_use_id,
      toolName: event.tool_name,
      argsPreview: event.args_preview,
      statusText: null,
      summary: null,
      errorType: null,
      partialResultCount: 0,
      status: "running",
    });
    next[idx] = { ...target, toolSteps: steps };
    return next;
  });
} else if (event.type === "tool_progress") {
  setMessages((msgs) => updateToolStep(msgs, event.tool_use_id, {
    statusText: event.status_text,
  }));
} else if (event.type === "tool_finished") {
  setMessages((msgs) => updateToolStep(msgs, event.tool_use_id, {
    summary: event.summary,
    status: "finished",
  }));
} else if (event.type === "tool_error") {
  setMessages((msgs) => updateToolStep(msgs, event.tool_use_id, {
    errorType: event.error_type,
    partialResultCount: event.partial_result_count,
    status: "error",
  }));
}
```

Add a helper at the top of the file (or inline):

```javascript
function updateToolStep(msgs, toolUseId, patch) {
  return msgs.map((m) => {
    if (!m.streaming || !m.toolSteps) return m;
    const idx = m.toolSteps.findIndex((s) => s.toolUseId === toolUseId);
    if (idx === -1) return m;
    const nextSteps = [...m.toolSteps];
    nextSteps[idx] = { ...nextSteps[idx], ...patch };
    return { ...m, toolSteps: nextSteps };
  });
}
```

- [ ] **Step 4: Run tests**

Run: `cd ui && npm test -- useChatStream`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add ui/src/hooks/useChatStream.js ui/src/hooks/useChatStream.test.js
git commit -m "feat(ui): useChatStream attaches toolSteps to active message (#181)"
```

### Task 16: `ToolResultBlock` seed component

**Files:**
- Create: `ui/src/app/ToolResultBlock.jsx`
- Create: `ui/src/app/ToolResultBlock.test.jsx`

Policy P3: introduce a discriminated-union renderer. PR-3 doesn't ship any branches yet (no rich tool output — `current_time` is just a text summary); the component exists so #182 and #183 land into a known shape.

- [ ] **Step 1: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import ToolResultBlock from "./ToolResultBlock.jsx";

describe("ToolResultBlock", () => {
  it("renders the default branch when kind is unrecognised", () => {
    render(<ToolResultBlock kind="unknown" summary="hello" />);
    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  it("renders summary text by default", () => {
    render(<ToolResultBlock summary="the answer is 4" />);
    expect(screen.getByText("the answer is 4")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ui && npm test -- ToolResultBlock`
Expected: fail (component doesn't exist).

- [ ] **Step 3: Implement the seed component**

Create `ui/src/app/ToolResultBlock.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
//
// Discriminated-union renderer for tool results in the Conversation
// view. Introduced by #181 PR-3 as the seed; #182 adds the
// kind="web-search-citations" branch, #183 adds kind="code-output".
// Strategy spec policy P3.
//
// Until rich branches land, the default branch is a plain monospace
// summary — what the chassis exit test (current_time) needs.

export default function ToolResultBlock({ kind, summary }) {
  // Future branches will switch on `kind`. Today only the default
  // text-summary path renders.
  return (
    <div className="tool-result-block" data-kind={kind || "default"}>
      <pre className="tool-result-summary">{summary}</pre>
    </div>
  );
}
```

- [ ] **Step 4: Run tests**

Run: `cd ui && npm test -- ToolResultBlock`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/ToolResultBlock.jsx ui/src/app/ToolResultBlock.test.jsx
git commit -m "feat(ui): ToolResultBlock seed component for #182/#183 to extend (#181)"
```

### Task 17: Collapsible step list rendering in `Conversation.jsx`

**Files:**
- Modify: `ui/src/app/Conversation.jsx`
- Modify: `ui/src/app/Conversation.test.jsx`
- Modify: `ui/src/styles/app.css` (new `.tool-steps` / `.tool-step-row` rules)

Policy preamble: "like having hands" → steps are collapsed-by-default, expandable, tool name + status visible without expansion. `chain_cap` errors render distinctly (graceful cap-reached message) rather than the generic error path.

- [ ] **Step 1: Write the failing tests**

Add to `Conversation.test.jsx`:

```jsx
it("renders a collapsible step list for an assistant message with toolSteps", () => {
  const messages = [
    {
      msg_id: "a1",
      role: "assistant",
      text: "Here's the time.",
      toolSteps: [
        {
          toolUseId: "tu-1",
          toolName: "current_time",
          argsPreview: "{}",
          status: "finished",
          summary: "2026-06-06T12:34:56Z",
        },
      ],
    },
  ];
  render(<Conversation messages={messages} ... />);
  expect(screen.getByText("current_time")).toBeInTheDocument();
  // Step list collapsed by default — summary not visible until click
  expect(screen.queryByText(/2026-06-06/)).not.toBeInTheDocument();
});

it("expands the step list to reveal the summary on click", () => {
  // ... same fixture; userEvent.click on the toggle; expect the
  // summary to appear ...
});

it("renders chain_cap error with a distinct affordance", () => {
  const messages = [
    {
      msg_id: "a1",
      role: "assistant",
      text: "",
      toolSteps: [
        {
          toolUseId: "tu-1",
          toolName: "current_time",
          status: "error",
          errorType: "chain_cap",
          partialResultCount: 7,
        },
      ],
    },
  ];
  render(<Conversation messages={messages} ... />);
  // Distinct text — chain-cap-specific copy, not generic "Tool failed"
  expect(screen.getByText(/reached the tool-use limit/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ui && npm test -- Conversation`
Expected: assertions fail on missing step list.

- [ ] **Step 3: Implement step list rendering**

In `Conversation.jsx`, inside the per-message render branch, when `message.role === "assistant"` and `message.toolSteps?.length > 0`, render a `<ToolStepList steps={message.toolSteps} />` below the message text.

Create the inline `ToolStepList` component (in the same file or extract — judge based on file length):

```jsx
function ToolStepList({ steps }) {
  const [expanded, setExpanded] = React.useState(false);
  return (
    <div className="tool-steps">
      <button
        type="button"
        className="tool-steps-toggle"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <Icon name={expanded ? "chevron-down" : "chevron-right"} />
        {steps.length} tool {steps.length === 1 ? "step" : "steps"}
      </button>
      {expanded && (
        <ol className="tool-steps-list">
          {steps.map((s) => (
            <ToolStepRow key={s.toolUseId} step={s} />
          ))}
        </ol>
      )}
      {/* Always-visible compact header per step (the "what's happening
          right now" surface). When expanded, the row's summary panel
          replaces this. */}
      {!expanded && (
        <ol className="tool-steps-compact">
          {steps.map((s) => (
            <li key={s.toolUseId}>{s.toolName} — {s.status}</li>
          ))}
        </ol>
      )}
    </div>
  );
}

function ToolStepRow({ step }) {
  if (step.status === "error" && step.errorType === "chain_cap") {
    return (
      <li className="tool-step-row tool-step-row-chain-cap">
        I reached the tool-use limit for this turn
        ({step.partialResultCount} step{step.partialResultCount === 1 ? "" : "s"} completed).
      </li>
    );
  }
  if (step.status === "error") {
    return (
      <li className="tool-step-row tool-step-row-error">
        {step.toolName} failed ({step.errorType})
      </li>
    );
  }
  return (
    <li className="tool-step-row">
      <div className="tool-step-row-head">
        <strong>{step.toolName}</strong>
        <span className="tool-step-status">{step.status}</span>
      </div>
      {step.summary && <ToolResultBlock summary={step.summary} />}
    </li>
  );
}
```

Add the imports for `React` and `Icon` and `ToolResultBlock` at the top of the file.

Add CSS to `ui/src/styles/app.css`:

```css
.tool-steps {
  margin: 0.5rem 0;
  border-left: 2px solid var(--border);
  padding-left: 0.75rem;
}

.tool-steps-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  background: none;
  border: none;
  color: var(--ink-muted);
  cursor: pointer;
  font: inherit;
  padding: 0;
}

.tool-steps-compact { margin: 0.25rem 0 0; padding-left: 1rem; color: var(--ink-muted); }
.tool-steps-list { margin: 0.5rem 0 0; padding-left: 1rem; }
.tool-step-row { padding: 0.25rem 0; }
.tool-step-row-chain-cap { color: var(--ink-muted); font-style: italic; }
.tool-step-row-error { color: var(--danger); }
.tool-step-row-head { display: flex; gap: 0.5rem; align-items: baseline; }
.tool-step-status { color: var(--ink-muted); font-size: 0.85em; }

.tool-result-block { margin-top: 0.25rem; }
.tool-result-summary {
  background: var(--raised);
  border-radius: var(--radius-sm);
  padding: 0.5rem;
  font-family: var(--font-mono);
  font-size: 0.85em;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
}
```

- [ ] **Step 4: Run tests**

Run: `cd ui && npm test -- Conversation`
Expected: pass. Watch for jsdom hex→rgb gotchas from CLAUDE.md (`rgb(...)` not `#hex`).

- [ ] **Step 5: Visual verification** — per the "UI changes need live verification" memory

Start the dev stack: `uv run inv dev`
In a browser, send a message that triggers `current_time` (e.g. "what time is it?"). Confirm:
- The collapsible step list appears below the assistant message
- Clicking expands; clicking again collapses
- Step name + status visible without expansion
- Take a screenshot for the PR

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/Conversation.jsx ui/src/app/Conversation.test.jsx ui/src/styles/app.css
git commit -m "feat(ui): collapsible tool-step list in Conversation (#181)"
```

### Task 18: Smoke test exercising the full chassis

**Files:**
- Create: `tests/e2e/test_tool_use_smoke.py`

This is the chassis exit test — the end-to-end verification that #181 actually works.

- [ ] **Step 1: Write the smoke test**

Pattern: mirror `tests/e2e/test_memory_writes.py`'s shape. Authenticate via the test-email bypass, open a chat, send "what time is it?", read the SSE stream, assert at least one `tool_started` event appears for `current_time` and at least one `tool_finished` event with a parseable ISO-8601 string in the summary.

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Chassis smoke test (#181). Exercises current_time end-to-end."""

import os
import re
import pytest

from tests.e2e.conftest import (
    create_chat,
    open_sse_stream,
    require_env_var,
)


@pytest.mark.skipif(
    os.environ.get("STARTER_CLOCK_TOOL_ENABLED") != "1",
    reason="chassis exit test requires STARTER_CLOCK_TOOL_ENABLED=1",
)
def test_current_time_round_trip(live_admin_token):
    chat = create_chat(live_admin_token, title="smoke")
    events = open_sse_stream(
        live_admin_token, chat["chat_id"], "what time is it?"
    )

    started = [e for e in events if e.get("type") == "tool_started"]
    finished = [e for e in events if e.get("type") == "tool_finished"]
    assert any(e["tool_name"] == "current_time" for e in started)
    finished_for_clock = [
        e for e in finished
        if any(s["tool_use_id"] == e["tool_use_id"] for s in started if s["tool_name"] == "current_time")
    ]
    assert finished_for_clock
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", finished_for_clock[0]["summary"])
```

The `create_chat` / `open_sse_stream` helpers may not exist in conftest yet — if not, write a thin wrapper that hits the relevant API endpoints. Pattern from existing `tests/e2e/test_memory_writes.py`.

- [ ] **Step 2: Run locally**

Start the dev stack with the clock tool enabled:
```bash
STARTER_CLOCK_TOOL_ENABLED=1 uv run inv dev --seed
```

In another terminal:
```bash
STARTER_CLOCK_TOOL_ENABLED=1 uv run inv e2e-local --tests tests/e2e/test_tool_use_smoke.py
```
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_tool_use_smoke.py
git commit -m "test(e2e): chassis smoke test exercising current_time (#181)"
```

### Task 19: Write the chassis design doc

**Files:**
- Create: `docs/superpowers/specs/2026-06-06-tool-use-chassis-design.md`

The strategy spec at the top of this plan references this doc. #181 lists writing it as a task. It documents:
- The "like having hands" phenomenology preamble (Channel-the-product's input)
- MCP-Sampling vs MCP-as-tool-server callout
- Hook architecture (the three handlers, their event types, what state each reads/writes)
- ChainState lifecycle (per-turn instantiation, cancel-signal registry)
- Strands 1.41.0 cited paths (the same four `strands/*` modules from the decisions comment)
- ADR-0009 cross-reference
- SSE protocol spec (the four event types + their payload shapes — defer to `sse_*` emitter docstrings as the contract; restate here for design-doc completeness)
- EMF cardinality discipline note ("no per-tool/per-actor dimensions — see `metrics.py::record_tool_call_outcome`")
- The model-visibility addendum rationale ("walls are worse than dashboards" — Channel 2026-06-03)
- Policy callouts that distinguish this PR from #182/#183 (the three-PR split, the `current_time` smoke-test policy, the `ToolResultBlock` seed)

The doc is descriptive (what we built and why), not prescriptive (we already built it).

- [ ] **Step 1: Write the doc** (head section + each subsection above, ~250-400 lines).

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-06-06-tool-use-chassis-design.md
git commit -m "docs(specs): tool-use chassis design doc (#181)"
```

### Task 20: CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add entries under `[Unreleased]`**

In `### Added`:
- Tool-use chassis: Strands `tools=[...]` wiring, three event hooks, four new SSE event types, collapsible step list in the Conversation view. Per-chain budget envelope + chain-length cap + mid-chain interrupt. (#181, epic #128)

In `### Meta`:
- Memory invariant lifted into the `_payload_from_messages` docstring + hardened test (#181, epic #128)
- Strategy spec for epic #128 sequencing: `docs/superpowers/specs/2026-06-06-epic-128-tool-use-sequencing.md`
- Chassis design doc: `docs/superpowers/specs/2026-06-06-tool-use-chassis-design.md`

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "chore(changelog): #181 tool-use chassis (epic #128)"
```

### Task 21: PR-3 — open PR, run pre-push, wait for CI

- [ ] **Step 1: Pre-push**

Run: `uv run inv pre-push`
Expected: green.

- [ ] **Step 2: Push + open PR**

Open PR:
```bash
gh pr create --base development --title "feat(ui): tool-use chassis PR-3 (SPA + smoke test, #181)" --body "$(cat <<'EOF'
## Summary

PR-3 of three for #181 — the SPA layer + chassis exit test:
- `useChatStream` handles the four new tool SSE event types; attaches `toolSteps` to the in-flight assistant message
- Collapsible step list in `Conversation.jsx` (default collapsed, expandable). chain_cap errors render distinctly per Channel-input #5
- `ToolResultBlock` seed component for #182 / #183 to extend (policy P3)
- E2e smoke test exercising `current_time` end-to-end
- Design doc + CHANGELOG entry

Closes #181 (with PR-1 and PR-2).

## Test plan

- [x] vitest passes (Conversation + useChatStream + ToolResultBlock)
- [x] Live verification in a browser via `inv dev` — screenshot attached
- [x] `inv e2e-local --tests tests/e2e/test_tool_use_smoke.py` passes
- [x] `uv run inv pre-push` passes
- [ ] CI green
EOF
)"
```

Attach the screenshot from Task 17 Step 5 (drag-drop in the PR description after creation).

- [ ] **Step 3: Watch CI + Copilot review**

Run: `gh run watch`
Run: `gh pr view --json reviewRequests` once initial CI lands; respond to Copilot review feedback per the issue-worker §7.5 protocol.

- [ ] **Step 4: Once all three PRs merge, close #181**

GitHub auto-closes via the per-PR `Closes #181` (last PR carries it; first two say "Part of #181"). Verify.

---

## Self-review checklist (executor runs this before declaring complete)

- [ ] All hook handlers fire under unit tests with mocked Strands events
- [ ] Memory invariant test passes — no `toolUse` or `toolResult` string in the produced payload
- [ ] EMF counter test enforces signature shape (no dimensions parameter)
- [ ] `STARTER_CLOCK_TOOL_ENABLED=0` in prod (verified in CDK env var diff)
- [ ] Conversation step list collapsed by default; expand reveals summary
- [ ] `chain_cap` error renders distinct copy, not the generic error path
- [ ] `RemainingBudget` line is OMITTED from the addendum (policy P1)
- [ ] Tool META events appear in AgentCore Memory — confirmed via `GET /api/_debug/memory/events?chat_id=...` showing `[meta] used current_time` entries
- [ ] **Dashboards gap** — strategy spec policy P6 wants CloudWatch dashboards in PR-1, but a `CloudWatchDashboards` CDK construct is its own meaningful task (~50-100 LOC). This plan does NOT include the dashboard construct; the EMF counters are in place so dashboards can be built on top. **Recommended:** file a follow-up issue `chore(infra): tool-use CloudWatch dashboards (epic #128 P6)` after PR-1 lands, with `size:s` `priority:p2`. If the implementer disagrees and wants dashboards inline, add them as Task 9.5 between Task 9 and Task 10.
- [ ] PR-1, PR-2, PR-3 all merged
- [ ] #181 closes via the last PR
- [ ] No code touches `infra/stacks/channel_stack.py` other than the `STARTER_CLOCK_TOOL_ENABLED` env-var addition (and any CodeExecLambda-related changes belong to #183 — out of scope here)

## Notes for the implementer

- **Empirical Strands probing matters.** The exact event shapes (Strands `_events.py`) and hook event-callback signatures (Strands `hooks/events.py`) are the source of truth — write the probe scripts from the relevant tasks and use the output to confirm the test fixtures and implementation match reality. Probing prevents the "compiles but the integration is wrong" failure mode.
- **Three PRs, not one.** Don't squash. PR-1 backend → PR-2 SSE → PR-3 SPA, each with its own CI run.
- **The `current_time` tool is the chassis exit test, not a product feature.** If anyone asks "why is the model telling people the time," the answer is: behind a feature flag for dev only. Don't flip the flag in prod without a separate design pass.
- **The `RemainingBudget` line is OMITTED under the stub.** The #131 swap-in PR adds the line for the first time. Until then, only chain-progress renders in the addendum.
- **CLAUDE.md invariants already landed via PR #185** — don't re-add the "Tool payloads never persist..." and "Tool integrations use Strands' native..." product-decision bullets.
- **agent-safe scope:** #181 does NOT carry the `agent-safe` label. Stop after CI passes; the human drives the merge. No `gh pr merge --auto`.
