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


def test_list_servers_returns_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel import storage

    monkeypatch.setattr(storage, "list_mcp_servers_for_user", lambda u: [])
    resp = client.get("/api/mcp/servers")
    assert resp.status_code == 200
    assert resp.json() == {"servers": []}


def test_register_server_runs_dcr_and_returns_auth_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
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
        mcp_auth, "discover_resource_metadata",
        AsyncMock(return_value=ProtectedResourceMetadata(
            authorization_servers=["https://auth.example.com"],
            resource="https://hive.example.com/mcp",
        )),
    )
    monkeypatch.setattr(
        mcp_auth, "discover_auth_server_metadata",
        AsyncMock(return_value=OAuthMetadata(
            issuer="https://auth.example.com",
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            registration_endpoint="https://auth.example.com/register",
            response_types_supported=["code"],
        )),
    )
    monkeypatch.setattr(
        mcp_auth, "register_dynamic_client",
        AsyncMock(return_value=OAuthClientInformationFull(
            client_id="dcr-new",
            redirect_uris=["https://channel.example.com/auth/mcp/callback"],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        )),
    )
    monkeypatch.setattr(
        "channel.api.mcp.state_store.put_state",
        lambda *_a, **_kw: None,
    )

    created: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> MCPServer:
        created.update(kwargs)
        return MCPServer(
            server_id="srv-1", user_id="user-1", name=kwargs["name"],
            url=kwargs["url"], client_id=kwargs["client_id"],
            tool_prefix=kwargs["tool_prefix"],
            auth_status=MCPServerAuthStatus.NEVER_AUTHED,
            created_at="x", updated_at="x",
        )

    monkeypatch.setattr(storage, "create_mcp_server", fake_create)

    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI", "https://channel.example.com/auth/mcp/callback",
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
) -> None:
    from mcp.shared.auth import OAuthMetadata, OAuthToken, ProtectedResourceMetadata

    from channel import storage
    from channel.auth import state_store
    from channel.mcp import auth as mcp_auth
    from channel.mcp import crypto
    from channel.models import MCPServer, MCPServerAuthStatus

    monkeypatch.setattr(
        state_store, "consume_state",
        lambda _: {
            "purpose": "mcp",
            "user_id": "u1",
            "server_id": "srv-1",
            "code_verifier": "verifier",
            "redirect_uri": "https://channel.example.com/auth/mcp/callback",
        },
    )
    monkeypatch.setattr(
        storage, "get_mcp_server",
        lambda **_: MCPServer(
            server_id="srv-1", user_id="u1", name="Hive",
            url="https://hive.example.com/mcp",
            client_id="dcr-1", tool_prefix="hive",
            auth_status=MCPServerAuthStatus.NEVER_AUTHED,
            created_at="x", updated_at="x",
        ),
    )
    monkeypatch.setattr(
        mcp_auth, "discover_resource_metadata",
        AsyncMock(return_value=ProtectedResourceMetadata(
            authorization_servers=["https://auth.example.com"],
            resource="https://hive.example.com/mcp",
        )),
    )
    monkeypatch.setattr(
        mcp_auth, "discover_auth_server_metadata",
        AsyncMock(return_value=OAuthMetadata(
            issuer="https://auth.example.com",
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            response_types_supported=["code"],
        )),
    )
    monkeypatch.setattr(
        mcp_auth, "exchange_code",
        AsyncMock(return_value=OAuthToken(
            access_token="acc-1", refresh_token="ref-1",
            expires_in=3600, token_type="Bearer", scope="read",
        )),
    )
    monkeypatch.setattr(crypto, "encrypt_blob", lambda s: ("ENC::" + s).encode())
    persisted: dict[str, Any] = {}
    monkeypatch.setattr(storage, "put_mcp_token", lambda **kw: persisted.update(kw))
    flipped: dict[str, Any] = {}
    monkeypatch.setattr(
        storage, "set_mcp_server_auth_status",
        lambda **kw: flipped.update(kw),
    )

    raw_client = TestClient(app)
    resp = raw_client.get(
        "/auth/mcp/callback?state=ok&code=auth-code", follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    assert "mcp_authed=ok" in resp.headers["location"]
    assert "server_id=srv-1" in resp.headers["location"]
    assert persisted["access_token_ciphertext"] == b"ENC::acc-1"
    assert flipped["status"] == MCPServerAuthStatus.ACTIVE
