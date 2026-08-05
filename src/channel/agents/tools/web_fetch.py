# Copyright (c) 2026 John Carter. All rights reserved.
"""``web_fetch`` — direct URL → content fetching (#232).

Calls Exa's ``POST /contents`` REST endpoint directly so the model can
fetch one specific URL the user pasted (or one it picked out of
``web_search`` results) instead of guessing at a search query that might
surface the page. Same Exa API key, same extraction quality, same
SSM/IAM plumbing as ``web_search`` — the two tools are a
discover/deep-read pair and ship under the same
``CHANNEL_WEB_SEARCH_ENABLED`` kill switch.

The shared machinery is imported from ``web_search`` rather than
copied: ``_resolve_exa_api_key`` keeps its single warm-pool
``lru_cache`` (one SSM read serves both tools), ``_error_result`` /
``_success_result`` keep the Strands-``ToolResult`` contract in exactly
one place (see ``web_search._error_result``'s docstring for why the
shape matters to ``strands_sse.translate_event``), and ``_exa_post``
keeps the HTTP transport and its status→token mapping in one place so
the two tools cannot drift into two vocabularies.

Error vocabulary — this tool emits every token ``web_search`` does
(``missing_key``, ``timeout``, ``rate_limit``, ``upstream_5xx``,
``bad_request``, ``connection_error``) plus two only it can reach:
``invalid_url`` (client-side URL validation) and ``fetch_failed`` (Exa
answered 200, but the crawl of *this* URL failed — see
``_crawl_failed_tag``).

Before #269 the four status-derived tokens above were unreachable:
``strands_tools.exa`` swallowed its own network failures and never read
``response.status``, so a 429 or 5xx arrived here wrapped as a nominal
success carrying the error body — the model saw a "successful" fetch
containing an error, and the SPA never saw a ``tool_error`` frame.
Owning the transport is what makes the mapping fire; the rationale in
full is in ``web_search``'s module docstring.

The fetch itself runs on Exa's crawlers, not in this Lambda — a
user-supplied URL pointing at link-local/loopback targets never
produces a request from inside our network, so no SSRF allowlist is
needed here. The ``invalid_url`` check is mostly a UX guard (fail fast
with a stable token the SPA can render), with one security-shaped
rule: userinfo URLs are rejected so embedded credentials never transit
to Exa — see ``_is_fetchable_url``.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from strands import tool

from channel.agents.tools.web_search import (
    _EXA_CONTENTS_ENDPOINT,
    _EXA_LIVECRAWL_TIMEOUT_MS,
    _error_result,
    _exa_post,
    _resolve_exa_api_key,
    _success_result,
)

logger = logging.getLogger(__name__)

# Exa rejects non-positive ``maxCharacters``; clamp the model-supplied
# cap the same way ``web_search`` clamps ``num_results``.
_MAX_CHARS_MIN = 1

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


def _crawl_failed_tag(data: Any) -> str | None:
    """Return Exa's failure tag when a 200 response carries no content.

    ``/contents`` answers 200 even when the crawl of the requested URL
    failed: the per-URL verdict lives in a ``statuses[]`` entry shaped
    ``{"id": <url>, "status": "error", "error": {"tag": …}}`` while
    ``results`` comes back empty. Before #269 that reached the model as
    a nominal success containing a failure — the "failures masquerade as
    success" limb of #232's finding.

    Any tag maps to the single ``fetch_failed`` token; the tag itself is
    logged, never enumerated. Exa's tag vocabulary is undocumented, and
    branching on it would recreate exactly the brittle
    match-upstream's-strings coupling this issue removes.

    The recognised shapes are deliberately narrow in BOTH directions,
    because each direction has its own failure mode:

    - ``results`` non-empty → partial success (content returned
      alongside a warning) stays on the success path. We ask for
      exactly one URL, so there is no ambiguity about whose status it is.
    - ``results`` present but **not a list** → an unfamiliar envelope.
      Return ``None`` and let the model see it. Treating an unknown
      shape as "no content" would turn a valid-but-unexpected 2xx into
      a phantom `fetch_failed` (Copilot review, PR #540).
    - ``results`` empty **or absent** → genuinely no content, so an
      error status is the real failure this guard exists to catch.
      Requiring the key to be present would let Exa reintroduce #269's
      bug simply by omitting it on failure."""
    if not isinstance(data, dict):
        return None
    results = data.get("results")
    if results:
        return None
    if results is not None and not isinstance(results, list):
        return None
    statuses = data.get("statuses")
    if not isinstance(statuses, list):
        return None
    for entry in statuses:
        if isinstance(entry, dict) and entry.get("status") == "error":
            error = entry.get("error")
            tag = error.get("tag") if isinstance(error, dict) else None
            return str(tag) if tag else "unknown"
    return None


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
    # ``web_search``: Strands' ``DecoratedFunctionTool.stream`` only
    # ``await``s tools whose underlying function is a coroutine
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
    # exception bubbling through the chassis. The key stays in a local —
    # never written back to ``os.environ``, which a warm Lambda shares
    # across concurrent invocations (#269).
    try:
        api_key = _resolve_exa_api_key()
    except Exception as exc:
        logger.warning("web_fetch.config_error %r", exc)
        return _error_result("missing_key")
    text: bool | dict[str, Any] = True
    if max_chars is not None:
        # Server-side truncation: Exa applies ``maxCharacters`` during
        # extraction, so the Lambda never buffers the untruncated page.
        text = {"maxCharacters": max(_MAX_CHARS_MIN, max_chars)}
    # Flat payload (no nested ``contents`` block — that shape belongs to
    # ``/search``), matching what ``strands_tools.exa.exa_get_contents``
    # sent so Exa sees an unchanged request.
    payload: dict[str, Any] = {
        "urls": [url],
        "text": text,
        "livecrawl": "fallback",
        "livecrawlTimeout": _EXA_LIVECRAWL_TIMEOUT_MS,
    }

    data, error_type = await _exa_post(
        _EXA_CONTENTS_ENDPOINT,
        payload,
        api_key,
        log_prefix="web_fetch",
        log_context=f"url_len={len(url)}",
    )
    if error_type is not None:
        return _error_result(error_type)
    if (tag := _crawl_failed_tag(data)) is not None:
        logger.warning("web_fetch.fetch_failed tag=%s url_len=%d", tag, len(url))
        return _error_result("fetch_failed")
    return _success_result(data)
