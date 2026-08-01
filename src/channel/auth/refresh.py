# Copyright (c) 2026 John Carter. All rights reserved.
"""
Refresh-token exchange — ``POST /auth/refresh`` (#291, epic #241).

Trades a valid refresh token for a freshly minted 1-hour access JWT,
rotating the refresh token in the same round trip. This is the endpoint
that makes the short access-token TTL invisible to users: the SPA (#295)
and the desktop app (#297) call it silently rather than sending anyone
back through Google.

**The refresh token is the credential here, so the route is
unauthenticated.** Requiring a valid access JWT would defeat the purpose
— by the time a client needs to refresh, the access token it holds has
usually already expired.

Transport is pluggable, per the epic's design decisions (Q4/Q5):

- **Web SPA — HttpOnly cookie.** The token arrives in the
  ``channel_refresh`` cookie and the rotated successor goes back in a
  ``Set-Cookie`` with ``HttpOnly`` + ``Secure`` + ``SameSite=Strict``,
  path-scoped to ``/auth/refresh``. XSS cannot read it, and the path
  scope means it is not attached to any other request in the app.
- **Desktop / future mobile — request body.** Electron persists the
  token in the OS keychain via ``safeStorage`` (#297), so it sends
  ``{"refresh_token": ...}`` and gets the successor back in the JSON
  body. Cookies do not fit that storage model.

The response carries the rotated token **only** on the body transport.
A cookie client must never see the plaintext in JavaScript-readable
form; that is the entire point of the HttpOnly transport.

CSRF
----
``SameSite=Strict`` already keeps the cookie off cross-site requests,
and this is the only ambient-cookie-reachable endpoint in the app. The
required ``X-Channel-Refresh`` header is the second layer: a
cross-origin attacker cannot set a custom header without a CORS
preflight this API will not grant, so a forged form/image POST is
rejected before any token is consumed. The header's *value* is
irrelevant — its presence is what a forged request cannot fake.

Not in scope here
-----------------
Minting the first refresh token at login (#292), the sessions API
(#293), rate limiting + EMF counters (#294). This module deliberately
only consumes and rotates; #290's storage layer owns the rest.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from channel.auth.mgmt_auth import make_mgmt_user
from channel.auth.tokens import MGMT_JWT_TTL_SECONDS, issue_mgmt_jwt
from channel.logging_config import fingerprint_id, get_logger
from channel.models import RefreshConsumeOutcome, RefreshToken
from channel.storage import consume_refresh_token

router = APIRouter(tags=["mgmt-auth"])
logger = get_logger(__name__)

# Cookie carrying the web SPA's refresh token. Path-scoped so the
# browser attaches it to exactly one endpoint — every other request in
# the app travels without it.
REFRESH_COOKIE_NAME = "channel_refresh"
REFRESH_COOKIE_PATH = "/auth/refresh"

# CSRF guard. Matched case-insensitively by Starlette's header mapping,
# so the constant is stored lowercase.
REFRESH_CSRF_HEADER = "x-channel-refresh"

# Single rejection message for every failure mode. ``consume_refresh_token``
# distinguishes not-found / revoked / reused / idle-expired /
# absolute-expired, and the server logs which one, but the client is told
# only "this token is no good": the finer taxonomy is an oracle for an
# attacker probing the token space and the SPA's response is identical in
# every case (clear local state, go to /app/login).
_REJECTION_DETAIL = "Invalid or expired refresh token"


class RefreshRequest(BaseModel):
    """Body of ``POST /auth/refresh`` — desktop/mobile transport only.

    Optional in its entirety: a web client sends no body at all and
    presents the cookie instead. ``extra="forbid"`` keeps an unexpected
    field from being silently ignored on an auth-critical route.
    """

    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = None


def _cookie_max_age(absolute_expires_at: str) -> int:
    """Seconds until the family's absolute deadline, floored at zero.

    Pinned to ``absolute_expires_at`` rather than the idle window so a
    rotation cannot walk the cookie's lifetime past the deadline fixed at
    login — the same invariant :func:`~channel.storage.mint_refresh_token`
    enforces server-side, mirrored into the browser so the cookie dies
    with the family rather than lingering as a credential the server has
    already stopped honouring.
    """

    remaining = datetime.fromisoformat(absolute_expires_at) - datetime.now(timezone.utc)
    return max(0, int(remaining.total_seconds()))


def _rejection(*, via_cookie: bool) -> JSONResponse:
    """401 for any unusable refresh token, clearing a dead cookie.

    Returned rather than raised because ``HTTPException`` is rendered by
    a handler that never sees this function's ``Set-Cookie``. Clearing
    matters: a browser holding a revoked or expired cookie would
    otherwise keep replaying it on every refresh attempt, and on the
    reuse path each replay re-triggers the family cascade in
    :func:`~channel.storage.consume_refresh_token`.
    """

    response = JSONResponse(status_code=401, content={"detail": _REJECTION_DETAIL})
    if via_cookie:
        response.delete_cookie(
            REFRESH_COOKIE_NAME,
            path=REFRESH_COOKIE_PATH,
            httponly=True,
            secure=True,
            samesite="strict",
        )
    return response


def _issued(row: RefreshToken, rotated: str, *, via_cookie: bool) -> JSONResponse:
    """200 carrying a fresh access JWT and the rotated refresh token.

    .. warning::
       **``display_name`` degrades here, and #292 is where that gets
       fixed.** Claims are rebuilt through the same helper the login
       flow uses, so a refreshed session cannot drift from a freshly
       logged-in one — except in the one field the refresh row cannot
       supply. ``RefreshToken`` (#290) stores only ``user_id``, which
       equals the user's email in the current mgmt-JWT shape, so the
       best this can do for ``display_name`` is the email's local-part.
       Google's real name ("John Carter" → "john") is lost, and
       ``Sidebar.jsx`` / ``ChatHome.jsx`` treat that as the *legacy
       token* fallback rather than a normal state.

       This is unreachable today: nothing in ``src/`` mints a first
       refresh token — ``mint_refresh_token``'s only production caller
       is ``consume_refresh_token`` rotating an existing family — so no
       client can reach this branch until #292 wires minting into the
       Google callback. #292 is also the only place with the name to
       persist (it is a claim on the Google ID token, present nowhere
       else), so the fix belongs there: carry ``display_name`` onto the
       refresh row and forward through rotation the way
       ``absolute_expires_at`` already is, then read it here.
    """

    user = make_mgmt_user(row.user_id, row.user_id.split("@")[0])
    payload: dict[str, Any] = {
        "access_token": issue_mgmt_jwt(user),
        "token_type": "bearer",
        "expires_in": MGMT_JWT_TTL_SECONDS,
    }
    if not via_cookie:
        payload["refresh_token"] = rotated

    response = JSONResponse(content=payload)
    if via_cookie:
        response.set_cookie(
            REFRESH_COOKIE_NAME,
            rotated,
            max_age=_cookie_max_age(row.absolute_expires_at),
            path=REFRESH_COOKIE_PATH,
            httponly=True,
            secure=True,
            samesite="strict",
        )
    logger.info(
        "auth.refresh rotated user=%s device=%s transport=%s",
        fingerprint_id(row.user_id),
        fingerprint_id(row.device_id),
        "cookie" if via_cookie else "body",
    )
    return response


@router.post("/auth/refresh", include_in_schema=False)
async def refresh_session(request: Request, body: RefreshRequest | None = None) -> JSONResponse:
    """Exchange a refresh token for a new access JWT + rotated refresh token.

    Returns 403 when the CSRF header is absent, 401 for any unusable
    token, and 200 with ``{access_token, token_type, expires_in}`` (plus
    ``refresh_token`` on the body transport) on success.

    A client with no refresh token at all also gets a 401 — that is the
    epic's migration path, not an error: a session predating the refresh
    flow keeps using its existing long-lived access token until it
    expires, and the SPA treats this 401 as "nothing to refresh" rather
    than "signed out".
    """

    if not request.headers.get(REFRESH_CSRF_HEADER):
        # Rejected before the token is even read: a request that cannot
        # prove it was made by our own JavaScript must not be allowed to
        # burn a rotation, which would log the real client out.
        raise HTTPException(status_code=403, detail="Missing X-Channel-Refresh header")

    body_token = (body.refresh_token or "").strip() if body else ""
    cookie_token = (request.cookies.get(REFRESH_COOKIE_NAME) or "").strip()
    # Body wins: an Electron client that also happens to carry a cookie
    # means the explicit credential, not an incidental one.
    presented = body_token or cookie_token
    via_cookie = not body_token and bool(cookie_token)

    if not presented:
        return _rejection(via_cookie=False)

    result = consume_refresh_token(presented)
    rotated, row = result.raw_token, result.token
    # ``rotated``/``row`` are non-None whenever the outcome is OK — the
    # RefreshConsumeResult validator enforces it. The explicit checks are
    # type narrowing, folded into this branch so there is no unreachable
    # arm to exclude from coverage.
    if result.outcome is not RefreshConsumeOutcome.OK or rotated is None or row is None:
        logger.warning("auth.refresh rejected outcome=%s", result.outcome.value)
        return _rejection(via_cookie=via_cookie)

    return _issued(row, rotated, via_cookie=via_cookie)
