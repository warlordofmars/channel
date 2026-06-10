# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api/mcp/* + /auth/mcp/callback surface.

Mirrors the dependency-override pattern in ``tests/unit/test_chats_api.py``
— ``require_mgmt_user`` returns a stub claims dict, and all DDB / OAuth
boundaries are monkeypatched at the module seam."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from channel.api._auth import require_mgmt_user
from channel.api.main import app


@pytest.fixture
def client() -> TestClient:
    def _stub_user() -> dict[str, Any]:
        return {"sub": "user-1", "role": "user"}

    app.dependency_overrides[require_mgmt_user] = _stub_user
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub socket.getaddrinfo so test hostnames resolve to a public IP.

    Without this, the SSRF validator runs real DNS and ``hive.example.com``
    may not resolve, which would 400 every register/callback test."""
    import socket as _socket

    def fake_getaddrinfo(host: str, _port: int | None, *_a: Any, **_kw: Any) -> Any:
        # 8.8.8.8 — globally-routable IPv4 not in any blocked range.
        return [(2, 1, 6, "", ("8.8.8.8", 0))]

    monkeypatch.setattr(_socket, "getaddrinfo", fake_getaddrinfo)


def test_list_servers_returns_empty(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel import storage

    monkeypatch.setattr(storage, "list_mcp_servers_for_user", lambda u: [])
    resp = client.get("/api/mcp/servers")
    assert resp.status_code == 200
    assert resp.json() == {"servers": []}


def test_register_server_runs_dcr_and_returns_auth_url(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    from mcp.shared.auth import (
        OAuthClientInformationFull,
        OAuthMetadata,
        ProtectedResourceMetadata,
    )

    from channel import storage
    from channel.mcp import auth as mcp_auth
    from channel.models import MCPServer, MCPServerAuthStatus

    monkeypatch.setattr(
        mcp_auth,
        "discover_resource_metadata",
        AsyncMock(
            return_value=ProtectedResourceMetadata(
                authorization_servers=["https://auth.example.com"],
                resource="https://hive.example.com/mcp",
            )
        ),
    )
    monkeypatch.setattr(
        mcp_auth,
        "discover_auth_server_metadata",
        AsyncMock(
            return_value=OAuthMetadata(
                issuer="https://auth.example.com",
                authorization_endpoint="https://auth.example.com/authorize",
                token_endpoint="https://auth.example.com/token",
                registration_endpoint="https://auth.example.com/register",
                response_types_supported=["code"],
            )
        ),
    )
    monkeypatch.setattr(
        mcp_auth,
        "register_dynamic_client",
        AsyncMock(
            return_value=OAuthClientInformationFull(
                client_id="dcr-new",
                redirect_uris=["https://channel.example.com/auth/mcp/callback"],
                token_endpoint_auth_method="none",
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
            )
        ),
    )
    monkeypatch.setattr(
        "channel.api.mcp.state_store.put_state",
        lambda *_a, **_kw: None,
    )

    created: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> MCPServer:
        created.update(kwargs)
        return MCPServer(
            server_id="srv-1",
            user_id="user-1",
            name=kwargs["name"],
            url=kwargs["url"],
            client_id=kwargs["client_id"],
            tool_prefix=kwargs["tool_prefix"],
            auth_status=MCPServerAuthStatus.NEVER_AUTHED,
            created_at="x",
            updated_at="x",
        )

    monkeypatch.setattr(storage, "create_mcp_server", fake_create)

    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI",
        "https://channel.example.com/auth/mcp/callback",
    )

    resp = client.post(
        "/api/mcp/servers",
        json={"name": "Hive", "url": "https://hive.example.com/mcp", "tool_prefix": "hive"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["server_id"] == "srv-1"
    assert body["auth_start_url"].startswith("https://auth.example.com/authorize?")
    assert created["client_id"] == "dcr-new"


def test_register_rejects_userinfo_in_url(
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "https://user:pass@hive.example.com/mcp"},
    )
    assert resp.status_code == 400
    assert "credentials" in resp.json()["detail"]


def test_register_rejects_http_when_not_localhost(
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "http://hive.example.com/mcp"},
    )
    assert resp.status_code == 400
    assert "https" in resp.json()["detail"]


def test_register_rejects_link_local_metadata_ip(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block the AWS IMDS at 169.254.169.254 — the SSRF threat that the
    URL validator exists to prevent."""
    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI",
        "https://channel.example.com/auth/mcp/callback",
    )
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "https://169.254.169.254/mcp"},
    )
    assert resp.status_code == 400
    assert "blocked" in resp.json()["detail"]


def test_register_rejects_loopback_when_carve_out_disabled(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STARTER_MCP_ALLOW_LOCALHOST", raising=False)
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "http://localhost:9000/mcp"},
    )
    assert resp.status_code == 400


def test_register_rejects_when_dns_resolves_to_private(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a non-loopback hostname is blocked if DNS resolves to a
    private address."""
    import socket as _socket

    def fake_getaddrinfo(host: str, _port: int | None, *_a: Any, **_kw: Any) -> Any:
        # AF_INET, SOCK_STREAM, IPPROTO_TCP, '', (addr, port)
        return [(2, 1, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(_socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI",
        "https://channel.example.com/auth/mcp/callback",
    )
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "https://internal.example.com/mcp"},
    )
    assert resp.status_code == 400
    assert "blocked" in resp.json()["detail"]


def test_register_allows_localhost_carve_out_when_flag_set(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local dev still works behind the explicit STARTER_MCP_ALLOW_LOCALHOST=1."""
    from channel.api.mcp import _validate_mcp_server_url

    monkeypatch.setenv("STARTER_MCP_ALLOW_LOCALHOST", "1")
    # Direct call; the carve-out only suppresses HTTPException — no fetch.
    _validate_mcp_server_url("http://localhost:9000/mcp")


def test_callback_with_invalid_state_redirects_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callback route is unauthenticated — no dependency override needed."""
    from channel.auth import state_store

    monkeypatch.setattr(state_store, "consume_state", lambda _: None)
    raw_client = TestClient(app)
    resp = raw_client.get("/auth/mcp/callback?state=bogus&code=abc", follow_redirects=False)
    assert resp.status_code == 302
    assert "mcp_authed=error" in resp.headers["location"]
    assert "reason=invalid_state" in resp.headers["location"]


def test_callback_happy_path_persists_token_and_flips_status(
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    from mcp.shared.auth import OAuthMetadata, OAuthToken, ProtectedResourceMetadata

    from channel import storage
    from channel.auth import state_store
    from channel.mcp import auth as mcp_auth
    from channel.mcp import crypto
    from channel.models import MCPServer, MCPServerAuthStatus

    monkeypatch.setattr(
        state_store,
        "consume_state",
        lambda _: {
            "purpose": "mcp",
            "user_id": "u1",
            "server_id": "srv-1",
            "code_verifier": "verifier",
            "redirect_uri": "https://channel.example.com/auth/mcp/callback",
        },
    )
    monkeypatch.setattr(
        storage,
        "get_mcp_server",
        lambda **_: MCPServer(
            server_id="srv-1",
            user_id="u1",
            name="Hive",
            url="https://hive.example.com/mcp",
            client_id="dcr-1",
            tool_prefix="hive",
            auth_status=MCPServerAuthStatus.NEVER_AUTHED,
            created_at="x",
            updated_at="x",
        ),
    )
    monkeypatch.setattr(
        mcp_auth,
        "discover_resource_metadata",
        AsyncMock(
            return_value=ProtectedResourceMetadata(
                authorization_servers=["https://auth.example.com"],
                resource="https://hive.example.com/mcp",
            )
        ),
    )
    monkeypatch.setattr(
        mcp_auth,
        "discover_auth_server_metadata",
        AsyncMock(
            return_value=OAuthMetadata(
                issuer="https://auth.example.com",
                authorization_endpoint="https://auth.example.com/authorize",
                token_endpoint="https://auth.example.com/token",
                response_types_supported=["code"],
            )
        ),
    )
    monkeypatch.setattr(
        mcp_auth,
        "exchange_code",
        AsyncMock(
            return_value=OAuthToken(
                access_token="acc-1",
                refresh_token="ref-1",
                expires_in=3600,
                token_type="Bearer",
                scope="read",
            )
        ),
    )
    monkeypatch.setattr(crypto, "encrypt_blob", lambda s: ("ENC::" + s).encode())
    persisted: dict[str, Any] = {}
    monkeypatch.setattr(storage, "put_mcp_token", lambda **kw: persisted.update(kw))
    flipped: dict[str, Any] = {}
    monkeypatch.setattr(
        storage,
        "set_mcp_server_auth_status",
        lambda **kw: flipped.update(kw),
    )

    raw_client = TestClient(app)
    resp = raw_client.get(
        "/auth/mcp/callback?state=ok&code=auth-code",
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    assert "mcp_authed=ok" in resp.headers["location"]
    assert "server_id=srv-1" in resp.headers["location"]
    assert persisted["access_token_ciphertext"] == b"ENC::acc-1"
    assert flipped["status"] == MCPServerAuthStatus.ACTIVE
