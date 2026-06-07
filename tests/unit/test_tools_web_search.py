# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the web_search Exa wrapper (#182)."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx


def test_web_search_returns_results_on_happy_path(monkeypatch):
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
    fake_exa = MagicMock(return_value=fake_response)
    monkeypatch.setattr("channel.agents.tools.web_search.exa_search", fake_exa)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = web_search(query="latest RAG paper")

    assert result == fake_response


def test_web_search_passes_text_true_and_livecrawl_fallback_server_side(monkeypatch):
    """``text=True`` + ``livecrawl="fallback"`` are ALWAYS set by the
    wrapper regardless of caller args — caller can't override them."""
    fake_exa = MagicMock(return_value={"results": []})
    monkeypatch.setattr("channel.agents.tools.web_search.exa_search", fake_exa)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    web_search(query="anything")

    call_kwargs = fake_exa.call_args.kwargs
    assert call_kwargs["text"] is True
    assert call_kwargs["livecrawl"] == "fallback"


def test_web_search_passes_query_through(monkeypatch):
    fake_exa = MagicMock(return_value={"results": []})
    monkeypatch.setattr("channel.agents.tools.web_search.exa_search", fake_exa)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    web_search(query="latest RAG paper", num_results=3)

    assert fake_exa.call_args.kwargs["query"] == "latest RAG paper"
    assert fake_exa.call_args.kwargs["num_results"] == 3


def test_web_search_timeout_returns_error_status(monkeypatch):
    def boom(**_):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr("channel.agents.tools.web_search.exa_search", boom)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = web_search(query="anything")

    assert result == {"status": "error", "error_type": "timeout"}


def test_web_search_upstream_5xx_returns_error_status(monkeypatch):
    def boom(**_):
        resp = httpx.Response(503)
        raise httpx.HTTPStatusError("server", request=MagicMock(), response=resp)

    monkeypatch.setattr("channel.agents.tools.web_search.exa_search", boom)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = web_search(query="anything")

    assert result == {"status": "error", "error_type": "upstream_5xx"}


def test_web_search_429_returns_rate_limit(monkeypatch):
    def boom(**_):
        resp = httpx.Response(429)
        raise httpx.HTTPStatusError("throttle", request=MagicMock(), response=resp)

    monkeypatch.setattr("channel.agents.tools.web_search.exa_search", boom)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = web_search(query="anything")

    assert result == {"status": "error", "error_type": "rate_limit"}


def test_web_search_4xx_returns_bad_request(monkeypatch):
    def boom(**_):
        resp = httpx.Response(400)
        raise httpx.HTTPStatusError("bad", request=MagicMock(), response=resp)

    monkeypatch.setattr("channel.agents.tools.web_search.exa_search", boom)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = web_search(query="anything")

    assert result == {"status": "error", "error_type": "bad_request"}


def test_web_search_empty_results_is_success_not_error(monkeypatch):
    """Exa returns 0 hits → that's a successful search, not an error.
    The model decides whether 'no sources' should change its reply."""
    monkeypatch.setattr(
        "channel.agents.tools.web_search.exa_search",
        MagicMock(return_value={"results": []}),
    )
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    result = web_search(query="something obscure")

    assert result == {"results": []}
    assert "error_type" not in result


def test_web_search_clamps_num_results_high(monkeypatch):
    """Model could ask for 100 results; wrapper clamps to 10."""
    fake_exa = MagicMock(return_value={"results": []})
    monkeypatch.setattr("channel.agents.tools.web_search.exa_search", fake_exa)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    web_search(query="x", num_results=100)
    assert fake_exa.call_args.kwargs["num_results"] == 10


def test_web_search_clamps_num_results_low(monkeypatch):
    """Model could ask for 0 results; wrapper clamps to 1."""
    fake_exa = MagicMock(return_value={"results": []})
    monkeypatch.setattr("channel.agents.tools.web_search.exa_search", fake_exa)
    monkeypatch.setenv("EXA_API_KEY", "ek-test")

    from channel.agents.tools.web_search import web_search

    web_search(query="x", num_results=0)
    assert fake_exa.call_args.kwargs["num_results"] == 1


def test_web_search_is_a_strands_tool():
    """Strands' @tool decorator wraps the function as DecoratedFunctionTool
    and attaches tool_spec metadata. Same probe shape as current_time
    in PR-1 Task 4."""
    from channel.agents.tools.web_search import web_search

    assert hasattr(web_search, "tool_spec")


def test_resolve_exa_api_key_prefers_env_var(monkeypatch):
    """Local dev path: EXA_API_KEY env var short-circuits the SSM call."""
    monkeypatch.setenv("EXA_API_KEY", "ek-from-env")

    from channel.agents.tools.web_search import _resolve_exa_api_key

    _resolve_exa_api_key.cache_clear()

    assert _resolve_exa_api_key() == "ek-from-env"


def test_web_search_returns_missing_key_when_resolve_fails(monkeypatch):
    """When ``_resolve_exa_api_key()`` raises (no env var + no SSM
    access), the wrapper returns the structured error rather than
    letting the exception bubble up through the chassis."""
    from channel.agents.tools.web_search import _resolve_exa_api_key, web_search

    _resolve_exa_api_key.cache_clear()
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("STARTER_EXA_API_KEY_PARAM", raising=False)

    result = web_search(query="anything")

    assert result == {"status": "error", "error_type": "missing_key"}
