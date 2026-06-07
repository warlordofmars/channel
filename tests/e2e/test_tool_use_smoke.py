# Copyright (c) 2026 John Carter. All rights reserved.
"""Chassis smoke test (#181). Exercises ``current_time`` end-to-end.

This is the chassis exit test from the strategy spec at
``docs/superpowers/specs/2026-06-06-epic-128-tool-use-sequencing.md``
(policy P2). It exists to prove the chassis works in deployed
environments: send "what time is it?", read the SSE stream, and assert
the new tool SSE event types appear with a ``current_time``
``tool_use_id`` correlating across them.

Gated by ``STARTER_CLOCK_TOOL_ENABLED=1`` — default on in jc/dev, off
in prod. The test skips when the flag is unset so prod runs don't fail.

The harness is direct HTTP rather than Playwright: this test exercises
the BACKEND-to-stream layer (PR-1 chassis + PR-2 SSE protocol + the
wire format from ``chats.py``), not any SPA component. The Playwright-
driven flows live in the other ``tests/e2e/*.py`` files.

The four sync HTTP helpers (mint JWT, create / delete chat, stream
messages) live in ``tests/e2e/_http_helpers.py`` so the duplicated
bodies that used to ship in this file and ``test_web_search_smoke.py``
(and to a lesser extent ``test_memory_writes.py``) collapse into one
shared module.

Run:

    STARTER_CLOCK_TOOL_ENABLED=1 \
        uv run inv e2e-local --tests tests/e2e/test_tool_use_smoke.py
"""

from __future__ import annotations

import os
import re
import time
import uuid

import pytest

from tests.e2e._http_helpers import (
    _create_chat,
    _delete_chat,
    _mint_jwt_via_bypass,
    _stream_messages,
)


@pytest.mark.skipif(
    os.environ.get("STARTER_CLOCK_TOOL_ENABLED") != "1",
    reason="chassis exit test requires STARTER_CLOCK_TOOL_ENABLED=1",
)
def test_current_time_round_trip() -> None:
    """The chassis exit test: send "what time is it?" and assert the
    SSE stream emits ``tool_started`` + ``tool_finished`` (correlated by
    ``tool_use_id``) for a ``current_time`` call, and that the model's
    reply incorporates the result.

    Asserts:

    1. At least one ``tool_started`` event for ``current_time``.
    2. A matching ``tool_finished`` event with ``summary == "completed"``
       (PR-2 narrowed the contract to this literal — round 5 review).
    3. NO ``tool_error`` arrived for the matched ``tool_use_id`` — happy
       path means a clean finished, not a cancelled/failed call that
       still happened to emit deltas.
    4. The model's reply text contains a parseable time reference
       (sanity check that the chassis returned the tool result to the
       model and the model used it).

    ``tool_progress`` is not asserted: ``current_time`` doesn't yield
    streaming progress events, and the SSE protocol covers it via unit
    tests in ``test_strands_sse.py``.
    """
    api_url = os.environ.get("STARTER_API_URL", "http://localhost:8001")

    tag = f"e2e-181-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)
    chat_id = _create_chat(api_url, jwt, title=f"chassis-smoke-{tag}")

    try:
        events = _stream_messages(api_url, jwt, chat_id, "what time is it?")

        started = [e for e in events if e.get("type") == "tool_started"]
        finished = [e for e in events if e.get("type") == "tool_finished"]
        errors = [e for e in events if e.get("type") == "tool_error"]
        deltas = [e for e in events if e.get("type") == "delta"]

        # 1. A ``current_time`` tool was started.
        current_time_starts = [e for e in started if e.get("tool_name") == "current_time"]
        assert current_time_starts, (
            f"Expected at least one tool_started for current_time; got tool_started"
            f" tool_names={[e.get('tool_name') for e in started]!r};"
            f" all event types={[e.get('type') for e in events]!r}"
        )

        # 2. The matching ``tool_finished`` event arrived with ``summary='completed'``.
        started_ids = {e["tool_use_id"] for e in current_time_starts}
        finished_ids = {e["tool_use_id"] for e in finished}
        matched = started_ids & finished_ids
        assert matched, (
            f"current_time tool_use_id from started ({started_ids}) not found in "
            f"finished events ({finished_ids})"
        )
        finished_for_clock = [e for e in finished if e["tool_use_id"] in matched]
        assert finished_for_clock[0].get("summary") == "completed", (
            "Expected summary='completed' (PR-2 narrowed contract); got "
            f"{finished_for_clock[0].get('summary')!r}"
        )

        # 3. No ``tool_error`` arrived for the matched ``tool_use_id`` —
        #    happy path means the call finished cleanly, not "errored
        #    but also emitted some deltas". A regression that cancels
        #    the call mid-chain (chain_cap / wall_clock / cancelled)
        #    would show up here as a fast failure with the exact
        #    ``error_type`` for diagnosis.
        errors_for_clock = [e for e in errors if e.get("tool_use_id") in matched]
        assert not errors_for_clock, (
            "Expected NO tool_error for current_time's tool_use_id on the "
            f"happy path; got {errors_for_clock!r}"
        )

        # 4. The model's reply text contains a time reference — proof the
        #    tool result reached the model. We accept any of: an ISO-8601
        #    UTC timestamp (the tool returns this), a weekday name, or an
        #    HH:MM clock string. Any one is sufficient sanity-check
        #    evidence that the model incorporated the tool output.
        reply_text = "".join(e.get("text", "") for e in deltas)
        has_iso = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", reply_text)
        has_weekday = re.search(
            r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
            reply_text,
            re.IGNORECASE,
        )
        has_clock = re.search(r"\b\d{1,2}:\d{2}\b", reply_text)
        assert has_iso or has_weekday or has_clock, (
            f"Expected a time reference in model reply; got: {reply_text[:300]!r}"
        )
    finally:
        # Best-effort cleanup so repeated dev-env runs don't accumulate
        # chassis-smoke chats. Failure here is suppressed so it can't
        # mask a real assertion failure above.
        _delete_chat(api_url, jwt, chat_id)
