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

The cancel-signal registry is a module-level set keyed by ``chat_id``;
``chats.py`` calls ``set_cancel_signal(chat_id)`` from the
``finally:`` block when the SSE client disconnects.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from strands.hooks.events import (
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
)

from channel.metrics import record_tool_call_outcome


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
    return prompt[:idx] + prompt[end + len(_ADDENDUM_CLOSE) :]


class ModelVisibilityAddendumHook:
    """Strands ``HookProvider`` that renders the chain-progress addendum
    into the system prompt before each model call.

    Subscribes to ``BeforeModelCallEvent``; reads
    ``event.agent.chain_state`` (set by ``build_agent`` at Task 8) and
    appends ``tool calls used: N of M`` to ``event.agent.system_prompt``
    so the model can economize.

    Policy P1 (epic #128 strategy spec): the ``RemainingBudget`` line is
    OMITTED under the v1 stub. The #131 swap-in PR adds it for the first
    time; until then, only ``tool calls used: N of M`` renders.

    Conforms to the ``HookProvider`` protocol (``strands.hooks.registry``)
    structurally; no explicit base class — Strands uses
    ``@runtime_checkable`` — matches the pattern in
    ``AgentCoreMemoryHook`` / ``AgentCoreRecallHook``.

    The addendum is wrapped in ``<tool-use-budget>...</tool-use-budget>``
    sentinels so re-invocations within the same chain replace rather
    than compound (otherwise the system prompt grows per turn).
    """

    def register_hooks(self, registry: Any, **_: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self.on_before_model_call)

    def on_before_model_call(self, event: BeforeModelCallEvent) -> None:
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


class ToolCallGuardHook:
    """Strands ``HookProvider`` that enforces the three pre-call gates:
    user-initiated cancel, the chain-length cap, and the soft wall-clock
    budget.

    Subscribes to ``BeforeToolCallEvent``. Reads
    ``event.agent.chain_state`` (attached by Task 8's ``build_agent``) and
    ``event.agent.chat_id``. When any gate trips, assigns a reason string
    to ``event.cancel_tool`` — Strands then wraps the call into a
    tool-result error whose message is the reason string. PR-2's
    ``sse_tool_error`` translator surfaces that message verbatim as the
    SPA-visible ``error_type``, so the reason vocabulary
    (``cancelled`` / ``chain_cap`` / ``wall_clock``) is the contract
    between this hook and the SSE layer.

    Order of guards (high-fidelity user intent first):

    1. ``cancelled`` — explicit SSE-client disconnect; the user already
       decided to stop. Surfacing any other reason would be misleading.
    2. ``chain_cap`` — structural ceiling on tool-call depth; concrete
       enough for the SPA to surface.
    3. ``wall_clock`` — soft budget; last because it's the fuzziest
       signal and tends to fire after the chain has already produced
       useful work.

    The ``RemainingBudget`` gate is intentionally absent from v1 — the
    #131 swap-in PR adds it once the context-budget plumbing lands.
    """

    def register_hooks(self, registry: Any, **_: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.on_before_tool_call)

    def on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        agent = event.agent
        state = getattr(agent, "chain_state", None)
        if state is None:
            return  # chassis not yet wired through this call path
        chat_id = getattr(agent, "chat_id", None)

        if chat_id and is_cancel_requested(chat_id):
            event.cancel_tool = "cancelled"
            return

        if state.is_chain_cap_exhausted():
            event.cancel_tool = "chain_cap"
            return

        if state.is_wall_clock_exhausted():
            event.cancel_tool = "wall_clock"
            return
        # RemainingBudget gate stub: always allow under v1. The #131
        # swap-in PR adds the real check here.


class ToolCallTelemetryHook:
    """Strands ``HookProvider`` for the ``AfterToolCallEvent`` side of the
    chassis.

    Three responsibilities per tool call:

    1. **Increment ``ChainState.tool_calls_used``** — the chain budget is
       spent whether the tool succeeded, raised, or was cancelled by the
       guard. Surfacing failures as "free" would let a misbehaving tool
       loop indefinitely.
    2. **Emit ``ToolCallSuccesses`` / ``ToolCallFailures`` EMF** via
       ``record_tool_call_outcome`` — counter-only, no per-tool
       dimensions (cf. ``metrics.record_tool_call_outcome`` docstring).
    3. **Fire a synthetic ``[meta] used <tool>`` ASSISTANT message** via
       the injected ``memory_writer`` callable (Task 8 wires this to
       ``AgentCoreMemoryHook.write_meta_event``). The recall hook
       (``recall.py``) reads these META events back into future system
       prompts so the model can avoid re-trying a tool that just failed
       — hence the ``used`` / ``tried`` verb split.

    **Sync callback, async EMF.** Strands' ``AfterToolCallEvent``
    callback signature is sync (the ``HookEvent`` machinery doesn't
    await callbacks). ``record_tool_call_outcome`` is async, so this
    hook fires-and-forgets via ``asyncio.create_task`` with a strong-ref
    discard callback (Sonar python:S7502 — same pattern as
    ``AgentCoreMemoryHook._on_after_invocation``).

    **Success criterion**: ``event.exception is None`` AND
    ``event.cancel_message is None`` AND ``event.result["status"] != "error"``.
    Any of those three failure signals counts as a tool-call failure —
    Strands surfaces all three independently (exception is raised by the
    tool fn; cancel_message is set by the guard's ``cancel_tool`` path;
    result.status='error' is set by tools that return an error result
    without raising).

    Conforms to the ``HookProvider`` protocol structurally — no explicit
    base class, matching the pattern in this module + ``memory.py`` +
    ``recall.py``.
    """

    def __init__(self, memory_writer: Callable[[str], None] | None = None) -> None:
        # memory_writer: sync, fail-soft caller; passed text is the META
        # body (without the ``[meta]`` prefix — write_meta_event adds it).
        # Task 8 wires this to AgentCoreMemoryHook.write_meta_event.
        self._memory_writer = memory_writer
        # Strong refs to in-flight EMF emissions — without this, Python's
        # GC can reclaim the task before record_tool_call_outcome
        # completes. See asyncio.create_task() docs (Sonar python:S7502).
        self._pending_tasks: set[asyncio.Task[None]] = set()

    def register_hooks(self, registry: Any, **_: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.on_after_tool_call)

    def on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        agent = event.agent
        state = getattr(agent, "chain_state", None)
        if state is not None:
            state.increment()

        # Success = no exception, not cancelled, result status not "error".
        # Any single failure signal collapses to ``success=False`` so the
        # EMF counter + META verb both reflect the failure.
        exception = getattr(event, "exception", None)
        cancel_message = getattr(event, "cancel_message", None)
        result = getattr(event, "result", None) or {}
        result_status = result.get("status") if isinstance(result, dict) else None
        success = exception is None and cancel_message is None and result_status != "error"

        # EMF: fire-and-forget. The strong-ref + discard-on-done pattern
        # mirrors AgentCoreMemoryHook._on_after_invocation.
        task = asyncio.create_task(record_tool_call_outcome(success=success))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

        if self._memory_writer is not None:
            tool_use = getattr(event, "tool_use", None) or {}
            tool_name = tool_use.get("name") if isinstance(tool_use, dict) else None
            tool_name = tool_name or "unknown"
            verb = "used" if success else "tried"
            self._memory_writer(f"{verb} {tool_name}")
