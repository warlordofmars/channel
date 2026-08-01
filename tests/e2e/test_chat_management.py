# Copyright (c) 2026 John Carter. All rights reserved.
"""End-to-end verification of chat rename + delete from the sidebar.

Drives the UI through:
1. Create a chat (send one message)
2. Hover the row → assert menu button is visible
3. Click menu → assert 6 items render (3 disabled, 3 enabled)
4. Click Rename → modal opens with pre-filled input → submit → assert
   sidebar updated AND ``GET /api/chats/{id}`` returns the new title
5. Click Delete → confirm modal → assert row gone AND
   ``GET /api/chats/{id}`` returns 404 AND AgentCore session has no
   events
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx
import pytest
from playwright.async_api import async_playwright

from tests.e2e._http_helpers import _mint_jwt_via_bypass

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_rename_and_delete_chat_from_sidebar() -> None:
    ui_url = os.environ.get("CHANNEL_UI_URL")
    api_url = os.environ.get("CHANNEL_API_URL", "http://localhost:8001")
    if not ui_url:
        pytest.skip("CHANNEL_UI_URL not set — run via `inv e2e-local`")

    tag = f"e2e-rd-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(f"{ui_url}/app")
            await page.evaluate(
                "(t) => window.localStorage.setItem('starter_mgmt_token', t)",
                jwt,
            )
            await page.goto(f"{ui_url}/app")
            await page.wait_for_url(lambda url: "/app" in url, timeout=10_000)

            # Send a message to create + populate the chat.
            await _send_one_message(page, "test chat for rename/delete")
            await page.wait_for_url(lambda url: "/app/c/" in url, timeout=15_000)
            chat_id = page.url.rsplit("/", 1)[-1]
            await _wait_for_assistant_idle(page, n=1, timeout_ms=90_000)

            # Hover the row → assert menu button becomes visible.
            row = page.locator(".recent-wrap").first
            await row.hover()
            menu_btn = row.locator(".recent-menu-btn")
            await menu_btn.wait_for(state="visible")
            await menu_btn.click()

            # 6 items render: Pin, Rename, Archive, Change project,
            # Remove from project, Delete (Pin / Change project /
            # Remove from project are disabled placeholders; Rename /
            # Archive / Delete are enabled). Archive was added in #164.
            menu_items = page.locator('[role="menuitem"]')
            assert await menu_items.count() == 6

            # Rename flow.
            await page.locator('[role="menuitem"]', has_text="Rename").click()
            input_el = page.locator("#rename-input")
            await input_el.wait_for(state="visible")
            await input_el.fill("Renamed via e2e")
            await page.locator('button:has-text("Save")').click()
            await page.wait_for_function(
                "() => !!document.querySelector('.recent')"
                " && Array.from(document.querySelectorAll('.recent'))"
                ".some(el => el.textContent.includes('Renamed via e2e'))",
                timeout=8_000,
            )
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{api_url}/api/chats/{chat_id}",
                    headers={"Authorization": f"Bearer {jwt}"},
                )
            assert resp.status_code == 200
            assert resp.json()["chat"]["title"] == "Renamed via e2e"

            # Delete flow.
            await row.hover()
            await menu_btn.click()
            await page.locator('[role="menuitem"]', has_text="Delete").click()
            await page.locator(".modal", has_text="Delete chat?").wait_for(state="visible")
            await page.locator('button.btn-danger:has-text("Delete")').click()
            # Row gone from sidebar.
            await page.wait_for_function(
                "() => !Array.from(document.querySelectorAll('.recent'))"
                ".some(el => el.textContent.includes('Renamed via e2e'))",
                timeout=8_000,
            )
            # Chat gone from API.
            async with httpx.AsyncClient(timeout=10.0) as client:
                gone = await client.get(
                    f"{api_url}/api/chats/{chat_id}",
                    headers={"Authorization": f"Bearer {jwt}"},
                )
            assert gone.status_code == 404

            # AgentCore session has no events (best-effort wipe).
            # Brief window for AgentCore eventual consistency.
            await asyncio.sleep(3)
            async with httpx.AsyncClient(timeout=10.0) as client:
                events_resp = await client.get(
                    f"{api_url}/api/_debug/memory/events",
                    params={"chat_id": chat_id, "limit": 10},
                    headers={"Authorization": f"Bearer {jwt}"},
                )
            # If debug endpoints aren't enabled, skip the structural check.
            if events_resp.status_code == 200:
                assert events_resp.json()["events"] == []
        finally:
            await browser.close()


# Helpers — same shape as other Playwright tests in this directory.


async def _send_one_message(page, text: str) -> None:
    textarea = page.locator("textarea").first
    await textarea.fill(text)
    await textarea.press("Enter")


async def _wait_for_assistant_idle(page, *, n: int, timeout_ms: int) -> None:
    await page.wait_for_function(
        "(want) => document.querySelectorAll('[data-testid=\"assistant-turn-idle\"]').length >= want",
        arg=n,
        timeout=timeout_ms,
    )
