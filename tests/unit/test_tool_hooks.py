# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for tool_hooks chain state + cancel registry."""

from __future__ import annotations

import time

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
