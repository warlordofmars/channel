# Copyright (c) 2026 John Carter. All rights reserved.
"""End-to-end verification of Phase 7d auto-titling + Phase 8a recall.

Drives chats through the UI and asserts (1) cross-session recall
surfaces specific keywords in chat B based on facts planted in chat A,
and (2) auto-title fires within 5s of the first assistant reply.

Phase 8a recall is synchronous (ListSessions + ListEvents), so facts
written 3 s ago are immediately queryable — the SemanticMemoryStrategy
ingestion lag from 7d is gone.

Requirements (same as 7c):

- ``inv dev`` running in another terminal.
- ``CHANNEL_RECALL_ENABLED=1`` AND ``CHANNEL_AUTO_TITLE_ENABLED=1``
  (``inv dev`` sets both).
- Personal AWS credentials with Bedrock + AgentCore permissions.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from typing import Any

import httpx
import pytest
from playwright.async_api import Browser, Page, async_playwright

from tests.e2e._http_helpers import _mint_jwt_via_bypass

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_cross_session_recall_surfaces_planted_facts() -> None:
    """Plant facts in chat A; assert chat B's agent recalls them.

    Phase 8a: recall is synchronous (ListSessions + ListEvents), so
    facts written 3s ago are immediately queryable. No multi-minute
    wait required (the SemanticMemoryStrategy ingestion lag from 7d
    is gone)."""
    ui_url = os.environ.get("CHANNEL_UI_URL")
    api_url = os.environ.get("CHANNEL_API_URL", "http://localhost:8001")
    if not ui_url:
        pytest.skip("CHANNEL_UI_URL not set — run via `inv e2e-local`")

    tag = f"e2e-8a-recall-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            # Chat A — plant two facts.
            chat_a_id, _ = await _drive_chat(
                browser,
                ui_url,
                jwt,
                messages=[
                    "I'm building a chess engine called Nightfall.",
                    "My favourite colour is sage green.",
                ],
            )
            # Brief eventual-consistency window for CreateEvent.
            await asyncio.sleep(3)
            events = _list_events(api_url, jwt, chat_a_id, limit=10)
            assert len(events) == 2, f"expected 2 events for chat A, got {len(events)}"

            # Chat B — recall must surface Nightfall + sage.
            chat_b_id, b_replies = await _drive_chat(
                browser,
                ui_url,
                jwt,
                messages=[
                    "What was the project I'm working on?",
                    "And the colour I like?",
                ],
            )
            await asyncio.sleep(3)
            b_events = _list_events(api_url, jwt, chat_b_id, limit=10)
        finally:
            await browser.close()

    # Strong deterministic-keyword assertions (the 7d weakening is
    # rolled back now that recall is synchronous).
    assert any("nightfall" in r.lower() for r in b_replies), (
        f"chat B should mention 'Nightfall' (recall worked); got: {b_replies!r}"
    )
    # Word-boundary match so "message"/"passage" don't spuriously match.
    assert any(re.search(r"\bsage\b", r, re.IGNORECASE) for r in b_replies), (
        f"chat B should mention 'sage' (recall worked); got: {b_replies!r}"
    )

    # Structural check: the recall addendum heading must NEVER appear
    # inside a USER-role event payload. The bug from issue #95 was
    # that the recall hook mutated event.messages[0] (the new user
    # turn) instead of event.agent.system_prompt — when that
    # happened, the polluted user message reached the memory write
    # hook and the addendum heading was stored as part of the user's
    # turn text in AgentCore. This is a model-independent signal: a
    # robust model (Sonnet) still answers correctly when the addendum
    # lands in the user message, so a behavioural assertion would
    # spuriously pass under Sonnet but fail under Opus. The
    # structural check fires regardless of model and pins the
    # contract: only the assistant's own output and the user's
    # literal input belong in the conversational payload.
    for ev in b_events:
        for entry in ev.get("payload", []) or []:
            conv = entry.get("conversational") or {}
            if conv.get("role") != "USER":
                continue
            content_text = (conv.get("content") or {}).get("text", "")
            assert "What we've talked about before" not in content_text, (
                "recall addendum heading found inside a USER-role event "
                "payload — the hook is mutating event.messages[0] (the "
                "new user turn) instead of event.agent.system_prompt. "
                "See PR #96 / issue #95.\n"
                f"Polluted USER content:\n{content_text!r}"
            )

    # "No memory" disclaimer absence — these phrases surface when the
    # recall addendum lands in the user turn rather than the system
    # prompt under sensitive models (Opus on dev). Kept alongside the
    # structural check above because Opus might respond differently
    # than the structural check catches.
    disclaimers = (
        "don't have any information",
        "don't have access to",
        "don't have any context",
        "no previous conversations",
        "no prior",
        "no memory",
        "don't retain",
        "starts fresh",
        "no information about you",
    )
    for reply in b_replies:
        for d in disclaimers:
            assert d not in reply.lower(), (
                "chat B reply contained a no-memory disclaimer "
                f"({d!r}); recall addendum may have landed in the user "
                f"turn rather than agent.system_prompt. See PR #96. "
                f"Reply: {reply!r}"
            )


@pytest.mark.asyncio
async def test_auto_title_fires_on_first_round_trip_only() -> None:
    """First assistant reply triggers title; second turn doesn't change it."""
    ui_url = os.environ.get("CHANNEL_UI_URL")
    api_url = os.environ.get("CHANNEL_API_URL", "http://localhost:8001")
    if not ui_url:
        pytest.skip("CHANNEL_UI_URL not set — run via `inv e2e-local`")

    tag = f"e2e-7d-title-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(f"{ui_url}/app")
            await page.evaluate(
                "(t) => window.localStorage.setItem('channel_mgmt_token', t)",
                jwt,
            )
            await page.goto(f"{ui_url}/app")
            await page.wait_for_url(lambda url: "/app" in url, timeout=10_000)

            # First message + first reply → title should land.
            await _send_one_message(
                page,
                "Help me debug a flaky pytest fixture that uses tmp_path.",
            )
            await page.wait_for_url(
                lambda url: "/app/c/" in url,
                timeout=15_000,
            )
            chat_id = page.url.rsplit("/", 1)[-1]
            await _wait_for_assistant_idle(page, n=1, timeout_ms=90_000)

            # Poll the sidebar for a title change (away from "New chat").
            title_after_first = await _wait_for_sidebar_title_change(
                page,
                default_text="New chat",
                timeout_ms=8_000,
            )
            assert title_after_first != "New chat"
            keywords = {"pytest", "fixture", "tmp_path", "debug"}
            lower = title_after_first.lower()
            assert any(k in lower for k in keywords), (
                "title should mention a substantive keyword from the message; "
                f"got: {title_after_first!r}"
            )
            # 3-6 words.
            word_count = len(title_after_first.split())
            assert 3 <= word_count <= 6, (
                f"title word count {word_count} outside 3-6 range: {title_after_first!r}"
            )

            # Verify persistence via API. Async client to avoid blocking
            # the event loop (Sonar S7499).
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{api_url}/api/chats/{chat_id}",
                    headers={"Authorization": f"Bearer {jwt}"},
                )
            assert resp.status_code == 200
            persisted_title = resp.json()["chat"]["title"]
            assert persisted_title == title_after_first

            # Second message + reply → title MUST NOT change.
            await _send_one_message(page, "What does tmp_path do?")
            await _wait_for_assistant_idle(page, n=2, timeout_ms=90_000)
            # Give the (non-existent) titler 5s to misfire.
            await asyncio.sleep(5)
            current_title = await _sidebar_title(page)
            assert current_title == title_after_first, (
                "title changed after second turn — auto-title idempotency broken"
            )
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Helpers (adapted from tests/e2e/test_memory_writes.py)
# ---------------------------------------------------------------------------


async def _drive_chat(
    browser: Browser,
    ui_url: str,
    jwt: str,
    *,
    messages: list[str],
) -> tuple[str, list[str]]:
    """Run N messages in one chat. Return (chat_id, list of assistant texts)."""
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(f"{ui_url}/app")
    await page.evaluate(
        "(t) => window.localStorage.setItem('channel_mgmt_token', t)",
        jwt,
    )
    await page.goto(f"{ui_url}/app")
    await page.wait_for_url(lambda url: "/app" in url, timeout=10_000)

    chat_id: str | None = None
    replies: list[str] = []
    for i, msg in enumerate(messages):
        await _send_one_message(page, msg)
        if chat_id is None:
            await page.wait_for_url(
                lambda url: "/app/c/" in url,
                timeout=15_000,
            )
            chat_id = page.url.rsplit("/", 1)[-1]
        await _wait_for_assistant_idle(page, n=i + 1, timeout_ms=90_000)
        # Grab the latest assistant turn text.
        turns = await page.locator('[data-testid="assistant-turn-idle"] .msg').all_text_contents()
        if turns:
            replies.append(turns[-1])

    assert chat_id is not None
    await context.close()
    return chat_id, replies


async def _send_one_message(page: Page, text: str) -> None:
    textarea = page.locator("textarea").first
    await textarea.fill(text)
    await textarea.press("Enter")


async def _wait_for_assistant_idle(
    page: Page,
    *,
    n: int,
    timeout_ms: int,
) -> None:
    await page.wait_for_function(
        "(want) => document.querySelectorAll('[data-testid=\"assistant-turn-idle\"]').length >= want",
        arg=n,
        timeout=timeout_ms,
    )


async def _wait_for_sidebar_title_change(
    page: Page,
    *,
    default_text: str,
    timeout_ms: int,
) -> str:
    """Poll the active sidebar row until its text differs from default."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        rows = await page.locator(".recent").all_text_contents()
        for txt in rows:
            stripped = txt.strip()
            if stripped and stripped != default_text:
                return stripped
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"sidebar title stayed {default_text!r} for {timeout_ms}ms",
    )


async def _sidebar_title(page: Page) -> str:
    rows = await page.locator(".recent").all_text_contents()
    return rows[0].strip() if rows else ""


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
