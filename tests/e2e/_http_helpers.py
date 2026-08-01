# Copyright (c) 2026 John Carter. All rights reserved.
"""Shared sync HTTP helpers for e2e tests.

Multiple e2e tests (``test_memory_writes.py``, ``test_tool_use_smoke.py``,
``test_web_search_smoke.py``, ``test_chat_management.py``,
``test_memory_recall_and_titling.py``, ``test_attachments.py``) drive the
deployed (or local dev) stack via direct ``httpx`` calls in addition to
Playwright. Each used to inline its own copy of the same four helpers:

- ``_mint_jwt_via_bypass`` — hit ``/auth/login?test_email=...`` and pull
  the mgmt JWT out of the HTML response the bypass page renders.
- ``_create_chat`` — ``POST /api/chats`` and return the new ``chat_id``.
- ``_delete_chat`` — best-effort ``DELETE /api/chats/{chat_id}`` for
  cleanup (swallows network errors so cleanup never masks real test
  failures).
- ``_stream_messages`` — ``POST /api/chats/{id}/messages`` and collect
  parsed SSE events from the streamed response.

Promoted here once the third HTTP-driven e2e test landed
(``test_web_search_smoke.py``), matching the threshold called out in
the original in-file docstrings. The leading underscore is preserved
because these remain test-infrastructure functions, not a public API.

The helpers live in a module (not ``conftest.py``) so they remain
plain functions — easier to import explicitly, no fixture-shape
constraints, and the conftest stays focused on pytest fixtures
(``live_admin_token``) rather than utility functions.
"""

from __future__ import annotations

import contextlib
import html as html_lib
import json
import re
from typing import Any

import httpx
import pytest


def _mint_jwt_via_bypass(api_url: str, email: str) -> str:
    """Hit ``/auth/login?test_email=...`` and parse the JWT from the HTML.

    The bypass-login page sets ``localStorage.starter_mgmt_token`` and
    then redirects. We don't follow the redirect — we just parse the
    embedded token so we have it as a string for the Authorization
    header. ``pytest.skip`` if the bypass isn't enabled on the server.
    """
    resp = httpx.get(
        f"{api_url}/auth/login",
        params={"test_email": email},
        follow_redirects=False,
        timeout=15.0,
    )
    if resp.status_code in (301, 302, 307, 308):
        pytest.skip("Google OAuth redirect — CHANNEL_BYPASS_GOOGLE_AUTH not enabled")
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

    The body sends only ``{"title": ...}`` — ``ChatCreate`` also accepts
    an optional ``model_default``, but smoke tests rely on the
    server-side default so we don't pin a particular model id here. The
    response is the serialised chat row with ``chat_id`` at the top level.
    """
    resp = httpx.post(
        f"{api_url}/api/chats",
        json={"title": title},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()["chat_id"]


def _delete_chat(api_url: str, jwt: str, chat_id: str) -> None:
    """Best-effort ``DELETE /api/chats/{chat_id}``.

    Swallows network / HTTP errors so cleanup never masks a real test
    failure or leaves the test hanging on a deployed-env hiccup.
    """
    with contextlib.suppress(httpx.HTTPError):  # pragma: no cover — cleanup only
        httpx.delete(
            f"{api_url}/api/chats/{chat_id}",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=10.0,
        )


def _stream_messages(
    api_url: str,
    jwt: str,
    chat_id: str,
    user_text: str,
    *,
    timeout: float = 120.0,
) -> list[dict[str, Any]]:
    """``POST /api/chats/{chat_id}/messages`` and collect parsed SSE events.

    The endpoint streams ``text/event-stream``. We iterate the response
    line-by-line via ``resp.iter_lines()``, JSON-parse the payload of
    each line that starts with ``data: ``, and append it to ``events``.
    Non-``data:`` lines (blank separators between frames, comments,
    other SSE fields) and any line whose payload fails to JSON-parse
    are skipped — callers only care about the typed events emitted by
    ``strands_sse.py``.

    ``timeout`` defaults to 120 s (matches the original
    ``test_tool_use_smoke.py`` value). Tests that exercise slower tools
    (e.g. ``web_search``) should pass a larger value.
    """
    events: list[dict[str, Any]] = []
    with httpx.stream(
        "POST",
        f"{api_url}/api/chats/{chat_id}/messages",
        json={"message": user_text},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=timeout,
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
