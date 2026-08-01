# Copyright (c) 2026 John Carter. All rights reserved.
"""End-to-end verification of attachment upload + model recall (#179).

Drives a real browser through the Composer's file picker against a
deployed environment, sends the message, and asserts the streamed
assistant reply references the attached content. Each test uses a
fresh chat so the assertions stay independent.

Required env vars (mirrors the other suites in this directory):
- ``CHANNEL_UI_URL`` — Vite / SPA base URL
- ``CHANNEL_API_URL`` — FastAPI base URL
- ``CHANNEL_BYPASS_GOOGLE_AUTH=1`` on the API side

If S3 / boto3 credentials are present (``CHANNEL_ATTACHMENTS_BUCKET``
+ default AWS creds chain) the cleanup test additionally verifies
that ``DELETE /api/chats/{id}`` cascade-removes the uploaded S3
object. Without creds it falls back to asserting the chat row is
gone, which still proves the API side of the cascade.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import httpx
import pytest
from playwright.async_api import Page, async_playwright

from tests.e2e._http_helpers import _mint_jwt_via_bypass

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).parent / "fixtures"
PDF_PATH = FIXTURES / "test.pdf"
PNG_PATH = FIXTURES / "test.png"
XLSX_PATH = FIXTURES / "test.xlsx"

# Strings the model must surface back from each fixture. Keep in lock-
# step with ``tests/e2e/fixtures/_generate.py``.
PDF_PHRASE = "ELDERBERRY CONSORTIUM 4471"
XLSX_A1_VALUE = "PEPPERMINT"

_ASSISTANT_TIMEOUT_MS = 180_000  # Vision/PDF replies can take longer than text.


def _skip_if_unconfigured() -> tuple[str, str]:
    ui_url = os.environ.get("CHANNEL_UI_URL")
    api_url = os.environ.get("CHANNEL_API_URL", "http://localhost:8001")
    if not ui_url:
        pytest.skip("CHANNEL_UI_URL not set — run via `inv e2e` or `inv e2e-local`")
    return ui_url, api_url


def _tag(slug: str) -> tuple[str, str]:
    """Build a unique tag + email pair for one test run."""

    tag = f"e2e-att-{slug}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    return tag, f"{tag}@example.com"


async def _open_app(page: Page, ui_url: str, jwt: str) -> None:
    """Land on /app with the bypass JWT pre-injected into localStorage."""

    await page.goto(f"{ui_url}/app")
    await page.evaluate("(t) => window.localStorage.setItem('starter_mgmt_token', t)", jwt)
    await page.goto(f"{ui_url}/app")
    await page.wait_for_url(lambda url: "/app" in url, timeout=10_000)


async def _attach_and_send(page: Page, *, text: str, files: list[Path]) -> str:
    """Drop ``files`` onto the Composer, wait for each chip to flip from
    ``attaching`` to ``attached``, type ``text`` and submit. Returns
    the new chat id parsed from the URL."""

    file_input = page.locator('[data-testid="attach-file-input"]')
    await file_input.set_input_files([str(p) for p in files])

    want_attached = len(files)
    # Each chip carries data-status; wait until every one is attached.
    await page.wait_for_function(
        "(want) => document.querySelectorAll('.attach-chip[data-status=\"attached\"]').length >= want",
        arg=want_attached,
        timeout=60_000,
    )
    textarea = page.locator("textarea").first
    await textarea.fill(text)
    await textarea.press("Enter")

    await page.wait_for_url(lambda url: "/app/c/" in url, timeout=15_000)
    return page.url.rsplit("/", 1)[-1]


async def _wait_for_assistant_idle(page: Page, *, n: int = 1) -> None:
    await page.wait_for_function(
        "(want) => document.querySelectorAll('[data-testid=\"assistant-turn-idle\"]').length >= want",
        arg=n,
        timeout=_ASSISTANT_TIMEOUT_MS,
    )


async def _assistant_text(page: Page, *, idx: int = 0) -> str:
    """Read the rendered assistant message text for turn ``idx``."""

    return await page.evaluate(
        """(i) => {
            const turns = document.querySelectorAll('[data-testid="assistant-turn-idle"]');
            return turns[i] ? turns[i].textContent || "" : "";
        }""",
        idx,
    )


async def _delete_chat(api_url: str, jwt: str, chat_id: str) -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(
            f"{api_url}/api/chats/{chat_id}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        # 200 or 404 both mean "gone" from the user's perspective.
        assert resp.status_code in (200, 204, 404), resp.text


@pytest.mark.asyncio
async def test_pdf_attachment_referenced_in_reply() -> None:
    ui_url, api_url = _skip_if_unconfigured()
    _, email = _tag("pdf")
    jwt = _mint_jwt_via_bypass(api_url, email)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await _open_app(page, ui_url, jwt)

            chat_id = await _attach_and_send(
                page,
                text="What phrase is written in this PDF? Reply with only the phrase.",
                files=[PDF_PATH],
            )
            try:
                await _wait_for_assistant_idle(page)
                body = (await _assistant_text(page)).upper()
                assert PDF_PHRASE in body, f"Expected {PDF_PHRASE!r} in reply, got: {body!r}"
            finally:
                await _delete_chat(api_url, jwt, chat_id)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_image_attachment_described_by_model() -> None:
    ui_url, api_url = _skip_if_unconfigured()
    _, email = _tag("img")
    jwt = _mint_jwt_via_bypass(api_url, email)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await _open_app(page, ui_url, jwt)

            chat_id = await _attach_and_send(
                page,
                text="What colour and shape do you see in this image? Reply in one short sentence.",
                files=[PNG_PATH],
            )
            try:
                await _wait_for_assistant_idle(page)
                body = (await _assistant_text(page)).lower()
                # The PNG is a red filled circle on a light background;
                # accept any common synonym for the shape and colour.
                shape_terms = {"circle", "disc", "disk", "dot", "round"}
                colour_terms = {"red", "crimson", "scarlet"}
                assert any(t in body for t in shape_terms), (
                    f"Expected a circle/disc/round descriptor, got: {body!r}"
                )
                assert any(t in body for t in colour_terms), (
                    f"Expected a red descriptor, got: {body!r}"
                )
            finally:
                await _delete_chat(api_url, jwt, chat_id)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_xlsx_attachment_surfaces_cell_value() -> None:
    ui_url, api_url = _skip_if_unconfigured()
    _, email = _tag("xlsx")
    jwt = _mint_jwt_via_bypass(api_url, email)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await _open_app(page, ui_url, jwt)

            chat_id = await _attach_and_send(
                page,
                text="Read cell A1 of the attached spreadsheet and reply with only its value.",
                files=[XLSX_PATH],
            )
            try:
                await _wait_for_assistant_idle(page)
                body = (await _assistant_text(page)).upper()
                assert XLSX_A1_VALUE in body, f"Expected {XLSX_A1_VALUE!r} in reply, got: {body!r}"
            finally:
                await _delete_chat(api_url, jwt, chat_id)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_multiple_attachments_both_referenced() -> None:
    ui_url, api_url = _skip_if_unconfigured()
    _, email = _tag("multi")
    jwt = _mint_jwt_via_bypass(api_url, email)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await _open_app(page, ui_url, jwt)

            chat_id = await _attach_and_send(
                page,
                text=(
                    "I've attached two files: a PDF and an image. Tell me the "
                    "phrase written in the PDF and the colour of the shape in "
                    "the image, in that order."
                ),
                files=[PDF_PATH, PNG_PATH],
            )
            try:
                await _wait_for_assistant_idle(page)
                body = await _assistant_text(page)
                assert PDF_PHRASE in body.upper(), f"Expected PDF phrase in reply, got: {body!r}"
                assert any(t in body.lower() for t in {"red", "crimson", "scarlet"}), (
                    f"Expected the image's red colour in reply, got: {body!r}"
                )
            finally:
                await _delete_chat(api_url, jwt, chat_id)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_chat_delete_cascades_attachment() -> None:
    """Delete the chat that owns an uploaded attachment and verify the
    cascade: the chat row 404s, and — when boto3 + bucket env are
    configured — the S3 object is also gone."""

    ui_url, api_url = _skip_if_unconfigured()
    _, email = _tag("cleanup")
    jwt = _mint_jwt_via_bypass(api_url, email)

    bucket = os.environ.get("CHANNEL_ATTACHMENTS_BUCKET")
    s3_client = None
    if bucket:
        try:
            import boto3

            s3_client = boto3.client("s3")
        except Exception:  # pragma: no cover — boto3 import or creds missing
            s3_client = None

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await _open_app(page, ui_url, jwt)

            chat_id = await _attach_and_send(
                page,
                text="Note: I'm only testing cleanup. No reply needed in detail.",
                files=[PDF_PATH],
            )
            # Capture the S3 key the user just attached so the post-
            # delete probe knows what to look for. The chat row's
            # attachments come back via the chats GET.
            s3_key: str | None = None
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Wait briefly for the user-message row to land.
                for _ in range(20):
                    msg_resp = await client.get(
                        f"{api_url}/api/chats/{chat_id}/messages",
                        headers={"Authorization": f"Bearer {jwt}"},
                    )
                    if msg_resp.status_code == 200:
                        msgs = msg_resp.json().get("messages", [])
                        if any(m.get("attachments") for m in msgs):
                            atts = [m["attachments"] for m in msgs if m.get("attachments")][0]
                            s3_key = atts[0].get("s3_key") if atts else None
                            break
                    await page.wait_for_timeout(500)

            await _wait_for_assistant_idle(page)
            await _delete_chat(api_url, jwt, chat_id)

            # Confirm the chat is gone by querying the list endpoint.
            # ``GET /api/chats/{chat_id}`` would also work in principle,
            # but CloudFront's SPA-fallback behaviour rewrites every
            # API 404 to ``/index.html`` (200 HTML), so the per-chat GET
            # can't distinguish "chat exists" from "chat absent" once
            # the request is fronted by CF. The list endpoint never
            # 404s, so its JSON body is the reliable probe.
            async with httpx.AsyncClient(timeout=15.0) as client:
                list_resp = await client.get(
                    f"{api_url}/api/chats",
                    params={"limit": 200, "include_archived": 1},
                    headers={"Authorization": f"Bearer {jwt}"},
                )
            assert list_resp.status_code == 200, list_resp.text
            chat_ids = {c["chat_id"] for c in list_resp.json().get("chats", [])}
            assert chat_id not in chat_ids, (
                f"Expected chat {chat_id} to be gone after delete, but the "
                f"list endpoint still returns it among {chat_ids!r}"
            )

            if s3_client and bucket and s3_key:
                try:
                    s3_client.head_object(Bucket=bucket, Key=s3_key)
                except s3_client.exceptions.ClientError as exc:  # pragma: no cover
                    code = exc.response.get("Error", {}).get("Code", "")
                    assert code in {"404", "NoSuchKey", "NotFound"}, (
                        f"Expected S3 object {s3_key!r} to be deleted, got {code!r}"
                    )
                else:
                    pytest.fail(f"S3 object {s3_key!r} still present after chat delete cascade")
        finally:
            await browser.close()
