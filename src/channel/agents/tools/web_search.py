# Copyright (c) 2026 John Carter. All rights reserved.
"""``web_search`` — first concrete chassis tool (#128 / #182).

Wraps ``strands_tools.exa.exa_search`` with ``text=True`` so the model
gets page content alongside URLs in a single tool call. Citations flow
as inline markdown links in the model's reply (the tool docstring
nudges the model toward that format); no SSE protocol changes.

The Exa API key resolves on the first ``web_search()`` invocation (NOT
at module import time): the wrapper checks ``EXA_API_KEY`` (local dev)
first, then falls back to the SSM parameter named by
``STARTER_EXA_API_KEY_PARAM``. The resolved value is cached for the
lifetime of the Lambda warm pool (``@functools.lru_cache(maxsize=1)``).
The Exa SDK itself (``strands_tools.exa``) is also lazy-loaded on first
invocation to keep the cold-start dependency tree small. Mirrors the
runtime-resolution pattern in ``src/channel/auth/tokens.py``.

Errors from Exa (timeout / 5xx / 429 / 4xx) become Strands-ToolResult-
shaped dicts (``{"status": "error", "content": [{"text": "<reason>"}]}``)
so the chassis's ``translate_event`` extracts ``<reason>`` as the
stable ``error_type`` token over SSE — the SPA can render distinct
affordances for ``timeout`` vs ``rate_limit`` vs ``upstream_5xx``
vs ``bad_request`` vs ``missing_key`` (see PR-2 round-1 fix in
``strands_sse.translate_event``). Returning a plain dict with an
``error_type`` key instead would be re-wrapped by Strands' ``@tool``
decorator as a single JSON-stringified text block, defeating the
``translate_event`` reason extraction.
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable
from typing import Any

import httpx
from strands import tool

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


def _error_result(error_type: str) -> dict[str, Any]:
    """Build a Strands-``ToolResult``-shaped error dict.

    ``translate_event`` in ``strands_sse.py`` extracts ``error_type``
    by concatenating ``text`` blocks from ``ToolResult.content`` — so
    we MUST emit ``{"status": "error", "content": [{"text": "<reason>"}]}``,
    not ``{"status": "error", "error_type": "<reason>"}``. A plain
    error-keyed dict gets re-wrapped by Strands' ``@tool`` decorator as
    a single JSON-stringified text block (``status`` becomes ``success``
    and ``content`` becomes ``[{"text": "{...}"}]``), which both
    breaks the error path AND leaks the dict's repr as the SSE reason.
    The probe in ``tests/unit/test_tools_web_search.py`` covers both
    halves of the contract; see the PR-2 round-1 fix discussion."""
    return {"status": "error", "content": [{"text": error_type}]}


@functools.lru_cache(maxsize=1)
def _get_exa_search() -> Callable[..., dict[str, Any]]:
    """Lazy-load ``strands_tools.exa.exa_search`` on first invocation.

    The Exa SDK pulls in aiohttp, Rich, Console, Panel, etc. — a
    substantial transitive dependency tree. With
    ``STARTER_WEB_SEARCH_ENABLED=1`` in all envs (the env var is a
    kill switch, not a feature flag), this module always loads at
    cold start. Deferring the Exa import until the model actually
    calls ``web_search`` keeps those deps off the cold-start path
    for turns that don't trigger a search."""
    from strands_tools.exa import exa_search  # noqa: PLC0415

    return exa_search


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
        return _error_result("missing_key")
    clamped = max(_NUM_RESULTS_MIN, min(num_results, _NUM_RESULTS_MAX))
    exa_search = _get_exa_search()
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
        logger.warning("web_search.timeout query_len=%d", len(query))
        return _error_result("timeout")
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 429:
            error_type = "rate_limit"
        elif 500 <= code < 600:
            error_type = "upstream_5xx"
        else:
            error_type = "bad_request"
        logger.warning("web_search.http_error status=%s query_len=%d", code, len(query))
        return _error_result(error_type)
