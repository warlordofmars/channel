# Copyright (c) 2026 John Carter. All rights reserved.
"""End-to-end verification that AgentCore Memory writes land.

Phase 7c is write-only — there's no user-visible behaviour change, so
this test asserts directly against the dev-only
``GET /api/_debug/memory/events`` endpoint after driving real chats
through the UI with Playwright.

Requirements:

- ``inv dev`` running in another terminal (DDB Local + FastAPI on
  :8001 against real Bedrock + real AgentCore in the developer's
  personal AWS env, Vite on :5173).
- ``CHANNEL_ENABLE_DEBUG_ENDPOINTS=1`` (``inv dev`` sets this).
- Personal AWS credentials with Bedrock + AgentCore permissions.
- ``inv e2e-local`` passes ``CHANNEL_UI_URL`` (the Vite URL) and
  ``CHANNEL_API_URL`` (the API URL) through to the test process.

Run:

    uv run inv e2e-local --tests tests/e2e/test_memory_writes.py
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from typing import Any

import httpx
import pytest
from playwright.async_api import Browser, Page, async_playwright

from tests.e2e._http_helpers import _mint_jwt_via_bypass

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_memory_writes_land_per_turn_and_isolate_per_actor() -> None:
    """Drive two distinct users through the UI, send messages, verify
    AgentCore captured the writes scoped to each actor + session."""

    ui_url = os.environ.get("CHANNEL_UI_URL")
    api_url = os.environ.get("CHANNEL_API_URL", "http://localhost:8001")
    if not ui_url:
        pytest.skip("CHANNEL_UI_URL not set — run via `inv e2e-local`")

    tag = f"e2e-7c-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email_a = f"{tag}-a@example.com"
    email_b = f"{tag}-b@example.com"

    # Mint JWTs up-front via the auth bypass so we can call the debug
    # endpoint directly with each user's token.
    jwt_a = _mint_jwt_via_bypass(api_url, email_a)
    jwt_b = _mint_jwt_via_bypass(api_url, email_b)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            chat_a_id = await _drive_chat_as_user(
                browser,
                ui_url,
                jwt_a,
                messages=("hello A", "second A", "third A"),
            )
            chat_b_id = await _drive_chat_as_user(
                browser,
                ui_url,
                jwt_b,
                messages=("hello B",),
            )
        finally:
            await browser.close()

    # User A's chat — exactly 3 events.
    events_a = _list_events(api_url, jwt_a, chat_a_id, limit=10)
    assert len(events_a) == 3, f"expected 3 events for chat A ({chat_a_id}), got {len(events_a)}"

    # User B's chat — exactly 1 event.
    events_b = _list_events(api_url, jwt_b, chat_b_id, limit=10)
    assert len(events_b) == 1, f"expected 1 event for chat B ({chat_b_id}), got {len(events_b)}"

    # Cross-actor isolation: user B looking at user A's chat sees zero.
    events_a_from_b = _list_events(api_url, jwt_b, chat_a_id, limit=10)
    assert events_a_from_b == [], "user B must not see user A's events — actorId scoping broke"

    # Cleanup — best-effort.
    for evt in events_a:
        _delete_event(api_url, jwt_a, chat_a_id, evt["eventId"])
    for evt in events_b:
        _delete_event(api_url, jwt_b, chat_b_id, evt["eventId"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _drive_chat_as_user(
    browser: Browser,
    ui_url: str,
    jwt: str,
    messages: tuple[str, ...],
) -> str:
    """Log in, send N messages in one chat, return the chat id from the URL."""
    context = await browser.new_context()
    page = await context.new_page()

    # Seed the JWT BEFORE first navigation so AuthGate doesn't redirect.
    await page.goto(f"{ui_url}/app")
    await page.evaluate(
        "(token) => window.localStorage.setItem('channel_mgmt_token', token)",
        jwt,
    )
    await page.goto(f"{ui_url}/app")
    # Predicate callables avoid ReDoS-shaped regex (Sonar S5852) — the URL
    # set is small and known so a substring check is both simpler and safer.
    await page.wait_for_url(lambda url: "/app" in url, timeout=10_000)

    chat_id: str | None = None
    for i, msg in enumerate(messages):
        await _send_one_message(page, msg)
        if chat_id is None:
            # First message creates the chat; URL flips to /app/c/<id>.
            await page.wait_for_url(
                lambda url: "/app/c/" in url,
                timeout=15_000,
            )
            chat_id = page.url.rsplit("/", 1)[-1]
        # Wait for THIS turn's assistant reply to settle. We can't just
        # count testids because earlier replies already have it; we
        # wait for the count to grow.
        await page.wait_for_function(
            "(n) => document.querySelectorAll('[data-testid=\"assistant-turn-idle\"]').length >= n",
            arg=i + 1,
            timeout=90_000,
        )

    assert chat_id is not None
    await context.close()
    return chat_id


async def _send_one_message(page: Page, text: str) -> None:
    """Type ``text`` into the composer textarea and press Enter."""
    textarea = page.locator("textarea").first
    await textarea.fill(text)
    await textarea.press("Enter")


def _list_events(
    api_url: str,
    jwt: str,
    chat_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    resp = httpx.get(
        f"{api_url}/api/_debug/memory/events",
        params={"chat_id": chat_id, "limit": limit},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()["events"]


def _delete_event(api_url: str, jwt: str, chat_id: str, event_id: str) -> None:
    """Best-effort cleanup — ignore failures (test already asserted).

    ``event_id`` goes as a query parameter (not path) because the
    AgentCore id format ``<digits>#<hex>`` contains a URL fragment
    delimiter.
    """
    with contextlib.suppress(httpx.HTTPError):  # pragma: no cover — cleanup only
        httpx.delete(
            f"{api_url}/api/_debug/memory/events",
            params={"chat_id": chat_id, "event_id": event_id},
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=10.0,
        )
