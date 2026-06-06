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
