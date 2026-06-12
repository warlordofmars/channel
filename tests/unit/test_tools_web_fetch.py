# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the web_fetch Exa wrapper (#232).

Mirrors ``test_tools_web_search.py`` — same lazy-cache fixture, same
AsyncMock happy-path shape, same httpx-exception error-path mocks, plus
the ``invalid_url`` client-side validation paths that are new to
``web_fetch``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


@pytest.fixture(autouse=True)
def _clear_lazy_caches():
    """Reset the ``_get_exa_get_contents`` / ``_resolve_exa_api_key``
    lru_caches between tests so a real-loaded ``exa_get_contents`` from a
    sibling test can't leak across — monkeypatch unsetting the patched
    return value isn't enough; the cached function object itself needs to
    vanish. ``_resolve_exa_api_key`` lives in ``web_search`` (web_fetch
    imports it so both tools share one warm-pool SSM resolution)."""
    from channel.agents.tools.web_fetch import _get_exa_get_contents
    from channel.agents.tools.web_search import _resolve_exa_api_key

    _get_exa_get_contents.cache_clear()
    _resolve_exa_api_key.cache_clear()
    yield
    _get_exa_get_contents.cache_clear()
    _resolve_exa_api_key.cache_clear()


async def test_web_fetch_returns_contents_on_happy_path(monkeypatch):
    """exa_get_contents returns a normal response → wrapper passes it
    through unchanged so the model gets the extracted page content."""
    fake_response = {
        "status": "success",
        "content": [{"text": "{'results': [{'url': 'https://example.com', 'text': 'Page.'}]}"}],
    }
    fake_exa = AsyncMock(return_value=fake_response)
    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == fake_response


async def test_web_fetch_wraps_url_in_single_element_list(monkeypatch):
    """Exa's contents endpoint takes ``urls: list`` — the wrapper exposes
    a single ``url`` arg (multi-URL fetch is out of scope per #232) and
    wraps it."""
    fake_exa = AsyncMock(return_value={"status": "success", "content": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    await web_fetch(url="https://example.com/page")

    assert fake_exa.call_args.kwargs["urls"] == ["https://example.com/page"]


async def test_web_fetch_forces_text_true_and_livecrawl_fallback(monkeypatch):
    """``text=True`` + ``livecrawl="fallback"`` + a 30_000 ms crawl budget
    are ALWAYS set by the wrapper when no ``max_chars`` cap is given —
    matching ``web_search``'s extraction quality. The timeout assertion is
    load-bearing: Exa's ``livecrawlTimeout`` is in MILLISECONDS, and
    "harmonizing" the constant down to ``web_search``'s ``30`` would be a
    30 ms crawl budget that fails every uncached fetch in production."""
    fake_exa = AsyncMock(return_value={"status": "success", "content": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    await web_fetch(url="https://example.com/page")

    call_kwargs = fake_exa.call_args.kwargs
    assert call_kwargs["text"] is True
    assert call_kwargs["livecrawl"] == "fallback"
    assert call_kwargs["livecrawl_timeout"] == 30_000


async def test_web_fetch_max_chars_maps_to_exa_max_characters(monkeypatch):
    """``max_chars`` becomes Exa's server-side ``text.maxCharacters`` cap —
    no client-side slicing."""
    fake_exa = AsyncMock(return_value={"status": "success", "content": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    await web_fetch(url="https://example.com/page", max_chars=5000)

    assert fake_exa.call_args.kwargs["text"] == {"maxCharacters": 5000}


async def test_web_fetch_clamps_max_chars_low(monkeypatch):
    """Model could ask for 0 (or negative) chars; Exa rejects non-positive
    ``maxCharacters``, so the wrapper clamps to 1."""
    fake_exa = AsyncMock(return_value={"status": "success", "content": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    await web_fetch(url="https://example.com/page", max_chars=0)

    assert fake_exa.call_args.kwargs["text"] == {"maxCharacters": 1}


@pytest.mark.parametrize(
    "bad_url",
    [
        "not a url",
        "ftp://example.com/file.txt",
        "javascript:alert(1)",
        "http://",
        "//example.com/page",
        "http://[::1",  # unclosed IPv6 bracket — urlparse raises ValueError
        # Userinfo URLs: credentials must never transit to Exa —
        # authenticated fetching is out of scope (#232); mirrors
        # mcp.url_guard.validate_mcp_server_url's rule.
        "https://user:pass@example.com/page",
        "https://token@example.com/page",
        # Internal whitespace / control chars: urlparse strips many of
        # them PRE-parse, so without an explicit reject the validator
        # would judge a cleaned string while Exa receives the raw one.
        "https://exam ple.com/page",
        "https://example.com/pa\nth",
        "https://:8080/page",  # port-only authority — netloc truthy, hostname empty
    ],
)
async def test_web_fetch_rejects_invalid_urls(monkeypatch, bad_url):
    """Malformed / non-http(s) / credential-bearing URLs short-circuit to
    ``invalid_url`` BEFORE key resolution or any Exa call — both keys are
    unset here, so a ``missing_key`` result would mean the validation
    order regressed."""
    fake_exa = AsyncMock(return_value={"status": "success", "content": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: fake_exa,
    )
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("STARTER_EXA_API_KEY_PARAM", raising=False)

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url=bad_url)

    assert result == {"status": "error", "content": [{"text": "invalid_url"}]}
    fake_exa.assert_not_called()


async def test_web_fetch_strips_surrounding_whitespace_before_fetching(monkeypatch):
    """User-pasted URLs commonly carry stray whitespace. Python 3.12's
    ``urlparse`` strips it before parsing (so validation already passes),
    but the string handed to Exa must be the CLEANED one — un-stripped,
    ``"https://example.com "`` even keeps the trailing space inside
    ``netloc``. Normalize once at entry (Copilot review, PR #255)."""
    fake_exa = AsyncMock(return_value={"status": "success", "content": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    await web_fetch(url="  https://example.com/page  ")

    assert fake_exa.call_args.kwargs["urls"] == ["https://example.com/page"]


async def test_web_fetch_accepts_uppercase_scheme(monkeypatch):
    """urlparse lowercases the scheme — ``HTTPS://`` is a valid fetch, not
    an ``invalid_url``."""
    fake_exa = AsyncMock(return_value={"status": "success", "content": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: fake_exa,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="HTTPS://EXAMPLE.COM/page")

    assert result == {"status": "success", "content": []}


async def test_web_fetch_returns_missing_key_when_resolve_fails(monkeypatch):
    """When ``_resolve_exa_api_key()`` raises (no env var + no SSM access),
    the wrapper returns the structured error rather than letting the
    exception bubble up through the chassis."""
    fake_exa = AsyncMock(return_value={"status": "success", "content": []})
    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: fake_exa,
    )
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("STARTER_EXA_API_KEY_PARAM", raising=False)

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": "missing_key"}]}
    fake_exa.assert_not_called()


# NOTE on the four httpx error-path tests below: they pin the wrapper's
# defensive exception→token mapping (issue #232 mandates parity with
# web_search's error contract). The installed ``strands_tools.exa``
# currently catches its own network failures internally and returns
# error dicts instead of raising, so this mapping fires only if a
# future SDK bump lets exceptions escape — see the module docstring of
# ``web_fetch.py`` and the follow-up flagged on #232.


async def test_web_fetch_timeout_returns_error_status(monkeypatch):
    async def boom(**_):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: boom,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": "timeout"}]}


async def test_web_fetch_upstream_5xx_returns_error_status(monkeypatch):
    async def boom(**_):
        resp = httpx.Response(503)
        raise httpx.HTTPStatusError("server", request=MagicMock(), response=resp)

    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: boom,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": "upstream_5xx"}]}


async def test_web_fetch_429_returns_rate_limit(monkeypatch):
    async def boom(**_):
        resp = httpx.Response(429)
        raise httpx.HTTPStatusError("throttle", request=MagicMock(), response=resp)

    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: boom,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": "rate_limit"}]}


async def test_web_fetch_4xx_returns_bad_request(monkeypatch):
    async def boom(**_):
        resp = httpx.Response(400)
        raise httpx.HTTPStatusError("bad", request=MagicMock(), response=resp)

    monkeypatch.setattr(
        "channel.agents.tools.web_fetch._get_exa_get_contents",
        lambda: boom,
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_fetch import web_fetch

    result = await web_fetch(url="https://example.com/page")

    assert result == {"status": "error", "content": [{"text": "bad_request"}]}


def test_web_fetch_is_a_strands_tool():
    """Strands' @tool decorator wraps the function as DecoratedFunctionTool
    and attaches tool_spec metadata. Same probe shape as ``web_search``."""
    from channel.agents.tools.web_fetch import web_fetch

    assert hasattr(web_fetch, "tool_spec")


def test_web_fetch_underlying_is_async_so_strands_awaits_it():
    """Parity guard with ``web_search``'s 2026-06-07 coroutine-leak fix.

    ``web_fetch`` must be ``async def`` because
    ``strands_tools.exa.exa_get_contents`` (the function it wraps) is
    itself ``async def``. A sync wrapper would route through Strands'
    ``asyncio.to_thread`` branch and ship a coroutine repr to the model
    instead of the fetched page. See
    ``test_web_search_underlying_is_async_so_strands_awaits_it``."""
    import inspect

    from channel.agents.tools.web_fetch import web_fetch

    underlying = web_fetch._tool_func
    assert inspect.iscoroutinefunction(underlying)


def test_get_exa_get_contents_returns_real_strands_tools_callable():
    """The lazy-loader's whole point is deferring the
    ``strands_tools.exa`` import to first call. Verify it returns the
    real ``exa_get_contents`` symbol — this is the production cold-start
    path that every other test patches around."""
    from strands_tools.exa import exa_get_contents as real_exa_get_contents

    from channel.agents.tools.web_fetch import _get_exa_get_contents

    assert _get_exa_get_contents() is real_exa_get_contents


async def test_invalid_url_error_round_trips_through_translate_event(monkeypatch):
    """End-to-end contract test for the SSE error-type chain, exercising
    the ``invalid_url`` token that is NEW with web_fetch: the wrapper
    returns a ``ToolResult``-shaped dict, Strands' ``@tool`` decorator
    preserves it, and ``translate_event`` extracts the reason string from
    ``content[].text`` — so the SPA renders ``invalid_url`` rather than a
    generic ``tool_failed``."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("STARTER_EXA_API_KEY_PARAM", raising=False)

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.web_fetch import web_fetch

    tool_result = await web_fetch(url="not a url")
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
    assert payload["error_type"] == "invalid_url"
