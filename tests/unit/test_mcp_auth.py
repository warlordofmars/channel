# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for MCP DCR + token-endpoint helpers."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from channel import storage
from channel.mcp import auth as mcp_auth
from channel.models import MCPServer, MCPServerAuthStatus


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

        async def __aenter__(self) -> _StubClient:
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
        _ok_json(
            {
                "authorization_servers": ["https://hive.example.com"],
                "resource": "https://hive.example.com/mcp",
            }
        )
    )
    meta = await mcp_auth.discover_resource_metadata("https://hive.example.com/mcp")
    # pydantic AnyHttpUrl normalises with a trailing slash; compare as strings.
    assert [str(u) for u in meta.authorization_servers] == ["https://hive.example.com/"]


@pytest.mark.asyncio
async def test_discover_auth_server_metadata(fake_async_client: dict[str, Any]) -> None:
    fake_async_client["responses"].append(
        _ok_json(
            {
                "issuer": "https://auth.example.com",
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
                "response_types_supported": ["code"],
            }
        )
    )
    meta = await mcp_auth.discover_auth_server_metadata("https://auth.example.com")
    assert str(meta.token_endpoint) == "https://auth.example.com/token"


@pytest.mark.asyncio
async def test_register_dynamic_client(fake_async_client: dict[str, Any]) -> None:
    fake_async_client["responses"].append(
        _ok_json(
            {
                "client_id": "dcr-issued-id",
                "redirect_uris": ["https://channel.example.com/auth/mcp/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            }
        )
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
        _ok_json(
            {
                "access_token": "acc-1",
                "refresh_token": "ref-1",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "read",
            }
        )
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
        _ok_json(
            {
                "access_token": "acc-2",
                "expires_in": 3600,
                "token_type": "Bearer",
            }
        )
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


# ----------------------------------------------------------------
# get_valid_access_token (#207 Task 6)
# ----------------------------------------------------------------


class _FakeTable:
    """Minimal in-memory table for storage helpers."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, Item: dict[str, Any]) -> None:
        self.items[(Item["PK"], Item["SK"])] = Item

    def get_item(self, Key: dict[str, Any]) -> dict[str, Any]:
        it = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": it} if it else {}


@pytest.fixture
def storage_table(monkeypatch: pytest.MonkeyPatch) -> _FakeTable:
    table = _FakeTable()
    monkeypatch.setattr(storage, "_get_table", lambda: table)
    return table


@pytest.fixture
def fake_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.mcp import crypto

    monkeypatch.setattr(crypto, "encrypt_blob", lambda s: ("ENC::" + s).encode())
    monkeypatch.setattr(
        crypto,
        "decrypt_blob",
        lambda b: b.decode("utf-8").removeprefix("ENC::"),
    )


@pytest.mark.asyncio
async def test_get_valid_access_token_returns_cached_when_not_expiring(
    storage_table: _FakeTable, fake_crypto: None
) -> None:
    storage.put_mcp_token(
        user_id="u1",
        server_id="s1",
        access_token_ciphertext=b"ENC::acc-cached",
        refresh_token_ciphertext=b"ENC::ref-1",
        expires_at=int(time.time()) + 600,  # well above the 60s skew window
        granted_scope="read",
    )
    token = await mcp_auth.get_valid_access_token(
        user_id="u1",
        server=MCPServer(
            server_id="s1",
            user_id="u1",
            name="Hive",
            url="https://hive.example.com/mcp",
            client_id="dcr-1",
            tool_prefix="hive",
            auth_status=MCPServerAuthStatus.ACTIVE,
            created_at="x",
            updated_at="x",
        ),
    )
    assert token == "acc-cached"


@pytest.mark.asyncio
async def test_get_valid_access_token_refreshes_when_expiring(
    storage_table: _FakeTable, fake_crypto: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage.put_mcp_token(
        user_id="u1",
        server_id="s1",
        access_token_ciphertext=b"ENC::stale",
        refresh_token_ciphertext=b"ENC::ref-1",
        expires_at=int(time.time()) + 30,  # within the 60s refresh skew window
        granted_scope="read",
    )

    async def fake_discover(_url: str) -> Any:
        from mcp.shared.auth import ProtectedResourceMetadata

        return ProtectedResourceMetadata(
            authorization_servers=["https://auth.example.com"],
            resource="https://hive.example.com/mcp",
        )

    async def fake_discover_as(_url: str) -> Any:
        from mcp.shared.auth import OAuthMetadata

        return OAuthMetadata(
            issuer="https://auth.example.com",
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            response_types_supported=["code"],
        )

    async def fake_refresh(**_: Any) -> Any:
        from mcp.shared.auth import OAuthToken

        return OAuthToken(
            access_token="fresh-acc",
            refresh_token="fresh-ref",
            expires_in=3600,
            token_type="Bearer",
        )

    monkeypatch.setattr(mcp_auth, "discover_resource_metadata", fake_discover)
    monkeypatch.setattr(mcp_auth, "discover_auth_server_metadata", fake_discover_as)
    monkeypatch.setattr(mcp_auth, "refresh_token", fake_refresh)

    token = await mcp_auth.get_valid_access_token(
        user_id="u1",
        server=MCPServer(
            server_id="s1",
            user_id="u1",
            name="Hive",
            url="https://hive.example.com/mcp",
            client_id="dcr-1",
            tool_prefix="hive",
            auth_status=MCPServerAuthStatus.ACTIVE,
            created_at="x",
            updated_at="x",
        ),
    )
    assert token == "fresh-acc"
    # Confirm the refreshed token landed back in storage.
    refreshed = storage.get_mcp_token(user_id="u1", server_id="s1")
    assert refreshed is not None
    assert refreshed.access_token_ciphertext == b"ENC::fresh-acc"


@pytest.mark.asyncio
async def test_get_valid_access_token_raises_when_no_token_row(
    storage_table: _FakeTable, fake_crypto: None
) -> None:
    """No token row in DDB → MCPAuthFailedError."""
    from channel.mcp.auth import MCPAuthFailedError

    server = MCPServer(
        server_id="s1",
        user_id="u1",
        name="Hive",
        url="https://hive.example.com/mcp",
        client_id="dcr-1",
        tool_prefix="hive",
        auth_status=MCPServerAuthStatus.ACTIVE,
        created_at="x",
        updated_at="x",
    )
    with pytest.raises(MCPAuthFailedError, match="no token row"):
        await mcp_auth.get_valid_access_token(user_id="u1", server=server)


@pytest.mark.asyncio
async def test_get_valid_access_token_raises_when_expired_no_refresh(
    storage_table: _FakeTable, fake_crypto: None
) -> None:
    """Token within skew window AND no refresh token → MCPAuthFailedError."""
    from channel.mcp.auth import MCPAuthFailedError

    storage.put_mcp_token(
        user_id="u1",
        server_id="s1",
        access_token_ciphertext=b"ENC::stale",
        refresh_token_ciphertext=None,
        expires_at=int(time.time()) + 30,
        granted_scope="read",
    )
    server = MCPServer(
        server_id="s1",
        user_id="u1",
        name="Hive",
        url="https://hive.example.com/mcp",
        client_id="dcr-1",
        tool_prefix="hive",
        auth_status=MCPServerAuthStatus.ACTIVE,
        created_at="x",
        updated_at="x",
    )
    with pytest.raises(MCPAuthFailedError, match="no refresh token"):
        await mcp_auth.get_valid_access_token(user_id="u1", server=server)


@pytest.mark.asyncio
async def test_get_valid_access_token_raises_on_http_failure(
    storage_table: _FakeTable, fake_crypto: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refresh round-trip raises httpx.HTTPError → MCPAuthFailedError."""
    from channel.mcp.auth import MCPAuthFailedError

    storage.put_mcp_token(
        user_id="u1",
        server_id="s1",
        access_token_ciphertext=b"ENC::stale",
        refresh_token_ciphertext=b"ENC::ref-1",
        expires_at=int(time.time()) + 30,
        granted_scope="read",
    )

    async def fake_discover(_url: str) -> Any:
        raise httpx.ConnectError("upstream unreachable")

    monkeypatch.setattr(mcp_auth, "discover_resource_metadata", fake_discover)

    server = MCPServer(
        server_id="s1",
        user_id="u1",
        name="Hive",
        url="https://hive.example.com/mcp",
        client_id="dcr-1",
        tool_prefix="hive",
        auth_status=MCPServerAuthStatus.ACTIVE,
        created_at="x",
        updated_at="x",
    )
    with pytest.raises(MCPAuthFailedError, match="refresh round-trip"):
        await mcp_auth.get_valid_access_token(user_id="u1", server=server)
