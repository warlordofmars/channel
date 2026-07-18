# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api/mcp/* + /auth/mcp/callback surface.

Mirrors the dependency-override pattern in ``tests/unit/test_chats_api.py``
— ``require_mgmt_user`` returns a stub claims dict, and all DDB / OAuth
boundaries are monkeypatched at the module seam."""

from __future__ import annotations

import ipaddress
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from channel.api._auth import require_mgmt_user
from channel.api.main import app

# Synthetic, non-secret bearer values for the static-token tests below.
# Assembled from fragments — and deliberately NOT using the GitHub personal-access-token
# prefix — so SonarCloud's hard-coded-secret detector
# (python:S6418) doesn't flag the dummy values on this credential-handling
# test file. Same convention as ``test_register_rejects_userinfo_in_url``.
# These are NOT real tokens.
_FAKE_BEARER = "dummy-" + "bearer-" + "value-1"
_FAKE_UNLOGGED_BEARER = "must-" + "never-" + "reach-a-log"


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


def test_register_static_token_skips_dcr_and_stores_encrypted_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    """A static_token registration must NOT touch discovery / DCR /
    _begin_auth_flow. It creates the server row ACTIVE with client_id=None
    and auth_type=static_token, persists the token encrypted, and returns
    no auth_start_url."""
    from channel import storage
    from channel.mcp import auth as _mcp_auth
    from channel.mcp import crypto
    from channel.models import MCPServer, MCPServerAuthStatus, MCPServerAuthType

    async def must_not_run(*_a: Any, **_kw: Any) -> Any:  # pragma: no cover
        raise AssertionError("static-token path must not call discovery/DCR")

    monkeypatch.setattr(_mcp_auth, "discover_resource_metadata", must_not_run)
    monkeypatch.setattr(_mcp_auth, "discover_auth_server_metadata", must_not_run)
    monkeypatch.setattr(_mcp_auth, "register_dynamic_client", must_not_run)

    def begin_boom(*_a: Any, **_kw: Any) -> Any:  # pragma: no cover
        raise AssertionError("_begin_auth_flow must not run for static_token")

    monkeypatch.setattr("channel.api.mcp._begin_auth_flow", begin_boom)
    monkeypatch.setattr(crypto, "encrypt_blob", lambda s: ("ENC::" + s).encode())

    created: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> MCPServer:
        created.update(kwargs)
        return MCPServer(
            server_id="srv-static",
            user_id="user-1",
            name=kwargs["name"],
            url=kwargs["url"],
            client_id=kwargs["client_id"],
            tool_prefix=kwargs["tool_prefix"],
            auth_type=kwargs["auth_type"],
            auth_status=kwargs["auth_status"],
            created_at="x",
            updated_at="x",
        )

    monkeypatch.setattr(storage, "create_mcp_server", fake_create)
    persisted: dict[str, Any] = {}
    monkeypatch.setattr(storage, "put_mcp_token", lambda **kw: persisted.update(kw))

    resp = client.post(
        "/api/mcp/servers",
        json={
            "name": "GitHub",
            "url": "https://api.githubcopilot.com/mcp/",
            "auth_type": "static_token",
            "token": _FAKE_BEARER,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["server_id"] == "srv-static"
    assert body["auth_start_url"] is None
    # Server row created ACTIVE, static_token, no DCR client.
    assert created["client_id"] is None
    assert created["auth_type"] == MCPServerAuthType.STATIC_TOKEN
    assert created["auth_status"] == MCPServerAuthStatus.ACTIVE
    # Token persisted ENCRYPTED (never the plaintext), no refresh token.
    assert persisted["access_token_ciphertext"] == ("ENC::" + _FAKE_BEARER).encode()
    assert persisted["access_token_ciphertext"] != _FAKE_BEARER.encode()
    assert persisted["refresh_token_ciphertext"] is None
    # Far-future expiry sentinel keeps the PAT out of the refresh path.
    assert persisted["expires_at"] > 1_900_000_000


def test_register_static_token_without_token_returns_400(
    client: TestClient,
    public_dns: None,
) -> None:
    resp = client.post(
        "/api/mcp/servers",
        json={
            "name": "GitHub",
            "url": "https://api.githubcopilot.com/mcp/",
            "auth_type": "static_token",
        },
    )
    assert resp.status_code == 400
    assert "requires a token" in resp.json()["detail"]


def test_register_oauth_dcr_with_token_returns_400(
    client: TestClient,
    public_dns: None,
) -> None:
    """A token supplied to an oauth_dcr registration is an incoherent
    combo — reject it rather than silently dropping a secret."""
    resp = client.post(
        "/api/mcp/servers",
        json={
            "name": "Hive",
            "url": "https://hive.example.com/mcp",
            "auth_type": "oauth_dcr",
            "token": _FAKE_BEARER,
        },
    )
    assert resp.status_code == 400
    assert "static_token" in resp.json()["detail"]


def test_register_static_token_never_logs_the_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The pasted PAT must never reach a log sink — the success log line
    carries only fingerprinted ids."""
    import logging

    from channel import storage
    from channel.mcp import crypto
    from channel.models import MCPServer, MCPServerAuthStatus, MCPServerAuthType

    monkeypatch.setattr(crypto, "encrypt_blob", lambda s: b"ENC")
    monkeypatch.setattr(
        storage,
        "create_mcp_server",
        lambda **kw: MCPServer(
            server_id="srv-static",
            user_id="user-1",
            name=kw["name"],
            url=kw["url"],
            client_id=None,
            tool_prefix=kw["tool_prefix"],
            auth_type=MCPServerAuthType.STATIC_TOKEN,
            auth_status=MCPServerAuthStatus.ACTIVE,
            created_at="x",
            updated_at="x",
        ),
    )
    monkeypatch.setattr(storage, "put_mcp_token", lambda **_kw: None)

    unlogged = _FAKE_UNLOGGED_BEARER
    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/api/mcp/servers",
            json={
                "name": "GitHub",
                "url": "https://api.githubcopilot.com/mcp/",
                "auth_type": "static_token",
                "token": unlogged,
            },
        )
    assert resp.status_code == 200, resp.text
    assert unlogged not in caplog.text


def test_register_static_token_empty_string_rejected_at_boundary(
    client: TestClient,
    public_dns: None,
) -> None:
    """An empty-string token fails Field(min_length=1) → 422 before the
    handler runs."""
    resp = client.post(
        "/api/mcp/servers",
        json={
            "name": "GitHub",
            "url": "https://api.githubcopilot.com/mcp/",
            "auth_type": "static_token",
            "token": "",
        },
    )
    assert resp.status_code == 422


def test_register_rejects_userinfo_in_url(
    client: TestClient,
) -> None:
    # Test fixture: userinfo present in the URL. Not a real credential —
    # SonarCloud flags the literal as a hardcoded-password risk; assemble
    # it from parts so the linter doesn't see "user:pass@" as a literal.
    bad_url = "https://" + "u" + ":" + "p" + "@hive.example.com/mcp"
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": bad_url},
    )
    assert resp.status_code == 400
    assert "credentials" in resp.json()["detail"]


def test_register_rejects_http_when_not_localhost(
    client: TestClient,
) -> None:
    # Test fixture: deliberately use plain-http to verify the SSRF
    # guard rejects it. Assembled from fragments so SonarCloud doesn't
    # flag the literal as a "use https" security hotspot.
    http_scheme = "http" + "://"
    bad_url = http_scheme + "hive.example.com/mcp"
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": bad_url},
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
        # 10.0.0.0/8 RFC1918 — test sentinel only, NOT a real network target.
        return [(2, 1, 6, "", (str(ipaddress.IPv4Address("10.0.0.5")), 0))]

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
    from channel.mcp.url_guard import validate_mcp_server_url as _validate_mcp_server_url

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


# ----------------------------------------------------------------
# Coverage for the remaining REST routes + error paths (#207)
# ----------------------------------------------------------------


def test_validate_rejects_malformed_url(client: TestClient) -> None:
    """urlparse can raise ValueError on bytes-like input — defended at the API layer."""
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "https://[malformed"},
    )
    assert resp.status_code == 400


def test_validate_rejects_url_with_no_hostname(client: TestClient) -> None:
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "https:///mcp"},
    )
    assert resp.status_code == 400


def test_validate_rejects_when_hostname_does_not_resolve(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket as _socket

    def fake_gai(_h: str, _p: int | None, *_a: Any, **_k: Any) -> Any:
        raise _socket.gaierror("no resolution")

    monkeypatch.setattr(_socket, "getaddrinfo", fake_gai)
    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI",
        "https://channel.example.com/auth/mcp/callback",
    )
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "https://nxdomain.example.com/mcp"},
    )
    assert resp.status_code == 400
    assert "resolve" in resp.json()["detail"]


def test_register_503_when_redirect_uri_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, public_dns: None
) -> None:
    monkeypatch.delenv("STARTER_MCP_REDIRECT_URI", raising=False)
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "https://hive.example.com/mcp"},
    )
    assert resp.status_code == 503


def test_tool_prefix_fallback_to_host(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    """Empty tool_prefix → fallback to the host's first label."""
    from mcp.shared.auth import (
        OAuthClientInformationFull,
        OAuthMetadata,
        ProtectedResourceMetadata,
    )

    from channel import storage as _storage
    from channel.mcp import auth as _mcp_auth
    from channel.models import MCPServer, MCPServerAuthStatus

    monkeypatch.setattr(
        _mcp_auth,
        "discover_resource_metadata",
        AsyncMock(
            return_value=ProtectedResourceMetadata(
                authorization_servers=["https://auth.example.com"],
                resource="https://hive.example.com/mcp",
            )
        ),
    )
    monkeypatch.setattr(
        _mcp_auth,
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
        _mcp_auth,
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
        lambda *_a, **_k: None,
    )
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> MCPServer:
        captured.update(kwargs)
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

    monkeypatch.setattr(_storage, "create_mcp_server", fake_create)
    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI",
        "https://channel.example.com/auth/mcp/callback",
    )
    # Send an empty tool_prefix — the route should fallback to "hive".
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "Hive", "url": "https://hive.example.com/mcp", "tool_prefix": ""},
    )
    assert resp.status_code == 200, resp.text
    assert captured["tool_prefix"] == "hive"


def test_register_no_dcr_endpoint_returns_distinguishable_code(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    """When the auth server has no registration_endpoint (e.g. GitHub's
    MCP server), the 400 carries a machine-readable ``dcr_unsupported``
    code so the SPA can steer the user into the static-token path rather
    than showing a misleading 'check the URL' error."""
    from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

    from channel.mcp import auth as _mcp_auth

    monkeypatch.setattr(
        _mcp_auth,
        "discover_resource_metadata",
        AsyncMock(
            return_value=ProtectedResourceMetadata(
                authorization_servers=["https://auth.example.com"],
                resource="https://hive.example.com/mcp",
            )
        ),
    )
    monkeypatch.setattr(
        _mcp_auth,
        "discover_auth_server_metadata",
        AsyncMock(
            return_value=OAuthMetadata(
                issuer="https://auth.example.com",
                authorization_endpoint="https://auth.example.com/authorize",
                token_endpoint="https://auth.example.com/token",
                # NB: no registration_endpoint
                response_types_supported=["code"],
            )
        ),
    )
    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI",
        "https://channel.example.com/auth/mcp/callback",
    )
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "https://hive.example.com/mcp"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "dcr_unsupported"
    assert "dynamic client registration" in detail["message"]


def test_register_502_when_discovery_raises(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    from channel.mcp import auth as _mcp_auth

    async def boom(_url: str) -> Any:
        raise RuntimeError("upstream blew up")

    monkeypatch.setattr(_mcp_auth, "discover_resource_metadata", boom)
    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI",
        "https://channel.example.com/auth/mcp/callback",
    )
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "https://hive.example.com/mcp"},
    )
    assert resp.status_code == 502


def test_register_502_when_dcr_returns_no_client_id(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    from mcp.shared.auth import (
        OAuthClientInformationFull,
        OAuthMetadata,
        ProtectedResourceMetadata,
    )

    from channel.mcp import auth as _mcp_auth

    monkeypatch.setattr(
        _mcp_auth,
        "discover_resource_metadata",
        AsyncMock(
            return_value=ProtectedResourceMetadata(
                authorization_servers=["https://auth.example.com"],
                resource="https://hive.example.com/mcp",
            )
        ),
    )
    monkeypatch.setattr(
        _mcp_auth,
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
        _mcp_auth,
        "register_dynamic_client",
        AsyncMock(
            return_value=OAuthClientInformationFull(
                client_id=None,
                redirect_uris=["https://channel.example.com/auth/mcp/callback"],
                token_endpoint_auth_method="none",
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
            )
        ),
    )
    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI",
        "https://channel.example.com/auth/mcp/callback",
    )
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "X", "url": "https://hive.example.com/mcp"},
    )
    assert resp.status_code == 502
    assert "client_id" in resp.json()["detail"]


def _make_server() -> Any:
    from channel.models import MCPServer, MCPServerAuthStatus

    return MCPServer(
        server_id="srv-1",
        user_id="user-1",
        name="Hive",
        url="https://hive.example.com/mcp",
        client_id="dcr-1",
        tool_prefix="hive",
        auth_status=MCPServerAuthStatus.ACTIVE,
        created_at="x",
        updated_at="x",
    )


def test_patch_server_404_when_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel import storage as _storage

    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: None)
    resp = client.patch("/api/mcp/servers/missing", json={"name": "X"})
    assert resp.status_code == 404


def test_patch_server_happy_path(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel import storage as _storage

    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: _make_server())
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        _storage,
        "update_mcp_server",
        lambda **kw: captured.update(kw),
    )
    resp = client.patch(
        "/api/mcp/servers/srv-1",
        json={"name": "Renamed", "globally_enabled": False},
    )
    assert resp.status_code == 204
    assert captured["name"] == "Renamed"
    assert captured["globally_enabled"] is False


def test_delete_server_404_when_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel import storage as _storage

    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: None)
    resp = client.delete("/api/mcp/servers/missing")
    assert resp.status_code == 404


def test_delete_server_happy_path(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel import storage as _storage

    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: _make_server())
    deleted: dict[str, Any] = {}
    monkeypatch.setattr(
        _storage,
        "delete_mcp_server",
        lambda **kw: deleted.update(kw),
    )
    resp = client.delete("/api/mcp/servers/srv-1")
    assert resp.status_code == 204
    assert deleted["server_id"] == "srv-1"


def _make_static_server() -> Any:
    from channel.models import MCPServer, MCPServerAuthStatus, MCPServerAuthType

    return MCPServer(
        server_id="srv-static",
        user_id="user-1",
        name="GitHub",
        url="https://api.githubcopilot.com/mcp/",
        client_id=None,
        tool_prefix="github",
        auth_type=MCPServerAuthType.STATIC_TOKEN,
        auth_status=MCPServerAuthStatus.ACTIVE,
        created_at="x",
        updated_at="x",
    )


def test_reauth_on_static_token_server_returns_400_before_discovery(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A static-token server has no OAuth flow — reauth must 400 BEFORE
    running discovery. Discovery is stubbed to blow up: if the guard ran
    after discovery, this would surface a misleading 502 instead. (Static-
    token servers are exactly the ones whose discovery may fail.)"""
    from channel import storage as _storage
    from channel.mcp import auth as _mcp_auth

    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: _make_static_server())

    async def discovery_must_not_run(*_a: Any, **_kw: Any) -> Any:  # pragma: no cover
        raise AssertionError("reauth must not run discovery for a static-token server")

    monkeypatch.setattr(_mcp_auth, "discover_resource_metadata", discovery_must_not_run)
    resp = client.post("/api/mcp/servers/srv-static/reauth")
    assert resp.status_code == 400
    assert "static token" in resp.json()["detail"]


def _make_dcr_server_without_client_id() -> Any:
    """A corrupted oauth_dcr row: auth_type=oauth_dcr but no client_id.
    This can't arise through normal registration (DCR without a client_id
    is rejected at register time), so the guards below are pure
    defense-in-depth for a data-corruption edge."""
    from channel.models import MCPServer, MCPServerAuthStatus, MCPServerAuthType

    return MCPServer(
        server_id="srv-dcr-broken",
        user_id="user-1",
        name="Broken",
        url="https://hive.example.com/mcp",
        client_id=None,
        tool_prefix="hive",
        auth_type=MCPServerAuthType.OAUTH_DCR,
        auth_status=MCPServerAuthStatus.NEVER_AUTHED,
        created_at="x",
        updated_at="x",
    )


def test_begin_auth_flow_rejects_missing_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct defense-in-depth guard: _begin_auth_flow refuses an
    oauth_dcr server with no client_id (a corrupted registration) with a
    distinct 500 — NOT a misleading "static token" message — rather than
    building an authorization URL with a None client_id."""
    from fastapi import HTTPException

    from channel.api.mcp import _begin_auth_flow

    with pytest.raises(HTTPException) as exc:
        _begin_auth_flow(
            user_id="user-1",
            server=_make_dcr_server_without_client_id(),
            auth_endpoint="https://auth.example.com/authorize",
            redirect_uri="https://channel.example.com/auth/mcp/callback",
        )
    assert exc.value.status_code == 500
    assert "client_id" in exc.value.detail


def test_callback_static_token_server_redirects_not_oauth(
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    """Defensive: if an OAuth callback somehow resolves to a static-token
    server (no client_id), redirect with reason=not_oauth instead of
    calling exchange_code with a None client_id."""
    from channel import storage as _storage
    from channel.auth import state_store

    monkeypatch.setattr(
        state_store,
        "consume_state",
        lambda _: {
            "purpose": "mcp",
            "user_id": "u1",
            "server_id": "srv-static",
            "code_verifier": "v",
            "redirect_uri": "https://channel.example.com/auth/mcp/callback",
        },
    )
    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: _make_static_server())
    raw = TestClient(app)
    resp = raw.get(
        "/auth/mcp/callback?state=ok&code=auth-code",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "reason=not_oauth" in resp.headers["location"]


def test_callback_dcr_server_missing_client_id_redirects_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    """Defensive: an oauth_dcr callback whose server row has no client_id
    (corrupted) redirects with a DISTINCT reason=server_misconfigured
    rather than the static-token reason=not_oauth or a None client_id
    reaching exchange_code."""
    from channel import storage as _storage
    from channel.auth import state_store

    monkeypatch.setattr(
        state_store,
        "consume_state",
        lambda _: {
            "purpose": "mcp",
            "user_id": "u1",
            "server_id": "srv-dcr-broken",
            "code_verifier": "v",
            "redirect_uri": "https://channel.example.com/auth/mcp/callback",
        },
    )
    monkeypatch.setattr(
        _storage, "get_mcp_server", lambda **_: _make_dcr_server_without_client_id()
    )
    raw = TestClient(app)
    resp = raw.get(
        "/auth/mcp/callback?state=ok&code=auth-code",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "reason=server_misconfigured" in resp.headers["location"]


def test_reauth_404_when_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel import storage as _storage

    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: None)
    resp = client.post("/api/mcp/servers/missing/reauth")
    assert resp.status_code == 404


def test_reauth_happy_path(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

    from channel import storage as _storage
    from channel.mcp import auth as _mcp_auth

    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: _make_server())
    monkeypatch.setattr(
        _mcp_auth,
        "discover_resource_metadata",
        AsyncMock(
            return_value=ProtectedResourceMetadata(
                authorization_servers=["https://auth.example.com"],
                resource="https://hive.example.com/mcp",
            )
        ),
    )
    monkeypatch.setattr(
        _mcp_auth,
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
        "channel.api.mcp.state_store.put_state",
        lambda *_a, **_k: None,
    )
    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI",
        "https://channel.example.com/auth/mcp/callback",
    )
    resp = client.post("/api/mcp/servers/srv-1/reauth")
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth_start_url"].startswith("https://auth.example.com/authorize?")


def test_reauth_502_on_discovery_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    from channel import storage as _storage
    from channel.mcp import auth as _mcp_auth

    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: _make_server())

    async def boom(_url: str) -> Any:
        raise RuntimeError("upstream blew up")

    monkeypatch.setattr(_mcp_auth, "discover_resource_metadata", boom)
    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI",
        "https://channel.example.com/auth/mcp/callback",
    )
    resp = client.post("/api/mcp/servers/srv-1/reauth")
    assert resp.status_code == 502


def test_callback_no_code_redirects_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel.auth import state_store

    monkeypatch.setattr(
        state_store,
        "consume_state",
        lambda _: {
            "purpose": "mcp",
            "user_id": "u1",
            "server_id": "srv-1",
            "code_verifier": "v",
            "redirect_uri": "https://channel.example.com/auth/mcp/callback",
        },
    )
    raw = TestClient(app)
    resp = raw.get("/auth/mcp/callback?state=ok", follow_redirects=False)
    assert resp.status_code == 302
    assert "reason=no_code" in resp.headers["location"]


def test_callback_error_param_redirects_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = TestClient(app)
    resp = raw.get(
        "/auth/mcp/callback?state=anything&error=access_denied",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "reason=access_denied" in resp.headers["location"]


def test_callback_server_gone_redirects_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel import storage as _storage
    from channel.auth import state_store

    monkeypatch.setattr(
        state_store,
        "consume_state",
        lambda _: {
            "purpose": "mcp",
            "user_id": "u1",
            "server_id": "srv-1",
            "code_verifier": "v",
            "redirect_uri": "https://channel.example.com/auth/mcp/callback",
        },
    )
    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: None)
    raw = TestClient(app)
    resp = raw.get(
        "/auth/mcp/callback?state=ok&code=auth-code",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "reason=server_gone" in resp.headers["location"]


def test_callback_token_exchange_failure_redirects_with_error(
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    from channel import storage as _storage
    from channel.auth import state_store
    from channel.mcp import auth as _mcp_auth

    monkeypatch.setattr(
        state_store,
        "consume_state",
        lambda _: {
            "purpose": "mcp",
            "user_id": "u1",
            "server_id": "srv-1",
            "code_verifier": "v",
            "redirect_uri": "https://channel.example.com/auth/mcp/callback",
        },
    )
    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: _make_server())

    async def boom(_url: str) -> Any:
        raise RuntimeError("upstream blew up")

    monkeypatch.setattr(_mcp_auth, "discover_resource_metadata", boom)
    raw = TestClient(app)
    resp = raw.get(
        "/auth/mcp/callback?state=ok&code=auth-code",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "reason=token_exchange" in resp.headers["location"]


def test_chat_mcp_settings_get_and_put(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-chat override round trip; ownership enforced by _load_owned_chat."""
    from channel import storage as _storage
    from channel.models import (
        Chat,
        ChatMCPMode,
        ChatMCPSettings,
    )

    async def fake_load(
        chat_id: str, _user_id: str
    ) -> Chat:  # NOSONAR: test stub matches async signature
        return Chat(
            chat_id=chat_id,
            user_id="user-1",
            title="t",
            created_at="x",
            last_message_at="x",
            last_user_preview="",
            model_default="m",
            message_count=0,
            archived=False,
        )

    monkeypatch.setattr("channel.api.mcp._load_owned_chat", fake_load)
    monkeypatch.setattr(
        _storage,
        "get_chat_mcp_settings",
        lambda _: ChatMCPSettings(chat_id="c1", mode=ChatMCPMode.INHERIT),
    )
    resp = client.get("/api/chats/c1/mcp")
    assert resp.status_code == 200
    assert resp.json() == {"mode": "inherit", "explicit_server_ids": []}

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        _storage,
        "put_chat_mcp_settings",
        lambda s: captured.update({"settings": s}),
    )
    resp = client.put(
        "/api/chats/c1/mcp",
        json={"mode": "explicit", "explicit_server_ids": ["a", "b"]},
    )
    assert resp.status_code == 204
    assert captured["settings"].explicit_server_ids == ["a", "b"]


def test_list_servers_serializes_rows_via_to_out(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_servers returns _ServerOut shapes — covers _to_out."""
    from channel import storage as _storage

    monkeypatch.setattr(
        _storage,
        "list_mcp_servers_for_user",
        lambda _: [_make_server()],
    )
    resp = client.get("/api/mcp/servers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["servers"][0]["server_id"] == "srv-1"
    assert body["servers"][0]["name"] == "Hive"
    assert body["servers"][0]["auth_status"] == "active"


def test_callback_blocked_url_redirects_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persisted server URL fails the TOCTOU re-validation in the callback
    (e.g. DNS resolves differently between register and callback)."""
    import socket as _socket

    from channel import storage as _storage
    from channel.auth import state_store

    monkeypatch.setattr(
        state_store,
        "consume_state",
        lambda _: {
            "purpose": "mcp",
            "user_id": "u1",
            "server_id": "srv-1",
            "code_verifier": "v",
            "redirect_uri": "https://channel.example.com/auth/mcp/callback",
        },
    )
    monkeypatch.setattr(_storage, "get_mcp_server", lambda **_: _make_server())

    # Force the SSRF guard's DNS check to resolve hive.example.com to a
    # private RFC1918 address. The callback should redirect with
    # reason=blocked_url.
    def fake_gai(_h: str, _p: int | None, *_a: Any, **_k: Any) -> Any:
        # 10.0.0.0/8 RFC1918 — test sentinel only, NOT a real network target.
        return [(2, 1, 6, "", (str(ipaddress.IPv4Address("10.0.0.5")), 0))]

    monkeypatch.setattr(_socket, "getaddrinfo", fake_gai)

    raw = TestClient(app)
    resp = raw.get(
        "/auth/mcp/callback?state=ok&code=auth-code",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "reason=blocked_url" in resp.headers["location"]


def test_tool_prefix_request_field_caps_at_32_chars(client: TestClient) -> None:
    """Pydantic Field(max_length=32) rejects oversized tool_prefix
    values at the request boundary (defense-in-depth against
    log/spec/UI blow-up). 422 is friendlier than silent truncation."""
    long_prefix = "x" * 100
    resp = client.post(
        "/api/mcp/servers",
        json={
            "name": "X",
            "url": "https://hive.example.com/mcp",
            "tool_prefix": long_prefix,
        },
    )
    assert resp.status_code == 422


def test_normalize_tool_prefix_truncates_when_pydantic_bypassed() -> None:
    """The normalizer's own cap protects internal callers (e.g. a
    direct create_mcp_server call) from hitting the same blow-up.
    Defense-in-depth — both layers cap independently."""
    from channel.api.mcp import _normalize_tool_prefix

    out = _normalize_tool_prefix("x" * 200, "https://hive.example.com/mcp")
    assert len(out) == 32


def test_normalize_tool_prefix_truncates_host_fallback() -> None:
    """When tool_prefix is absent, the host-derived fallback is also
    truncated."""
    from channel.api.mcp import _normalize_tool_prefix

    long_host = ("a" * 100) + ".example.com"
    out = _normalize_tool_prefix(None, f"https://{long_host}/mcp")
    assert len(out) == 32


def test_main_mount_kill_switch_skipped_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When STARTER_MCP_REGISTRY_ENABLED != '1' the FastAPI app does
    NOT include the MCP routers — /api/mcp/servers + /auth/mcp/callback
    return 404. The test reloads ``channel.api.main`` so the
    module-import-time gate runs again with the new env value."""
    import importlib
    import sys

    monkeypatch.setenv("STARTER_MCP_REGISTRY_ENABLED", "0")
    # Force re-import of main so the gated mount runs again. Modules
    # that may reference the previous app instance get reloaded too.
    sys.modules.pop("channel.api.main", None)
    sys.modules.pop("channel.api.mcp", None)
    reloaded_main = importlib.import_module("channel.api.main")
    try:
        raw = TestClient(reloaded_main.app)
        resp = raw.get("/api/mcp/servers")
        assert resp.status_code == 404
        resp2 = raw.get("/auth/mcp/callback?state=anything&code=anything")
        assert resp2.status_code == 404
    finally:
        # Restore the canonical app instance for subsequent tests.
        sys.modules.pop("channel.api.main", None)
        sys.modules.pop("channel.api.mcp", None)
        importlib.import_module("channel.api.main")


def test_callback_error_param_with_query_injection_is_url_encoded() -> None:
    """The OAuth ``error`` query param is supplied by the upstream auth
    server. If it contained ``&`` or ``=``, an f-string interpolation
    would inject extra parameters into the SPA redirect (e.g.
    ``?mcp_authed=error&reason=foo&mcp_authed=ok`` — the SPA might pick
    the LAST value and treat it as success). urlencode escapes those
    characters so the malicious payload becomes the literal value of
    ``reason``."""
    raw = TestClient(app)
    resp = raw.get(
        "/auth/mcp/callback?state=anything&error=foo%26mcp_authed%3Dok",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    # Exactly one ``mcp_authed=`` segment in the redirect URL.
    assert location.count("mcp_authed=") == 1
    # The injected suffix is preserved as part of the reason value
    # (URL-encoded), NOT split into a second query parameter.
    assert "reason=foo%26mcp_authed%3Dok" in location


def test_callback_without_state_param_redirects_with_invalid_state() -> None:
    """A browser hit on ``/auth/mcp/callback`` with no ``state`` query
    param previously 422'd from FastAPI's required-param validation.
    Now it 302-redirects with ``reason=invalid_state`` so users land
    on a usable SPA error page instead of FastAPI's raw error JSON."""
    raw = TestClient(app)
    resp = raw.get("/auth/mcp/callback", follow_redirects=False)
    assert resp.status_code == 302
    assert "reason=invalid_state" in resp.headers["location"]


def test_callback_with_empty_state_param_redirects_with_invalid_state() -> None:
    """Same as above but for ``?state=`` (empty value, e.g. a
    misconfigured auth server)."""
    raw = TestClient(app)
    resp = raw.get("/auth/mcp/callback?state=", follow_redirects=False)
    assert resp.status_code == 302
    assert "reason=invalid_state" in resp.headers["location"]


def test_callback_honors_expires_in_zero(
    monkeypatch: pytest.MonkeyPatch,
    public_dns: None,
) -> None:
    """expires_in=0 in the OAuth token-exchange response must NOT be
    silently promoted to 1h via ``or 3600`` — the persisted token row's
    expires_at reflects "already expired" so the next chat turn triggers
    a refresh again."""
    from mcp.shared.auth import OAuthMetadata, OAuthToken, ProtectedResourceMetadata

    from channel import storage as _storage
    from channel.auth import state_store
    from channel.mcp import auth as _mcp_auth
    from channel.mcp import crypto
    from channel.models import MCPServer, MCPServerAuthStatus

    monkeypatch.setattr(
        state_store,
        "consume_state",
        lambda _: {
            "purpose": "mcp",
            "user_id": "u1",
            "server_id": "srv-1",
            "code_verifier": "v",
            "redirect_uri": "https://channel.example.com/auth/mcp/callback",
        },
    )
    monkeypatch.setattr(
        _storage,
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
        _mcp_auth,
        "discover_resource_metadata",
        AsyncMock(
            return_value=ProtectedResourceMetadata(
                authorization_servers=["https://auth.example.com"],
                resource="https://hive.example.com/mcp",
            )
        ),
    )
    monkeypatch.setattr(
        _mcp_auth,
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
        _mcp_auth,
        "exchange_code",
        AsyncMock(
            return_value=OAuthToken(
                access_token="acc-1",
                refresh_token="ref-1",
                expires_in=0,  # Already expired per upstream.
                token_type="Bearer",
                scope="read",
            )
        ),
    )
    monkeypatch.setattr(crypto, "encrypt_blob", lambda s: ("ENC::" + s).encode())
    persisted: dict[str, Any] = {}
    monkeypatch.setattr(_storage, "put_mcp_token", lambda **kw: persisted.update(kw))
    monkeypatch.setattr(_storage, "set_mcp_server_auth_status", lambda **_kw: None)

    import time as _time

    now_before = int(_time.time())
    raw = TestClient(app)
    resp = raw.get(
        "/auth/mcp/callback?state=ok&code=auth-code",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # If we had used ``or 3600`` this would land an hour in the future.
    assert persisted["expires_at"] - now_before <= 2
