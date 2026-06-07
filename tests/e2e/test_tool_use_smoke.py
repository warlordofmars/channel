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

Run:

    STARTER_CLOCK_TOOL_ENABLED=1 \
        uv run inv e2e-local --tests tests/e2e/test_tool_use_smoke.py
"""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import time
import uuid
from typing import Any

import httpx
import pytest


# ---------------------------------------------------------------------------
# Local helpers — kept in-file rather than added to conftest.py because the
# rest of the e2e suite drives Playwright; these are the only HTTP-only
# helpers and only one test uses them.
# ---------------------------------------------------------------------------


def _mint_jwt_via_bypass(api_url: str, email: str) -> str:
    """Hit ``/auth/login?test_email=...`` and parse the JWT from the HTML.

    Mirrors the pattern from ``test_memory_writes.py`` /
    ``test_chat_management.py`` — extracts the token from the
    ``localStorage.setItem`` snippet the bypass page renders.
    """
    resp = httpx.get(
        f"{api_url}/auth/login",
        params={"test_email": email},
        follow_redirects=False,
        timeout=15.0,
    )
    if resp.status_code in (301, 302, 307, 308):
        pytest.skip("Google OAuth redirect — STARTER_BYPASS_GOOGLE_AUTH not enabled")
    resp.raise_for_status()
    m = re.search(
        r"localStorage\.setItem\('starter_mgmt_token',\s*'([^']+)'\)",
        resp.text,
    )
    if not m:
        pytest.fail("Could not extract mgmt token from bypass login response")
    return html_lib.unescape(m.group(1))


def _create_chat(api_url: str, jwt: str, *, title: str) -> str:
    """``POST /api/chats`` → return ``chat_id``.

    The body shape matches ``ChatCreate`` (``title`` + ``model_default``);
    the response is the serialised chat row with ``chat_id`` at the top
    level.
    """
    resp = httpx.post(
        f"{api_url}/api/chats",
        json={"title": title},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()["chat_id"]


def _stream_messages(
    api_url: str,
    jwt: str,
    chat_id: str,
    user_text: str,
) -> list[dict[str, Any]]:
    """``POST /api/chats/{chat_id}/messages`` and collect parsed SSE events.

    The endpoint streams ``text/event-stream``; each frame is a single
    ``data: <json>\\n\\n`` line. We slurp the body, split on the empty
    line that separates frames, and JSON-parse each ``data:`` payload.
    Unknown / malformed frames are skipped — the test only cares about
    the typed events emitted by ``strands_sse.py``.
    """
    events: list[dict[str, Any]] = []
    with httpx.stream(
        "POST",
        f"{api_url}/api/chats/{chat_id}/messages",
        json={"message": user_text},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            payload = line.removeprefix("data: ").strip()
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                # Non-JSON lines (e.g. heartbeats, comments) — ignore.
                continue
    return events


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("STARTER_CLOCK_TOOL_ENABLED") != "1",
    reason="chassis exit test requires STARTER_CLOCK_TOOL_ENABLED=1",
)
def test_current_time_round_trip() -> None:
    """The chassis exit test: send "what time is it?" and assert the
    SSE stream emits the new tool event types correlating to a
    ``current_time`` call.

    Asserts:

    1. At least one ``tool_started`` event for ``current_time``
    2. A matching ``tool_finished`` event with ``summary == "completed"``
       (PR-2 narrowed the contract to this literal — round 5 review).
    3. The model's reply text contains a parseable time reference
       (sanity check that the chassis returned the tool result to the
       model and the model used it).
    """
    api_url = os.environ.get("STARTER_API_URL", "http://localhost:8001")

    tag = f"e2e-181-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)
    chat_id = _create_chat(api_url, jwt, title=f"chassis-smoke-{tag}")

    events = _stream_messages(api_url, jwt, chat_id, "what time is it?")

    started = [e for e in events if e.get("type") == "tool_started"]
    finished = [e for e in events if e.get("type") == "tool_finished"]
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

    # 3. The model's reply text contains a time reference — proof the
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
