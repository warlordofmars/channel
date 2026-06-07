# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the Strands event → SSE translator."""

from __future__ import annotations

import json

from channel.agents.strands_sse import (
    sse_delta,
    sse_done,
    sse_tool_error,
    sse_tool_finished,
    sse_tool_progress,
    sse_tool_started,
    sse_user_persisted,
    translate_event,
)


def test_translate_content_block_delta_returns_delta():
    event = {"event": {"contentBlockDelta": {"delta": {"text": "Hello"}}}}
    kind, payload = translate_event(event)
    assert kind == "delta"
    assert payload == "Hello"


def test_translate_data_shortcut_is_skipped():
    """The ``data`` shorthand is a Strands duplicate of contentBlockDelta;
    we skip it so the SPA doesn't see deltas twice."""

    event = {"data": "world"}
    kind, payload = translate_event(event)
    assert kind == "skip"
    assert payload is None


def test_translate_message_stop_returns_stop():
    event = {"event": {"messageStop": {"stopReason": "end_turn"}}}
    kind, payload = translate_event(event)
    assert kind == "stop"
    assert payload == {"stop_reason": "end_turn"}


def test_translate_metadata_returns_usage():
    event = {
        "event": {
            "metadata": {
                "usage": {"inputTokens": 12, "outputTokens": 8},
                "metrics": {"latencyMs": 42},
            }
        }
    }
    kind, payload = translate_event(event)
    assert kind == "usage"
    assert payload == {"input_tokens": 12, "output_tokens": 8}


def test_translate_unknown_event_returns_skip():
    event = {"event": {"contentBlockStart": {}}}
    kind, payload = translate_event(event)
    assert kind == "skip"
    assert payload is None


def test_translate_content_block_delta_with_empty_text_returns_skip():
    # Falls through the `if text:` guard so an empty / missing text
    # chunk doesn't masquerade as a delta event.
    event = {"event": {"contentBlockDelta": {"delta": {"text": ""}}}}
    kind, payload = translate_event(event)
    assert kind == "skip"
    assert payload is None


def test_sse_delta_emits_bytes_with_data_prefix():
    raw = sse_delta("Hello")
    assert raw.startswith(b"data: ")
    assert raw.endswith(b"\n\n")
    payload = json.loads(raw[6:-2])
    assert payload == {"type": "delta", "text": "Hello"}


def test_sse_done_includes_usage_and_metadata():
    raw = sse_done(
        msg_id="m-1",
        seq=1,
        model="anthropic.claude-sonnet-4-6",
        input_tokens=12,
        output_tokens=8,
        stop_reason="end_turn",
    )
    payload = json.loads(raw[6:-2])
    assert payload == {
        "type": "done",
        "msg_id": "m-1",
        "seq": 1,
        "model": "anthropic.claude-sonnet-4-6",
        "input_tokens": 12,
        "output_tokens": 8,
        "stop_reason": "end_turn",
    }


def test_sse_user_persisted_includes_msg_id_and_seq():
    raw = sse_user_persisted(msg_id="u-1", seq=0)
    payload = json.loads(raw[6:-2])
    assert payload == {"type": "user_persisted", "msg_id": "u-1", "seq": 0}


def test_sse_title_suggested_emits_chat_id_and_title():
    from channel.agents.strands_sse import sse_title_suggested

    raw = sse_title_suggested(chat_id="c-1", title="Pytest fixture debug")
    payload = json.loads(raw[6:-2])
    assert payload == {
        "type": "title_suggested",
        "chat_id": "c-1",
        "title": "Pytest fixture debug",
    }


def test_sse_follow_ups_suggested_emits_chat_id_message_id_and_suggestions():
    from channel.agents.strands_sse import sse_follow_ups_suggested

    raw = sse_follow_ups_suggested(
        chat_id="c-1",
        message_id="m-1",
        suggestions=["First", "Second", "Third"],
    )
    payload = json.loads(raw[6:-2])
    assert payload == {
        "type": "follow_ups_suggested",
        "chat_id": "c-1",
        "message_id": "m-1",
        "suggestions": ["First", "Second", "Third"],
    }


def test_sse_attachment_error_emits_id_filename_and_reason():
    """#176 — fetch-time S3 failure surfaces via attachment_error SSE so
    the SPA can prompt the user to re-upload."""

    from channel.agents.strands_sse import sse_attachment_error

    raw = sse_attachment_error(
        attachment_id="att-1",
        filename="spec.pdf",
        reason="S3 object not found",
    )
    payload = json.loads(raw[6:-2])
    assert payload == {
        "type": "attachment_error",
        "attachment_id": "att-1",
        "filename": "spec.pdf",
        "reason": "S3 object not found",
    }


def test_sse_tool_started_shape():
    """Epic #128 / #181 — ``tool_started`` marks the start of one tool
    call inside an assistant turn. The SPA appends a collapsible step
    row to the active message."""

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
    raw = sse_tool_started(tool_name="x", tool_use_id="t", args_preview="A" * 500).decode()
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert len(payload["args_preview"]) == 200
    assert payload["args_preview"] == "A" * 200


def test_sse_tool_progress_shape():
    raw = sse_tool_progress(tool_use_id="t1", status_text="searching...").decode()
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert payload == {
        "type": "tool_progress",
        "tool_use_id": "t1",
        "status_text": "searching...",
    }


def test_sse_tool_finished_shape():
    raw = sse_tool_finished(tool_use_id="t1", summary="3 results").decode()
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert payload == {
        "type": "tool_finished",
        "tool_use_id": "t1",
        "summary": "3 results",
    }


def test_sse_tool_finished_truncates_summary_at_500():
    raw = sse_tool_finished(tool_use_id="t1", summary="X" * 1000).decode()
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert len(payload["summary"]) == 500
    assert payload["summary"] == "X" * 500


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


# ---------------------------------------------------------------------------
# translate_event — Strands tool events
# ---------------------------------------------------------------------------
#
# Strands' ``stream_async`` yields ``TypedEvent`` dict-subclasses (see
# ``strands/types/_events.py`` in 1.41.0). Tool-related events appear at
# the TOP LEVEL — not nested inside the ``event`` envelope that wraps
# model stream chunks. The empirically-observed shapes are::
#
#   ToolUseStreamEvent  {"type": "tool_use_stream",
#                        "delta": {"toolUse": {"input": "<chunk>"}},
#                        "current_tool_use": {"toolUseId", "name", "input"}}
#   ToolStreamEvent     {"type": "tool_stream",
#                        "tool_stream_event": {"tool_use": {...},
#                                              "data": <yield>}}
#   ToolResultEvent     {"type": "tool_result",
#                        "tool_result": {"toolUseId", "content": [...],
#                                        "status": "success"|"error"}}
#   ToolCancelEvent     {"tool_cancel_event": {"tool_use": {...},
#                                              "message": "<why>"}}
#   ToolInterruptEvent  {"tool_interrupt_event": {"tool_use": {...},
#                                                 "interrupts": [...]}}


def test_translate_tool_use_stream_returns_tool_started():
    """The first ``tool_use_stream`` event for a given toolUseId carries
    enough info (name + accumulating input) to surface ``tool_started``.
    The dispatcher in chats.py dedupes per toolUseId."""

    event = {
        "type": "tool_use_stream",
        "delta": {"toolUse": {"input": ""}},
        "current_tool_use": {
            "toolUseId": "tu-1",
            "name": "current_time",
            "input": {},
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_started"
    assert payload["tool_name"] == "current_time"
    assert payload["tool_use_id"] == "tu-1"
    # args_preview is a JSON-serialised view of current input (may be
    # partial mid-stream — dispatcher handles dedup, so the last preview
    # wins in practice).
    assert payload["args_preview"] == "{}"


def test_translate_tool_use_stream_serialises_input_dict():
    event = {
        "type": "tool_use_stream",
        "delta": {"toolUse": {"input": '"UTC"'}},
        "current_tool_use": {
            "toolUseId": "tu-2",
            "name": "current_time",
            "input": {"timezone": "UTC"},
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_started"
    assert payload["args_preview"] == '{"timezone": "UTC"}'


def test_translate_tool_use_stream_missing_current_tool_use_skips():
    """Defensive: if Strands emits a malformed event with no
    current_tool_use, fall through to ``skip`` rather than crash."""

    event = {"type": "tool_use_stream", "delta": {"toolUse": {"input": "x"}}}
    kind, payload = translate_event(event)
    assert kind == "skip"
    assert payload is None


def test_translate_tool_stream_returns_tool_progress():
    """``ToolStreamEvent`` carries arbitrary yields from inside a tool's
    execution. Map non-empty string data to ``tool_progress``."""

    event = {
        "type": "tool_stream",
        "tool_stream_event": {
            "tool_use": {"toolUseId": "tu-3", "name": "search", "input": {}},
            "data": "searching...",
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_progress"
    assert payload == {"tool_use_id": "tu-3", "status_text": "searching..."}


def test_translate_tool_stream_non_string_data_skips():
    """Tools can yield arbitrary objects (dicts, model events, etc.).
    Only surface string yields as progress text to keep the SSE shape
    bounded and predictable."""

    event = {
        "type": "tool_stream",
        "tool_stream_event": {
            "tool_use": {"toolUseId": "tu-3"},
            "data": {"partial": 1},
        },
    }
    kind, payload = translate_event(event)
    assert kind == "skip"
    assert payload is None


def test_translate_tool_result_returns_tool_finished():
    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-4",
            "status": "success",
            "content": [{"text": "2026-06-07T12:00:00Z"}],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    assert payload["tool_use_id"] == "tu-4"
    assert "2026-06-07" in payload["summary"]


def test_translate_tool_result_concatenates_text_blocks():
    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-5",
            "status": "success",
            "content": [
                {"text": "first "},
                {"text": "second"},
                {"json": {"ignored": True}},
            ],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    assert payload["summary"] == "first second"


def test_translate_tool_result_error_status_returns_tool_error():
    """A ``ToolResultEvent`` with ``status=error`` is a tool-side failure;
    surface it as ``tool_error`` so the SPA renders the failure UI."""

    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-6",
            "status": "error",
            "content": [{"text": "Boom"}],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_error"
    assert payload == {
        "tool_use_id": "tu-6",
        "error_type": "tool_failed",
        "partial_result_count": 0,
    }


def test_translate_tool_cancel_event_returns_tool_error():
    event = {
        "tool_cancel_event": {
            "tool_use": {"toolUseId": "tu-7", "name": "current_time", "input": {}},
            "message": "chain_cap",
        }
    }
    kind, payload = translate_event(event)
    assert kind == "tool_error"
    assert payload == {
        "tool_use_id": "tu-7",
        "error_type": "chain_cap",
        "partial_result_count": 0,
    }


def test_translate_tool_cancel_event_missing_message_defaults_cancelled():
    event = {
        "tool_cancel_event": {
            "tool_use": {"toolUseId": "tu-8"},
        }
    }
    kind, payload = translate_event(event)
    assert kind == "tool_error"
    assert payload["error_type"] == "cancelled"


def test_translate_tool_interrupt_event_returns_tool_error():
    event = {
        "tool_interrupt_event": {
            "tool_use": {"toolUseId": "tu-9"},
            "interrupts": [object()],
        }
    }
    kind, payload = translate_event(event)
    assert kind == "tool_error"
    assert payload == {
        "tool_use_id": "tu-9",
        "error_type": "interrupted",
        "partial_result_count": 0,
    }
