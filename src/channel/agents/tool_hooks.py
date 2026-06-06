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

import time
from dataclasses import dataclass, field
from typing import Any

from strands.hooks.events import BeforeModelCallEvent


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
