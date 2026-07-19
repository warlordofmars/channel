# Copyright (c) 2026 John Carter. All rights reserved.
"""Tool-use hooks for the Channel chat agent (epic #128 / #181).

Four hook handlers — see the design doc at
``docs/superpowers/specs/2026-06-06-tool-use-chassis-design.md`` for the
phenomenology preamble and the rationale behind each.

* ``ModelVisibilityAddendumHook`` — ``BeforeModelCallEvent``. Renders
  the per-chain "tool calls used: N of M" line into a system-prompt
  addendum so the model can economize. Per policy P1 the
  ``RemainingBudget`` line is OMITTED under the stub — it is added by
  the #131 swap-in PR for the first time.

* ``ToolCallGuardHook`` — ``BeforeToolCallEvent``. Enforces the
  chain-length cap, the soft wall-clock budget, and the cancel-signal
  flag (mid-chain interrupt; PR-2 will set the signal from
  ``chats.py`` on SSE disconnect — Task 13 of the chassis plan).

* ``ToolCallTelemetryHook`` — ``AfterToolCallEvent``. Increments the
  per-chain counter, fires the ``[meta] used <tool> ...`` synthetic
  ASSISTANT message via ``AgentCoreMemoryHook``'s ``CreateEvent`` path,
  and emits ``ToolCallSuccesses`` / ``ToolCallFailures`` EMF counters.

* ``ToolResultSizeBoundHook`` — ``AfterToolCallEvent`` (#390). Bounds
  each oversized ``text`` / ``json`` content block on the tool result to
  a UTF-8 byte budget BEFORE it re-enters the Converse loop as input
  tokens, appending a ``[truncated N bytes]`` marker so the model can
  reason about the clipped span. Complements the persist-time strip in
  ``memory.py`` (which drops tool payloads from AgentCore Memory
  entirely): the two are independent — the persist-time strip governs
  what lands in Memory, this bound governs what returns to the model
  in-turn.

The cancel-signal registry is a module-level dict keyed by ``chat_id``,
mapping to a ``time.monotonic()`` timestamp. ``chats.py``'s SSE
generator calls ``set_cancel_signal(chat_id)`` from its disconnect
exception handler (``except (asyncio.CancelledError, GeneratorExit)``)
so in-flight tool calls can be cancelled. The signal is intentionally
NOT cleared in a ``finally:`` block — it must persist past the dying
generator to be observable by the next ``BeforeToolCallEvent``.

Three clearing mechanisms keep the registry bounded (defense in depth):

1. **Entry-time clear** — the next turn's ``_stream_bedrock_reply``
   for this chat calls ``clear_cancel_signal`` at the top, wiping any
   stale state from the previous disconnect.
2. **Guard one-shot consume** — ``ToolCallGuardHook`` clears the
   signal immediately after observing it and setting
   ``event.cancel_tool = "cancelled"``.
3. **TTL prune** — entries older than ``_CANCEL_SIGNAL_TTL_SEC`` are
   pruned opportunistically on every public op, bounding the registry
   against the disconnect-without-followup leak in a warm Lambda (a
   chat that disconnects mid-chain and never receives another turn
   would otherwise leave its signal in the registry forever).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from strands.hooks.events import (
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
)

from channel.metrics import (
    record_mcp_tool_result_truncated,
    record_tool_call_outcome,
)

logger = logging.getLogger(__name__)


@dataclass
class ChainState:
    """Per-chain mutable state. Lives on the Strands ``Agent`` instance
    as ``agent.chain_state`` (parallel to ``agent.chat_id``)."""

    tool_calls_used: int = 0
    tool_calls_max: int = 8
    wall_clock_budget_sec: int = 120
    started_at: float = field(default_factory=time.monotonic)

    def increment(self) -> None:
        # Clamp at ``tool_calls_max`` so the counter can't grow past the
        # cap. ``ToolCallTelemetryHook`` increments on every
        # ``AfterToolCallEvent`` — including calls cancelled by the
        # guard via ``chain_cap`` — so an unclamped increment would
        # render misleading addenda like "tool calls used: 9 of 8" and
        # could grow unbounded if the model kept attempting tool calls
        # after the cap. ``is_chain_cap_exhausted`` still works (the
        # ``>= tool_calls_max`` predicate fires the moment the cap is
        # reached, regardless of further attempts).
        self.tool_calls_used = min(self.tool_calls_used + 1, self.tool_calls_max)

    def is_chain_cap_exhausted(self) -> bool:
        return self.tool_calls_used >= self.tool_calls_max

    def is_wall_clock_exhausted(self) -> bool:
        return (time.monotonic() - self.started_at) >= self.wall_clock_budget_sec


# Module-level cancel-signal registry. Keyed by chat_id (UUID), value is
# a ``time.monotonic()`` timestamp captured at signal-set time.
# chats.py's SSE generator sets the signal from its disconnect
# exception handler (``except (asyncio.CancelledError, GeneratorExit)``)
# and intentionally does NOT clear it in ``finally:`` — the signal must
# persist past the dying generator to be observable by the next
# ``BeforeToolCallEvent``, which reads it and assigns
# ``event.cancel_tool = "cancelled"``. Cold start clears all signals
# (no persistence needed — a request that hasn't reached its first tool
# call by the time the Lambda restarts is already dead).
#
# Three clearing mechanisms keep the dict bounded (defense in depth):
# 1. **Entry-time clear** — the next turn's ``_stream_bedrock_reply``
#    for this chat calls ``clear_cancel_signal`` at the top.
# 2. **Guard one-shot consume** — ``ToolCallGuardHook`` clears the
#    signal immediately after observing it.
# 3. **TTL prune** — opportunistic prune on every public op bounds the
#    dict against the disconnect-without-followup leak in a warm
#    Lambda (a client that disconnects after the chain ends, and never
#    sends another message on that chat, would otherwise leave the
#    entry in the registry forever). One O(n) walk per call —
#    amortized O(1) because n stays small. TTL is much longer than any
#    chain wall-clock budget so the signal stays observable for the
#    entire relevant window.
_CANCEL_SIGNAL_TTL_SEC = 300  # 5 min — longer than any chain wall-clock budget
_CANCEL_SIGNALS: dict[str, float] = {}


def _prune_expired_cancel_signals() -> None:
    """Drop any signal entries older than ``_CANCEL_SIGNAL_TTL_SEC``.

    Bounds the registry against the disconnect-without-followup leak in a
    warm Lambda — a chat that disconnects mid-chain and never receives
    another turn would otherwise leave its signal in the registry
    forever. Run opportunistically from each public op."""
    now = time.monotonic()
    expired = [cid for cid, ts in _CANCEL_SIGNALS.items() if now - ts >= _CANCEL_SIGNAL_TTL_SEC]
    for cid in expired:
        del _CANCEL_SIGNALS[cid]


def set_cancel_signal(chat_id: str) -> None:
    _prune_expired_cancel_signals()
    _CANCEL_SIGNALS[chat_id] = time.monotonic()


def clear_cancel_signal(chat_id: str) -> None:
    _prune_expired_cancel_signals()
    _CANCEL_SIGNALS.pop(chat_id, None)


def is_cancel_requested(chat_id: str) -> bool:
    _prune_expired_cancel_signals()
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
            # One-shot consume: clear the signal immediately so it can't
            # survive into a subsequent turn on the same chat in a warm
            # Lambda. PR-2's chats.py ``finally:`` block is the primary
            # clearer; this is defense in depth in case that path fails.
            clear_cancel_signal(chat_id)
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
       (``recall.py``) reads all prior events back into future system
       prompts; it does not currently special-case the ``[meta]``
       prefix, so META events surface as normal conversational text.
       The ``used`` / ``tried`` verb split keeps the failure-vs-success
       signal legible in that raw text — and leaves room for a future
       recall-hook enhancement to filter or condense META events
       separately.

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
        # mirrors AgentCoreMemoryHook._on_after_invocation. The wrapper
        # ``_record_outcome_safe`` swallows + logs any emit failure so the
        # task never raises into asyncio's "Task exception was never
        # retrieved" warning channel — EMF telemetry must be best-effort.
        task = asyncio.create_task(self._record_outcome_safe(success=success))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

        if self._memory_writer is not None:
            tool_use = getattr(event, "tool_use", None) or {}
            tool_name = tool_use.get("name") if isinstance(tool_use, dict) else None
            tool_name = tool_name or "unknown"
            verb = "used" if success else "tried"
            self._memory_writer(f"{verb} {tool_name}")

    async def _record_outcome_safe(self, success: bool) -> None:
        """Wrap ``record_tool_call_outcome`` so the create_task'd coroutine
        never raises. EMF emission can fail on transient network /
        permissions issues; surfacing that as an unhandled task exception
        produces noisy "Task exception was never retrieved" warnings. The
        counter is best-effort telemetry — log + swallow."""
        try:
            await record_tool_call_outcome(success=success)
        except Exception:
            logger.warning(
                "tool_call.emf_dispatch_failed success=%s",
                success,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# ToolResultSizeBoundHook — AfterToolCallEvent (#390)
#
# Bounds oversized tool_result content BEFORE it returns to Bedrock
# in-loop. After a tool fires, Strands appends the (post-hook)
# ``AfterToolCallEvent.result`` to the conversation history as a
# ``toolResult`` block, which becomes INPUT tokens on every subsequent
# Converse call in the tool-calling chain. A single fat MCP response
# (GitHub API JSON, search results, file contents) therefore inflates
# the input-token count of the whole chain. This hook clips each
# oversized ``text`` / stringified-``json`` block to a UTF-8 byte budget
# and appends a ``\n[truncated N bytes]`` marker so the model can reason
# about the clipped span (and, if needed, re-fetch a narrower slice)
# rather than silently losing the tail.
#
# Independent of the persist-time strip (``memory.py``'s
# ``_payload_from_messages`` drops tool payloads from AgentCore Memory
# entirely, per ADR-0009). Both still apply: the strip governs what
# lands in Memory; this bound governs what returns to the model in-turn.
# ---------------------------------------------------------------------------

# Appended after the retained content when a block is clipped. ``N`` is
# the number of UTF-8 bytes removed from the ORIGINAL block content (the
# marker's own bytes sit on top of the budget — the marker is bounded
# metadata, not part of the clipped payload).
_TRUNCATION_MARKER_TEMPLATE = "\n[truncated {removed} bytes]"


def _truncation_marker(removed_bytes: int) -> str:
    return _TRUNCATION_MARKER_TEMPLATE.format(removed=removed_bytes)


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, int]:
    """Clip ``text`` to at most ``max_bytes`` UTF-8 bytes on a character
    boundary. Returns ``(kept_text, removed_bytes)``.

    Under budget (``<= max_bytes``) the text is returned byte-for-byte
    with ``removed_bytes == 0`` — callers use that as the "leave the
    block untouched" signal. Over budget, the byte string is sliced at
    ``max_bytes`` and decoded with ``errors="ignore"``: because the
    source is valid UTF-8, a mid-character slice can only leave a partial
    multibyte sequence at the TAIL, so ``ignore`` drops exactly that
    dangling char and never splits a multibyte character. ``removed`` is
    computed from the ACTUAL retained byte length (which may be a few
    bytes under ``max_bytes`` after backing off the boundary), so the
    marker's count always reconciles ``original == kept + removed``.
    """
    encoded = text.encode("utf-8")
    original_len = len(encoded)
    if original_len <= max_bytes:
        return text, 0
    kept_text = encoded[:max_bytes].decode("utf-8", errors="ignore")
    removed = original_len - len(kept_text.encode("utf-8"))
    return kept_text, removed


class ToolResultSizeBoundHook:
    """Strands ``HookProvider`` that bounds oversized tool_result content
    blocks on ``AfterToolCallEvent`` before they re-enter the Converse
    loop (#390).

    Walks ``event.result["content"]`` and clips each block whose UTF-8
    size exceeds ``max_bytes``:

    * ``text`` block — the string is byte-safe truncated in place and the
      ``[truncated N bytes]`` marker appended.
    * ``json`` block — the value is serialized (``json.dumps``,
      ``ensure_ascii=False`` so multibyte chars count as their real UTF-8
      width), and if the serialization exceeds the budget the block is
      REPLACED by a ``text`` block carrying the truncated serialization +
      marker. A partially-truncated JSON string is no longer valid
      structured data, so surfacing it as text (rather than a broken
      ``json`` block) is the honest degradation — and the marker reads
      naturally in the text register the model already consumes.

    Non-text blocks (``image`` / ``document``) are out of scope — this is
    about text/JSON API responses, not binary payloads (cf. the #390
    scope note).

    Each clipped block emits a structured ``mcp.tool_result_truncated``
    log line (carrying ``tool_use_id`` + byte counts) and increments the
    ``MCPToolResultTruncated`` EMF counter. Like ``ToolCallTelemetryHook``
    the counter is dispatched fire-and-forget via ``asyncio.create_task``
    with a strong-ref discard set (Sonar python:S7502) and a
    swallow-and-log wrapper — bounding the result must never break the
    turn on a transient EMF failure.

    Conforms to the ``HookProvider`` protocol structurally — no explicit
    base class, matching the pattern across this module + ``memory.py`` +
    ``recall.py``.
    """

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        # Strong refs to in-flight EMF emissions — without this, Python's
        # GC can reclaim the task before the counter coroutine completes.
        # Mirrors ToolCallTelemetryHook (Sonar python:S7502).
        self._pending_tasks: set[asyncio.Task[None]] = set()

    def register_hooks(self, registry: Any, **_: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.on_after_tool_call)

    def on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        result = getattr(event, "result", None)
        if not isinstance(result, dict):
            return  # e.g. an Exception result — nothing to bound
        content = result.get("content")
        if not isinstance(content, list):
            return
        tool_use = getattr(event, "tool_use", None)
        tool_use_id = (
            (tool_use.get("toolUseId") if isinstance(tool_use, dict) else None)
            or result.get("toolUseId")
            or "unknown"
        )
        for block in content:
            self._bound_block(block, tool_use_id)

    def _bound_block(self, block: Any, tool_use_id: str) -> None:
        if not isinstance(block, dict):
            return
        text = block.get("text")
        if isinstance(text, str):
            kept, removed = _truncate_utf8(text, self._max_bytes)
            if removed == 0:
                return  # under budget — byte-for-byte untouched
            original_bytes = len(text.encode("utf-8"))
            block["text"] = kept + _truncation_marker(removed)
            self._record_truncation(tool_use_id, original_bytes, len(kept.encode("utf-8")))
            return
        if "json" in block:
            serialized = json.dumps(block["json"], ensure_ascii=False, default=str)
            kept, removed = _truncate_utf8(serialized, self._max_bytes)
            if removed == 0:
                return  # under budget — leave the json block untouched
            original_bytes = len(serialized.encode("utf-8"))
            del block["json"]
            block["text"] = kept + _truncation_marker(removed)
            self._record_truncation(tool_use_id, original_bytes, len(kept.encode("utf-8")))

    def _record_truncation(self, tool_use_id: str, original_bytes: int, kept_bytes: int) -> None:
        logger.info(
            "mcp.tool_result_truncated tool_use_id=%s original_bytes=%d kept_bytes=%d",
            tool_use_id,
            original_bytes,
            kept_bytes,
        )
        task = asyncio.create_task(self._emit_truncated_counter_safe())
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _emit_truncated_counter_safe(self) -> None:
        """Wrap ``record_mcp_tool_result_truncated`` so the create_task'd
        coroutine never raises. EMF emission can fail on transient network
        / permissions issues; the counter is best-effort telemetry —
        log + swallow rather than surface a "Task exception was never
        retrieved" warning."""
        try:
            await record_mcp_tool_result_truncated()
        except Exception:
            logger.warning(
                "mcp.tool_result_truncated_emf_dispatch_failed",
                exc_info=True,
            )
