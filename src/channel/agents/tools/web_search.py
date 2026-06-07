# Copyright (c) 2026 John Carter. All rights reserved.
"""``web_search`` — first concrete chassis tool (#128 / #182).

Wraps ``strands_tools.exa.exa_search`` with ``text=True`` so the model
gets page content alongside URLs in a single tool call. Citations flow
as inline markdown links in the model's reply (the tool docstring
nudges the model toward that format); no SSE protocol changes.

The Exa API key resolves at runtime from either ``EXA_API_KEY`` (local
dev) or the SSM parameter named by ``STARTER_EXA_API_KEY_PARAM`` (Lambda
cold-start). Mirrors the pattern in ``src/channel/auth/tokens.py``.

Errors from Exa (timeout / 5xx / 429 / 4xx) become structured
``{"status": "error", "error_type": "..."}`` dicts so the chassis's
``translate_event`` surfaces them as ``sse_tool_error`` with the reason
preserved (see PR-2 round-1 fix).
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any

import httpx
from strands import tool
from strands_tools.exa import exa_search

logger = logging.getLogger(__name__)

# Clamp range for ``num_results`` exposed to the model. Exa accepts up
# to 100 but chat-context can't usefully consume more than ~10.
_NUM_RESULTS_MIN = 1
_NUM_RESULTS_MAX = 10

# Exa live-crawl timeout — passed as ``livecrawl_timeout`` to
# ``exa_search``. Long enough for ``livecrawl="fallback"`` to fetch a
# slow page, short enough that the chassis wall-clock budget (120s)
# survives multiple search attempts in one chain. Exa's other request
# phases (index lookup, result assembly) are fast and don't need a
# separate budget here.
_EXA_TIMEOUT_SEC = 30


@functools.lru_cache(maxsize=1)
def _resolve_exa_api_key() -> str:
    """Return the Exa API key, preferring ``EXA_API_KEY`` env var (local
    dev), then SSM at the path in ``STARTER_EXA_API_KEY_PARAM``."""
    if key := os.environ.get("EXA_API_KEY"):
        return key
    import boto3  # pragma: no cover

    param_name = os.environ["STARTER_EXA_API_KEY_PARAM"]  # pragma: no cover
    ssm = boto3.client("ssm")  # pragma: no cover
    resp = ssm.get_parameter(Name=param_name, WithDecryption=True)  # pragma: no cover
    return resp["Parameter"]["Value"]  # pragma: no cover


@tool
def web_search(
    query: str,
    num_results: int = 5,
    category: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> dict[str, Any]:
    """Search the web and return relevant pages with content.

    Use when you need facts from sources outside the conversation —
    current events, recent papers, specific URLs, etc.

    When you incorporate findings into your reply, cite them as inline
    markdown links: ``[the relevant claim](https://example.com/page)``.
    Don't dump raw search results back to the user — distill them.

    Args:
        query: The search query string.
        num_results: How many results to return (1-10, default 5).
        category: Optional filter — one of ``"news"``, ``"research paper"``,
            ``"github"``, ``"pdf"``, ``"company"``, or ``None`` for general
            web search.
        include_domains: Optional list of domains to restrict to.
        exclude_domains: Optional list of domains to exclude.
    """
    # Make the key available to strands_tools.exa, which reads it from
    # the environment at call time. ``_resolve_exa_api_key`` can raise
    # for multiple reasons (``KeyError`` when ``STARTER_EXA_API_KEY_PARAM``
    # is unset in local dev, ``botocore`` ``ClientError`` for SSM
    # failures, network errors during boto3 client init, etc.). The
    # bare ``Exception`` catch is intentional: the contract requires a
    # stable ``error_type`` the SPA can render distinctly rather than
    # bubbling raw exceptions through the chassis.
    try:
        os.environ["EXA_API_KEY"] = _resolve_exa_api_key()
    except Exception as exc:
        logger.warning("web_search.config_error %r", exc)
        return {"status": "error", "error_type": "missing_key"}
    clamped = max(_NUM_RESULTS_MIN, min(num_results, _NUM_RESULTS_MAX))
    try:
        return exa_search(
            query=query,
            num_results=clamped,
            category=category,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            text=True,
            livecrawl="fallback",
            livecrawl_timeout=_EXA_TIMEOUT_SEC,
        )
    except httpx.ReadTimeout:
        logger.warning("web_search.timeout query=%r", query[:80])
        return {"status": "error", "error_type": "timeout"}
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 429:
            error_type = "rate_limit"
        elif 500 <= code < 600:
            error_type = "upstream_5xx"
        else:
            error_type = "bad_request"
        logger.warning("web_search.http_error status=%s query=%r", code, query[:80])
        return {"status": "error", "error_type": error_type}
