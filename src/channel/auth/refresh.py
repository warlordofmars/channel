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
  path-scoped to ``/auth``. XSS cannot read it, and the path scope keeps
  it off every ``/api/*`` request — the app's entire data surface.
  (#291 scoped it to ``/auth/refresh`` exactly; #292 widened it by one
  segment because ``POST /auth/logout`` has to *read* the cookie to
  revoke the family, and a browser only sends a cookie to paths under
  its ``Path``. See :func:`set_refresh_cookie`.)
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
rejected before any token is consumed.

The exact contract is **a non-empty value is required, and its content
is never inspected** — send any non-empty string. Requiring non-empty
rather than bare presence is deliberate: an empty header is
indistinguishable from an absent one in most proxy and client stacks
(several strip empty headers outright), so accepting it would make the
gate's behaviour depend on intermediaries rather than on our own rule.
It is also the stricter of the two readings, which is the right default
for a CSRF control.

Not in scope here
-----------------
The sessions API (#293), rate limiting + EMF counters (#294). This
module deliberately only consumes and rotates; #290's storage layer owns
the rest. Minting the session's *first* refresh token belongs to the
Google callback (#292, :mod:`channel.auth.mgmt_auth`) — but the cookie
itself is defined here, and both that callback and ``POST /auth/logout``
drive it through :func:`set_refresh_cookie` /
:func:`clear_refresh_cookie` rather than re-spelling the attribute
matrix. Three call sites writing the same ``Set-Cookie`` by hand is
exactly how a mint/rotate mismatch silently breaks the web flow.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from channel.auth.mgmt_auth import make_mgmt_user
from channel.auth.tokens import MGMT_JWT_TTL_SECONDS, issue_mgmt_jwt
from channel.logging_config import fingerprint_id, get_logger
from channel.models import RefreshConsumeOutcome, RefreshToken

# ``_parse_iso_utc`` is module-private by naming convention but is the
# canonical parser for this table's ISO columns — ``consume_refresh_token``
# runs it against ``absolute_expires_at`` (the very field read below)
# earlier in this same request. Re-implementing the normalisation here
# would be exactly the drift it exists to prevent.
from channel.storage import _parse_iso_utc, consume_refresh_token

router = APIRouter(tags=["mgmt-auth"])
logger = get_logger(__name__)

# Cookie carrying the web SPA's refresh token. Path-scoped so the
# browser keeps it off every ``/api/*`` request — the app's whole data
# surface travels without it.
#
# ``/auth`` rather than ``/auth/refresh`` (#292): a browser sends a
# cookie only to request paths *under* its ``Path``, and
# ``POST /auth/logout`` must read this cookie to revoke the token family
# server-side. Scoped to ``/auth/refresh`` the logout endpoint never
# receives it, so "log out" would clear the browser's copy while leaving
# a live 30-day credential on the server — the exact hole #292's third
# acceptance criterion closes. Widening admits four sibling endpoints
# (``/auth/login``, ``/auth/callback``, ``/auth/logout``,
# ``/auth/mcp/callback``), all first-party, all low-traffic, and none of
# which echo cookies back; ``SameSite=Strict`` additionally keeps the
# cookie off the two that are reached by cross-site redirect.
REFRESH_COOKIE_NAME = "channel_refresh"
REFRESH_COOKIE_PATH = "/auth"

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

    Parsed with storage's ``_parse_iso_utc`` rather than a bare
    ``fromisoformat``: this runs *after* ``consume_refresh_token`` has
    already revoked the presented row and minted its successor, so an
    ``aware - naive`` ``TypeError`` here would 500 a client that has
    just irrecoverably spent its refresh token — on the one path whose
    whole job is to stop exactly that.
    """

    remaining = _parse_iso_utc(absolute_expires_at) - datetime.now(timezone.utc)
    return max(0, int(remaining.total_seconds()))


def set_refresh_cookie(response: Response, raw_token: str, absolute_expires_at: str) -> None:
    """Attach the web transport's refresh cookie to ``response``.

    The single definition of the attribute matrix. Three flows write
    this cookie — the Google callback minting a session's first token
    (#292), this module rotating it, and any future re-issue — and they
    must agree on every attribute or the browser silently ends up with
    two cookies (``Path`` and ``Name`` together form a cookie's identity,
    so a mismatched ``Path`` *adds* rather than replaces). A helper is
    the cheapest way to make that class of bug unrepresentable.

    ``max_age`` is pinned to the family's absolute deadline rather than
    the idle window — see :func:`_cookie_max_age`.
    """

    response.set_cookie(
        REFRESH_COOKIE_NAME,
        raw_token,
        max_age=_cookie_max_age(absolute_expires_at),
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response) -> None:
    """Expire the refresh cookie in the caller's browser.

    Attributes must match :func:`set_refresh_cookie`'s for the deletion
    to land on the same cookie. Note a response is free to clear a
    cookie scoped to a path it was not itself served from — ``Path`` is
    chosen by the server on the way out, not constrained by the request
    URL — which is what lets ``/auth/logout`` retire this cookie.

    Clearing is never the whole job on the logout path: the browser's
    copy going away does nothing about a copy already exfiltrated, so
    the server-side family revoke is what actually ends the session.
    """

    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="strict",
    )


def _no_store(response: JSONResponse) -> JSONResponse:
    """Mark a response uncacheable.

    Every response this module returns is a token response — the 200
    carries a freshly minted access JWT and, on the body transport, the
    plaintext rotated refresh token. RFC 6749 §5.1 and RFC 9700 both
    require ``no-store`` on those. POST responses are already exempt
    from heuristic caching (RFC 7234 §4), so this is belt-and-braces
    rather than a live hole — but it is the highest-value body the app
    emits, and the repo already sets the header on far less sensitive
    payloads (``api/prefs.py``, ``api/mcp.py``).
    """

    response.headers["Cache-Control"] = "no-store"
    return response


def _rejection(*, via_cookie: bool) -> JSONResponse:
    """401 for any unusable refresh token, clearing a dead cookie.

    Returned rather than raised because ``HTTPException`` is rendered by
    a handler that never sees this function's ``Set-Cookie``. Clearing
    matters: a browser holding a revoked or expired cookie would
    otherwise keep replaying it on every refresh attempt, and on the
    reuse path each replay re-triggers the family cascade in
    :func:`~channel.storage.consume_refresh_token`.
    """

    response = _no_store(JSONResponse(status_code=401, content={"detail": _REJECTION_DETAIL}))
    if via_cookie:
        clear_refresh_cookie(response)
    return response


def _issued(row: RefreshToken, rotated: str, *, via_cookie: bool) -> JSONResponse:
    """200 carrying a fresh access JWT and the rotated refresh token.

    Claims are rebuilt through the same helper the login flow uses, so a
    refreshed session cannot drift from a freshly logged-in one.

    ``display_name`` was the one field that used to drift: #290's row
    stored only ``user_id``, so #291 could do no better than the email's
    local-part, which ``Sidebar.jsx`` / ``ChatHome.jsx`` render as the
    *legacy token* fallback rather than a normal state. #292 closed that
    by persisting Google's ``name`` claim on the row at login and
    carrying it through every rotation, so the value below is the real
    name for any session minted since. The local-part fallback survives
    for exactly two cases: a family minted before #292, and an account
    Google gave no ``name`` for.

    ``role``, by contrast, is deliberately *recomputed* by
    ``make_mgmt_user`` on every call rather than stored — an admin grant
    or revocation lands on the next refresh instead of the next full
    sign-in.
    """

    user = make_mgmt_user(row.user_id, row.display_name or row.user_id.split("@")[0])
    payload: dict[str, Any] = {
        "access_token": issue_mgmt_jwt(user),
        "token_type": "bearer",
        "expires_in": MGMT_JWT_TTL_SECONDS,
    }
    if not via_cookie:
        payload["refresh_token"] = rotated

    response = _no_store(JSONResponse(content=payload))
    if via_cookie:
        set_refresh_cookie(response, rotated, row.absolute_expires_at)
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
        # Non-empty required; the value itself is never inspected (see
        # the module docstring's CSRF section for why empty is treated as
        # absent). Rejected before the token is even read: a request that
        # cannot prove it was made by our own JavaScript must not be
        # allowed to burn a rotation, which would log the real client out.
        raise HTTPException(status_code=403, detail="Missing X-Channel-Refresh header")

    body_token = (body.refresh_token or "").strip() if body else ""
    raw_cookie = request.cookies.get(REFRESH_COOKIE_NAME)
    cookie_token = (raw_cookie or "").strip()
    # Body wins: an Electron client that also happens to carry a cookie
    # means the explicit credential, not an incidental one.
    presented = body_token or cookie_token
    # Keyed off the cookie's *presence*, not its stripped value, so a
    # whitespace-only cookie still gets cleared on the way out instead of
    # sitting in the jar forever failing every subsequent refresh.
    via_cookie = not body_token and raw_cookie is not None

    if not presented:
        return _rejection(via_cookie=via_cookie)

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
