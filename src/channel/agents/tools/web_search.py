# Copyright (c) 2026 John Carter. All rights reserved.
"""``web_search`` — first concrete chassis tool (#128 / #182).

Calls Exa's ``POST /search`` REST endpoint directly with ``httpx`` and
``text=True`` so the model gets page content alongside URLs in a single
tool call. Citations flow as inline markdown links in the model's reply
(the tool docstring nudges the model toward that format); no SSE
protocol changes.

The Exa API key resolves on the first ``web_search()`` invocation (NOT
at module import time): the wrapper checks ``EXA_API_KEY`` (local dev)
first, then falls back to the SSM parameter named by
``CHANNEL_EXA_API_KEY_PARAM``. The resolved value is cached for the
lifetime of the Lambda warm pool (``@functools.lru_cache(maxsize=1)``).
Mirrors the runtime-resolution pattern in ``src/channel/auth/tokens.py``.
The key travels as the ``x-api-key`` request header, is held only in a
local, and is never written to a log line or into a payload returned to
the model.

Errors from Exa (timeout / 5xx / 429 / 4xx / transport) become
Strands-``ToolResult``-shaped dicts (``{"status": "error", "content":
[{"text": "<reason>"}]}``) so the chassis's ``translate_event`` extracts
``<reason>`` as the stable ``error_type`` token over SSE — the SPA can
render distinct affordances for ``timeout`` vs ``rate_limit`` vs
``upstream_5xx`` vs ``bad_request`` vs ``connection_error`` vs
``missing_key`` (see PR-2 round-1 fix in
``strands_sse.translate_event``). Returning a plain dict with an
``error_type`` key instead would be re-wrapped by Strands' ``@tool``
decorator as a single JSON-stringified text block, defeating the
``translate_event`` reason extraction.

**Why a direct REST call rather than ``strands_tools.exa`` (#269).**
Every token above except ``missing_key`` is *derived from the HTTP
status code*, and the SDK discards it: ``strands_tools.exa`` (0.8.0)
never reads ``response.status``, never calls ``raise_for_status``, and
wraps whatever JSON it parsed — including 429 and 5xx error bodies — as
``{"status": "success", "content": [{"text": str(data)}]}``. A 429, a
503 and a 200 therefore arrived here indistinguishable, so no amount of
post-parsing could reconstruct the token; the SDK's four prose failure
strings are unversioned internals besides. Owning the transport is the
only way the documented vocabulary can fire at all — and it is *less*
code, since upstream's function bodies are payload assembly, one POST,
and a Rich render we don't want.

This does NOT conflict with CLAUDE.md §"Product decisions" (*"Don't
build a parallel tool-calling shim"*): that governs the
tool-**calling** machinery — ``BedrockModel``, ``MCPClient``, Strands
tool hooks — all untouched. ``web_search`` remains a ``@tool``-decorated
coroutine registered through ``chats._build_tool_registry``. What
changed is which HTTP client fetches an upstream REST API inside the
tool body.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from typing import Any

import httpx
from strands import tool

logger = logging.getLogger(__name__)

# Clamp range for ``num_results`` exposed to the model. Exa accepts up
# to 100 but chat-context can't usefully consume more than ~10.
_NUM_RESULTS_MIN = 1
_NUM_RESULTS_MAX = 10

# Exa's ``livecrawlTimeout`` is in MILLISECONDS (upstream default
# 10_000; the value is forwarded verbatim to Exa). 30s gives
# ``livecrawl="fallback"`` room to fetch a slow uncached page, short
# enough that the chassis wall-clock budget (120s) survives multiple
# search attempts in one chain. Exa's other request phases (index
# lookup, result assembly) are fast and don't need a separate budget.
_EXA_LIVECRAWL_TIMEOUT_MS = 30_000

# Exa's REST surface. Both endpoints are stable, take the same
# ``x-api-key`` header, and are shared with ``web_fetch`` via
# ``_exa_post``. The payload field names assembled by each tool mirror
# what ``strands_tools.exa`` sent, so the request Exa receives is
# unchanged by #269 — only our reading of the response is.
_EXA_API_BASE_URL = "https://api.exa.ai"
_EXA_SEARCH_ENDPOINT = "/search"
_EXA_CONTENTS_ENDPOINT = "/contents"

# Exa's per-integration attribution header. The SDK sent
# ``aws-strands-agent``; now that we own the transport, say who we are.
_EXA_INTEGRATION = "channel"

# Read budget sits above the 30s ``livecrawlTimeout`` we already send,
# so Exa's own crawl deadline — not ours — is what expires first on a
# slow page, and well inside the chassis's 120s wall-clock budget.
# Connect is short because a slow TCP/TLS handshake to api.exa.ai is a
# reachability failure, not a slow crawl. The SDK set no explicit
# timeout at all (aiohttp's 5-minute default), so this is strictly
# tighter. Neither the SDK nor this client retries.
_EXA_READ_TIMEOUT_SECONDS = 45.0
_EXA_CONNECT_TIMEOUT_SECONDS = 5.0


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


def _success_result(data: Any) -> dict[str, Any]:
    """Build the Strands-``ToolResult``-shaped success dict.

    Same envelope ``strands_tools.exa`` returned, with one deliberate
    improvement (#269): the body is ``json.dumps(data)`` rather than
    ``str(data)``. The SDK handed the model a *Python repr* — single
    quotes, ``True``, ``None`` — which is not valid JSON and cost the
    model a parse it should not have to guess at."""
    return {"status": "success", "content": [{"text": json.dumps(data)}]}


def _classify_status(code: int) -> str:
    """Map an HTTP status code to a stable error token.

    This mapping is the whole point of #269: the SDK discarded
    ``response.status``, so ``rate_limit`` / ``upstream_5xx`` /
    ``bad_request`` could never fire in production no matter how
    carefully the response was post-parsed."""
    if code == 429:
        return "rate_limit"
    if 500 <= code < 600:
        return "upstream_5xx"
    return "bad_request"


@functools.lru_cache(maxsize=1)
def _resolve_exa_api_key() -> str:
    """Return the Exa API key, preferring ``EXA_API_KEY`` env var (local
    dev), then SSM at the path in ``CHANNEL_EXA_API_KEY_PARAM``."""
    if key := os.environ.get("EXA_API_KEY"):
        return key
    import boto3  # pragma: no cover

    param_name = os.environ["CHANNEL_EXA_API_KEY_PARAM"]  # pragma: no cover
    ssm = boto3.client("ssm")  # pragma: no cover
    resp = ssm.get_parameter(Name=param_name, WithDecryption=True)  # pragma: no cover
    return resp["Parameter"]["Value"]  # pragma: no cover


async def _exa_post(
    endpoint: str,
    payload: dict[str, Any],
    api_key: str,
    *,
    log_prefix: str,
    log_context: str,
) -> tuple[Any, str | None]:
    """POST ``payload`` to an Exa endpoint.

    Returns ``(data, None)`` on success or ``(None, error_type)`` on
    failure, where ``error_type`` is one of the stable tokens. Shared by
    ``web_search`` and ``web_fetch`` so both tools speak one error
    vocabulary from one implementation — the same reason
    ``_error_result`` and ``_resolve_exa_api_key`` live in this module.

    The client is constructed per call rather than held module-level:
    it scopes the connection's lifetime to the tool invocation (a frozen
    Lambda resuming with a half-open pooled socket is a class of bug
    worth not having), and it preserves the *intent* of the lazy
    SDK-import pattern this replaced — nothing heavyweight happens until
    the model actually calls the tool.

    ``api_key`` goes into the ``x-api-key`` header and is never logged.
    ``log_context`` must likewise carry only non-sensitive scalars
    (lengths, counts): it lands in CloudWatch verbatim. Exa's error
    *bodies* are deliberately never logged either — they are
    unversioned and may echo request content back."""
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "x-exa-integration": _EXA_INTEGRATION,
    }
    timeout = httpx.Timeout(_EXA_READ_TIMEOUT_SECONDS, connect=_EXA_CONNECT_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{_EXA_API_BASE_URL}{endpoint}",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json(), None
    except httpx.TimeoutException:
        # Covers ReadTimeout / ConnectTimeout / WriteTimeout / PoolTimeout —
        # to the model every one of them is "Exa didn't answer in time".
        # MUST precede the TransportError handler below: TimeoutException
        # is a subclass of it, so the wider catch would shadow this one.
        logger.warning("%s.timeout %s", log_prefix, log_context)
        return None, "timeout"
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        logger.warning("%s.http_error status=%s %s", log_prefix, code, log_context)
        return None, _classify_status(code)
    except httpx.TransportError:
        # Connect / DNS / TLS failure — we never reached Exa. Kept
        # distinct from ``upstream_5xx`` (Exa reached, Exa broke)
        # because the operator response differs; folding them would make
        # the log line say something untrue (#269 design decision 4).
        logger.warning("%s.connection_error %s", log_prefix, log_context)
        return None, "connection_error"
    except ValueError:
        # ``response.json()`` on a 2xx whose body isn't JSON — Exa
        # answered, but with something we can't hand the model. Rare,
        # and outside the design's status-derived table, but without a
        # handler it escapes as a raw exception through the chassis:
        # exactly the unstable-prose failure mode #269 closes.
        logger.warning("%s.bad_response %s", log_prefix, log_context)
        return None, "bad_response"


@tool
async def web_search(
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
    # ``async def`` is mandatory: Strands' ``DecoratedFunctionTool.stream``
    # dispatches ``inspect.iscoroutinefunction`` tools through ``await``
    # and routes everything else through ``asyncio.to_thread`` (see
    # ``strands/tools/decorator.py``). A sync wrapper around an async
    # HTTP call would ship a ``<coroutine object ...>`` repr to the model
    # instead of the search results — the 2026-06-07 dev-environment bug
    # this shape exists to prevent.
    #
    # ``_resolve_exa_api_key`` can raise for multiple reasons
    # (``KeyError`` when ``CHANNEL_EXA_API_KEY_PARAM`` is unset in local
    # dev, ``botocore`` ``ClientError`` for SSM failures, network errors
    # during boto3 client init, etc.). The bare ``Exception`` catch is
    # intentional: the contract requires a stable ``error_type`` the SPA
    # can render distinctly rather than bubbling raw exceptions through
    # the chassis. The key stays in a local and is never written back to
    # ``os.environ`` — a warm Lambda shares that mapping across
    # concurrent invocations, so the SDK-era assignment was a latent
    # cross-request bug (#269).
    try:
        api_key = _resolve_exa_api_key()
    except Exception as exc:
        logger.warning("web_search.config_error %r", exc)
        return _error_result("missing_key")
    clamped = max(_NUM_RESULTS_MIN, min(num_results, _NUM_RESULTS_MAX))
    # Field names, the ``type: "auto"`` default, the nested ``contents``
    # block and the drop-``None``-filters step all mirror what
    # ``strands_tools.exa.exa_search`` sent, so Exa sees the same request
    # it did before #269.
    payload: dict[str, Any] = {
        "query": query,
        "type": "auto",
        "numResults": clamped,
        "contents": {
            "text": True,
            "livecrawl": "fallback",
            "livecrawlTimeout": _EXA_LIVECRAWL_TIMEOUT_MS,
        },
    }
    if category is not None:
        payload["category"] = category
    if include_domains is not None:
        payload["includeDomains"] = include_domains
    if exclude_domains is not None:
        payload["excludeDomains"] = exclude_domains

    data, error_type = await _exa_post(
        _EXA_SEARCH_ENDPOINT,
        payload,
        api_key,
        log_prefix="web_search",
        log_context=f"query_len={len(query)}",
    )
    if error_type is not None:
        return _error_result(error_type)
    return _success_result(data)
