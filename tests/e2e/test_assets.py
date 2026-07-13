# Copyright (c) 2026 John Carter. All rights reserved.
"""Asset lifecycle e2e suite (#329, epic #321 — assets as first-class citizens).

End-to-end coverage for the whole asset lifecycle the epic shipped
(#324 data model → #325 REST → #326 producers/SSE → #327 inline cards →
#328 browse/panel), following the e2e conventions in CLAUDE.md: a unique
per-run identity (a fresh ``?test_email=`` user per test — the asset
browse is JWT-``sub`` scoped, so a fresh user's ``GET /api/assets`` sees
ONLY this run's rows, dodging pagination/accumulation without needing a
tag filter) and ``has_text=`` selectors to avoid Playwright strict-mode
violations.

Flows, mapped to the issue's five scope bullets:

1. **Fenced-code asset** (``test_fenced_code_asset_lifecycle``) — a chat
   turn that emits a >= ``FENCE_MIN_LINES``-line non-mermaid code fence
   trips the post-stream extraction producer: the stream carries an
   ``asset_created`` frame (``kind=code`` / ``origin=generated``), the
   row survives (history-reattach via ``GET /api/chats/{id}/assets``),
   its bytes stream from the content endpoint, it appears in the
   cross-chat browse, and chat deletion cascades it out of the browse.
2. **Inline card + panel** (``test_inline_card_reload_and_panel``) — the
   transcript renders the persisted asset as an ``.art-inline`` card that
   survives a page reload, opens ArtifactPanel with the real code
   payload, and whose Copy + Download affordances work.
3. **Browse + deep-link** (``test_browse_view_and_chatless_deeplink``) —
   ``/app/artifacts`` lists the asset row, a row click opens the panel,
   and a chat-less ``?artifact=<id>`` deep link (the shape the #327
   inline cards emit — resolved via the owner browse per #350/#351)
   opens the panel cold.
4. **Upload card** (``test_upload_projection_creates_asset``) — an
   uploaded file is projected into an ``origin=upload`` asset that shows
   as an inline card. Skipped where the API has no attachments bucket
   (local ``inv dev`` doesn't provision one — the presign probe skips).
5. **Code-exec plot** (``test_code_exec_plot_persists_as_image_asset``) —
   a matplotlib plot from the sandbox persists as an ``origin=tool_output``
   ``kind=image`` asset that renders from the content endpoint on a fresh
   read (not just the live base64-over-SSE path). Skipped without the
   code-exec sandbox (deployed-env config only).

Tests 1-3 run locally against ``inv dev`` (Bedrock via the developer
profile + inline text assets need no S3); tests 4-5 skip locally and
exercise on the deployed env via ``inv e2e``.

Required env vars (mirrors the other suites here):
- ``STARTER_UI_URL`` — Vite / SPA base URL (Playwright tests)
- ``STARTER_API_URL`` — FastAPI base URL
- ``STARTER_BYPASS_GOOGLE_AUTH=1`` on the API side
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import Page, async_playwright

from tests.e2e._http_helpers import (
    _create_chat,
    _delete_chat,
    _mint_jwt_via_bypass,
    _stream_messages,
)

FIXTURES = Path(__file__).parent / "fixtures"
PNG_PATH = FIXTURES / "test.png"

# A prompt engineered to reliably elicit a SINGLE, long, non-mermaid
# Python fence so the post-stream extraction producer (>= 15 body lines,
# ``src/channel/agents/asset_producers.py``) fires deterministically. We
# assert on the resulting ``asset_created`` frame / persisted row — the
# binding contract — never on the model's prose, so wording drift can't
# make the suite flaky (same discipline as ``test_code_exec.py``).
_FENCE_PROMPT = (
    "Write a small Python module defining four functions: is_prime(n), "
    "factorial(n), fibonacci(n), and gcd(a, b). Give each function a "
    "one-line docstring and a couple of inline comments. Output the module "
    "as a SINGLE fenced Python code block (```python ... ```) that is at "
    "least 25 lines long. Do not write any prose before or after the code "
    "block."
)

# A text turn that produces a long code fence is a normal (non-tool) reply;
# 150 s is generous headroom over a typical 10-30 s completion.
_STREAM_TIMEOUT = 150.0
# Vision/PDF/tool replies can run long; matches ``test_attachments.py``.
_ASSISTANT_TIMEOUT_MS = 180_000
# The post-stream ``asset_created`` frame lands after ``done``; the SPA
# reattaches cards a beat after history paints. 40 s covers a cold nav +
# the async assets fetch.
_CARD_TIMEOUT_MS = 40_000
# Poll budget for the browse GSI to reflect a cascade delete.
_CASCADE_POLL_SECONDS = 25.0


# ----------------------------------------------------------------------
# Config / identity helpers
# ----------------------------------------------------------------------


def _api_url() -> str:
    return os.environ.get("STARTER_API_URL", "http://localhost:8001")


def _skip_if_no_ui() -> tuple[str, str]:
    """Return (ui_url, api_url); skip when the SPA URL isn't configured."""
    ui_url = os.environ.get("STARTER_UI_URL")
    if not ui_url:
        pytest.skip("STARTER_UI_URL not set — run via `inv e2e` or `inv e2e-local`")
    return ui_url, _api_url()


def _tag(slug: str) -> tuple[str, str]:
    """Unique (tag, email) pair for one test run — the email is the fresh
    per-run identity that isolates this test's asset browse."""
    tag = f"e2e-329-{slug}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    return tag, f"{tag}@example.com"


# ----------------------------------------------------------------------
# Asset REST helpers (sync httpx — the asset surface has no shared helper
# yet; these stay test-local like the other suites' inline helpers)
# ----------------------------------------------------------------------


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _list_chat_assets(api_url: str, jwt: str, chat_id: str) -> httpx.Response:
    return httpx.get(f"{api_url}/api/chats/{chat_id}/assets", headers=_auth(jwt), timeout=20.0)


def _get_asset_content(api_url: str, jwt: str, chat_id: str, asset_id: str) -> httpx.Response:
    return httpx.get(
        f"{api_url}/api/chats/{chat_id}/assets/{asset_id}/content",
        headers=_auth(jwt),
        timeout=20.0,
    )


def _browse_assets(api_url: str, jwt: str) -> list[dict[str, Any]]:
    """First browse page (limit 100) for the caller. A fresh per-run user
    never accumulates enough assets to need a second page."""
    resp = httpx.get(
        f"{api_url}/api/assets",
        params={"limit": 100},
        headers=_auth(jwt),
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def _stream_fence_asset(api_url: str, jwt: str, chat_id: str) -> dict[str, Any]:
    """Stream ``_FENCE_PROMPT`` and return the first ``kind=code`` asset
    descriptor from the ``asset_created`` frames.

    Asserts (not skips) if extraction produced no code asset — the prompt
    is engineered to force a qualifying fence, so a miss is a real
    regression, mirroring the tool-use smoke suites' posture on model
    output.
    """
    events = _stream_messages(api_url, jwt, chat_id, _FENCE_PROMPT, timeout=_STREAM_TIMEOUT)
    created = [e["asset"] for e in events if e.get("type") == "asset_created" and e.get("asset")]
    code_assets = [a for a in created if a.get("kind") == "code"]
    assert code_assets, (
        "Expected a kind=code asset_created frame from the fenced-code "
        f"extraction producer; got asset_created kinds="
        f"{[a.get('kind') for a in created]!r} across event types="
        f"{sorted({e.get('type') for e in events})!r}"
    )
    return code_assets[0]


def _wait_asset_gone_from_browse(api_url: str, jwt: str, asset_id: str) -> None:
    """Poll the browse until ``asset_id`` disappears (cascade + GSI lag)."""
    deadline = time.time() + _CASCADE_POLL_SECONDS
    while time.time() < deadline:
        ids = {a.get("asset_id") for a in _browse_assets(api_url, jwt)}
        if asset_id not in ids:
            return
        time.sleep(1.0)
    pytest.fail(
        f"Asset {asset_id} still present in browse {_CASCADE_POLL_SECONDS:.0f}s "
        "after chat delete — cascade did not remove the ASSET row"
    )


def _uploads_supported(api_url: str, jwt: str) -> bool:
    """True when the API can presign an upload (i.e. an attachments bucket
    is configured). Local ``inv dev`` sets no bucket, so ``presign`` raises
    ``KeyError`` → 500; the deployed stacks return 200. A clean, env-
    agnostic gate so the upload flow runs where it can and skips where it
    can't."""
    with contextlib.suppress(httpx.HTTPError):
        resp = httpx.post(
            f"{api_url}/api/attachments/presign",
            json={"name": "probe.png", "mime": "image/png", "size_bytes": 128},
            headers=_auth(jwt),
            timeout=15.0,
        )
        return resp.status_code == 200
    return False


# ----------------------------------------------------------------------
# Playwright helpers
# ----------------------------------------------------------------------


async def _open_app(page: Page, ui_url: str, jwt: str) -> None:
    """Land on /app with the bypass JWT pre-injected into localStorage."""
    await page.goto(f"{ui_url}/app")
    await page.evaluate("(t) => window.localStorage.setItem('starter_mgmt_token', t)", jwt)
    await page.goto(f"{ui_url}/app")
    await page.wait_for_url(lambda url: "/app" in url, timeout=10_000)


async def _wait_for_assistant_idle(page: Page, *, n: int = 1) -> None:
    await page.wait_for_function(
        "(want) => document.querySelectorAll('[data-testid=\"assistant-turn-idle\"]').length >= want",
        arg=n,
        timeout=_ASSISTANT_TIMEOUT_MS,
    )


async def _attach_and_send(page: Page, *, text: str, files: list[Path]) -> str:
    """Drop ``files`` onto the Composer, wait for each chip to attach, type
    ``text`` and submit. Returns the new chat id parsed from the URL.
    (Mirrors ``test_attachments._attach_and_send``.)"""
    file_input = page.locator('[data-testid="attach-file-input"]')
    await file_input.set_input_files([str(p) for p in files])
    await page.wait_for_function(
        "(want) => document.querySelectorAll('.attach-chip[data-status=\"attached\"]').length >= want",
        arg=len(files),
        timeout=60_000,
    )
    textarea = page.locator("textarea").first
    await textarea.fill(text)
    await textarea.press("Enter")
    await page.wait_for_url(lambda url: "/app/c/" in url, timeout=15_000)
    return page.url.rsplit("/", 1)[-1]


# ----------------------------------------------------------------------
# 1. Fenced-code asset — creation, persistence, content, browse, cascade
# ----------------------------------------------------------------------


def test_fenced_code_asset_lifecycle() -> None:
    """The full API-level lifecycle of a generated code asset.

    Binds the ``asset_created`` SSE contract, the history-reattach read,
    the content endpoint, the cross-chat browse, and the delete cascade —
    the four non-UI scope bullets in one robust pass (no browser, so it
    can't flake on rendering timing).
    """
    api_url = _api_url()
    _, email = _tag("api")
    jwt = _mint_jwt_via_bypass(api_url, email)
    chat_id = _create_chat(api_url, jwt, title="asset-lifecycle")
    cleaned_up = False
    try:
        asset = _stream_fence_asset(api_url, jwt, chat_id)
        asset_id = asset["asset_id"]

        # Frame shape (decision Q4 descriptor contract).
        assert asset["origin"] == "generated", asset
        assert asset["chat_id"] == chat_id, asset
        assert asset["msg_id"], f"asset_created frame missing msg_id: {asset!r}"
        assert isinstance(asset["source"].get("fence_index"), int), asset
        assert asset["source"].get("lang") == "python", asset

        # Persistence / history reattach — the SPA's cold-load read.
        list_resp = _list_chat_assets(api_url, jwt, chat_id)
        assert list_resp.status_code == 200, list_resp.text
        items = list_resp.json()["items"]
        persisted = {a["asset_id"]: a for a in items}
        assert asset_id in persisted, (
            f"Asset {asset_id} not returned by GET chat assets; got {list(persisted)!r}"
        )
        assert persisted[asset_id]["kind"] == "code"

        # Content endpoint — real bytes, text/plain, the extracted body.
        content_resp = _get_asset_content(api_url, jwt, chat_id, asset_id)
        assert content_resp.status_code == 200, content_resp.text
        assert content_resp.headers["content-type"].startswith("text/plain"), content_resp.headers
        body = content_resp.text
        assert "def " in body, f"Expected Python source in asset content; got {body[:200]!r}"

        # Cross-chat browse (owner GSI, newest-first) surfaces it.
        browse_ids = {a["asset_id"] for a in _browse_assets(api_url, jwt)}
        assert asset_id in browse_ids, (
            f"Asset {asset_id} missing from GET /api/assets browse; got {browse_ids!r}"
        )

        # Cascade — deleting the chat wipes the ASSET row; browse drops it.
        _delete_chat(api_url, jwt, chat_id)
        cleaned_up = True
        _wait_asset_gone_from_browse(api_url, jwt, asset_id)
    finally:
        if not cleaned_up:
            _delete_chat(api_url, jwt, chat_id)


# ----------------------------------------------------------------------
# 2. Inline transcript card — reload reattach + ArtifactPanel Copy/Download
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_card_reload_and_panel() -> None:
    """Seed a code asset via HTTP, then drive the browser: the transcript
    shows the inline card, it survives a reload (history reattach), and
    clicking it opens ArtifactPanel with the real payload where Copy and
    Download both work.

    Seeding the asset over HTTP (rather than a browser-driven send)
    decouples the model's non-determinism from the UI assertions — the
    browser half only ever runs against a known-persisted asset.
    """
    ui_url, api_url = _skip_if_no_ui()
    _, email = _tag("card")
    jwt = _mint_jwt_via_bypass(api_url, email)
    chat_id = _create_chat(api_url, jwt, title="asset-card-ui")
    asset = _stream_fence_asset(api_url, jwt, chat_id)
    title = asset["title"]

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            # Clipboard perms so the Copy button's async write resolves.
            ctx = await browser.new_context(permissions=["clipboard-read", "clipboard-write"])
            page = await ctx.new_page()
            await _open_app(page, ui_url, jwt)

            try:
                # Cold-load the chat: history + async asset reattach.
                await page.goto(f"{ui_url}/app/c/{chat_id}")
                card = page.locator("button.art-inline", has_text=title)
                await card.first.wait_for(timeout=_CARD_TIMEOUT_MS)

                # Survives a full page reload (history reattach path).
                await page.reload()
                await card.first.wait_for(timeout=_CARD_TIMEOUT_MS)

                # Open the panel — the card navigates to the chat-less
                # ?artifact= deep link, which the browse resolves.
                await card.first.click()
                panel = page.locator(".art-panel")
                await panel.wait_for(timeout=_CARD_TIMEOUT_MS)
                code_block = page.locator("pre.art-code code")
                await code_block.wait_for(timeout=_CARD_TIMEOUT_MS)
                rendered = await code_block.inner_text()
                assert "def " in rendered, f"Panel code body missing source: {rendered[:200]!r}"

                # Copy — the button flips to "Copied" only after the
                # clipboard write resolves, so the flip is the proof.
                await page.locator('button[aria-label="Copy"]').click()
                await page.locator('button[aria-label="Copied"]').wait_for(timeout=5_000)

                # Download — a real blob download fires with the asset name.
                async with page.expect_download(timeout=10_000) as dl_info:
                    await page.locator('button[aria-label="Download"]').click()
                download = await dl_info.value
                assert download.suggested_filename, "Download produced no filename"
            finally:
                # ``_delete_chat`` is a sync best-effort helper — call it
                # directly (a brief blocking teardown), never awaited.
                _delete_chat(api_url, jwt, chat_id)
        finally:
            await browser.close()


# ----------------------------------------------------------------------
# 3. Browse view row + chat-less deep link
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_view_and_chatless_deeplink() -> None:
    """The ``/app/artifacts`` browse lists the seeded asset as a row that
    opens the panel on click, and a chat-less ``?artifact=<id>`` deep link
    (the shape #327 inline cards emit, resolved via the owner browse per
    #350/#351) opens the panel cold on direct navigation."""
    ui_url, api_url = _skip_if_no_ui()
    _, email = _tag("browse")
    jwt = _mint_jwt_via_bypass(api_url, email)
    chat_id = _create_chat(api_url, jwt, title="asset-browse-ui")
    asset = _stream_fence_asset(api_url, jwt, chat_id)
    asset_id, title = asset["asset_id"], asset["title"]

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await _open_app(page, ui_url, jwt)

            try:
                # Browse view lists the row (has_text avoids strict-mode
                # violations against the shared .list-row class).
                await page.goto(f"{ui_url}/app/artifacts")
                row = page.locator(".list-row", has_text=title)
                await row.first.wait_for(timeout=_CARD_TIMEOUT_MS)

                # Row click opens the panel with the real payload.
                await row.first.click()
                await page.locator("pre.art-code code").wait_for(timeout=_CARD_TIMEOUT_MS)

                # Chat-less deep link: fresh navigation straight to
                # ?artifact=<id> (no &chat=) resolves via the owner browse
                # and opens the panel.
                await page.goto(f"{ui_url}/app/artifacts?artifact={asset_id}")
                panel = page.locator(".art-panel")
                await panel.wait_for(timeout=_CARD_TIMEOUT_MS)
                head = page.locator(".art-phead .t", has_text=title)
                await head.wait_for(timeout=_CARD_TIMEOUT_MS)
            finally:
                _delete_chat(api_url, jwt, chat_id)
        finally:
            await browser.close()


# ----------------------------------------------------------------------
# 4. Upload projection — origin=upload asset card (deployed-env only)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_projection_creates_asset() -> None:
    """An uploaded image is projected into an ``origin=upload`` asset that
    renders as an inline card. Skips where the API has no attachments
    bucket (local ``inv dev`` — the presign probe returns non-200)."""
    ui_url, api_url = _skip_if_no_ui()
    _, email = _tag("upload")
    jwt = _mint_jwt_via_bypass(api_url, email)
    if not _uploads_supported(api_url, jwt):
        pytest.skip("attachments bucket not configured on the API (no upload support)")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await _open_app(page, ui_url, jwt)

            chat_id = await _attach_and_send(
                page,
                text="Here's an image — just acknowledge you received it.",
                files=[PNG_PATH],
            )
            try:
                await _wait_for_assistant_idle(page)

                # The upload projection ASSET row exists (origin=upload,
                # image kind for the PNG) via the REST surface.
                items = _list_chat_assets(api_url, jwt, chat_id).json()["items"]
                uploads = [a for a in items if a["origin"] == "upload"]
                assert uploads, f"Expected an origin=upload asset; got {items!r}"
                assert uploads[0]["kind"] == "image", uploads[0]

                # And it renders as an inline card in the transcript.
                await page.locator("button.art-inline").first.wait_for(timeout=_CARD_TIMEOUT_MS)
            finally:
                _delete_chat(api_url, jwt, chat_id)
        finally:
            await browser.close()


# ----------------------------------------------------------------------
# 5. Code-exec plot — origin=tool_output image asset (deployed-env only)
# ----------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("STARTER_CODE_EXEC_ENABLED") != "1"
    or not os.environ.get("STARTER_CODE_EXEC_LAMBDA_ARN"),
    reason=(
        "requires STARTER_CODE_EXEC_ENABLED=1 AND STARTER_CODE_EXEC_LAMBDA_ARN "
        "set (deployed-env sandbox config)"
    ),
)
def test_code_exec_plot_persists_as_image_asset() -> None:
    """A matplotlib plot from the sandbox persists as an
    ``origin=tool_output`` ``kind=image`` asset whose bytes stream from the
    content endpoint on a fresh read — the durable path, distinct from the
    live base64-over-SSE render."""
    api_url = _api_url()
    _, email = _tag("plot")
    jwt = _mint_jwt_via_bypass(api_url, email)
    chat_id = _create_chat(api_url, jwt, title="asset-plot")
    try:
        events = _stream_messages(
            api_url,
            jwt,
            chat_id,
            (
                "Use the code_exec tool to run this exact Python: "
                "import matplotlib; matplotlib.use('Agg'); "
                "import matplotlib.pyplot as plt; plt.plot([1,2,3]); "
                "plt.savefig('/tmp/plot.png')"
            ),
            timeout=180.0,
        )
        created = [
            e["asset"] for e in events if e.get("type") == "asset_created" and e.get("asset")
        ]
        images = [a for a in created if a["kind"] == "image" and a["origin"] == "tool_output"]
        assert images, (
            "Expected an origin=tool_output image asset_created frame from "
            f"the code-exec producer; got {[(a.get('kind'), a.get('origin')) for a in created]!r}"
        )
        asset_id = images[0]["asset_id"]

        # Fresh read from the content endpoint (the post-reload path) —
        # real PNG bytes, image/* content type.
        content_resp = _get_asset_content(api_url, jwt, chat_id, asset_id)
        assert content_resp.status_code == 200, content_resp.text
        assert content_resp.headers["content-type"].startswith("image/"), content_resp.headers
        assert len(content_resp.content) > 100, "Expected non-trivial image payload"
    finally:
        _delete_chat(api_url, jwt, chat_id)
