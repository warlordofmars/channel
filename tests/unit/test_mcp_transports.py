# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the authenticated transport thunk."""

from __future__ import annotations

from typing import Any

import pytest

from channel.mcp.transports import make_authenticated_transport


def test_thunk_returns_streamable_http_context_manager() -> None:
    """The thunk wraps mcp.client.streamable_http.streamable_http_client.

    Strands' MCPClient calls the thunk and ``async with``-es the result;
    that result must be the async-context-manager from mcp's transport
    module. We can't actually open the connection in a unit test (no
    real server) but we can verify the call shape."""
    thunk = make_authenticated_transport(
        server_url="https://hive.example.com/mcp",
        access_token="bearer-abc",
    )
    ctx = thunk()
    # The mcp lib's streamable_http_client returns an @asynccontextmanager
    # decorated function. Calling it yields an _AsyncGeneratorContextManager.
    assert hasattr(ctx, "__aenter__")
    assert hasattr(ctx, "__aexit__")


def test_thunk_attaches_authorization_header(monkeypatch: Any) -> None:
    """The httpx.AsyncClient handed to streamable_http_client carries the
    Authorization header for every request the MCP transport makes."""
    captured: dict[str, Any] = {}

    class _FakeAsyncClient:
        def __init__(
            self, *args: Any, headers: dict[str, str] | None = None, **kwargs: Any
        ) -> None:
            captured["headers"] = headers

    from channel.mcp import transports

    monkeypatch.setattr(transports.httpx, "AsyncClient", _FakeAsyncClient)
    thunk = transports.make_authenticated_transport(
        server_url="https://hive.example.com/mcp",
        access_token="bearer-abc",
    )
    # Calling the thunk constructs the AsyncClient internally.
    thunk()
    assert captured["headers"] == {"Authorization": "Bearer bearer-abc"}


def test_thunk_disables_redirects_and_sets_timeout(monkeypatch: Any) -> None:
    """SSRF defense: a 302 from a registered MCP server to a different
    host (e.g. 169.254.169.254) must not be silently followed by the
    transport. Timeout is explicit (matches the OAuth helpers in
    channel.mcp.auth) so chassis behavior doesn't depend on httpx
    defaults."""
    captured: dict[str, Any] = {}

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

    from channel.mcp import transports

    monkeypatch.setattr(transports.httpx, "AsyncClient", _FakeAsyncClient)
    thunk = transports.make_authenticated_transport(
        server_url="https://hive.example.com/mcp",
        access_token="bearer-abc",
    )
    thunk()
    assert captured["follow_redirects"] is False
    # Float comparison via pytest.approx — Sonar python:S1244 rightly
    # flags raw == on floats.
    assert captured["timeout"] == pytest.approx(30.0)
