# Copyright (c) 2026 John Carter. All rights reserved.
"""REST surface for MCP-server registry + OAuth callback.

Routes:
  * ``GET    /api/mcp/servers`` — list the user's registered servers
  * ``POST   /api/mcp/servers`` — register new + run DCR + return auth_start_url
  * ``PATCH  /api/mcp/servers/{id}`` — rename / toggle globally_enabled
  * ``DELETE /api/mcp/servers/{id}`` — best-effort revoke + delete rows
  * ``POST   /api/mcp/servers/{id}/reauth`` — re-trigger auth-code flow
  * ``GET    /api/chats/{chat_id}/mcp`` — read per-chat override
  * ``PUT    /api/chats/{chat_id}/mcp`` — write per-chat override
  * ``GET    /auth/mcp/callback`` — browser-redirected OAuth callback

The callback router intentionally lives outside ``/api/*`` because it's
a browser-redirect target carrying ``?code=&state=``, not a
JWT-authenticated endpoint. State verification is the security barrier —
:mod:`channel.auth.state_store` provides atomic consume-once semantics.

For per-chat ownership, the read/write routes call
``_load_owned_chat(chat_id, jwt_sub)`` from :mod:`channel.api.chats`.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
import socket
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from channel import storage
from channel.api._auth import require_mgmt_user
from channel.api.chats import _load_owned_chat
from channel.auth import state_store
from channel.mcp import auth as mcp_auth
from channel.mcp import crypto
from channel.models import (
    ChatMCPMode,
    ChatMCPSettings,
    MCPServer,
    MCPServerAuthStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Routes that should NOT carry the /api prefix mount under this router.
callback_router = APIRouter()


_TOOL_PREFIX_OK_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def _is_dangerous_address(addr: str) -> bool:
    """Return True for any address that should never be the target of an
    MCP-server URL — loopback, link-local (incl. cloud metadata at
    169.254.169.254), private RFC1918, multicast, or unspecified.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _validate_mcp_server_url(url: str) -> None:
    """SSRF defense for the MCP server URL.

    Reject any URL whose scheme isn't ``https`` (except the explicit
    dev-only ``http://localhost[:port]/...`` form, gated by
    ``STARTER_MCP_ALLOW_LOCALHOST=1``), whose userinfo is set, or whose
    hostname resolves to a loopback / link-local / private / multicast
    / reserved address. Raise :class:`HTTPException` 400 on failure.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="malformed URL") from exc
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="URL must not include credentials")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="URL must include a hostname")

    allow_localhost = os.environ.get("STARTER_MCP_ALLOW_LOCALHOST") == "1"
    if parsed.scheme == "https":
        pass
    elif parsed.scheme == "http" and allow_localhost and parsed.hostname == "localhost":
        # Dev-only carve-out — only when the env flag is explicitly set
        # AND the hostname is the literal "localhost" (not "localhost.evil.com").
        return
    else:
        raise HTTPException(status_code=400, detail="MCP server URL must use https")

    # If the hostname IS an IP literal, validate it directly.
    if _is_dangerous_address(parsed.hostname):
        raise HTTPException(status_code=400, detail="URL targets a blocked address range")
    # Otherwise resolve via DNS and reject if ANY resolved address is dangerous.
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="hostname did not resolve") from exc
    for info in infos:
        # getaddrinfo's sockaddr is (host, port[, flow, scope]); the host
        # is always a str for the IPv4/IPv6 address families. mypy types
        # it loosely as ``str | int`` because of the Unix-socket case.
        addr = str(info[4][0])
        if _is_dangerous_address(addr):
            raise HTTPException(
                status_code=400, detail="URL targets a blocked address range"
            )


def _normalize_tool_prefix(value: str | None, fallback_url: str) -> str:
    """Pick a Strands-safe tool prefix.

    User-supplied prefixes are lowercased + filtered to ``[a-z0-9_]+``.
    On empty / missing input, fall back to a sanitised host token from
    the URL (e.g. ``https://hive.warlordofmars.net/mcp`` → ``hive``).
    """
    if value:
        cleaned = "".join(c for c in value.lower() if c in _TOOL_PREFIX_OK_CHARS)
        if cleaned:
            return cleaned
    host = urlparse(fallback_url).hostname or "mcp"
    return "".join(c for c in host.split(".")[0].lower() if c in _TOOL_PREFIX_OK_CHARS) or "mcp"


def _redirect_uri() -> str:
    uri = os.environ.get("STARTER_MCP_REDIRECT_URI")
    if not uri:
        raise HTTPException(
            status_code=503,
            detail="MCP registry is not configured (STARTER_MCP_REDIRECT_URI unset)",
        )
    return uri


def _spa_base_url() -> str:
    return os.environ.get("STARTER_SPA_BASE_URL", "http://localhost:5173")


# ---------- list / register ----------


class _ServerOut(BaseModel):
    server_id: str
    name: str
    url: str
    tool_prefix: str
    auth_status: MCPServerAuthStatus
    globally_enabled: bool
    created_at: str
    updated_at: str


class _ServerListResponse(BaseModel):
    servers: list[_ServerOut]


@router.get("/api/mcp/servers", response_model=_ServerListResponse)
def list_servers(
    response: Response,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> _ServerListResponse:
    response.headers["Cache-Control"] = "no-store"
    servers = storage.list_mcp_servers_for_user(claims["sub"])
    return _ServerListResponse(
        servers=[_to_out(s) for s in servers]
    )


def _to_out(s: MCPServer) -> _ServerOut:
    return _ServerOut(
        server_id=s.server_id, name=s.name, url=s.url,
        tool_prefix=s.tool_prefix, auth_status=s.auth_status,
        globally_enabled=s.globally_enabled,
        created_at=s.created_at, updated_at=s.updated_at,
    )


class _RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    url: str = Field(..., min_length=10, max_length=2048)
    tool_prefix: str | None = None


class _RegisterResponse(BaseModel):
    server_id: str
    auth_start_url: str


@router.post("/api/mcp/servers", response_model=_RegisterResponse)
async def register_server(
    body: _RegisterRequest,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> _RegisterResponse:
    _validate_mcp_server_url(body.url)
    redirect_uri = _redirect_uri()
    try:
        prm = await mcp_auth.discover_resource_metadata(body.url)
        as_url = (
            str(prm.authorization_servers[0])
            if prm.authorization_servers
            else body.url
        )
        as_meta = await mcp_auth.discover_auth_server_metadata(as_url)
        if not as_meta.registration_endpoint:
            raise HTTPException(
                status_code=400,
                detail="MCP server's auth server does not support dynamic client registration",
            )
        client_info = await mcp_auth.register_dynamic_client(
            registration_endpoint=str(as_meta.registration_endpoint),
            redirect_uri=redirect_uri,
            client_name="Channel",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("mcp.register.discovery_or_dcr_failed url=%s %r", body.url, exc)
        raise HTTPException(
            status_code=502,
            detail="Failed to register with MCP server (discovery or DCR)",
        ) from exc

    # DCR responses are technically allowed to omit client_id (per
    # OAuthClientInformationFull's optional field) but every conformant
    # auth server issues one. Treat a missing client_id as a 502 so the
    # SPA surfaces the same "failed to register" affordance.
    if client_info.client_id is None:
        raise HTTPException(
            status_code=502,
            detail="MCP server's auth server did not issue a client_id",
        )

    tool_prefix = _normalize_tool_prefix(body.tool_prefix, body.url)
    server = storage.create_mcp_server(
        user_id=claims["sub"],
        name=body.name,
        url=body.url,
        client_id=client_info.client_id,
        tool_prefix=tool_prefix,
    )
    auth_start_url = _begin_auth_flow(
        user_id=claims["sub"],
        server=server,
        auth_endpoint=str(as_meta.authorization_endpoint),
        redirect_uri=redirect_uri,
    )
    return _RegisterResponse(server_id=server.server_id, auth_start_url=auth_start_url)


def _begin_auth_flow(
    *,
    user_id: str,
    server: MCPServer,
    auth_endpoint: str,
    redirect_uri: str,
) -> str:
    code_verifier, code_challenge = mcp_auth.generate_pkce()
    state = secrets.token_urlsafe(32)
    state_store.put_state(
        state,
        payload={
            "purpose": "mcp",
            "user_id": user_id,
            "server_id": server.server_id,
            "code_verifier": code_verifier,
            "auth_endpoint": auth_endpoint,
            "redirect_uri": redirect_uri,
        },
        ttl_seconds=900,  # 15 min — generous for the OAuth round-trip
    )
    return mcp_auth.build_authorization_url(
        authorization_endpoint=auth_endpoint,
        client_id=server.client_id,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
    )


# ---------- callback ----------


@callback_router.get("/auth/mcp/callback")
async def mcp_callback(state: str, code: str | None = None, error: str | None = None) -> Any:
    """Handle the OAuth callback. Exchanges code → tokens, stores them,
    redirects browser to the SPA's Customize view with a status param.
    """
    spa = _spa_base_url()
    if error:
        return RedirectResponse(
            url=f"{spa}/app/customize?mcp_authed=error&reason={error}",
            status_code=302,
        )
    payload = state_store.consume_state(state)
    if not payload or payload.get("purpose") != "mcp":
        return RedirectResponse(
            url=f"{spa}/app/customize?mcp_authed=error&reason=invalid_state",
            status_code=302,
        )
    if not code:
        return RedirectResponse(
            url=f"{spa}/app/customize?mcp_authed=error&reason=no_code",
            status_code=302,
        )
    user_id = payload["user_id"]
    server_id = payload["server_id"]
    code_verifier = payload["code_verifier"]
    redirect_uri = payload["redirect_uri"]

    server = storage.get_mcp_server(user_id=user_id, server_id=server_id)
    if server is None:
        return RedirectResponse(
            url=f"{spa}/app/customize?mcp_authed=error&reason=server_gone",
            status_code=302,
        )
    # Re-validate the persisted server URL — defense in depth against
    # registration-time TOCTOU (DNS changes between register + callback).
    try:
        _validate_mcp_server_url(server.url)
    except HTTPException:
        return RedirectResponse(
            url=f"{spa}/app/customize?mcp_authed=error&reason=blocked_url",
            status_code=302,
        )
    try:
        prm = await mcp_auth.discover_resource_metadata(server.url)
        as_url = (
            str(prm.authorization_servers[0])
            if prm.authorization_servers
            else server.url
        )
        as_meta = await mcp_auth.discover_auth_server_metadata(as_url)
        tok = await mcp_auth.exchange_code(
            token_endpoint=str(as_meta.token_endpoint),
            client_id=server.client_id,
            redirect_uri=redirect_uri,
            code=code,
            code_verifier=code_verifier,
        )
    except Exception as exc:
        logger.warning("mcp.callback.token_exchange_failed user=%s server=%s %r",
                       user_id, server_id, exc)
        return RedirectResponse(
            url=f"{spa}/app/customize?mcp_authed=error&reason=token_exchange",
            status_code=302,
        )

    expires_at = int(time.time()) + (tok.expires_in or 3600)
    storage.put_mcp_token(
        user_id=user_id,
        server_id=server_id,
        access_token_ciphertext=crypto.encrypt_blob(tok.access_token),
        refresh_token_ciphertext=(
            crypto.encrypt_blob(tok.refresh_token) if tok.refresh_token else None
        ),
        expires_at=expires_at,
        granted_scope=tok.scope or "",
    )
    storage.set_mcp_server_auth_status(
        user_id=user_id,
        server_id=server_id,
        status=MCPServerAuthStatus.ACTIVE,
    )
    return RedirectResponse(
        url=f"{spa}/app/customize?mcp_authed=ok&server_id={server_id}",
        status_code=302,
    )


# ---------- patch / delete / reauth ----------


class _PatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    globally_enabled: bool | None = None


@router.patch("/api/mcp/servers/{server_id}", status_code=204)
def patch_server(
    server_id: str,
    body: _PatchRequest,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> None:
    s = storage.get_mcp_server(user_id=claims["sub"], server_id=server_id)
    if s is None:
        raise HTTPException(status_code=404, detail="server not found")
    storage.update_mcp_server(
        user_id=claims["sub"],
        server_id=server_id,
        name=body.name,
        globally_enabled=body.globally_enabled,
    )


@router.delete("/api/mcp/servers/{server_id}", status_code=204)
def delete_server(
    server_id: str,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> None:
    s = storage.get_mcp_server(user_id=claims["sub"], server_id=server_id)
    if s is None:
        raise HTTPException(status_code=404, detail="server not found")
    # Best-effort revoke is out of v1 — RFC 7009 token revocation is
    # optional in OAuth 2.1 and the MCP servers we target don't advertise
    # it consistently. Delete + let the server's own token expiry handle
    # cleanup. Tracked as v2.
    storage.delete_mcp_server(user_id=claims["sub"], server_id=server_id)


@router.post("/api/mcp/servers/{server_id}/reauth", response_model=_RegisterResponse)
async def reauth_server(
    server_id: str,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> _RegisterResponse:
    s = storage.get_mcp_server(user_id=claims["sub"], server_id=server_id)
    if s is None:
        raise HTTPException(status_code=404, detail="server not found")
    _validate_mcp_server_url(s.url)
    redirect_uri = _redirect_uri()
    try:
        prm = await mcp_auth.discover_resource_metadata(s.url)
        as_url = (
            str(prm.authorization_servers[0])
            if prm.authorization_servers
            else s.url
        )
        as_meta = await mcp_auth.discover_auth_server_metadata(as_url)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Failed to refresh discovery metadata"
        ) from exc
    auth_url = _begin_auth_flow(
        user_id=claims["sub"],
        server=s,
        auth_endpoint=str(as_meta.authorization_endpoint),
        redirect_uri=redirect_uri,
    )
    return _RegisterResponse(server_id=s.server_id, auth_start_url=auth_url)


# ---------- per-chat override ----------


class _ChatMCPSettingsOut(BaseModel):
    mode: ChatMCPMode
    explicit_server_ids: list[str]


@router.get("/api/chats/{chat_id}/mcp", response_model=_ChatMCPSettingsOut)
async def get_chat_mcp_settings_route(
    chat_id: str,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> _ChatMCPSettingsOut:
    await _load_owned_chat(chat_id, claims["sub"])
    s = storage.get_chat_mcp_settings(chat_id)
    return _ChatMCPSettingsOut(mode=s.mode, explicit_server_ids=s.explicit_server_ids)


class _ChatMCPSettingsIn(BaseModel):
    mode: ChatMCPMode
    explicit_server_ids: list[str] = Field(default_factory=list)


@router.put("/api/chats/{chat_id}/mcp", status_code=204)
async def put_chat_mcp_settings_route(
    chat_id: str,
    body: _ChatMCPSettingsIn,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> None:
    await _load_owned_chat(chat_id, claims["sub"])
    storage.put_chat_mcp_settings(
        ChatMCPSettings(
            chat_id=chat_id,
            mode=body.mode,
            explicit_server_ids=body.explicit_server_ids,
        )
    )
