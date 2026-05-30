# Copyright (c) 2026 John Carter. All rights reserved.
"""
Management UI authentication — Google OAuth login flow for human users.

Issues short-lived management JWTs (typ=mgmt) stored in the browser's
localStorage.  Pending OAuth state is persisted in DynamoDB (see
:mod:`starter.auth.state_store`) so concurrent Lambda containers can
share state — the previous in-process dict broke under concurrent
execution because the callback could hit a different warm container
than the one that issued the state.

Routes:
  GET /auth/login    — redirect to Google (or issue bypass JWT in non-prod)
  GET /auth/callback — handle Google callback, issue mgmt JWT
"""

from __future__ import annotations

import html
import os
import re
import secrets
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from channel.auth import state_store
from channel.auth.google import (
    exchange_google_code,
    google_authorization_url,
    is_admin_email,
    is_email_allowed,
    verify_google_id_token,
)
from channel.auth.tokens import ISSUER, issue_mgmt_jwt
from channel.logging_config import get_logger

router = APIRouter(tags=["mgmt-auth"])
logger = get_logger(__name__)

_BYPASS = bool(os.environ.get("STARTER_BYPASS_GOOGLE_AUTH"))
_STATE_TTL_SECONDS = 600  # 10 minutes

_DESKTOP_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}


def _validate_desktop_callback(callback: str) -> str | None:
    """Return the callback if it's a safe loopback /callback URL, else None."""
    try:
        u = urlparse(callback)
        if u.scheme != "http":
            return None
        if u.hostname not in _LOOPBACK_HOSTS:
            return None
        if not u.port:
            return None
        if u.path != "/callback":
            return None
        if u.query or u.fragment:
            return None
    except Exception:
        return None
    return callback

# Redirect target after successful login — drops the user back into the
# authenticated chat app, not the marketing landing page.
_UI_ROOT = "/app"


def _mgmt_callback_uri() -> str:
    return f"{ISSUER}/auth/callback"


def _create_pending_state() -> str:
    state = secrets.token_urlsafe(32)
    state_store.put_state(state, ttl_seconds=_STATE_TTL_SECONDS)
    return state


def _consume_pending_state(state: str) -> bool:
    """Return True if state was present, unexpired, and successfully consumed."""
    return state_store.consume_state(state) is not None


def _html_redirect(jwt_token: str) -> HTMLResponse:
    """Return a minimal HTML page that writes the JWT to localStorage and redirects."""
    safe_token = html.escape(jwt_token, quote=True)
    body = (
        "<!DOCTYPE html><html><head><title>Logging in…</title></head><body>"
        "<script>"
        f"localStorage.setItem('starter_mgmt_token', '{safe_token}');"
        f"location.replace('{_UI_ROOT}');"
        "</script>"
        "<noscript>JavaScript is required to complete login.</noscript>"
        "</body></html>"
    )
    return HTMLResponse(content=body)


def _make_user(email: str, display_name: str) -> dict[str, Any]:
    return {
        "user_id": email,
        "email": email,
        "display_name": display_name,
        "role": "admin" if is_admin_email(email) else "user",
    }


@router.get("/auth/login", include_in_schema=False)
async def mgmt_login(request: Request) -> RedirectResponse:
    """Redirect the management UI user to Google for authentication.

    In STARTER_BYPASS_GOOGLE_AUTH mode (non-prod), issue a synthetic JWT directly
    when a test_email query parameter is provided, so e2e tests can run without
    a real Google account.

    Optionally accepts ``desktop_callback`` and ``state`` query parameters for
    the Electron loopback OAuth flow.  When present both are validated strictly:
    ``desktop_callback`` must be a loopback-only http URL on /callback, and
    ``state`` must be exactly 43 base64url characters.  The caller-supplied state
    is reused as both the DynamoDB key and Google's CSRF nonce so the loopback
    server can verify the same value on return.
    """
    test_email = request.query_params.get("test_email")
    if _BYPASS and test_email:
        user = _make_user(test_email, test_email.split("@")[0])
        token = issue_mgmt_jwt(user)
        return _html_redirect(token)  # type: ignore[return-value]

    desktop_callback = request.query_params.get("desktop_callback")
    caller_state = request.query_params.get("state")
    payload: dict[str, Any] = {}

    if desktop_callback is not None:
        validated = _validate_desktop_callback(desktop_callback)
        if validated is None or not caller_state or not _DESKTOP_STATE_RE.match(caller_state):
            raise HTTPException(status_code=400, detail="Invalid desktop_callback or state")
        state = caller_state
        payload["desktop_callback"] = validated
    else:
        state = secrets.token_urlsafe(32)

    state_store.put_state(state, payload=payload or None, ttl_seconds=_STATE_TTL_SECONDS)
    url = google_authorization_url(state, _mgmt_callback_uri())
    return RedirectResponse(url, status_code=302)


@router.get(
    "/auth/callback",
    include_in_schema=False,
    response_model=None,
    responses={400: {"description": "Invalid Google OAuth callback"}},
)
async def mgmt_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse | RedirectResponse:
    """Handle the Google OAuth callback for the management UI."""
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")

    record = state_store.consume_state(state)
    if record is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    try:
        id_token = await exchange_google_code(code, _mgmt_callback_uri())
        claims = await verify_google_id_token(id_token)
    except Exception as exc:
        logger.warning("Google token exchange failed: %s", exc)
        raise HTTPException(status_code=400, detail="Failed to verify Google identity") from exc

    if not claims.get("email_verified"):
        raise HTTPException(status_code=400, detail="Google email is not verified")

    email: str = claims["email"]
    if not is_email_allowed(email):
        logger.warning("Management login rejected — email not in allowlist: %s", email)
        raise HTTPException(status_code=403, detail="Email not authorised")

    display_name: str = claims.get("name", email.split("@")[0])
    user = _make_user(email, display_name)
    token = issue_mgmt_jwt(user)
    logger.info("Management login: %s (role=%s)", email, user["role"])

    desktop_callback = record.get("desktop_callback")
    if desktop_callback:
        # Defense-in-depth: validate again at consume time. The same check
        # ran at store time (mgmt_login), so failure here means the record
        # was tampered with at rest or written by an unvalidated code path.
        if _validate_desktop_callback(desktop_callback) is None:
            logger.warning("Stored desktop_callback failed re-validation: %r", desktop_callback)
            raise HTTPException(status_code=400, detail="Invalid stored desktop_callback")
        from urllib.parse import urlencode
        qs = urlencode({"token": token, "state": state})
        return RedirectResponse(f"{desktop_callback}?{qs}", status_code=302)

    return _html_redirect(token)
