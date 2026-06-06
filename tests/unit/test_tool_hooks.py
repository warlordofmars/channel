# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for tool_hooks chain state + cancel registry."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from channel.agents.tool_hooks import (
    ChainState,
    ModelVisibilityAddendumHook,
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


def test_chain_state_wall_clock_exhausted_uses_real_time():
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


def test_addendum_hook_appends_chain_progress_to_system_prompt():
    """The addendum surfaces ``tool calls used: N of M`` so the model can
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


def test_addendum_hook_no_chain_state_is_noop():
    """When the agent has no ``chain_state`` attribute (chassis not yet
    wired through this call path), the hook short-circuits without
    mutating the system prompt."""
    hook = ModelVisibilityAddendumHook()
    agent = MagicMock(spec=["system_prompt"])
    agent.system_prompt = "You are Channel."

    event = MagicMock()
    event.agent = agent

    hook.on_before_model_call(event)

    assert agent.system_prompt == "You are Channel."


def test_addendum_hook_handles_none_system_prompt():
    """A freshly-built agent may have ``system_prompt = None``. The hook
    must coerce to empty string rather than crashing on string concat."""
    hook = ModelVisibilityAddendumHook()
    state = ChainState()
    agent = MagicMock()
    agent.chain_state = state
    agent.system_prompt = None

    event = MagicMock()
    event.agent = agent

    hook.on_before_model_call(event)

    assert "tool calls used: 0 of 8" in agent.system_prompt


def test_addendum_hook_leaves_malformed_existing_addendum_alone():
    """Defensive: if a prior turn somehow left an opening sentinel without
    a closing one (corrupt prompt or external mutation), the strip helper
    returns the prompt as-is rather than truncating from the open sentinel
    to end-of-string. The new addendum still appends — slight bloat is
    preferred over data loss."""
    hook = ModelVisibilityAddendumHook()
    state = ChainState()
    agent = MagicMock()
    agent.chain_state = state
    # Manually craft a prompt with an opening sentinel and no close.
    agent.system_prompt = "You are Channel.\n\n<tool-use-budget>\nmalformed"

    event = MagicMock()
    event.agent = agent

    hook.on_before_model_call(event)

    # Original (malformed) block preserved verbatim; new addendum appended.
    assert "malformed" in agent.system_prompt
    assert "tool calls used: 0 of 8" in agent.system_prompt


def test_addendum_hook_register_hooks_subscribes_to_before_model_call():
    """``register_hooks`` wires the callback onto ``BeforeModelCallEvent``
    via the registry — matches the HookProvider pattern used by
    ``AgentCoreMemoryHook`` / ``AgentCoreRecallHook``."""
    from strands.hooks.events import BeforeModelCallEvent

    hook = ModelVisibilityAddendumHook()
    registry = MagicMock()
    hook.register_hooks(registry)
    registry.add_callback.assert_called_once_with(BeforeModelCallEvent, hook.on_before_model_call)
