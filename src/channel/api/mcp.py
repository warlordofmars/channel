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

import logging
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from channel import storage
from channel.api._auth import require_mgmt_user
from channel.api.chats import _load_owned_chat
from channel.auth import state_store
from channel.logging_config import fingerprint_id
from channel.mcp import auth as mcp_auth
from channel.mcp import crypto
from channel.mcp.url_guard import validate_mcp_server_url
from channel.models import (
    ChatMCPMode,
    ChatMCPSettings,
    MCPServer,
    MCPServerAuthStatus,
    MCPServerAuthType,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Routes that should NOT carry the /api prefix mount under this router.
callback_router = APIRouter()


_TOOL_PREFIX_OK_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")

# Hard upper bound on the normalized tool prefix. Strands prepends the
# prefix to every tool name surfaced to the model (``<prefix>_<tool>``);
# without a cap, a malicious client can blow up the model-visible tool
# spec, the SSE step-list UI, and operator logs. 32 chars is generous
# for any realistic server name + tool name combination.
_TOOL_PREFIX_MAX_LEN = 32


def _normalize_tool_prefix(value: str | None, fallback_url: str) -> str:
    """Pick a Strands-safe tool prefix.

    User-supplied prefixes are lowercased + filtered to ``[a-z0-9_]+``
    and then truncated to :data:`_TOOL_PREFIX_MAX_LEN` characters. On
    empty / missing input, fall back to a sanitised host token from
    the URL (e.g. ``https://hive.warlordofmars.net/mcp`` → ``hive``).
    """
    if value:
        cleaned = "".join(c for c in value.lower() if c in _TOOL_PREFIX_OK_CHARS)
        if cleaned:
            return cleaned[:_TOOL_PREFIX_MAX_LEN]
    host = urlparse(fallback_url).hostname or "mcp"
    derived = "".join(c for c in host.split(".")[0].lower() if c in _TOOL_PREFIX_OK_CHARS) or "mcp"
    return derived[:_TOOL_PREFIX_MAX_LEN]


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


def _customize_redirect(**params: str) -> str:
    """Build a Customize-view redirect URL with properly-encoded params.

    The OAuth ``error`` query value comes from the upstream auth server
    and can contain ``&``/``=``/other reserved characters; raw f-string
    interpolation would let those characters inject extra parameters
    into the SPA's URL. urlencode escapes them.
    """
    return f"{_spa_base_url()}/app/customize?{urlencode(params)}"


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
    return _ServerListResponse(servers=[_to_out(s) for s in servers])


def _to_out(s: MCPServer) -> _ServerOut:
    return _ServerOut(
        server_id=s.server_id,
        name=s.name,
        url=s.url,
        tool_prefix=s.tool_prefix,
        auth_status=s.auth_status,
        globally_enabled=s.globally_enabled,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


# Hard upper bound on a pasted static token. A PAT / bearer token is a
# small opaque string; 4 KB is far beyond any real token and keeps a
# hostile client from stuffing the encrypt path / DDB item with a
# multi-megabyte blob.
_STATIC_TOKEN_MAX_LEN = 4096


class _RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    url: str = Field(..., min_length=10, max_length=2048)
    # Bound at the request boundary too — _normalize_tool_prefix also
    # truncates to _TOOL_PREFIX_MAX_LEN, but a 422 here is friendlier
    # than silently dropping the tail of the user's input.
    tool_prefix: str | None = Field(default=None, max_length=_TOOL_PREFIX_MAX_LEN)
    # oauth_dcr (default; unchanged behaviour) or static_token (paste a
    # pre-issued bearer token / PAT). See #375.
    auth_type: MCPServerAuthType = MCPServerAuthType.OAUTH_DCR
    # Only meaningful when auth_type=static_token. min_length=1 rejects
    # an empty string at the boundary; the coherence check in
    # register_server rejects the None/wrong-auth_type combinations.
    token: str | None = Field(default=None, min_length=1, max_length=_STATIC_TOKEN_MAX_LEN)


class _RegisterResponse(BaseModel):
    server_id: str
    # None for a static-token registration — the SPA uses the presence of
    # this URL to decide whether to open the OAuth authorization tab.
    auth_start_url: str | None = None


# A static PAT has no refresh token and no token_endpoint, so it must
# never enter the refresh-skew path in get_valid_access_token. Persist a
# far-future expiry (~100 years) as a sentinel so the refresh check
# treats the token as valid forever. See #375.
_STATIC_TOKEN_EXPIRY_SENTINEL_SECONDS = 100 * 365 * 86400


def _register_static_token(
    *,
    user_id: str,
    name: str,
    url: str,
    tool_prefix: str | None,
    token: str,
) -> _RegisterResponse:
    """Register a static-token (PAT) MCP server.

    No discovery / DCR / auth-code dance: the caller supplies the bearer
    token directly, we encrypt + persist it via the existing MCPToken
    path, mark the server ACTIVE, and return no ``auth_start_url``.

    The token itself is never logged or echoed — only the fingerprinted
    user/server ids appear in the success log line, matching the
    callback path's ``fingerprint_id`` discipline.
    """
    tool_prefix_norm = _normalize_tool_prefix(tool_prefix, url)
    server = storage.create_mcp_server(
        user_id=user_id,
        name=name,
        url=url,
        client_id=None,
        tool_prefix=tool_prefix_norm,
        auth_type=MCPServerAuthType.STATIC_TOKEN,
        auth_status=MCPServerAuthStatus.ACTIVE,
    )
    expires_at = int(time.time()) + _STATIC_TOKEN_EXPIRY_SENTINEL_SECONDS
    storage.put_mcp_token(
        user_id=user_id,
        server_id=server.server_id,
        access_token_ciphertext=crypto.encrypt_blob(token),
        refresh_token_ciphertext=None,
        expires_at=expires_at,
        granted_scope="",
    )
    logger.info(
        "mcp.register.static_token",
        extra={
            "user_id_hash": fingerprint_id(user_id),
            "server_id_hash": fingerprint_id(server.server_id),
        },
    )
    return _RegisterResponse(server_id=server.server_id, auth_start_url=None)


@router.post("/api/mcp/servers", response_model=_RegisterResponse)
async def register_server(
    body: _RegisterRequest,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> _RegisterResponse:
    validate_mcp_server_url(body.url)
    # Branch on the credential type. Reject the incoherent combinations
    # at the boundary — never silently drop a pasted secret, never run a
    # tokenless static-token registration.
    if body.auth_type == MCPServerAuthType.STATIC_TOKEN:
        # Strip server-side and reject a blank/whitespace-only token — the
        # Field(min_length=1) only rejects the empty string, so "   " would
        # otherwise persist an unusable credential (easy for a non-UI
        # client to hit). Store the stripped value. See #375 / Copilot.
        token = body.token.strip() if body.token else ""
        if not token:
            raise HTTPException(
                status_code=400,
                detail="A static-token registration requires a token",
            )
        return _register_static_token(
            user_id=claims["sub"],
            name=body.name,
            url=body.url,
            tool_prefix=body.tool_prefix,
            token=token,
        )
    if body.token is not None:
        raise HTTPException(
            status_code=400,
            detail="A token may only be supplied with auth_type=static_token",
        )

    redirect_uri = _redirect_uri()
    try:
        prm = await mcp_auth.discover_resource_metadata(body.url)
        as_url = str(prm.authorization_servers[0]) if prm.authorization_servers else body.url
        as_meta = await mcp_auth.discover_auth_server_metadata(as_url)
        if not as_meta.registration_endpoint:
            # Distinguishable, machine-readable reason so the SPA can
            # branch on ``code`` (and steer the user into the
            # static-token path) instead of matching prose. This is the
            # ONLY branch that carries dcr_unsupported — genuine
            # URL/discovery failures keep their 400/502 shapes below.
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "dcr_unsupported",
                    "message": (
                        "MCP server's auth server does not support dynamic client registration"
                    ),
                },
            )
        client_info = await mcp_auth.register_dynamic_client(
            registration_endpoint=str(as_meta.registration_endpoint),
            redirect_uri=redirect_uri,
            client_name="Channel",
        )
    except HTTPException:
        raise
    except Exception as exc:
        # Don't log body.url verbatim — taint flow from a user-controlled
        # field to a log sink lets a malicious URL inject ANSI escapes or
        # newlines into operator-facing logs. The exception type tells us
        # enough for triage; the URL is reconstructable from the audit
        # trail / DDB row if needed.
        logger.warning(
            "mcp.register.discovery_or_dcr_failed exc_type=%s",
            type(exc).__name__,
        )
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
    # Callers guard static-token servers earlier (register never routes
    # them here; reauth checks auth_type first). This guard is the
    # remaining defense: an oauth_dcr row with no client_id is a corrupted
    # registration — surface it as a distinct 500 (not a "static token"
    # message) rather than building an authorization URL with a None
    # client_id. See #375 / Copilot review.
    if server.client_id is None:
        raise HTTPException(
            status_code=500,
            detail="Server is missing its OAuth client_id (corrupted registration)",
        )
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
async def mcp_callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> Any:
    """Handle the OAuth callback. Exchanges code → tokens, stores them,
    redirects browser to the SPA's Customize view with a status param.

    ``state`` is typed as optional so a missing param 302-redirects with
    ``reason=invalid_state`` (matching the other error paths) rather
    than FastAPI's default 422 — browser OAuth callbacks shouldn't show
    a raw FastAPI error page to the user.
    """
    if error:
        return RedirectResponse(
            url=_customize_redirect(mcp_authed="error", reason=error),
            status_code=302,
        )
    if not state:
        return RedirectResponse(
            url=_customize_redirect(mcp_authed="error", reason="invalid_state"),
            status_code=302,
        )
    payload = state_store.consume_state(state)
    if not payload or payload.get("purpose") != "mcp":
        return RedirectResponse(
            url=_customize_redirect(mcp_authed="error", reason="invalid_state"),
            status_code=302,
        )
    if not code:
        return RedirectResponse(
            url=_customize_redirect(mcp_authed="error", reason="no_code"),
            status_code=302,
        )
    user_id = payload["user_id"]
    server_id = payload["server_id"]
    code_verifier = payload["code_verifier"]
    redirect_uri = payload["redirect_uri"]

    server = storage.get_mcp_server(user_id=user_id, server_id=server_id)
    if server is None:
        return RedirectResponse(
            url=_customize_redirect(mcp_authed="error", reason="server_gone"),
            status_code=302,
        )
    # Re-validate the persisted server URL — defense in depth against
    # registration-time TOCTOU (DNS changes between register + callback).
    try:
        validate_mcp_server_url(server.url)
    except HTTPException:
        return RedirectResponse(
            url=_customize_redirect(mcp_authed="error", reason="blocked_url"),
            status_code=302,
        )
    # A static-token server never starts an OAuth flow, so no valid state
    # should ever resolve to one here. Key off auth_type for the clear
    # "not an OAuth server" case.
    if server.auth_type == MCPServerAuthType.STATIC_TOKEN:
        return RedirectResponse(
            url=_customize_redirect(mcp_authed="error", reason="not_oauth"),
            status_code=302,
        )
    # Distinct from the static-token case: an oauth_dcr row with no
    # client_id is a corrupted registration. exchange_code requires a
    # client_id, so redirect rather than pass None downstream.
    if server.client_id is None:
        return RedirectResponse(
            url=_customize_redirect(mcp_authed="error", reason="server_misconfigured"),
            status_code=302,
        )
    try:
        prm = await mcp_auth.discover_resource_metadata(server.url)
        as_url = str(prm.authorization_servers[0]) if prm.authorization_servers else server.url
        as_meta = await mcp_auth.discover_auth_server_metadata(as_url)
        tok = await mcp_auth.exchange_code(
            token_endpoint=str(as_meta.token_endpoint),
            client_id=server.client_id,
            redirect_uri=redirect_uri,
            code=code,
            code_verifier=code_verifier,
        )
    except Exception as exc:
        # user_id (JWT sub) and server_id (DDB UUID) came in via the
        # request — log via fingerprint_id so Sonar's taint engine
        # doesn't follow them through a format-string sink, and so
        # the hashes are stable for cross-cold-start correlation.
        # Matches the pattern used in api/chats.py + storage.py.
        logger.warning(
            "mcp.callback.token_exchange_failed",
            extra={
                "user_id_hash": fingerprint_id(user_id),
                "server_id_hash": fingerprint_id(server_id),
                "exc_type": type(exc).__name__,
            },
        )
        return RedirectResponse(
            url=_customize_redirect(mcp_authed="error", reason="token_exchange"),
            status_code=302,
        )

    # `or 3600` would treat expires_in=0 as missing — explicit None check
    # so a server-supplied "already expired" token honours that signal.
    expires_in = tok.expires_in if tok.expires_in is not None else 3600
    expires_at = int(time.time()) + expires_in
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
        url=_customize_redirect(mcp_authed="ok", server_id=server_id),
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
    # Guard static-token servers BEFORE discovery. A static-token server
    # has no OAuth flow to re-authorize, and it's precisely the kind of
    # server (e.g. GitHub's MCP) whose discovery may fail — running
    # discovery first would surface a misleading 502 instead of this
    # clear 400. Key off auth_type (not client_id) so a corrupted
    # oauth_dcr row with a missing client_id gets its own distinct error
    # in _begin_auth_flow rather than a misleading "static token" message.
    # See #375 / Copilot review.
    if s.auth_type == MCPServerAuthType.STATIC_TOKEN:
        raise HTTPException(
            status_code=400,
            detail="This server uses a static token — there is no OAuth flow to re-authorize",
        )
    validate_mcp_server_url(s.url)
    redirect_uri = _redirect_uri()
    try:
        prm = await mcp_auth.discover_resource_metadata(s.url)
        as_url = str(prm.authorization_servers[0]) if prm.authorization_servers else s.url
        as_meta = await mcp_auth.discover_auth_server_metadata(as_url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Failed to refresh discovery metadata") from exc
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
