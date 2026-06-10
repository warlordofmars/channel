# Copyright (c) 2026 John Carter. All rights reserved.
"""OAuth 2.1 + DCR helpers for Channel's MCP-server lifecycle.

Reuses the protocol-level building blocks from ``mcp.client.auth.utils``
(discovery + DCR shapes) and ``mcp.shared.auth`` (pydantic OAuth
models). The flow orchestration lives here because Channel's auth-code
dance spans multiple HTTP requests — :mod:`mcp.client.auth.oauth2`'s
``OAuthClientProvider`` is built for a single-process flow.

Public surface:
  * :func:`discover_resource_metadata` — RFC 9728 protected-resource discovery
  * :func:`discover_auth_server_metadata` — RFC 8414 authorization-server discovery
  * :func:`register_dynamic_client` — RFC 7591 dynamic-client registration
  * :func:`build_authorization_url` — RFC 6749 §4.1.1 + PKCE (RFC 7636) URL build
  * :func:`exchange_code` — authorization-code → token exchange
  * :func:`refresh_token` — refresh-token → token exchange
  * :func:`generate_pkce` — PKCE code_verifier + S256 challenge pair
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import string
from urllib.parse import urlencode, urlparse

import httpx
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)

# Discovery endpoint suffixes (RFC 8414 / RFC 9728).
_PRM_PATH = "/.well-known/oauth-protected-resource"
_AS_PATH = "/.well-known/oauth-authorization-server"

# 30s is generous for token-endpoint round-trips; longer than the chain
# wall-clock budget would mask refresh stalls behind the cancel signal.
_HTTP_TIMEOUT_SEC = 30.0


def generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for PKCE S256."""
    verifier = "".join(
        secrets.choice(string.ascii_letters + string.digits + "-._~")
        for _ in range(128)
    )
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


async def discover_resource_metadata(server_url: str) -> ProtectedResourceMetadata:
    """Fetch the protected-resource metadata for an MCP server URL.

    Uses RFC 9728 — the ``.well-known/oauth-protected-resource`` endpoint
    served at the resource's origin. Returns the parsed
    :class:`ProtectedResourceMetadata` from ``mcp.shared.auth``.
    """
    origin = _origin_of(server_url)
    url = origin + _PRM_PATH
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return ProtectedResourceMetadata.model_validate(resp.json())


async def discover_auth_server_metadata(auth_server_url: str) -> OAuthMetadata:
    """Fetch RFC 8414 authorization-server metadata."""
    origin = _origin_of(auth_server_url)
    url = origin + _AS_PATH
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return OAuthMetadata.model_validate(resp.json())


async def register_dynamic_client(
    *,
    registration_endpoint: str,
    redirect_uri: str,
    client_name: str = "Channel",
) -> OAuthClientInformationFull:
    """Register a Channel-side DCR client at the MCP server's auth server.

    Returns the issued client info — ``client_id`` is opaque to Channel
    and stored on the MCPSERVER row. Public-client posture
    (``token_endpoint_auth_method = "none"``) — Channel uses PKCE so no
    client secret is required.
    """
    body = {
        "redirect_uris": [redirect_uri],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": client_name,
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = await client.post(registration_endpoint, json=body)
    resp.raise_for_status()
    return OAuthClientInformationFull.model_validate(resp.json())


def build_authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scopes: list[str] | None = None,
) -> str:
    """Build the RFC 6749 §4.1.1 authorization URL with PKCE S256."""
    params: list[tuple[str, str]] = [
        ("response_type", "code"),
        ("client_id", client_id),
        ("redirect_uri", redirect_uri),
        ("state", state),
        ("code_challenge", code_challenge),
        ("code_challenge_method", "S256"),
    ]
    if scopes:
        params.append(("scope", " ".join(scopes)))
    sep = "&" if "?" in authorization_endpoint else "?"
    return authorization_endpoint + sep + urlencode(params)


async def exchange_code(
    *,
    token_endpoint: str,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> OAuthToken:
    """Exchange an auth code for tokens (RFC 6749 §4.1.3 + PKCE)."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = await client.post(
            token_endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    resp.raise_for_status()
    return OAuthToken.model_validate(resp.json())


async def refresh_token(
    *,
    token_endpoint: str,
    client_id: str,
    refresh_token: str,
) -> OAuthToken:
    """Exchange a refresh token for a fresh access token (RFC 6749 §6)."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = await client.post(
            token_endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    resp.raise_for_status()
    return OAuthToken.model_validate(resp.json())


def _origin_of(url: str) -> str:
    """Strip path/query from an MCP server URL to its scheme+host[:port] root."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"
