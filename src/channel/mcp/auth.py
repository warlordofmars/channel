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
import time
from urllib.parse import urlencode, urlparse

import httpx
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)

from channel import storage
from channel.mcp import crypto
from channel.mcp.url_guard import validate_mcp_server_url
from channel.models import MCPServer, MCPServerAuthType

# Discovery endpoint suffixes (RFC 8414 / RFC 9728).
_PRM_PATH = "/.well-known/oauth-protected-resource"
_AS_PATH = "/.well-known/oauth-authorization-server"

# 30s is generous for token-endpoint round-trips; longer than the chain
# wall-clock budget would mask refresh stalls behind the cancel signal.
_HTTP_TIMEOUT_SEC = 30.0


def generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for PKCE S256."""
    verifier = "".join(
        secrets.choice(string.ascii_letters + string.digits + "-._~") for _ in range(128)
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
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT_SEC,
        follow_redirects=False,
    ) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return ProtectedResourceMetadata.model_validate(resp.json())


async def discover_auth_server_metadata(auth_server_url: str) -> OAuthMetadata:
    """Fetch RFC 8414 authorization-server metadata.

    The authorization-server URL is supplied (transitively) by the user-
    registered MCP server's resource metadata. Re-validate the scheme +
    host before issuing the network call — a misconfigured or hostile
    server could advertise ``http://`` or a loopback address as its
    authorization endpoint, and Channel would otherwise POST auth
    codes / refresh tokens over plaintext or into the Lambda's internal
    network.
    """
    validate_mcp_server_url(auth_server_url)
    origin = _origin_of(auth_server_url)
    url = origin + _AS_PATH
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT_SEC,
        follow_redirects=False,
    ) as client:
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

    Validates the ``registration_endpoint`` URL — it came from
    ``OAuthMetadata.registration_endpoint`` which is supplied by the
    auth server's discovery response and could otherwise carry a
    plaintext / internal-network URL.
    """
    validate_mcp_server_url(registration_endpoint)
    body = {
        "redirect_uris": [redirect_uri],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": client_name,
    }
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT_SEC,
        follow_redirects=False,
    ) as client:
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
    """Build the RFC 6749 §4.1.1 authorization URL with PKCE S256.

    Validates the ``authorization_endpoint`` — Channel redirects the
    browser here, and a plaintext / internal-network endpoint would
    leak the OAuth flow.
    """
    validate_mcp_server_url(authorization_endpoint)
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
    """Exchange an auth code for tokens (RFC 6749 §4.1.3 + PKCE).

    Validates the ``token_endpoint`` — it came from
    ``OAuthMetadata.token_endpoint`` which is supplied by the auth
    server's discovery response. POSTing an authorization code over
    plaintext (or to an internal-network address) would leak it.
    """
    validate_mcp_server_url(token_endpoint)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT_SEC,
        follow_redirects=False,
    ) as client:
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
    """Exchange a refresh token for a fresh access token (RFC 6749 §6).

    Same scheme/host validation as :func:`exchange_code` — a refresh
    token must not be POSTed over plaintext or to an internal-network
    address.
    """
    validate_mcp_server_url(token_endpoint)
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT_SEC,
        follow_redirects=False,
    ) as client:
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


# Refresh skew: if a token expires within this many seconds, force a
# refresh before the next tool call. Bigger window = more refreshes;
# smaller window = higher chance of mid-call expiry. 60s matches the
# spike's "Lazy check on every tool call" recommendation.
_REFRESH_SKEW_SEC = 60


class MCPAuthFailedError(RuntimeError):
    """Raised when token refresh fails — caller should surface as
    ``sse_tool_error(error_type="auth_expired")`` and flip the
    MCPSERVER row's auth_status to EXPIRED."""


async def get_valid_access_token(
    *,
    user_id: str,
    server: MCPServer,
) -> str:
    """Resolve the current access token for ``(user_id, server)``.

    Refreshes if within :data:`_REFRESH_SKEW_SEC` of expiry. Persists
    the refreshed token transparently. Raises
    :class:`MCPAuthFailedError` when refresh fails or no token exists.
    """
    token = storage.get_mcp_token(user_id=user_id, server_id=server.server_id)
    if token is None:
        raise MCPAuthFailedError(f"no token row for user={user_id} server={server.server_id}")

    # Static-token (PAT) servers never refresh: no refresh_token, no
    # token_endpoint, and ``server.client_id is None`` — so the refresh
    # path below (which passes ``client_id=server.client_id``) must not
    # be reachable for them. Decrypt + return the pasted bearer token
    # directly. A decrypt failure surfaces as MCPAuthFailedError just
    # like the DCR cached-token path. This check is intentionally before
    # the expiry/refresh logic. See #375.
    if server.auth_type == MCPServerAuthType.STATIC_TOKEN:
        try:
            return crypto.decrypt_blob(token.access_token_ciphertext)
        except Exception as exc:
            raise MCPAuthFailedError(
                f"static access token decrypt failed user={user_id} server={server.server_id}"
            ) from exc

    now = int(time.time())
    if token.expires_at - now > _REFRESH_SKEW_SEC:
        # KMS decrypt can raise (KMS unavailable, corrupted ciphertext,
        # decode error). Funnel through MCPAuthFailedError so the
        # chassis path skips + flips to EXPIRED instead of crashing
        # the chat turn.
        try:
            return crypto.decrypt_blob(token.access_token_ciphertext)
        except Exception as exc:
            raise MCPAuthFailedError(
                f"access token decrypt failed user={user_id} server={server.server_id}"
            ) from exc

    # Refresh required.
    if token.refresh_token_ciphertext is None:
        raise MCPAuthFailedError(
            f"token expired and no refresh token user={user_id} server={server.server_id}"
        )
    try:
        refresh_plain = crypto.decrypt_blob(token.refresh_token_ciphertext)
    except Exception as exc:
        raise MCPAuthFailedError(
            f"refresh token decrypt failed user={user_id} server={server.server_id}"
        ) from exc
    # A DCR refresh needs the DCR-issued client_id. Static-token servers
    # (client_id is None) already returned above, so this only guards the
    # unreachable-in-practice case of a broken oauth_dcr row — surface it
    # as MCPAuthFailedError rather than passing None into refresh_token.
    if server.client_id is None:
        raise MCPAuthFailedError(
            f"cannot refresh without client_id user={user_id} server={server.server_id}"
        )
    try:
        prm = await discover_resource_metadata(server.url)
        # MCP servers either point at an external auth server or self-issue.
        as_url = str(prm.authorization_servers[0]) if prm.authorization_servers else server.url
        as_meta = await discover_auth_server_metadata(as_url)
        new_tok = await refresh_token(
            token_endpoint=str(as_meta.token_endpoint),
            client_id=server.client_id,
            refresh_token=refresh_plain,
        )
    except Exception as exc:
        # Broad catch on purpose: the chat path only handles
        # MCPAuthFailedError. Anything else surfacing from the
        # discovery / refresh sequence — httpx.HTTPError on a network
        # blip, pydantic ValidationError on malformed metadata,
        # OAuthTokenError, AttributeError on a missing
        # token_endpoint — should not crash the streaming generator.
        # Wrap it and let the chassis log + skip the server.
        raise MCPAuthFailedError(
            f"refresh round-trip failed user={user_id} server={server.server_id}"
        ) from exc

    # `or 3600` would treat expires_in=0 as missing — explicit None check
    # so a server-supplied "already expired" token honours that signal
    # (we'd then immediately refresh again on the next turn, which is
    # the correct behaviour).
    expires_in = new_tok.expires_in if new_tok.expires_in is not None else 3600
    new_expires_at = now + expires_in
    new_refresh = new_tok.refresh_token or refresh_plain
    storage.put_mcp_token(
        user_id=user_id,
        server_id=server.server_id,
        access_token_ciphertext=crypto.encrypt_blob(new_tok.access_token),
        refresh_token_ciphertext=crypto.encrypt_blob(new_refresh),
        expires_at=new_expires_at,
        granted_scope=new_tok.scope or token.granted_scope,
    )
    return new_tok.access_token
