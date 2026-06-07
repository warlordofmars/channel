# Copyright (c) 2026 John Carter. All rights reserved.
"""Web-search smoke test (#182). Exercises the Exa-backed tool end-to-end.

Gated by ``STARTER_WEB_SEARCH_ENABLED=1`` AND ``EXA_API_KEY`` present.
The test skips when either is missing so prod runs (where the flag may
be on but Lambda gets the key from SSM, not env) don't fail collection.

Sends a query likely to trigger a search, then asserts the SSE stream
emits ``tool_started`` + ``tool_finished`` for ``web_search`` (no
``tool_error``), and the model's reply text contains at least one
inline markdown link — proof the model wove a citation in.
"""

from __future__ import annotations

import contextlib
import html as html_lib
import json
import os
import re
import time
import uuid
from typing import Any

import httpx
import pytest


def _mint_jwt_via_bypass(api_url: str, email: str) -> str:
    """Hit ``/auth/login?test_email=...`` and extract the JWT — mirrors
    the helper in ``test_tool_use_smoke.py`` exactly."""
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
    resp = httpx.post(
        f"{api_url}/api/chats",
        json={"title": title},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()["chat_id"]


def _delete_chat(api_url: str, jwt: str, chat_id: str) -> None:
    """Best-effort cleanup — mirrors test_tool_use_smoke.py."""
    with contextlib.suppress(httpx.HTTPError):  # pragma: no cover — cleanup only
        httpx.delete(
            f"{api_url}/api/chats/{chat_id}",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=10.0,
        )


def _stream_messages(api_url: str, jwt: str, chat_id: str, user_text: str) -> list[dict[str, Any]]:
    """Stream the SSE response and JSON-parse each ``data:`` line."""
    events: list[dict[str, Any]] = []
    with httpx.stream(
        "POST",
        f"{api_url}/api/chats/{chat_id}/messages",
        json={"message": user_text},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=180.0,  # web search can be slower than current_time
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            payload = line.removeprefix("data: ").strip()
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                continue
    return events


@pytest.mark.skipif(
    os.environ.get("STARTER_WEB_SEARCH_ENABLED") != "1" or not os.environ.get("EXA_API_KEY"),
    reason="requires STARTER_WEB_SEARCH_ENABLED=1 AND EXA_API_KEY set",
)
def test_web_search_round_trip() -> None:
    """Send a query likely to trigger a search; assert tool_started +
    tool_finished for web_search, no tool_error, and at least one
    inline markdown link in the reply (citation contract)."""
    api_url = os.environ.get("STARTER_API_URL", "http://localhost:8001")

    tag = f"e2e-182-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)
    chat_id = _create_chat(api_url, jwt, title=f"web-search-smoke-{tag}")

    try:
        events = _stream_messages(
            api_url,
            jwt,
            chat_id,
            "Search the web for the latest news on retrieval augmented generation. "
            "Cite sources with inline markdown links.",
        )

        started = [e for e in events if e.get("type") == "tool_started"]
        finished = [e for e in events if e.get("type") == "tool_finished"]
        errors = [e for e in events if e.get("type") == "tool_error"]
        deltas = [e for e in events if e.get("type") == "delta"]

        # 1. A web_search tool was started
        web_search_starts = [e for e in started if e.get("tool_name") == "web_search"]
        assert web_search_starts, (
            f"Expected at least one tool_started for web_search; got tool_started "
            f"tool_names={[e.get('tool_name') for e in started]!r}"
        )

        # 2. The matching tool_finished arrived with summary='completed'
        started_ids = {e["tool_use_id"] for e in web_search_starts}
        finished_ids = {e["tool_use_id"] for e in finished}
        matched = started_ids & finished_ids
        assert matched, (
            f"web_search tool_use_id from started ({started_ids}) not found in "
            f"finished events ({finished_ids})"
        )
        finished_for_search = [e for e in finished if e["tool_use_id"] in matched]
        assert finished_for_search[0].get("summary") == "completed", (
            "Expected summary='completed' (PR-2 narrowed contract); got "
            f"{finished_for_search[0].get('summary')!r}"
        )

        # 3. No tool_error for the matched tool_use_id — happy path
        errors_for_search = [e for e in errors if e.get("tool_use_id") in matched]
        assert not errors_for_search, (
            "Expected NO tool_error for web_search's tool_use_id on the "
            f"happy path; got {errors_for_search!r}"
        )

        # 4. Model wove at least one inline markdown link into the reply
        reply_text = "".join(e.get("text", "") for e in deltas)
        has_markdown_link = re.search(r"\[[^\]]+\]\(https?://[^)]+\)", reply_text)
        assert has_markdown_link, (
            "Expected at least one inline markdown link in the model's "
            "reply (citation contract). Got: "
            f"{reply_text[:400]!r}"
        )
    finally:
        _delete_chat(api_url, jwt, chat_id)
