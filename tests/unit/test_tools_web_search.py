# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the web_search Exa wrapper (#182)."""

from __future__ import annotations

from unittest.mock import MagicMock


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
