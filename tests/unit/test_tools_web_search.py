# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the web_search Exa wrapper (#182)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


@pytest.fixture(autouse=True)
def _clear_lazy_caches():
    """Reset the ``_get_exa_search`` / ``_resolve_exa_api_key`` lru_caches
    between tests so a real-loaded ``exa_search`` from a sibling test
    can't leak across — monkeypatch unsetting the patched return value
    isn't enough; the cached function object itself needs to vanish."""
    from channel.agents.tools.web_search import (
        _get_exa_search,
        _resolve_exa_api_key,
    )

    _get_exa_search.cache_clear()
    _resolve_exa_api_key.cache_clear()
    yield
    _get_exa_search.cache_clear()
    _resolve_exa_api_key.cache_clear()


# NOTE on test shape (post-fix for the 2026-06-07 coroutine-leak bug):
#
# ``web_search`` is now ``async def`` because ``strands_tools.exa.exa_search``
# is an ``async def`` function — calling it without ``await`` returns a
# coroutine object that the chassis ships through SSE as if it were the
# tool result. Strands' ``DecoratedFunctionTool.stream`` correctly
# ``await``s ``iscoroutinefunction`` tools (see strands/tools/decorator.py
# stream() dispatch). Tests use ``AsyncMock`` for happy-path mocks so the
# wrapper's ``await`` lands on an awaitable; error-path mocks use plain
# ``async def`` functions that raise synchronously before ``await`` ever
# completes the coroutine. See PR #224 for the dev-deploy SecureString
# fix that landed before this one — the bug only surfaced once dev
# deploys started working.


async def test_web_search_returns_results_on_happy_path(monkeypatch):
    """exa_search returns a normal response → wrapper passes it through
    unchanged so the model gets the structured ``results`` list."""
    fake_response = {
        "results": [
            {
                "title": "Example",
                "url": "https://example.com",
                "snippet": "Some snippet",
                "text": "Full page text here.",
            }
        ]
    }
    fake_exa = AsyncMock(return_value=fake_response)
    monkeypatch.setattr(
        "channel.agents.tools.web_search._get_exa_search",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="latest RAG paper")

    assert result == fake_response


async def test_web_search_passes_text_true_and_livecrawl_fallback_server_side(
    monkeypatch,
):
    """``text=True`` + ``livecrawl="fallback"`` are ALWAYS set by the
    wrapper regardless of caller args — caller can't override them."""
    fake_exa = AsyncMock(return_value={"results": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_search._get_exa_search",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    await web_search(query="anything")

    call_kwargs = fake_exa.call_args.kwargs
    assert call_kwargs["text"] is True
    assert call_kwargs["livecrawl"] == "fallback"


async def test_web_search_passes_query_through(monkeypatch):
    fake_exa = AsyncMock(return_value={"results": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_search._get_exa_search",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    await web_search(query="latest RAG paper", num_results=3)

    assert fake_exa.call_args.kwargs["query"] == "latest RAG paper"
    assert fake_exa.call_args.kwargs["num_results"] == 3


async def test_web_search_timeout_returns_error_status(monkeypatch):
    async def boom(**_):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(
        "channel.agents.tools.web_search._get_exa_search",
        lambda: boom,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="anything")

    assert result == {"status": "error", "content": [{"text": "timeout"}]}


async def test_web_search_upstream_5xx_returns_error_status(monkeypatch):
    async def boom(**_):
        resp = httpx.Response(503)
        raise httpx.HTTPStatusError("server", request=MagicMock(), response=resp)

    monkeypatch.setattr(
        "channel.agents.tools.web_search._get_exa_search",
        lambda: boom,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="anything")

    assert result == {"status": "error", "content": [{"text": "upstream_5xx"}]}


async def test_web_search_429_returns_rate_limit(monkeypatch):
    async def boom(**_):
        resp = httpx.Response(429)
        raise httpx.HTTPStatusError("throttle", request=MagicMock(), response=resp)

    monkeypatch.setattr(
        "channel.agents.tools.web_search._get_exa_search",
        lambda: boom,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="anything")

    assert result == {"status": "error", "content": [{"text": "rate_limit"}]}


async def test_web_search_4xx_returns_bad_request(monkeypatch):
    async def boom(**_):
        resp = httpx.Response(400)
        raise httpx.HTTPStatusError("bad", request=MagicMock(), response=resp)

    monkeypatch.setattr(
        "channel.agents.tools.web_search._get_exa_search",
        lambda: boom,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="anything")

    assert result == {"status": "error", "content": [{"text": "bad_request"}]}


async def test_web_search_empty_results_is_success_not_error(monkeypatch):
    """Exa returns 0 hits → that's a successful search, not an error.
    The model decides whether 'no sources' should change its reply."""
    fake_exa = AsyncMock(return_value={"results": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_search._get_exa_search",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = await web_search(query="something obscure")

    assert result == {"results": []}
    assert "status" not in result


async def test_web_search_clamps_num_results_high(monkeypatch):
    """Model could ask for 100 results; wrapper clamps to 10."""
    fake_exa = AsyncMock(return_value={"results": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_search._get_exa_search",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    await web_search(query="x", num_results=100)
    assert fake_exa.call_args.kwargs["num_results"] == 10


async def test_web_search_clamps_num_results_low(monkeypatch):
    """Model could ask for 0 results; wrapper clamps to 1."""
    fake_exa = AsyncMock(return_value={"results": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_search._get_exa_search",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    await web_search(query="x", num_results=0)
    assert fake_exa.call_args.kwargs["num_results"] == 1


def test_web_search_is_a_strands_tool():
    """Strands' @tool decorator wraps the function as DecoratedFunctionTool
    and attaches tool_spec metadata. Same probe shape as current_time
    in PR-1 Task 4."""
    from channel.agents.tools.web_search import web_search

    assert hasattr(web_search, "tool_spec")


def test_web_search_underlying_is_async_so_strands_awaits_it():
    """REGRESSION GUARD for the 2026-06-07 coroutine-leak bug.

    ``web_search`` must be ``async def`` because
    ``strands_tools.exa.exa_search`` (the function it wraps) is itself
    ``async def``. If a future refactor makes ``web_search`` sync again,
    Strands' ``DecoratedFunctionTool.stream`` will route it through the
    ``asyncio.to_thread`` branch and silently produce coroutine results
    that get shipped to the model as ``<coroutine object ...>`` text.
    The bug surfaced only on the live dev environment (unit tests with
    sync ``MagicMock`` had passed). This guard catches the regression
    at the type-introspection layer."""
    import inspect

    from channel.agents.tools.web_search import web_search

    underlying = web_search._tool_func
    assert inspect.iscoroutinefunction(underlying), (
        "web_search must remain async — strands_tools.exa.exa_search is "
        "async def and calling it without await ships a coroutine object "
        "to the model instead of the resolved search results. See PR-225 "
        "(or whichever fix-PR replaced this fix) for the dev-environment "
        "smoke-test that surfaced the original bug."
    )


def test_get_exa_search_returns_real_strands_tools_callable():
    """The lazy-loader's whole point is deferring the
    ``strands_tools.exa`` import to first call. Verify it returns the
    real ``exa_search`` symbol — this is the production cold-start path
    that every other test patches around."""
    from strands_tools.exa import exa_search as real_exa_search

    from channel.agents.tools.web_search import _get_exa_search

    assert _get_exa_search() is real_exa_search


def test_resolve_exa_api_key_prefers_env_var(monkeypatch):
    """Local dev path: EXA_API_KEY env var short-circuits the SSM call."""
    monkeypatch.setenv("EXA_API_KEY", "ek-from-env")

    from channel.agents.tools.web_search import _resolve_exa_api_key

    _resolve_exa_api_key.cache_clear()

    assert _resolve_exa_api_key() == "ek-from-env"


async def test_web_search_returns_missing_key_when_resolve_fails(monkeypatch):
    """When ``_resolve_exa_api_key()`` raises (no env var + no SSM
    access), the wrapper returns the structured error rather than
    letting the exception bubble up through the chassis."""
    from channel.agents.tools.web_search import _resolve_exa_api_key, web_search

    _resolve_exa_api_key.cache_clear()
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("STARTER_EXA_API_KEY_PARAM", raising=False)

    result = await web_search(query="anything")

    assert result == {"status": "error", "content": [{"text": "missing_key"}]}


async def test_error_result_shape_round_trips_through_translate_event(monkeypatch):
    """End-to-end contract test for the SSE error-type chain.

    The wrapper returns a ``ToolResult``-shaped dict; Strands' ``@tool``
    decorator preserves it (vs re-wrapping a plain ``error_type`` dict
    as a JSON-stringified text block); ``translate_event`` extracts
    the reason string from ``content[].text`` blocks. This test fails
    if any link in that chain breaks — a defensive guard against
    re-introducing the original bug where the wrapper returned
    ``{"status": "error", "error_type": "timeout"}`` and the SPA saw
    ``error_type="tool_failed"`` instead."""

    async def boom(**_):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(
        "channel.agents.tools.web_search._get_exa_search",
        lambda: boom,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.web_search import web_search

    tool_result = await web_search(query="anything")
    # Simulate the ToolResultEvent Strands emits for our tool's return
    # value (toolUseId is required so translate_event doesn't skip).
    sse_event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-abc",
            "status": tool_result["status"],
            "content": tool_result["content"],
        },
    }
    kind, payload = translate_event(sse_event)
    assert kind == "tool_error"
    assert payload["error_type"] == "timeout"
