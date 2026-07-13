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


def test_sse_tool_error_truncates_error_type_at_200():
    raw = sse_tool_error(tool_use_id="t1", error_type="X" * 500, partial_result_count=0).decode()
    payload = json.loads(raw.removeprefix("data: ").strip())
    assert len(payload["error_type"]) == 200
    assert payload["error_type"] == "X" * 200


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
    # partial mid-stream — dispatcher in chats.py dedupes by adding
    # tool_use_id to ``emitted_tool_starts`` before emitting, so the
    # FIRST preview wins; subsequent tool_use_stream events for the same
    # toolUseId are dropped before they reach the SSE wire).
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


def test_translate_tool_stream_missing_tool_use_id_skips():
    """An uncorrelatable ``tool_progress`` payload (no ``toolUseId``)
    can't be attached to any SPA step row. Drop the event rather than
    emit a malformed payload."""

    event = {
        "type": "tool_stream",
        "tool_stream_event": {
            "tool_use": {},
            "data": "searching...",
        },
    }
    kind, payload = translate_event(event)
    assert kind == "skip"
    assert payload is None


def test_translate_tool_result_returns_tool_finished():
    """Successful tool result yields a ``tool_finished`` event with a
    generic ``"completed"`` summary — the chassis NEVER leaks raw tool
    output over SSE (Copilot review fix). Tool-specific structured
    summaries land in #182 / #183 via an explicit ``ToolResult`` field;
    the chassis does not infer summaries from raw content."""

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
    assert payload["summary"] == "completed"


def test_translate_tool_result_success_does_not_leak_raw_content():
    """Locks the contract: even when the tool result has rich text
    content, ``tool_finished.summary`` is ALWAYS ``"completed"`` and
    the raw text never reaches the SSE payload. This is the load-
    bearing invariant from ``sse_tool_finished``'s docstring."""

    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-5",
            "status": "success",
            "content": [
                {"text": "sensitive-payload-do-not-leak"},
                {"text": "another-secret-block"},
                {"json": {"ignored": True}},
            ],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    assert payload["summary"] == "completed"
    # Defense in depth — explicitly confirm no raw text leaked.
    assert "sensitive-payload-do-not-leak" not in payload["summary"]
    assert "another-secret-block" not in payload["summary"]


def test_translate_tool_result_error_status_returns_tool_error():
    """A ``ToolResultEvent`` with ``status=error`` is a tool-side failure;
    surface it as ``tool_error`` so the SPA renders the failure UI. The
    text content is propagated as ``error_type`` so reason codes set by
    ``ToolCallGuardHook`` via ``event.cancel_tool`` reach the SPA."""

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
        "error_type": "Boom",
        "partial_result_count": 0,
    }


def test_translate_tool_result_error_preserves_reason_string():
    """When ToolCallGuardHook sets event.cancel_tool = 'chain_cap',
    Strands wraps the reason into ToolResult.content as text. The
    translator MUST surface that string as error_type so the SPA can
    render the cap-reached affordance distinctly (vs a generic
    upstream_5xx). Locks the cross-PR contract with PR-1's hook."""

    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-1",
            "status": "error",
            "content": [{"text": "chain_cap"}],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_error"
    assert payload["error_type"] == "chain_cap"
    assert payload["tool_use_id"] == "tu-1"


def test_translate_tool_result_error_falls_back_when_content_empty():
    """If the tool result has status=error but no text content (rare —
    e.g., a tool exception path that didn't yield a reason), surface
    error_type='tool_failed' as a defensive default."""

    event = {
        "type": "tool_result",
        "tool_result": {"toolUseId": "tu-1", "status": "error", "content": []},
    }
    kind, payload = translate_event(event)
    assert kind == "tool_error"
    assert payload["error_type"] == "tool_failed"


def test_translate_tool_result_success_missing_tool_use_id_skips():
    """tool_result success path without toolUseId yields skip — SPA can't
    correlate a step-row update without the id."""

    event = {
        "type": "tool_result",
        "tool_result": {"status": "success", "content": [{"text": "ok"}]},
    }
    assert translate_event(event) == ("skip", None)


def test_translate_tool_result_error_missing_tool_use_id_skips():
    """tool_result error path without toolUseId yields skip — same
    correlation requirement as the success path."""

    event = {
        "type": "tool_result",
        "tool_result": {"status": "error", "content": [{"text": "chain_cap"}]},
    }
    assert translate_event(event) == ("skip", None)


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


def test_translate_tool_cancel_event_missing_tool_use_id_skips():
    """Cancel events without a ``toolUseId`` produce an uncorrelatable
    SSE payload — the SPA can't match it to a step row. Skip rather
    than emit garbage."""

    event = {"tool_cancel_event": {"tool_use": {}, "message": "chain_cap"}}
    kind, payload = translate_event(event)
    assert kind == "skip"
    assert payload is None


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


def test_translate_tool_interrupt_event_missing_tool_use_id_skips():
    """Same uncorrelatable-event guard for interrupts as for cancels."""

    event = {"tool_interrupt_event": {"tool_use": {}, "interrupts": [object()]}}
    kind, payload = translate_event(event)
    assert kind == "skip"
    assert payload is None


def test_translate_event_emits_code_output_kind_for_code_exec_shaped_result():
    """tool_result whose ``content[0].text`` parses as JSON with
    ``exit_code`` + ``stdout`` keys → emit ``kind="code-output"`` +
    ``payload`` on the ``tool_finished`` event. The default literal
    summary ``"completed"`` is still set so legacy SPA paths don't
    crash."""
    from channel.agents.strands_sse import translate_event

    sandbox_payload = {
        "stdout": "42\n",
        "stderr": "",
        "exit_code": 0,
        "duration_ms": 12,
        "truncated": False,
        "timed_out": False,
        "images": [],
    }
    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-abc",
            "status": "success",
            "content": [{"text": json.dumps(sandbox_payload)}],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    assert payload["tool_use_id"] == "tu-abc"
    assert payload["summary"] == "completed"
    assert payload["kind"] == "code-output"
    assert payload["payload"] == sandbox_payload


def test_looks_like_code_exec_payload_short_circuits_non_json_text():
    """Plain-text tool results (no leading ``{``) skip the JSON parse
    entirely — important for any tool that returns prose."""
    from channel.agents.strands_sse import _looks_like_code_exec_payload

    assert _looks_like_code_exec_payload("Found 3 results for ...") is False
    assert _looks_like_code_exec_payload("") is False


def test_looks_like_code_exec_payload_short_circuits_other_json_shapes():
    """Web-search-style JSON (no ``exit_code`` / ``duration_ms`` /
    ``timed_out`` sentinels) skips the JSON parse — that result can be
    hundreds of KB and we don't want to parse it on every successful
    tool call just to discriminate code_exec."""
    from channel.agents.strands_sse import _looks_like_code_exec_payload

    web_search_blob = json.dumps({"results": [{"url": "https://example.com", "text": "..."}]})
    assert _looks_like_code_exec_payload(web_search_blob) is False


def test_looks_like_code_exec_payload_passes_real_code_exec_blob():
    """A real sandbox-handler return blob passes the substring
    pre-check, advancing to the full JSON parse + key-set verification."""
    from channel.agents.strands_sse import _looks_like_code_exec_payload

    blob = json.dumps(
        {
            "stdout": "42\n",
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 12,
            "truncated": False,
            "timed_out": False,
            "images": [],
        }
    )
    assert _looks_like_code_exec_payload(blob) is True


def test_translate_event_handles_malformed_json_passing_precheck():
    """If a tool result LOOKS like code-exec to the cheap pre-check
    (leading ``{`` plus the three sentinel substrings) but the full
    JSON parse fails (truncated stream, mismatched quotes, etc.), we
    quietly fall back to the default summary path rather than
    crashing the chassis."""
    from channel.agents.strands_sse import translate_event

    # Crafted blob: passes the precheck (starts with { and contains
    # all three sentinels) but isn't valid JSON (unterminated string).
    bogus = '{"stdout": "broken, "exit_code": 0, "duration_ms": 1, "timed_out": false'
    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-malformed",
            "status": "success",
            "content": [{"text": bogus}],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    assert "kind" not in payload
    assert "payload" not in payload


def test_translate_event_does_not_misclassify_partial_code_exec_shape():
    """A hypothetical future tool that returns JSON with just ``stdout``
    + ``exit_code`` (the LOOSE check from the original design) must NOT
    be classified as code-output. The tightened check requires the
    FULL sandbox handler return contract before flipping the SSE
    kind — defense against accidentally leaking another tool's raw
    payload over the structured-payload channel."""
    from channel.agents.strands_sse import translate_event

    # Partial-shape blob: only 2 of the 7 sandbox-payload keys.
    bogus_payload = {"stdout": "fake", "exit_code": 0}
    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-bogus",
            "status": "success",
            "content": [{"text": json.dumps(bogus_payload)}],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    # No kind or payload fields — falls back to the default summary path.
    assert "kind" not in payload
    assert "payload" not in payload


def test_translate_event_falls_back_to_default_for_non_code_exec_results():
    """tool_result whose content does NOT match the code-exec shape
    (e.g. a web_search result) keeps the legacy summary-only payload —
    no ``kind`` or ``payload`` fields."""
    from channel.agents.strands_sse import translate_event

    web_search_payload = {"results": [{"url": "https://example.com"}]}
    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-xyz",
            "status": "success",
            "content": [{"text": json.dumps(web_search_payload)}],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    assert payload == {"tool_use_id": "tu-xyz", "summary": "completed"}


def test_translate_event_falls_back_when_content_is_not_valid_json():
    """tool_result content that doesn't parse as JSON → legacy payload."""
    from channel.agents.strands_sse import translate_event

    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-xyz",
            "status": "success",
            "content": [{"text": "not json"}],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    assert payload == {"tool_use_id": "tu-xyz", "summary": "completed"}


def test_translate_event_handles_tool_result_message_event():
    """Strands' ``ToolResultMessageEvent`` is the LIVE tool-result event
    in production (1.41.0). Its dict shape is ``{"message": {...}}`` with
    no ``type`` field — content carries ``[{"toolResult": result}, ...]``
    blocks. translate_event must split that into a ``("tool_results",
    list)`` batch with one (kind, payload) tuple per result.

    Regression guard for the 2026-06-08 dev-smoke bug where ``tool_started``
    fired but no ``tool_finished`` ever followed — the legacy
    ``"type": "tool_result"`` branch is dead code because
    ``ToolResultEvent.is_callback_event`` returns False in Strands 1.41."""
    from channel.agents.strands_sse import translate_event

    code_exec_payload = {
        "stdout": "42\n",
        "stderr": "",
        "exit_code": 0,
        "duration_ms": 12,
        "truncated": False,
        "timed_out": False,
        "images": [],
    }
    event = {
        "message": {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "tu-a",
                        "status": "success",
                        "content": [{"text": json.dumps(code_exec_payload)}],
                    },
                },
            ],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_results"
    assert len(payload) == 1
    sub_kind, sub_payload = payload[0]
    assert sub_kind == "tool_finished"
    assert sub_payload["tool_use_id"] == "tu-a"
    assert sub_payload["kind"] == "code-output"
    assert sub_payload["payload"] == code_exec_payload


def test_translate_event_tool_result_message_splits_mixed_success_and_error():
    """One ToolResultMessageEvent can carry multiple toolResult blocks
    (Strands batches all results from one event-loop cycle). Each must
    be translated independently — success → tool_finished, error →
    tool_error, with toolUseId-less blocks dropped."""
    from channel.agents.strands_sse import translate_event

    event = {
        "message": {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "tu-ok",
                        "status": "success",
                        "content": [{"text": "result-1"}],
                    },
                },
                {
                    "toolResult": {
                        "toolUseId": "tu-bad",
                        "status": "error",
                        "content": [{"text": "rate_limit"}],
                    },
                },
                # Uncorrelatable block (no toolUseId) — dropped.
                {
                    "toolResult": {
                        "status": "success",
                        "content": [{"text": "orphan"}],
                    },
                },
            ],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_results"
    assert len(payload) == 2
    assert payload[0] == ("tool_finished", {"tool_use_id": "tu-ok", "summary": "completed"})
    assert payload[1][0] == "tool_error"
    assert payload[1][1]["tool_use_id"] == "tu-bad"
    assert payload[1][1]["error_type"] == "rate_limit"


def test_translate_event_tool_result_message_skips_non_dict_blocks():
    """Defensive guard: content blocks that aren't dicts (malformed event,
    forward-compat with future block types) are silently skipped — the
    surrounding well-formed blocks still translate."""
    from channel.agents.strands_sse import translate_event

    event = {
        "message": {
            "role": "user",
            "content": [
                "not a dict",  # garbage block — must not crash
                {
                    "toolResult": {
                        "toolUseId": "tu-ok",
                        "status": "success",
                        "content": [{"text": "result"}],
                    },
                },
            ],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_results"
    assert len(payload) == 1
    assert payload[0][1]["tool_use_id"] == "tu-ok"


def test_translate_event_tool_result_message_with_no_valid_results_returns_skip():
    """A message event whose content has NO toolResult blocks (or only
    uncorrelatable ones) translates to ``("skip", None)`` — chats.py
    ignores skip kinds, so nothing flows over the wire."""
    from channel.agents.strands_sse import translate_event

    event = {
        "message": {
            "role": "user",
            "content": [
                {"text": "just text, no toolResult"},
                {"toolResult": {"content": [{"text": "no toolUseId"}]}},
            ],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "skip"
    assert payload is None


def test_sse_tool_finished_includes_kind_and_payload_when_provided():
    """``kind`` + ``payload`` ride along on the SSE wire."""
    from channel.agents.strands_sse import sse_tool_finished

    out = sse_tool_finished(
        tool_use_id="tu-x",
        summary="completed",
        kind="code-output",
        payload={"stdout": "42", "exit_code": 0},
    )
    text = out.decode()
    assert '"kind": "code-output"' in text or "'kind': 'code-output'" in text
    assert '"stdout": "42"' in text or "'stdout': '42'" in text


def test_sse_tool_finished_omits_kind_and_payload_when_unset():
    """Without ``kind``/``payload`` the wire shape is the legacy 3-field event."""
    from channel.agents.strands_sse import sse_tool_finished

    out = sse_tool_finished(tool_use_id="tu-x", summary="completed")
    text = out.decode()
    assert "kind" not in text
    assert "payload" not in text


def test_sse_error_shape():
    """``error`` frame (#212) round-trips through the SSE byte format."""
    from channel.agents.strands_sse import sse_error

    raw = sse_error(
        code="bedrock_throttled",
        message="The model is at capacity right now. Try again in a moment.",
        retryable=True,
    )
    assert raw.startswith(b"data: ")
    assert raw.endswith(b"\n\n")
    payload = json.loads(raw[6:-2])
    assert payload == {
        "type": "error",
        "code": "bedrock_throttled",
        "message": "The model is at capacity right now. Try again in a moment.",
        "retryable": True,
    }


def test_sse_error_truncates_message_at_500():
    """Defense in depth: the emitter caps ``message`` at the wire boundary."""
    from channel.agents.strands_sse import sse_error

    raw = sse_error(code="internal", message="x" * 600, retryable=False)
    payload = json.loads(raw[6:-2])
    assert len(payload["message"]) == 500
    assert payload["retryable"] is False


def test_sse_asset_created_wraps_descriptor_under_asset_key():
    """#326 — the asset_created frame carries the full inline-card
    descriptor (decision Q4) nested under ``asset``, with no payload
    coordinates. This is the exact wire shape #327's card renderer
    consumes."""
    from channel.agents.strands_sse import sse_asset_created

    descriptor = {
        "asset_id": "a-1",
        "chat_id": "c-1",
        "msg_id": "m-1",
        "kind": "code",
        "title": "fib.py",
        "mime": "text/plain",
        "size_bytes": 321,
        "origin": "generated",
        "created_at": "2026-07-13T00:00:00.000000+00:00",
        "source": {"msg_id": "m-1", "fence_index": 0, "lang": "python"},
    }
    raw = sse_asset_created(descriptor)
    assert raw.startswith(b"data: ")
    assert raw.endswith(b"\n\n")
    payload = json.loads(raw[6:-2])
    assert payload == {"type": "asset_created", "asset": descriptor}


def test_sse_asset_updated_mirrors_created_shape():
    """#326 — reserved regenerate-flow vocabulary; same descriptor
    contract as ``asset_created``."""
    from channel.agents.strands_sse import sse_asset_updated

    descriptor = {
        "asset_id": "a-2",
        "chat_id": "c-1",
        "msg_id": "m-2",
        "kind": "image",
        "title": "Figure 1",
        "mime": "image/png",
        "size_bytes": 2048,
        "origin": "tool_output",
        "created_at": "2026-07-13T00:00:01.000000+00:00",
        "source": {"msg_id": "m-2", "tool_use_id": "t-1"},
    }
    payload = json.loads(sse_asset_updated(descriptor)[6:-2])
    assert payload == {"type": "asset_updated", "asset": descriptor}
