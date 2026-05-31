# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the Strands event → SSE translator."""

from __future__ import annotations

import json

from channel.agents.strands_sse import (
    sse_delta,
    sse_done,
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
