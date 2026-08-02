# Copyright (c) 2026 John Carter. All rights reserved.
"""
Management UI authentication — Google OAuth login flow for human users.

Issues short-lived management JWTs (typ=mgmt) stored in the browser's
localStorage.  Pending OAuth state is persisted in DynamoDB (see
:mod:`channel.auth.state_store`) so concurrent Lambda containers can
share state — the previous in-process dict broke under concurrent
execution because the callback could hit a different warm container
than the one that issued the state.

Refresh tokens (#292, epic #241)
--------------------------------
``/auth/callback`` is where a session's *first* refresh token is minted.
That matters more than it sounds: #291 cut the access token's TTL to one
hour, and until this module started minting, ``mint_refresh_token``'s
only production caller was ``consume_refresh_token`` rotating a family
that nothing had ever created — so ``POST /auth/refresh`` could only
ever 401 and every user was bounced back through Google hourly.

Transport follows the client, mirroring ``/auth/refresh``'s own split:
the web flow sets the ``HttpOnly`` cookie on the login-completion page,
while the desktop loopback redirect carries ``{token, refresh_token}``
in its query string (Electron persists it via ``safeStorage`` — #297).
The cookie's attribute matrix lives in :mod:`channel.auth.refresh`, not
here; this module drives it through ``set_refresh_cookie`` so the mint
and the rotation cannot disagree.

Routes:
  GET /auth/login    — redirect to Google (or issue bypass JWT in non-prod)
  GET /auth/callback — handle Google callback, issue mgmt JWT + refresh token
"""

from __future__ import annotations

import html
import os
import re
import secrets
from typing import Any, TypeVar
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response
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
from channel.logging_config import fingerprint_id, get_logger
from channel.models import RefreshToken
from channel.storage import mint_refresh_token

router = APIRouter(tags=["mgmt-auth"])
logger = get_logger(__name__)

# Preserves the concrete response type through ``_finish_login`` so the
# route signatures keep saying HTMLResponse / RedirectResponse.
_ResponseT = TypeVar("_ResponseT", bound=Response)

# Entropy for the server-side opaque device id. Epic #241 Q2 settled
# this in favour of a server-generated opaque value over anything
# client-derived: a browser fingerprint is neither stable nor private,
# and a client-supplied id would let a caller choose which token-family
# its session joins. 128 bits is well past collision relevance inside a
# single user's partition, which is the only scope the id is ever
# compared in (``REFRESH_USER#{user_id}``).
_DEVICE_ID_BYTES = 16

# Opt-in ONLY on the exact string "1" — matching the repo's default-off
# flag convention (CHANNEL_ENABLE_DEBUG_ENDPOINTS, CHANNEL_MCP_ALLOW_LOCALHOST,
# CHANNEL_CLOCK_TOOL_ENABLED, ...). Deliberately NOT `bool(...)`: every
# non-empty string is truthy, so `CHANNEL_BYPASS_GOOGLE_AUTH=0` — the obvious
# way to turn a flag off — used to *enable* the auth bypass. A security
# control must fail closed on anything it doesn't explicitly recognise.
_BYPASS = os.environ.get("CHANNEL_BYPASS_GOOGLE_AUTH") == "1"
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


def make_mgmt_user(email: str, display_name: str) -> dict[str, Any]:
    """Build the user dict :func:`issue_mgmt_jwt` turns into claims.

    Public (not ``_``-prefixed) because ``POST /auth/refresh`` (#291)
    re-mints access tokens outside the login flow and must produce the
    *same* claim shape — a second, drifting copy of this mapping in
    ``auth/refresh.py`` is exactly the bug that would let a refreshed
    session carry a different ``role`` rule than a freshly logged-in
    one. ``role`` is recomputed from the allowlist on every call, so an
    admin grant or revocation takes effect on the next refresh rather
    than at the next full sign-in.
    """
    return {
        "user_id": email,
        "email": email,
        "display_name": display_name,
        "role": "admin" if is_admin_email(email) else "user",
    }


def _finish_login(response: _ResponseT, minted: tuple[str, RefreshToken] | None) -> _ResponseT:
    """Attach this login's refresh cookie — or clear whatever was there.

    **Every login-completing response must go through here**, including
    the ones that mint nothing. ``POST /auth/refresh`` is unauthenticated
    by design (the access token it renews has usually expired), so the
    cookie *is* the identity: the endpoint trusts ``row.user_id`` of
    whatever cookie arrives. A login that leaves a previous account's
    cookie untouched therefore hands the new user the old user's
    session on the next refresh.

    That is reachable on any deployed non-prod stack, where
    ``CHANNEL_BYPASS_GOOGLE_AUTH=1`` is always set: sign in as A through
    Google, then hit ``/auth/login?test_email=b@example.com``.
    localStorage holds B's access token while the jar still holds A's
    30-day refresh family — and the next refresh returns an access token
    for **A**. The fail-soft mint path has the same shape in prod (A
    signs in, B signs in on the same browser, minting hits a storage
    blip, B silently retains A's family).

    Clearing costs nothing when no cookie exists, so the invariant is
    cheap to hold unconditionally. The desktop transports pass
    ``minted=None`` deliberately: they carry the token in the loopback
    query string, and the browser that ran the OAuth dance is the user's
    *default* browser, which may well be holding a web session's cookie.
    """

    from channel.auth.refresh import (  # noqa: PLC0415
        clear_refresh_cookie,
        set_refresh_cookie,
    )

    if minted is None:
        clear_refresh_cookie(response)
    else:
        set_refresh_cookie(response, minted[0], minted[1].absolute_expires_at)
    return response


def _mint_session_refresh_token(email: str, display_name: str) -> tuple[str, RefreshToken] | None:
    """Mint this login's first refresh token, or ``None`` if minting failed.

    Every sign-in starts a new token-family under a fresh opaque
    ``device_id``. Signing in twice from the same browser therefore
    produces two families, which #293's session list shows as two
    entries — correct rather than a leak: the server has no
    device-stable identifier it can trust, each family is independently
    revocable, and both still die at their own 30-day ceiling. Binding
    families to a long-lived device *cookie* instead would trade that
    for a persistent tracking identifier, which is a worse deal.

    **Fail-soft.** A minting failure degrades the session to
    access-token-only — the user is signed in, just facing a re-login in
    an hour — whereas raising would convert a DynamoDB blip into a total
    login outage. That is the same best-effort posture ``/auth/logout``
    takes for its denylist and audit writes, and the wrong-way-round
    version of this trade is the more damaging one. The failure is
    logged loudly; #294 adds the EMF counter that makes it alarmable.
    """

    try:
        return mint_refresh_token(
            user_id=email,
            device_id=secrets.token_urlsafe(_DEVICE_ID_BYTES),
            display_name=display_name,
        )
    except Exception:
        logger.exception(
            "auth.login refresh-token mint failed user=%s — session is access-token-only",
            fingerprint_id(email),
        )
        return None


@router.get("/auth/login", include_in_schema=False)
async def mgmt_login(request: Request) -> RedirectResponse:
    """Redirect the management UI user to Google for authentication.

    In CHANNEL_BYPASS_GOOGLE_AUTH=1 mode (non-prod), issue a synthetic JWT
    directly when a test_email query parameter is provided, so e2e tests can run
    without a real Google account.  Any other value — including "0" and
    "false" — leaves the bypass disabled.

    Optionally accepts ``desktop_callback`` and ``state`` query parameters for
    the Electron loopback OAuth flow.  When present both are validated strictly:
    ``desktop_callback`` must be a loopback-only http URL on /callback, and
    ``state`` must be exactly 43 base64url characters.  The caller-supplied state
    is reused as both the DynamoDB key and Google's CSRF nonce so the loopback
    server can verify the same value on return.
    """
    test_email = request.query_params.get("test_email")
    if _BYPASS and test_email:
        user = make_mgmt_user(test_email, test_email.split("@")[0])
        token = issue_mgmt_jwt(user)
        # Mints nothing (see the module docstring on why the bypass stays
        # capped at the 1h access token) — so it must clear, or the jar
        # keeps the previous account's refresh family. See _finish_login.
        return _finish_login(_html_redirect(token), None)  # type: ignore[return-value]

    desktop_callback = request.query_params.get("desktop_callback")
    caller_state = request.query_params.get("state")
    payload: dict[str, Any] = {}

    if desktop_callback is not None:
        validated = _validate_desktop_callback(desktop_callback)
        if validated is None or not caller_state or not _DESKTOP_STATE_RE.match(caller_state):
            raise HTTPException(status_code=400, detail="Invalid desktop_callback or state")
        # Desktop bypass — mint a synthetic JWT and redirect to the loopback
        # without involving Google or DynamoDB. Saves devs from configuring
        # a separate Google OAuth flow for local Electron iteration.
        #
        # Requires BOTH _BYPASS (= CHANNEL_BYPASS_GOOGLE_AUTH=1) AND
        # CHANNEL_DESKTOP_DEV_EMAIL explicitly set. Deployed dev sets only
        # _BYPASS (for the ?test_email= e2e shortcut) and NOT dev_email, so
        # real desktop sign-in on deployed dev still goes through Google.
        # Only `inv desktop-dev` (which sets both) gets the short-circuit.
        # Without the dev_email gate, every desktop sign-in on the deployed
        # dev environment would silently auto-log-in as the placeholder
        # account — the bug fixed here.
        dev_email = os.environ.get("CHANNEL_DESKTOP_DEV_EMAIL")
        if _BYPASS and dev_email:
            from urllib.parse import urlencode

            user = make_mgmt_user(dev_email, dev_email.split("@")[0])
            token = issue_mgmt_jwt(user)
            qs = urlencode({"token": token, "state": caller_state})
            return _finish_login(RedirectResponse(f"{validated}?{qs}", status_code=302), None)
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
    user = make_mgmt_user(email, display_name)
    token = issue_mgmt_jwt(user)
    logger.info("Management login: %s (role=%s)", email, user["role"])

    # Defense-in-depth: validate again at consume time. The same check ran
    # at store time (mgmt_login), so failure here means the record was
    # tampered with at rest or written by an unvalidated code path.
    desktop_callback = record.get("desktop_callback")
    if desktop_callback and _validate_desktop_callback(desktop_callback) is None:
        logger.warning("Stored desktop_callback failed re-validation: %r", desktop_callback)
        raise HTTPException(status_code=400, detail="Invalid stored desktop_callback")

    # Minted only after every rejection path above has been cleared, so a
    # login that ends in a 400/403 never leaves an orphaned live row
    # behind in the user's token partition.
    minted = _mint_session_refresh_token(email, display_name)

    if desktop_callback:
        from urllib.parse import urlencode

        params = {"token": token, "state": state}
        if minted is not None:
            # Loopback-only (``_validate_desktop_callback`` pins host and
            # scheme), and the access token already rides the same query
            # string, so this adds no transport the flow did not have.
            params["refresh_token"] = minted[0]
        qs = urlencode(params)
        # ``None`` even though minting succeeded: the loopback query
        # string is this transport's carrier, and the browser running the
        # OAuth dance may still hold a *web* session's cookie for a
        # different account. Clearing keeps the two transports from
        # crossing.
        return _finish_login(RedirectResponse(f"{desktop_callback}?{qs}", status_code=302), None)

    return _finish_login(_html_redirect(token), minted)
