# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for MCP DCR + token-endpoint helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from channel.mcp import auth as mcp_auth


@pytest.fixture
def fake_async_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Stub httpx.AsyncClient so we can intercept the requests this module makes.

    Returns a dict of named mocks so individual tests can configure responses."""
    posts: list[dict[str, Any]] = []
    gets: list[str] = []
    responses: list[httpx.Response] = []

    class _StubClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_StubClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get(self, url: str, **kwargs: Any) -> httpx.Response:
            gets.append(url)
            return responses.pop(0)

        async def post(self, url: str, **kwargs: Any) -> httpx.Response:
            posts.append({"url": url, **kwargs})
            return responses.pop(0)

    monkeypatch.setattr(mcp_auth.httpx, "AsyncClient", _StubClient)
    return {"posts": posts, "gets": gets, "responses": responses}


def _ok_json(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body, request=httpx.Request("GET", "https://x"))


@pytest.mark.asyncio
async def test_discover_resource_metadata(fake_async_client: dict[str, Any]) -> None:
    fake_async_client["responses"].append(
        _ok_json({
            "authorization_servers": ["https://hive.example.com"],
            "resource": "https://hive.example.com/mcp",
        })
    )
    meta = await mcp_auth.discover_resource_metadata("https://hive.example.com/mcp")
    # pydantic AnyHttpUrl normalises with a trailing slash; compare as strings.
    assert [str(u) for u in meta.authorization_servers] == ["https://hive.example.com/"]


@pytest.mark.asyncio
async def test_register_dynamic_client(fake_async_client: dict[str, Any]) -> None:
    fake_async_client["responses"].append(
        _ok_json({
            "client_id": "dcr-issued-id",
            "redirect_uris": ["https://channel.example.com/auth/mcp/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })
    )
    info = await mcp_auth.register_dynamic_client(
        registration_endpoint="https://auth.example.com/register",
        redirect_uri="https://channel.example.com/auth/mcp/callback",
        client_name="Channel",
    )
    assert info.client_id == "dcr-issued-id"


def test_build_authorization_url() -> None:
    url = mcp_auth.build_authorization_url(
        authorization_endpoint="https://auth.example.com/authorize",
        client_id="dcr-1",
        redirect_uri="https://channel.example.com/auth/mcp/callback",
        state="opaque-state",
        code_challenge="challenge",
        scopes=["read", "write"],
    )
    assert url.startswith("https://auth.example.com/authorize?")
    assert "client_id=dcr-1" in url
    assert "code_challenge=challenge" in url
    assert "code_challenge_method=S256" in url
    assert "state=opaque-state" in url
    assert "scope=read+write" in url
    assert "response_type=code" in url


@pytest.mark.asyncio
async def test_exchange_code(fake_async_client: dict[str, Any]) -> None:
    fake_async_client["responses"].append(
        _ok_json({
            "access_token": "acc-1",
            "refresh_token": "ref-1",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "read",
        })
    )
    tok = await mcp_auth.exchange_code(
        token_endpoint="https://auth.example.com/token",
        client_id="dcr-1",
        redirect_uri="https://channel.example.com/auth/mcp/callback",
        code="auth-code",
        code_verifier="verifier",
    )
    assert tok.access_token == "acc-1"
    assert tok.refresh_token == "ref-1"
    assert tok.expires_in == 3600


@pytest.mark.asyncio
async def test_refresh_token(fake_async_client: dict[str, Any]) -> None:
    fake_async_client["responses"].append(
        _ok_json({
            "access_token": "acc-2",
            "expires_in": 3600,
            "token_type": "Bearer",
        })
    )
    tok = await mcp_auth.refresh_token(
        token_endpoint="https://auth.example.com/token",
        client_id="dcr-1",
        refresh_token="ref-1",
    )
    assert tok.access_token == "acc-2"


def test_generate_pkce_returns_43_to_128_chars() -> None:
    verifier, challenge = mcp_auth.generate_pkce()
    assert 43 <= len(verifier) <= 128
    assert 43 <= len(challenge) <= 128
    assert verifier != challenge
