# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for tool_hooks chain state + cancel registry."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from channel.agents import tool_hooks
from channel.agents.tool_hooks import (
    ChainState,
    ModelVisibilityAddendumHook,
    ToolCallGuardHook,
    ToolCallTelemetryHook,
    ToolResultSizeBoundHook,
    _truncate_utf8,
    _truncation_marker,
    clear_cancel_signal,
    is_cancel_requested,
    set_cancel_signal,
)


@pytest.fixture(autouse=True)
def _reset_cancel_signals():
    """Ensure no cancel-signal entries leak between tests. The registry is
    module-level so a test that sets a signal without clearing it would
    otherwise pollute downstream tests (especially the TTL tests, which
    inspect dict contents directly)."""
    tool_hooks._CANCEL_SIGNALS.clear()
    yield
    tool_hooks._CANCEL_SIGNALS.clear()


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


# ---------------------------------------------------------------------------
# Cancel-signal TTL — bounds the module-level registry against the
# disconnect-without-followup leak in a warm Lambda. A chat that
# disconnects mid-chain (no further BeforeToolCallEvent to one-shot
# consume) AND never receives another turn (no chats.py entry-time
# clear) would otherwise leave its entry in the registry forever.
# Opportunistic prune on every public op caps the dict size at the cost
# of one O(n) walk per call — amortized O(1) because n stays small.
# ---------------------------------------------------------------------------


def test_cancel_signal_ttl_prunes_expired_on_set():
    """A signal set long ago (timestamp older than the TTL) is pruned the
    next time ``set_cancel_signal`` runs — even when the target chat_id
    differs from the stale one. Bounds the registry under steady-state
    churn: every new disconnect drops every leaked entry that exceeded
    its 5-min observation window."""
    stale_ts = time.monotonic() - (tool_hooks._CANCEL_SIGNAL_TTL_SEC + 10)
    tool_hooks._CANCEL_SIGNALS["leaked-chat"] = stale_ts

    set_cancel_signal("fresh-chat")

    assert "leaked-chat" not in tool_hooks._CANCEL_SIGNALS
    assert "fresh-chat" in tool_hooks._CANCEL_SIGNALS


def test_cancel_signal_ttl_prunes_expired_on_is_cancel_requested():
    """The read path also prunes. A guard call that queries for a
    long-disconnected chat returns False AND removes the stale entry —
    so even read-only inspections bound the registry."""
    stale_ts = time.monotonic() - (tool_hooks._CANCEL_SIGNAL_TTL_SEC + 10)
    tool_hooks._CANCEL_SIGNALS["stale-chat"] = stale_ts

    assert is_cancel_requested("stale-chat") is False
    assert "stale-chat" not in tool_hooks._CANCEL_SIGNALS


def test_cancel_signal_ttl_keeps_fresh_entries():
    """A signal set within the TTL window survives subsequent public ops.
    Lower bound on the observation window — the guard must see the
    cancel for the duration of any reasonable chain wall-clock budget."""
    set_cancel_signal("fresh-1")
    # Run multiple ops — none of them should evict the fresh entry.
    set_cancel_signal("fresh-2")
    assert is_cancel_requested("fresh-1") is True
    clear_cancel_signal("unrelated")
    assert is_cancel_requested("fresh-1") is True


def test_cancel_signal_ttl_clear_handles_missing_chat_id_after_prune():
    """Defensive: the clear path is idempotent even when the chat_id has
    already been pruned. The current lifecycle clears stale signals via
    three mechanisms (entry-time clear in the next turn's
    ``_stream_bedrock_reply``, guard one-shot consume on observe, and
    TTL prune); any of them firing for an already-evicted entry must be
    a no-op rather than raising."""
    stale_ts = time.monotonic() - (tool_hooks._CANCEL_SIGNAL_TTL_SEC + 10)
    tool_hooks._CANCEL_SIGNALS["expired-chat"] = stale_ts

    # Should not raise; the prune evicts the entry, then pop(..., None)
    # is a no-op.
    clear_cancel_signal("expired-chat")
    assert "expired-chat" not in tool_hooks._CANCEL_SIGNALS


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


# ---------------------------------------------------------------------------
# _truncate_utf8 / _truncation_marker — byte-safe truncation primitives (#390)
# ---------------------------------------------------------------------------


def test_truncate_utf8_under_budget_is_byte_for_byte_untouched():
    """A string within the budget is returned unchanged with 0 bytes
    removed — the caller's "leave the block alone" signal."""
    text = "hello world"
    kept, removed = _truncate_utf8(text, 1000)
    assert kept == text
    assert removed == 0


def test_truncate_utf8_exactly_at_budget_is_untouched():
    """A string whose UTF-8 size exactly equals the budget is NOT
    truncated — the boundary is inclusive (``<= max_bytes``)."""
    text = "x" * 50
    kept, removed = _truncate_utf8(text, 50)
    assert kept == text
    assert removed == 0


def test_truncate_utf8_over_budget_clips_ascii_and_counts_removed():
    """ASCII (1 byte/char) over budget: kept is clipped to the budget and
    ``removed`` reconciles ``original == kept + removed``."""
    text = "x" * 1000
    kept, removed = _truncate_utf8(text, 100)
    assert len(kept.encode("utf-8")) == 100
    assert removed == 900
    assert len(kept.encode("utf-8")) + removed == len(text.encode("utf-8"))


def test_truncate_utf8_never_splits_a_multibyte_char():
    """The Euro sign is 3 UTF-8 bytes. A budget that lands mid-character
    must back off to the previous char boundary — the kept text decodes
    cleanly with no U+FFFD replacement char, and ``removed`` counts the
    whole dropped tail including the backed-off partial byte."""
    text = "€" * 200  # 600 bytes
    # 301 lands one byte into the 101st Euro sign (100*3 = 300, +1).
    kept, removed = _truncate_utf8(text, 301)
    assert kept == "€" * 100
    assert "�" not in kept  # no replacement char — the char was not split
    assert len(kept.encode("utf-8")) == 300  # backed off below the 301 budget
    assert removed == 300
    assert len(kept.encode("utf-8")) + removed == len(text.encode("utf-8"))


def test_truncation_marker_format():
    """The marker is exactly ``\\n[truncated N bytes]`` — the contract the
    model relies on to detect a clipped span."""
    assert _truncation_marker(42) == "\n[truncated 42 bytes]"


# ---------------------------------------------------------------------------
# ToolResultSizeBoundHook — AfterToolCallEvent (#390)
#
# Bounds oversized tool_result text/json content BEFORE it re-enters the
# Converse loop as input tokens. The counter is dispatched fire-and-forget
# via asyncio.create_task (same pattern as ToolCallTelemetryHook), so
# truncation-triggering tests are async and drain the pending tasks.
# ---------------------------------------------------------------------------


def _make_after_event(result, tool_use=None):
    event = MagicMock()
    event.result = result
    event.tool_use = tool_use if tool_use is not None else {"toolUseId": "tu-1"}
    return event


@pytest.mark.asyncio
async def test_size_bound_truncates_over_budget_text_block_with_marker(monkeypatch):
    """An over-budget text block is clipped to the budget and carries the
    exact ``[truncated N bytes]`` marker with the correct byte count."""
    counter_calls = []

    async def fake_counter():
        counter_calls.append(True)

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_mcp_tool_result_truncated",
        fake_counter,
    )

    hook = ToolResultSizeBoundHook(max_bytes=100)
    block = {"text": "x" * 1000}
    result = {"content": [block], "status": "success", "toolUseId": "tu-1"}

    hook.on_after_tool_call(_make_after_event(result))
    await _drain_pending_tasks()

    # Content clipped to the budget; marker appended on top.
    assert block["text"] == "x" * 100 + "\n[truncated 900 bytes]"
    # The retained content (before the marker) is exactly the budget.
    content, marker = block["text"].rsplit("\n[truncated", 1)
    assert len(content.encode("utf-8")) == 100
    assert marker == " 900 bytes]"
    # Counter fired exactly once for the one truncated block.
    assert counter_calls == [True]


@pytest.mark.asyncio
async def test_size_bound_leaves_under_budget_text_block_byte_for_byte(monkeypatch):
    """An under-budget text block passes through unchanged and emits no
    counter."""
    counter_calls = []

    async def fake_counter():
        counter_calls.append(True)

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_mcp_tool_result_truncated",
        fake_counter,
    )

    hook = ToolResultSizeBoundHook(max_bytes=1000)
    block = {"text": "small response"}
    result = {"content": [block], "status": "success"}

    hook.on_after_tool_call(_make_after_event(result))
    await _drain_pending_tasks()

    assert block["text"] == "small response"
    assert counter_calls == []


@pytest.mark.asyncio
async def test_size_bound_never_splits_multibyte_char_in_block(monkeypatch):
    """End-to-end multibyte safety on a real block: the clipped text
    decodes cleanly (no replacement char) and stays within the budget."""

    async def fake_counter():
        return None

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_mcp_tool_result_truncated",
        fake_counter,
    )

    hook = ToolResultSizeBoundHook(max_bytes=301)
    block = {"text": "€" * 200}
    result = {"content": [block], "status": "success"}

    hook.on_after_tool_call(_make_after_event(result))
    await _drain_pending_tasks()

    content, _marker = block["text"].rsplit("\n[truncated", 1)
    assert content == "€" * 100
    assert "�" not in content
    assert len(content.encode("utf-8")) <= 301


@pytest.mark.asyncio
async def test_size_bound_truncates_each_oversized_block_in_multi_block_result(monkeypatch):
    """A result with several content blocks: each oversized block is
    truncated independently, under-budget blocks are left untouched, and
    the counter fires once per truncated block."""
    counter_calls = []

    async def fake_counter():
        counter_calls.append(True)

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_mcp_tool_result_truncated",
        fake_counter,
    )

    hook = ToolResultSizeBoundHook(max_bytes=10)
    big_a = {"text": "a" * 50}
    small = {"text": "ok"}
    big_b = {"text": "b" * 40}
    result = {"content": [big_a, small, big_b], "status": "success"}

    hook.on_after_tool_call(_make_after_event(result))
    await _drain_pending_tasks()

    assert big_a["text"] == "a" * 10 + "\n[truncated 40 bytes]"
    assert small["text"] == "ok"  # under budget — untouched
    assert big_b["text"] == "b" * 10 + "\n[truncated 30 bytes]"
    # Two truncated blocks → two counter increments.
    assert counter_calls == [True, True]


@pytest.mark.asyncio
async def test_size_bound_truncates_oversized_json_block_to_text(monkeypatch):
    """An oversized ``json`` block is serialized, truncated, and REPLACED
    by a ``text`` block carrying the marker — a partial JSON string is no
    longer valid structured data, so text is the honest degradation."""

    async def fake_counter():
        return None

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_mcp_tool_result_truncated",
        fake_counter,
    )

    hook = ToolResultSizeBoundHook(max_bytes=20)
    payload = {"items": ["value" for _ in range(50)]}
    block = {"json": payload}
    result = {"content": [block], "status": "success"}

    hook.on_after_tool_call(_make_after_event(result))
    await _drain_pending_tasks()

    # The json key is gone; the block is now a text block with the marker.
    assert "json" not in block
    assert "text" in block
    content, _marker = block["text"].rsplit("\n[truncated", 1)
    assert len(content.encode("utf-8")) <= 20
    assert block["text"].endswith(" bytes]")


@pytest.mark.asyncio
async def test_size_bound_leaves_under_budget_json_block_untouched(monkeypatch):
    """A ``json`` block whose serialization fits the budget is left as a
    json block — no conversion, no counter."""
    counter_calls = []

    async def fake_counter():
        counter_calls.append(True)

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_mcp_tool_result_truncated",
        fake_counter,
    )

    hook = ToolResultSizeBoundHook(max_bytes=1000)
    block = {"json": {"ok": True}}
    result = {"content": [block], "status": "success"}

    hook.on_after_tool_call(_make_after_event(result))
    await _drain_pending_tasks()

    assert block == {"json": {"ok": True}}
    assert counter_calls == []


@pytest.mark.asyncio
async def test_size_bound_emits_structured_log_on_truncation(monkeypatch):
    """Each truncated block logs ``mcp.tool_result_truncated`` with the
    tool_use_id + original/kept byte counts."""

    async def fake_counter():
        return None

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_mcp_tool_result_truncated",
        fake_counter,
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("channel.agents.tool_hooks.logger", mock_logger)

    hook = ToolResultSizeBoundHook(max_bytes=100)
    result = {"content": [{"text": "x" * 1000}], "status": "success"}

    hook.on_after_tool_call(_make_after_event(result, tool_use={"toolUseId": "tu-42"}))
    await _drain_pending_tasks()

    mock_logger.info.assert_called_once()
    args = mock_logger.info.call_args[0]
    assert args[0] == "mcp.tool_result_truncated tool_use_id=%s original_bytes=%d kept_bytes=%d"
    assert args[1] == "tu-42"
    assert args[2] == 1000  # original bytes
    assert args[3] == 100  # kept bytes (content only, marker excluded)


@pytest.mark.asyncio
async def test_size_bound_falls_back_to_result_tool_use_id(monkeypatch):
    """When the event's ``tool_use`` carries no ``toolUseId``, the log
    falls back to the result's ``toolUseId``."""

    async def fake_counter():
        return None

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_mcp_tool_result_truncated",
        fake_counter,
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("channel.agents.tool_hooks.logger", mock_logger)

    hook = ToolResultSizeBoundHook(max_bytes=10)
    result = {
        "content": [{"text": "y" * 100}],
        "status": "success",
        "toolUseId": "res-tu",
    }

    hook.on_after_tool_call(_make_after_event(result, tool_use={}))
    await _drain_pending_tasks()

    assert mock_logger.info.call_args[0][1] == "res-tu"


@pytest.mark.asyncio
async def test_size_bound_tool_use_id_defaults_to_unknown(monkeypatch):
    """When neither the event nor the result carries a toolUseId, the log
    falls back to ``unknown`` rather than crashing."""

    async def fake_counter():
        return None

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_mcp_tool_result_truncated",
        fake_counter,
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("channel.agents.tool_hooks.logger", mock_logger)

    hook = ToolResultSizeBoundHook(max_bytes=10)
    result = {"content": [{"text": "z" * 100}], "status": "success"}
    # Build the event directly: event.tool_use is None (not the helper's
    # default) AND the result carries no toolUseId → the "unknown" branch.
    event = MagicMock()
    event.result = result
    event.tool_use = None

    hook.on_after_tool_call(event)
    await _drain_pending_tasks()

    assert mock_logger.info.call_args[0][1] == "unknown"


@pytest.mark.asyncio
async def test_size_bound_swallows_counter_dispatch_errors(monkeypatch):
    """If the counter coroutine raises (transient EMF flush failure), the
    wrapper swallows + logs at WARNING rather than surfacing an unhandled
    "Task exception was never retrieved" — bounding the result must never
    break the turn. Truncation of the block still happened."""

    async def raising_counter():
        raise RuntimeError("EMF flush failed")

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_mcp_tool_result_truncated",
        raising_counter,
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("channel.agents.tool_hooks.logger", mock_logger)

    hook = ToolResultSizeBoundHook(max_bytes=10)
    block = {"text": "w" * 100}
    result = {"content": [block], "status": "success"}

    hook.on_after_tool_call(_make_after_event(result))
    # Drain via gather — if _emit_truncated_counter_safe re-raised, gather
    # would propagate and fail the test.
    await asyncio.gather(*hook._pending_tasks)

    # Truncation still happened despite the EMF failure.
    assert block["text"] == "w" * 10 + "\n[truncated 90 bytes]"
    mock_logger.warning.assert_called_once()
    assert "mcp.tool_result_truncated_emf_dispatch_failed" in mock_logger.warning.call_args[0][0]
    assert mock_logger.warning.call_args[1].get("exc_info") is True


def test_size_bound_non_dict_result_is_noop():
    """When ``event.result`` is not a dict (e.g. an Exception on tool
    failure), the hook short-circuits without raising. No event loop is
    needed because no task is created."""
    hook = ToolResultSizeBoundHook(max_bytes=10)
    event = MagicMock()
    event.result = RuntimeError("tool blew up")
    hook.on_after_tool_call(event)  # must not raise


def test_size_bound_non_list_content_is_noop():
    """A malformed result whose ``content`` is not a list is skipped
    defensively."""
    hook = ToolResultSizeBoundHook(max_bytes=10)
    event = MagicMock()
    event.result = {"content": "not-a-list", "status": "success"}
    event.tool_use = {"toolUseId": "tu"}
    hook.on_after_tool_call(event)  # must not raise


def test_size_bound_missing_content_key_is_noop():
    """A result dict with no ``content`` key is skipped."""
    hook = ToolResultSizeBoundHook(max_bytes=10)
    event = MagicMock()
    event.result = {"status": "success"}
    event.tool_use = {"toolUseId": "tu"}
    hook.on_after_tool_call(event)  # must not raise


def test_size_bound_non_dict_block_is_skipped():
    """A content list carrying a non-dict entry (contract violation) is
    skipped rather than crashing the walk."""
    hook = ToolResultSizeBoundHook(max_bytes=10)
    event = MagicMock()
    event.result = {"content": ["not-a-dict", None], "status": "success"}
    event.tool_use = {"toolUseId": "tu"}
    hook.on_after_tool_call(event)  # must not raise


@pytest.mark.asyncio
async def test_size_bound_ignores_non_text_non_json_blocks(monkeypatch):
    """Image/document blocks are out of scope — they pass through
    untouched and emit no counter."""
    counter_calls = []

    async def fake_counter():
        counter_calls.append(True)

    monkeypatch.setattr(
        "channel.agents.tool_hooks.record_mcp_tool_result_truncated",
        fake_counter,
    )

    hook = ToolResultSizeBoundHook(max_bytes=1)
    block = {"image": {"format": "png", "source": {"bytes": b"...."}}}
    result = {"content": [block], "status": "success"}

    hook.on_after_tool_call(_make_after_event(result))
    await _drain_pending_tasks()

    assert block == {"image": {"format": "png", "source": {"bytes": b"...."}}}
    assert counter_calls == []


def test_size_bound_register_hooks_subscribes_to_after_tool_call():
    """``register_hooks`` wires the callback onto ``AfterToolCallEvent``
    via the registry — matches the HookProvider pattern."""
    from strands.hooks.events import AfterToolCallEvent

    hook = ToolResultSizeBoundHook(max_bytes=10)
    registry = MagicMock()
    hook.register_hooks(registry)
    registry.add_callback.assert_called_once_with(AfterToolCallEvent, hook.on_after_tool_call)
