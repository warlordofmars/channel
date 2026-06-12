# Copyright (c) 2026 John Carter. All rights reserved.
"""``web_fetch`` — direct URL → content fetching (#232).

Wraps ``strands_tools.exa.exa_get_contents`` so the model can fetch one
specific URL the user pasted (or one it picked out of ``web_search``
results) instead of guessing at a search query that might surface the
page. Same Exa API key, same extraction quality, same SSM/IAM plumbing
as ``web_search`` — the two tools are a discover/deep-read pair and
ship under the same ``STARTER_WEB_SEARCH_ENABLED`` kill switch.

The shared helpers are imported from ``web_search`` rather than copied:
``_resolve_exa_api_key`` keeps its single warm-pool ``lru_cache`` (one
SSM read serves both tools) and ``_error_result`` keeps the
Strands-``ToolResult``-shaped error contract in exactly one place — see
``web_search._error_result``'s docstring for why the shape matters to
``strands_sse.translate_event``.

Error vocabulary: this wrapper itself emits ``missing_key`` (key
resolution failed) and ``invalid_url`` (client-side URL validation —
only this tool can hit it). The ``timeout`` / ``rate_limit`` /
``upstream_5xx`` / ``bad_request`` httpx handlers mirror
``web_search``'s defensive layer, but note the installed
``strands_tools.exa`` catches its own network failures internally
(aiohttp + broad ``except``) and returns upstream-shaped result dicts
instead of raising — so in the current SDK those four tokens fire only
if a future SDK bump starts letting exceptions escape. Until then,
upstream failures pass through as Exa's own ``{"status": "error"}``
payloads (prose reason strings), or — for HTTP-error JSON bodies — as
nominal successes containing the error body. That pass-through is
``web_search`` parity today; tightening both tools to parse upstream's
error shapes into stable tokens is follow-up work flagged on #232.

The fetch itself runs on Exa's crawlers, not in this Lambda — a
user-supplied URL pointing at link-local/loopback targets never
produces a request from inside our network, so no SSRF allowlist is
needed here. The ``invalid_url`` check is mostly a UX guard (fail fast
with a stable token the SPA can render), with one security-shaped
rule: userinfo URLs are rejected so embedded credentials never transit
to Exa — see ``_is_fetchable_url``.
"""

from __future__ import annotations

import functools
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx
from strands import tool

from channel.agents.tools.web_search import _error_result, _resolve_exa_api_key

logger = logging.getLogger(__name__)

# Exa's ``livecrawlTimeout`` is in MILLISECONDS (upstream default
# 10_000 — see ``strands_tools.exa.exa_get_contents``'s docstring).
# 30s gives ``livecrawl="fallback"`` room to crawl an uncached page —
# the common case for user-pasted URLs — while staying well inside the
# chassis's 120s wall-clock budget.
_EXA_LIVECRAWL_TIMEOUT_MS = 30_000

# Exa rejects non-positive ``maxCharacters``; clamp the model-supplied
# cap the same way ``web_search`` clamps ``num_results``.
_MAX_CHARS_MIN = 1


@functools.lru_cache(maxsize=1)
def _get_exa_get_contents() -> Callable[..., Awaitable[dict[str, Any]]]:
    """Lazy-load ``strands_tools.exa.exa_get_contents`` on first invocation.

    Mirrors ``web_search._get_exa_search`` — the Exa SDK's transitive
    dependency tree (aiohttp, Rich, …) stays off the Lambda cold-start
    path for turns that never fetch a URL."""
    from strands_tools.exa import exa_get_contents  # noqa: PLC0415

    return exa_get_contents


# Internal whitespace / C0-control characters. ``urlparse`` strips many
# of these PRE-parse (Python 3.12, WHATWG-aligned), so without an
# explicit reject the validator would judge a cleaned string while Exa
# receives the raw one. Outer whitespace is already handled by the
# ``strip()`` at tool entry; anything matching here is embedded.
_FORBIDDEN_URL_CHARS_RE = re.compile(r"[\s\x00-\x1f\x7f]")


def _is_fetchable_url(url: str) -> bool:
    """True when ``url`` parses cleanly to an http(s) URL with a hostname
    and no embedded credentials.

    ``urlparse`` raises ``ValueError`` on structurally broken inputs
    (e.g. an unclosed IPv6 bracket); ``ftp://``, ``javascript:``,
    scheme-relative ``//host/path``, and bare prose fail the scheme /
    hostname checks. Userinfo URLs (``https://user:pass@host/…``) are
    rejected so credentials never transit to Exa — authenticated
    fetching is out of scope (#232), and this mirrors
    ``mcp.url_guard.validate_mcp_server_url``'s rule. Requiring
    ``hostname`` (vs bare ``netloc``) also rejects port-only
    authorities like ``https://:8080/path``."""
    if _FORBIDDEN_URL_CHARS_RE.search(url):
        return False
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return False
    if parsed.username or parsed.password:
        return False
    return parsed.scheme in ("http", "https") and bool(hostname)


@tool
async def web_fetch(url: str, max_chars: int | None = None) -> dict[str, Any]:
    """Fetch one specific URL and return its extracted page content.

    Use when the user provides a URL, or when you need to deep-read a
    specific page whose URL you already know (e.g. one surfaced by
    ``web_search``). To discover pages, use ``web_search`` instead; to
    fetch several URLs, call this tool once per URL.

    When you incorporate fetched content into your reply, cite it as an
    inline markdown link: ``[the relevant claim](https://example.com/page)``.
    Don't dump the raw page back to the user — distill it.

    Args:
        url: The http(s) URL to fetch.
        max_chars: Optional cap on the extracted text length — set it
            when you only need the start of a very long page.
    """
    # ``async def`` is mandatory for the same reason documented in
    # ``web_search``: ``strands_tools.exa.exa_get_contents`` is an
    # ``async def`` function, and Strands' ``DecoratedFunctionTool.stream``
    # only ``await``s tools whose underlying function is a coroutine
    # function — a sync wrapper would ship a coroutine repr to the model.
    #
    # Normalize once, up front: user-pasted URLs commonly carry stray
    # whitespace. ``urlparse`` already ignores the padding when judging
    # validity (Python 3.12 strips it pre-parse), but Exa must receive
    # the cleaned string — un-stripped, a trailing space even survives
    # inside ``netloc``.
    url = url.strip()
    # Validation precedes key resolution deliberately: a malformed URL is
    # diagnosable without credentials, so local devs with no Exa key get
    # the accurate ``invalid_url`` over a misleading ``missing_key``.
    if not _is_fetchable_url(url):
        logger.warning("web_fetch.invalid_url url_len=%d", len(url))
        return _error_result("invalid_url")
    # Same bare-Exception rationale as ``web_search``: the contract
    # requires a stable ``error_type`` token over SSE, not a raw
    # exception bubbling through the chassis.
    try:
        os.environ["EXA_API_KEY"] = _resolve_exa_api_key()
    except Exception as exc:
        logger.warning("web_fetch.config_error %r", exc)
        return _error_result("missing_key")
    text: bool | dict[str, Any] = True
    if max_chars is not None:
        # Server-side truncation: Exa applies ``maxCharacters`` during
        # extraction, so the Lambda never buffers the untruncated page.
        text = {"maxCharacters": max(_MAX_CHARS_MIN, max_chars)}
    exa_get_contents = _get_exa_get_contents()
    try:
        return await exa_get_contents(
            urls=[url],
            text=text,
            livecrawl="fallback",
            livecrawl_timeout=_EXA_LIVECRAWL_TIMEOUT_MS,
        )
    except httpx.ReadTimeout:
        logger.warning("web_fetch.timeout url_len=%d", len(url))
        return _error_result("timeout")
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 429:
            error_type = "rate_limit"
        elif 500 <= code < 600:
            error_type = "upstream_5xx"
        else:
            error_type = "bad_request"
        logger.warning("web_fetch.http_error status=%s url_len=%d", code, len(url))
        return _error_result(error_type)
