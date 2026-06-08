# Copyright (c) 2026 John Carter. All rights reserved.
"""Strands stream event → SSE byte translator.

The chat router consumes ``strands.Agent.stream_async()`` events and
must emit our existing SSE shapes (``user_persisted`` / ``delta`` /
``done``) that the SPA's ``useChatStream`` hook already understands.

This module is pure: no I/O, no Strands invocation.  The router calls
``translate_event`` per Strands event then ``sse_delta`` / ``sse_done``
to render the byte form.
"""

from __future__ import annotations

import json
from typing import Any


def translate_event(event: dict[str, Any]) -> tuple[str, Any]:
    """Classify one Strands stream event.

    Returns a ``(kind, payload)`` tuple where ``kind`` is one of:

    * ``"delta"`` — incremental text chunk; ``payload`` is a ``str``.
    * ``"stop"`` — turn boundary; ``payload`` is ``{"stop_reason": str}``.
    * ``"usage"`` — token-count metadata; ``payload`` is
      ``{"input_tokens": int, "output_tokens": int}``.
    * ``"tool_started"`` — model has begun emitting a tool call; payload
      is ``{"tool_name": str, "tool_use_id": str, "args_preview": str}``.
      Sourced from Strands' ``ToolUseStreamEvent``. The dispatcher in
      ``chats.py`` dedupes per ``tool_use_id`` because this event fires
      once per input-token, not once per tool call.
    * ``"tool_progress"`` — string yield from inside an executing tool;
      payload is ``{"tool_use_id": str, "status_text": str}``. Sourced
      from Strands' ``ToolStreamEvent`` when its ``data`` is a string.
    * ``"tool_finished"`` — successful tool completion; payload is
      ``{"tool_use_id": str, "summary": str}``. Sourced from Strands'
      ``ToolResultEvent`` with ``status="success"``. ``summary`` is a
      bounded marker — today the chassis emits a literal
      ``"completed"`` for every success and NEVER leaks the raw tool
      result text over SSE. Tool-specific structured summaries
      ("Found 3 results" for #182 Exa, "Ran 5 lines" for #183
      code-exec) will arrive via an explicit ``ToolResult`` summary
      field in those PRs; the chassis does not infer summaries from
      raw content. The actual tool output reaches the user via the
      model's text reply (Strands' event_loop feeds the result back
      into the model, which writes a reply that incorporates the data).
    * ``"tool_error"`` — tool failure / cancellation / interrupt; payload
      is ``{"tool_use_id": str, "error_type": str,
      "partial_result_count": int}``. Sourced from ``ToolResultEvent``
      with ``status="error"``, ``ToolCancelEvent``, or
      ``ToolInterruptEvent``.
    * ``"skip"`` — uninteresting event (content block start, telemetry,
      etc.); ``payload`` is ``None``.
    """

    # Tool events are emitted at the TOP LEVEL of the TypedEvent dict
    # (see ``strands/types/_events.py``) — not nested inside the
    # ``event`` envelope. Dispatch on the ``type`` discriminator first,
    # then the cancel/interrupt keys.
    event_type = event.get("type")

    if event_type == "tool_use_stream":
        current = event.get("current_tool_use") or {}
        tool_use_id = current.get("toolUseId")
        if tool_use_id:
            return (
                "tool_started",
                {
                    "tool_name": current.get("name", ""),
                    "tool_use_id": tool_use_id,
                    "args_preview": json.dumps(current.get("input", {})),
                },
            )

    if event_type == "tool_stream":
        inner_stream = event.get("tool_stream_event") or {}
        data = inner_stream.get("data")
        tool_use = inner_stream.get("tool_use") or {}
        tool_use_id = tool_use.get("toolUseId", "")
        # Skip uncorrelatable progress: the SPA keys step rows on
        # ``tool_use_id``, so an empty id produces a payload it can't
        # attach to any step.
        if isinstance(data, str) and data and tool_use_id:
            return (
                "tool_progress",
                {
                    "tool_use_id": tool_use_id,
                    "status_text": data,
                },
            )

    if event_type == "tool_result":
        result = event.get("tool_result") or {}
        tool_use_id = result.get("toolUseId", "")
        # Same uncorrelatable-event guard as ``tool_progress`` / cancel /
        # interrupt: without a ``tool_use_id`` the SPA can't attach the
        # ``tool_finished`` / ``tool_error`` payload to any step row.
        if not tool_use_id:
            return ("skip", None)
        status = result.get("status", "success")
        if status == "error":
            # Error path concatenates ``text`` blocks because Strands
            # wraps the cancel-reason string from
            # ``BeforeToolCallEvent.cancel_tool`` into ``content`` as
            # ``{"content": [{"text": cancel_message}]}`` (see
            # ``strands/tools/executors/_executor.py``). Surfacing
            # that string as ``error_type`` is how ``ToolCallGuardHook``'s
            # reason codes (``chain_cap`` / ``cancelled`` /
            # ``wall_clock``) reach the SPA so it can render the right
            # affordance. This IS bounded telemetry — short, fixed
            # reason strings — and ``sse_tool_error`` caps it at
            # ``_ARGS_PREVIEW_MAX`` as defense in depth.
            reason_text = "".join(
                block.get("text", "")
                for block in result.get("content", [])
                if isinstance(block, dict) and "text" in block
            )
            error_type = reason_text.strip() or "tool_failed"
            return (
                "tool_error",
                {
                    "tool_use_id": tool_use_id,
                    "error_type": error_type[:_ARGS_PREVIEW_MAX],
                    "partial_result_count": 0,
                },
            )
        # Success: emit a generic completion marker. The raw tool
        # result text DOES NOT leak over SSE by default — that is the
        # contract documented on ``sse_tool_finished``. The ONE
        # exception is the code-exec sandbox (#183), whose entire
        # purpose is to surface stdout/stderr/images to the user via
        # a structured renderer in the SPA. We detect the code-exec
        # shape by structure (parsed JSON with ``exit_code`` +
        # ``stdout`` keys) rather than plumbing tool_name through —
        # tool_name lives on the preceding ``ToolUseStreamEvent``
        # not on the result, and translate_event is stateless.
        success_payload: dict[str, Any] = {
            "tool_use_id": tool_use_id,
            "summary": "completed",
        }
        joined_text = "".join(
            block.get("text", "")
            for block in result.get("content", [])
            if isinstance(block, dict) and "text" in block
        )
        if joined_text:
            try:
                parsed = json.loads(joined_text)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict) and "exit_code" in parsed and "stdout" in parsed:
                success_payload["kind"] = "code-output"
                success_payload["payload"] = parsed
        return ("tool_finished", success_payload)

    if "tool_cancel_event" in event:
        cancel = event["tool_cancel_event"]
        tool_use = cancel.get("tool_use") or {}
        tool_use_id = tool_use.get("toolUseId", "")
        # Same uncorrelatable-event guard as ``tool_progress``: a cancel
        # SSE payload without a ``tool_use_id`` can't be attached to any
        # step row in the SPA.
        if not tool_use_id:
            return ("skip", None)
        return (
            "tool_error",
            {
                "tool_use_id": tool_use_id,
                "error_type": cancel.get("message") or "cancelled",
                "partial_result_count": 0,
            },
        )

    if "tool_interrupt_event" in event:
        interrupt = event["tool_interrupt_event"]
        tool_use = interrupt.get("tool_use") or {}
        tool_use_id = tool_use.get("toolUseId", "")
        if not tool_use_id:
            return ("skip", None)
        return (
            "tool_error",
            {
                "tool_use_id": tool_use_id,
                "error_type": "interrupted",
                "partial_result_count": 0,
            },
        )

    # Strands emits each text chunk twice: once as a top-level ``data``
    # shorthand and once inside the canonical ``event.contentBlockDelta``
    # envelope.  We process the canonical form only so the SPA doesn't
    # see duplicated deltas.
    inner = event.get("event") or {}

    if "contentBlockDelta" in inner:
        text = inner["contentBlockDelta"].get("delta", {}).get("text")
        if text:
            return ("delta", text)

    if "messageStop" in inner:
        return (
            "stop",
            {"stop_reason": inner["messageStop"].get("stopReason", "end_turn")},
        )

    if "metadata" in inner:
        usage = inner["metadata"].get("usage") or {}
        return (
            "usage",
            {
                "input_tokens": int(usage.get("inputTokens", 0)),
                "output_tokens": int(usage.get("outputTokens", 0)),
            },
        )

    return ("skip", None)


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def sse_user_persisted(*, msg_id: str, seq: int) -> bytes:
    return _sse({"type": "user_persisted", "msg_id": msg_id, "seq": seq})


def sse_delta(text: str) -> bytes:
    return _sse({"type": "delta", "text": text})


def sse_title_suggested(*, chat_id: str, title: str) -> bytes:
    """Emit a ``title_suggested`` SSE event.

    Phase 7d auto-title trigger. The SPA's useChatStream parses the
    payload and forwards the title to ChatsContext.renameChat for the
    sidebar.
    """
    return _sse({"type": "title_suggested", "chat_id": chat_id, "title": title})


def sse_attachment_error(*, attachment_id: str, filename: str, reason: str) -> bytes:
    """Emit an ``attachment_error`` SSE event (#176).

    Surfaces fetch-time S3 failures (object not found / inaccessible)
    to the SPA so it can prompt the user to re-upload. Distinct from
    tool failures because the user fix differs: re-upload vs. retry.
    """
    return _sse(
        {
            "type": "attachment_error",
            "attachment_id": attachment_id,
            "filename": filename,
            "reason": reason,
        }
    )


def sse_follow_ups_suggested(*, chat_id: str, message_id: str, suggestions: list[str]) -> bytes:
    """Emit a ``follow_ups_suggested`` SSE event.

    Carries 2-3 short prompts the user might want to send next. The
    SPA's ``useChatStream`` attaches them to the matching assistant
    turn so ``Conversation`` can render a chip row below it.
    """
    return _sse(
        {
            "type": "follow_ups_suggested",
            "chat_id": chat_id,
            "message_id": message_id,
            "suggestions": suggestions,
        }
    )


def sse_done(
    *,
    msg_id: str,
    seq: int,
    model: str,
    input_tokens: int,
    output_tokens: int,
    stop_reason: str,
) -> bytes:
    return _sse(
        {
            "type": "done",
            "msg_id": msg_id,
            "seq": seq,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "stop_reason": stop_reason,
        }
    )


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


def sse_tool_finished(
    *,
    tool_use_id: str,
    summary: str,
    kind: str | None = None,
    payload: dict[str, Any] | None = None,
) -> bytes:
    """Emit a ``tool_finished`` SSE event (epic #128 / #181, #183).

    Marks tool completion. ``summary`` is a short, bounded marker — the
    chassis never sends raw tool output over SSE for the default case.
    The chassis emits a literal ``"completed"`` for every success.

    ``kind`` and ``payload`` are #183's extension for structured tool
    results: when ``code_exec`` returns ``{stdout, stderr, exit_code,
    ...}``, ``translate_event`` sets ``kind="code-output"`` and
    ``payload=<that dict>`` so the SPA's ``ToolResultBlock`` can
    render the code-output branch. Other tools (e.g. ``web_search``)
    leave both ``None`` and the SPA falls back to the default summary
    branch.

    The actual tool output reaches the user via the model's text reply
    (the assistant invokes the tool, Strands' event_loop feeds the
    result back into the model, the model writes a reply that
    incorporates the data). The SSE step row is telemetry, not the
    delivery channel for tool data — except for code-exec, where the
    structured payload IS user-visible data."""
    body: dict[str, Any] = {
        "type": "tool_finished",
        "tool_use_id": tool_use_id,
        "summary": summary[:_SUMMARY_MAX],
    }
    if kind is not None:
        body["kind"] = kind
    if payload is not None:
        body["payload"] = payload
    return _sse(body)


def sse_tool_error(*, tool_use_id: str, error_type: str, partial_result_count: int) -> bytes:
    """Emit a ``tool_error`` SSE event (epic #128 / #181).

    ``error_type`` is a free-form short string the SPA can branch on:
    ``"timeout"``, ``"rate_limit"``, ``"upstream_5xx"``, ``"chain_cap"``,
    ``"cancelled"``, ``"wall_clock"``. Capped at ``_ARGS_PREVIEW_MAX``
    (200 chars) — defense in depth alongside ``translate_event``'s
    truncation when extracting from ``tool_result.content``, since the
    emitter is the bytes-on-the-wire boundary. Partial > none: a chain
    that fires 3 of 5 steps and fails on 4 still surfaces
    ``partial_result_count=3`` so the SPA renders "I have 3 of 5
    results; here's what I found" rather than a silent abort.
    """
    return _sse(
        {
            "type": "tool_error",
            "tool_use_id": tool_use_id,
            "error_type": error_type[:_ARGS_PREVIEW_MAX],
            "partial_result_count": partial_result_count,
        }
    )
