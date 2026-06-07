# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for tool_hooks chain state + cancel registry."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from channel.agents.tool_hooks import (
    ChainState,
    ModelVisibilityAddendumHook,
    ToolCallGuardHook,
    ToolCallTelemetryHook,
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


def test_chain_state_increment_clamps_at_max():
    """Once the chain cap is reached, additional increments don't push
    the counter past the max — keeps the addendum well-formed
    ("tool calls used: 2 of 2" rather than "4 of 2") and prevents
    unbounded growth if the model keeps attempting tool calls after the
    guard cancels them."""
    state = ChainState(tool_calls_max=2)
    state.increment()
    state.increment()  # used 2 of 2 — at cap
    state.increment()  # attempted again — clamped
    state.increment()
    assert state.tool_calls_used == 2
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


# ---------------------------------------------------------------------------
# ToolCallGuardHook — BeforeToolCallEvent
#
# Strands' ``BeforeToolCallEvent`` exposes ``cancel_tool: bool | str`` as a
# writable dataclass field; assigning a string cancels the tool call and
# Strands surfaces the string as the tool-result error message. That's the
# channel by which our reason codes (``cancelled`` / ``chain_cap`` /
# ``wall_clock``) reach PR-2's ``sse_tool_error`` translator — Strands'
# tool-result error message becomes the SSE ``error_type``.
# ---------------------------------------------------------------------------


def test_guard_hook_blocks_on_chain_cap():
    """When the chain cap is exhausted, the guard sets ``cancel_tool`` to
    the ``chain_cap`` reason string. Strands wraps that into a tool-result
    error which PR-2's translator surfaces to the SPA as
    ``error_type=chain_cap``."""
    hook = ToolCallGuardHook()
    state = ChainState(tool_calls_max=2)
    state.increment()
    state.increment()  # used 2 of 2

    agent = MagicMock()
    agent.chain_state = state
    agent.chat_id = "chat-1"

    event = MagicMock()
    event.agent = agent
    event.cancel_tool = False

    hook.on_before_tool_call(event)

    assert event.cancel_tool == "chain_cap"


def test_guard_hook_blocks_on_cancel_signal():
    """SSE-client disconnect sets the module-level cancel signal. The next
    BeforeToolCallEvent reads it and cancels with the ``cancelled``
    reason. The signal is consumed (one-shot) so a subsequent turn on
    the same chat in a warm Lambda doesn't inherit the stale cancel."""
    hook = ToolCallGuardHook()
    state = ChainState()
    agent = MagicMock()
    agent.chain_state = state
    agent.chat_id = "chat-cancelled"

    set_cancel_signal("chat-cancelled")
    try:
        event = MagicMock()
        event.agent = agent
        event.cancel_tool = False
        hook.on_before_tool_call(event)
        assert event.cancel_tool == "cancelled"
        # One-shot consume: the guard clears the signal immediately so a
        # warm-Lambda re-use on the same chat_id doesn't see a stale flag.
        assert is_cancel_requested("chat-cancelled") is False
    finally:
        clear_cancel_signal("chat-cancelled")


def test_guard_hook_blocks_on_wall_clock_exhaustion():
    """When the soft wall-clock budget is exceeded, the guard cancels with
    the ``wall_clock`` reason."""
    hook = ToolCallGuardHook()
    state = ChainState(wall_clock_budget_sec=10)
    state.started_at = time.monotonic() - 11  # simulate elapsed

    agent = MagicMock()
    agent.chain_state = state
    agent.chat_id = "chat-slow"

    event = MagicMock()
    event.agent = agent
    event.cancel_tool = False
    hook.on_before_tool_call(event)
    assert event.cancel_tool == "wall_clock"


def test_guard_hook_allows_under_caps_with_no_cancel():
    """Within all caps and with no cancel signal, the guard is a no-op —
    ``cancel_tool`` stays falsy and Strands proceeds with the call."""
    hook = ToolCallGuardHook()
    state = ChainState()
    state.increment()  # used 1 of 8

    agent = MagicMock()
    agent.chain_state = state
    agent.chat_id = "chat-ok"

    event = MagicMock()
    event.agent = agent
    event.cancel_tool = False
    hook.on_before_tool_call(event)
    assert event.cancel_tool is False


def test_guard_hook_cancel_signal_wins_over_chain_cap():
    """Order matters: a user-initiated cancel is the highest-fidelity
    intent signal and must take precedence over the structural chain_cap
    reason. Otherwise the SPA would surface ``chain_cap`` to a user who
    actually hit the cancel button."""
    hook = ToolCallGuardHook()
    state = ChainState(tool_calls_max=1)
    state.increment()  # also chain-cap-exhausted

    agent = MagicMock()
    agent.chain_state = state
    agent.chat_id = "chat-both"

    set_cancel_signal("chat-both")
    try:
        event = MagicMock()
        event.agent = agent
        event.cancel_tool = False
        hook.on_before_tool_call(event)
        assert event.cancel_tool == "cancelled"
        # One-shot consume applies here too — even when chain_cap would
        # have fired anyway, the cancel signal is consumed on observe.
        assert is_cancel_requested("chat-both") is False
    finally:
        clear_cancel_signal("chat-both")


def test_guard_hook_chain_cap_wins_over_wall_clock():
    """Chain cap is a structural ceiling; wall-clock is a soft budget.
    When both fire, surface ``chain_cap`` — it's the more concrete reason
    for the SPA to surface to the user."""
    hook = ToolCallGuardHook()
    state = ChainState(tool_calls_max=1, wall_clock_budget_sec=10)
    state.increment()
    state.started_at = time.monotonic() - 11  # both exhausted

    agent = MagicMock()
    agent.chain_state = state
    agent.chat_id = "chat-both-budgets"

    event = MagicMock()
    event.agent = agent
    event.cancel_tool = False
    hook.on_before_tool_call(event)
    assert event.cancel_tool == "chain_cap"


def test_guard_hook_no_chain_state_is_noop():
    """When the agent has no ``chain_state`` attribute (chassis not yet
    wired through this call path), the hook short-circuits without
    touching ``cancel_tool``."""
    hook = ToolCallGuardHook()
    agent = MagicMock(spec=["chat_id"])
    agent.chat_id = "chat-x"

    event = MagicMock()
    event.agent = agent
    event.cancel_tool = False
    hook.on_before_tool_call(event)
    assert event.cancel_tool is False


def test_guard_hook_missing_chat_id_skips_cancel_check():
    """When the agent lacks a ``chat_id`` attribute (defensive — shouldn't
    happen post-Task 8), the cancel-signal lookup is skipped but the
    chain-cap / wall-clock checks still run."""
    hook = ToolCallGuardHook()
    state = ChainState(tool_calls_max=1)
    state.increment()

    agent = MagicMock(spec=["chain_state"])
    agent.chain_state = state

    event = MagicMock()
    event.agent = agent
    event.cancel_tool = False
    hook.on_before_tool_call(event)
    assert event.cancel_tool == "chain_cap"


def test_guard_hook_register_hooks_subscribes_to_before_tool_call():
    """``register_hooks`` wires the callback onto ``BeforeToolCallEvent``
    via the registry — matches the HookProvider pattern."""
    from strands.hooks.events import BeforeToolCallEvent

    hook = ToolCallGuardHook()
    registry = MagicMock()
    hook.register_hooks(registry)
    registry.add_callback.assert_called_once_with(BeforeToolCallEvent, hook.on_before_tool_call)


# ---------------------------------------------------------------------------
# ToolCallTelemetryHook — AfterToolCallEvent
#
# Strands' ``AfterToolCallEvent`` exposes:
#   * ``tool_use`` (TypedDict with ``name`` / ``toolUseId`` / ``input``)
#   * ``result`` (ToolResult — ``status: "success" | "error"``)
#   * ``exception: Exception | None``  — populated when the tool raised
#   * ``cancel_message: str | None``   — populated when the guard cancelled
#
# Success = no exception AND result.status != "error" AND not cancelled.
# Callbacks are sync (same shape as AgentCoreMemoryHook._on_after_invocation);
# async work (``record_tool_call_outcome``) is dispatched via
# ``asyncio.create_task`` with a strong-ref discard callback per Sonar
# python:S7502.
# ---------------------------------------------------------------------------


async def _drain_pending_tasks() -> None:
    """Yield control to the event loop so create_task'd coroutines run.

    The telemetry hook fires-and-forgets via ``asyncio.create_task``;
    tests need to await the pending tasks before asserting EMF calls.
    """
    # One yield is sufficient — the inner coroutine just appends to a list.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_telemetry_hook_increments_counter_on_success(monkeypatch):
    """Success path: no exception, result.status == 'success'. Counter
    increments and ``record_tool_call_outcome(True)`` fires."""
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
    event.exception = None
    event.cancel_message = None
    event.result = {"status": "success"}
    event.tool_use = {"name": "current_time"}

    hook.on_after_tool_call(event)
    await _drain_pending_tasks()

    assert state.tool_calls_used == 1
    assert emitted == [True]


@pytest.mark.asyncio
async def test_telemetry_hook_records_failure_when_event_has_exception(monkeypatch):
    """The tool raised — ``event.exception`` is set. Counter still
    increments (the chain budget is spent regardless), and
    ``record_tool_call_outcome(False)`` fires."""
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
    event.exception = RuntimeError("upstream 5xx")
    event.cancel_message = None
    event.result = {"status": "success"}
    event.tool_use = {"name": "current_time"}

    hook.on_after_tool_call(event)
    await _drain_pending_tasks()

    # Counter still increments — the chain budget is spent regardless.
    assert state.tool_calls_used == 1
    assert emitted == [False]


@pytest.mark.asyncio
async def test_telemetry_hook_records_failure_when_result_status_is_error(monkeypatch):
    """No exception, but the tool returned an error-status result.
    Strands surfaces this as ``result.status == 'error'`` without raising
    — still counts as a tool-call failure."""
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
    event.exception = None
    event.cancel_message = None
    event.result = {"status": "error"}
    event.tool_use = {"name": "current_time"}

    hook.on_after_tool_call(event)
    await _drain_pending_tasks()

    assert state.tool_calls_used == 1
    assert emitted == [False]


@pytest.mark.asyncio
async def test_telemetry_hook_records_failure_on_cancelled(monkeypatch):
    """The guard cancelled the tool call — ``event.cancel_message`` is set.
    Counter increments + records failure (the cancel still spent a slot
    in the chain budget; surfacing that as success would be misleading)."""
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
    event.exception = None
    event.cancel_message = "cancelled"
    event.result = {"status": "success"}
    event.tool_use = {"name": "current_time"}

    hook.on_after_tool_call(event)
    await _drain_pending_tasks()

    assert state.tool_calls_used == 1
    assert emitted == [False]


@pytest.mark.asyncio
async def test_telemetry_hook_calls_memory_writer_with_used_verb_on_success(monkeypatch):
    """On success, the META message uses the ``used <tool>`` verb so the
    recall hook can surface "the model successfully used X" in future
    system prompts."""

    async def fake_record(success):  # noqa: ARG001
        return None

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_tool_call_outcome",
        fake_record,
    )

    calls = []
    hook = ToolCallTelemetryHook(memory_writer=lambda text: calls.append(text))
    agent = MagicMock()
    agent.chain_state = ChainState()

    event = MagicMock()
    event.agent = agent
    event.exception = None
    event.cancel_message = None
    event.result = {"status": "success"}
    event.tool_use = {"name": "current_time"}

    hook.on_after_tool_call(event)
    await _drain_pending_tasks()
    assert calls == ["used current_time"]


@pytest.mark.asyncio
async def test_telemetry_hook_calls_memory_writer_with_tried_verb_on_failure(monkeypatch):
    """On failure, the META message uses the ``tried <tool>`` verb. The
    recall hook reads these back into future system prompts so the model
    can avoid re-trying a tool that just failed."""

    async def fake_record(success):  # noqa: ARG001
        return None

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_tool_call_outcome",
        fake_record,
    )

    calls = []
    hook = ToolCallTelemetryHook(memory_writer=lambda text: calls.append(text))
    agent = MagicMock()
    agent.chain_state = ChainState()

    event = MagicMock()
    event.agent = agent
    event.exception = RuntimeError("boom")
    event.cancel_message = None
    event.result = {"status": "success"}
    event.tool_use = {"name": "current_time"}

    hook.on_after_tool_call(event)
    await _drain_pending_tasks()
    assert calls == ["tried current_time"]


@pytest.mark.asyncio
async def test_telemetry_hook_memory_writer_handles_missing_tool_name(monkeypatch):
    """Defensive: if ``tool_use`` is missing a ``name`` key (Strands
    contract violation — shouldn't happen, but fail-soft), fall back to
    ``unknown`` rather than crashing."""

    async def fake_record(success):  # noqa: ARG001
        return None

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_tool_call_outcome",
        fake_record,
    )

    calls = []
    hook = ToolCallTelemetryHook(memory_writer=lambda text: calls.append(text))
    agent = MagicMock()
    agent.chain_state = ChainState()

    event = MagicMock()
    event.agent = agent
    event.exception = None
    event.cancel_message = None
    event.result = {"status": "success"}
    event.tool_use = {}  # no "name"

    hook.on_after_tool_call(event)
    await _drain_pending_tasks()
    assert calls == ["used unknown"]


@pytest.mark.asyncio
async def test_telemetry_hook_no_memory_writer_is_noop(monkeypatch):
    """When ``memory_writer`` is None (e.g. unit tests, or AgentCore
    Memory disabled), the hook still increments + emits EMF but skips
    the META event write."""
    emitted = []

    async def fake_record(success):
        emitted.append(success)

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_tool_call_outcome",
        fake_record,
    )

    hook = ToolCallTelemetryHook(memory_writer=None)
    state = ChainState()
    agent = MagicMock()
    agent.chain_state = state

    event = MagicMock()
    event.agent = agent
    event.exception = None
    event.cancel_message = None
    event.result = {"status": "success"}
    event.tool_use = {"name": "current_time"}

    hook.on_after_tool_call(event)
    await _drain_pending_tasks()

    assert state.tool_calls_used == 1
    assert emitted == [True]


@pytest.mark.asyncio
async def test_telemetry_hook_no_chain_state_skips_increment(monkeypatch):
    """When the agent has no ``chain_state`` attribute (chassis not yet
    wired through this call path), the hook still emits EMF but skips
    the counter increment."""
    emitted = []

    async def fake_record(success):
        emitted.append(success)

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_tool_call_outcome",
        fake_record,
    )

    hook = ToolCallTelemetryHook()
    agent = MagicMock(spec=[])  # no chain_state

    event = MagicMock()
    event.agent = agent
    event.exception = None
    event.cancel_message = None
    event.result = {"status": "success"}
    event.tool_use = {"name": "current_time"}

    hook.on_after_tool_call(event)
    await _drain_pending_tasks()

    # EMF still fires — the tool call happened.
    assert emitted == [True]


@pytest.mark.asyncio
async def test_telemetry_hook_swallows_emf_dispatch_errors(monkeypatch):
    """If ``record_tool_call_outcome`` raises (transient EMF flush failure
    — network blip, missing IAM permission, etc.), the create_task'd
    coroutine must swallow + log rather than surfacing as an unhandled
    "Task exception was never retrieved" warning. The counter still
    increments — failure to emit telemetry is decoupled from the chain
    budget bookkeeping.

    We mock the module-level ``logger`` directly rather than using
    ``caplog`` because the channel logger sets ``propagate = False`` once
    ``configure_logging`` has been invoked by any prior test in the
    session, which prevents caplog (attached to the root logger) from
    seeing the record. Patching the logger sidesteps the global state
    coupling entirely."""

    async def raising_record(success):  # noqa: ARG001
        raise RuntimeError("EMF flush failed")

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_tool_call_outcome",
        raising_record,
    )

    mock_logger = MagicMock()
    monkeypatch.setattr("channel.agents.tool_hooks.logger", mock_logger)

    hook = ToolCallTelemetryHook()
    state = ChainState()
    agent = MagicMock()
    agent.chain_state = state

    event = MagicMock()
    event.agent = agent
    event.exception = None
    event.cancel_message = None
    event.result = {"status": "success"}
    event.tool_use = {"name": "current_time"}

    hook.on_after_tool_call(event)
    # Drain via gather — if _record_outcome_safe re-raised, gather would
    # propagate the exception and the test would fail.
    await asyncio.gather(*hook._pending_tasks)

    # Counter still incremented despite the EMF failure.
    assert state.tool_calls_used == 1
    # The wrapper logged the failure at WARNING with exc_info=True.
    mock_logger.warning.assert_called_once()
    args, kwargs = mock_logger.warning.call_args
    assert "tool_call.emf_dispatch_failed" in args[0]
    assert kwargs.get("exc_info") is True


def test_telemetry_hook_register_hooks_subscribes_to_after_tool_call():
    """``register_hooks`` wires the callback onto ``AfterToolCallEvent``
    via the registry — matches the HookProvider pattern."""
    from strands.hooks.events import AfterToolCallEvent

    hook = ToolCallTelemetryHook()
    registry = MagicMock()
    hook.register_hooks(registry)
    registry.add_callback.assert_called_once_with(AfterToolCallEvent, hook.on_after_tool_call)
