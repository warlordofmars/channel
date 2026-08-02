# Copyright (c) 2026 John Carter. All rights reserved.
"""
Management session logout endpoint.

Exposes ``POST /auth/logout`` for the management UI. The endpoint validates
the bearer mgmt JWT, revokes it via the JTI denylist, writes an
``auth.logout`` audit entry, and returns 204.

Logout is a true server-side revocation point (#240): it adds the token's
``jti`` to the ``DENY#{jti}`` denylist (TTL = the token's own ``exp``), so
the token is rejected by ``decode_mgmt_jwt`` on every subsequent request
rather than remaining valid until expiry. That still matters with the 1h
access-token TTL (#291) — an hour is a long time to leave a stolen
laptop's session live. The endpoint stays JWT-authenticated, so logging
out proves the caller controls the token being revoked.

Two credentials, both revoked (#292)
------------------------------------
Since the Google callback started minting refresh tokens, denying the
access JTI alone is no longer a logout: it retires a credential with an
hour left on it while leaving a **30-day** refresh token live. Anyone
holding that token — including whoever is sitting at the browser the
user just "signed out" of — could trade it straight back for a fresh
access token. So logout also calls ``revoke_refresh_token``, which
revokes the presented token's whole device family rather than the single
row, closing the successor a concurrent rotation may have just minted.

The token reaches this endpoint the same two ways it reaches
``/auth/refresh``: the ``channel_refresh`` cookie for the web SPA (which
cannot read it — it is ``HttpOnly`` — and does not need to), or a
``{"refresh_token": ...}`` body for desktop, which keeps it in the OS
keychain (#297). A body token wins over a cookie, matching
``/auth/refresh``'s precedence. The cookie is cleared on the way out
regardless, but clearing is cosmetic next to the server-side revoke: it
retires the browser's copy, not one already exfiltrated.

Logout's remaining jobs:

1. Record operator intent in the immutable audit log so a stolen-laptop
   event has a server-side signal for remediation.
2. Pair with the SPA's local-storage clear so the user-visible UX behaves
   as "I'm signed out".

The denylist write, the refresh revoke, and the audit write are all
best-effort: a failure is logged loudly but never strands the visible
logout (the SPA's local token clear runs regardless of the response
status).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from channel.api._auth import require_mgmt_user
from channel.auth.refresh import REFRESH_COOKIE_NAME, clear_refresh_cookie
from channel.logging_config import get_logger
from channel.storage import deny_jti, put_audit_event, revoke_refresh_token

router = APIRouter(tags=["mgmt-auth"])
logger = get_logger(__name__)


class LogoutRequest(BaseModel):
    """Body of ``POST /auth/logout`` — desktop/mobile transport only.

    Optional in its entirety: the web SPA sends no body and presents the
    ``HttpOnly`` cookie instead. ``extra="forbid"`` mirrors
    ``RefreshRequest`` — an unexpected field on an auth-critical route
    should be an error, not something silently dropped.
    """

    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = None


def _token_fingerprint(claims: dict[str, Any]) -> str:
    """Return a deterministic, non-reversible per-token correlation handle.

    Combines the immutable identity bits the issuer baked into the JWT
    (``sub``, ``iat``, ``exp``) under SHA-256 and truncates to 16 hex
    chars. Two properties matter:

    - **Per-token uniqueness.** ``iat`` is the Unix-second of issuance;
      two tokens for the same user can collide only if issued in the
      same second. Acceptable for an audit correlation handle.
    - **Non-reversible.** Even an attacker with the audit row cannot
      recover the original JWT — ``iat`` / ``exp`` are not secret, but
      they aren't sufficient to forge a valid signature.

    The fingerprint is the bridge until #114's JTI denylist lands; at
    that point ``jti`` becomes the correlation key and this helper is
    retired in favour of the explicit ``jti`` claim.
    """

    from channel.logging_config import fingerprint_id  # noqa: PLC0415

    source = f"{claims.get('sub', '')}|{claims.get('iat', 0)}|{claims.get('exp', 0)}"
    return fingerprint_id(source)


def _presented_refresh_token(request: Request, body: LogoutRequest | None) -> str:
    """The raw refresh token this caller presented, or ``""`` if none.

    Body before cookie, matching ``/auth/refresh``: a desktop client
    that also happens to carry a cookie means the credential it named
    explicitly, not an incidental one. Whitespace-only values are
    normalised to absent so they never reach a storage lookup.
    """

    body_token = (body.refresh_token or "").strip() if body else ""
    return body_token or (request.cookies.get(REFRESH_COOKIE_NAME) or "").strip()


def _revoke_presented_refresh_family(raw_token: str, fingerprint: str) -> int | None:
    """Revoke the presented token's device family. ``None`` if not attempted.

    Best-effort for the same reason the denylist write is: a storage
    failure here must not strand a user mid-logout. It is the more
    consequential of the two failures though — the access token still
    dies at its 1h ``exp`` (and usually on the denylist), while a
    surviving refresh family lives for up to 30 days. Hence the loud
    log; a user who logged out *because* they suspect compromise should
    reach for #293's "sign out everywhere" if this path is failing.
    """

    if not raw_token:
        return None
    try:
        return revoke_refresh_token(raw_token)
    except Exception:
        logger.exception("auth.logout refresh revoke failed for token_fingerprint=%s", fingerprint)
        return None


@router.post("/auth/logout", status_code=204, include_in_schema=False)
async def mgmt_logout(
    request: Request,
    body: LogoutRequest | None = None,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Revoke both of the session's credentials and return 204.

    Denies the access token's ``jti`` (#240), revokes the presented
    refresh token's device family (#292), clears the refresh cookie, and
    writes the audit row.

    The endpoint is best-effort by design: failure to write the audit
    entry is logged but does not prevent the client from completing the
    visible logout (which only requires clearing the local token). The
    SPA wraps the call in ``.catch(...)`` so transient API outages do
    not strand a signed-in user.
    """

    actor_id = str(claims.get("sub") or "")
    fingerprint = _token_fingerprint(claims)
    # NOTE: ``actor_id`` is the JWT ``sub``, which equals the user's email
    # in the current mgmt-JWT shape (``user_id = email`` in
    # ``mgmt_auth.make_mgmt_user``). We deliberately do NOT also write
    # ``details["email"]`` — it would duplicate ``actor_id`` byte-for-byte
    # without adding any correlation value. When a future #114 schema
    # moves to opaque user ids (e.g. ``USER#u-{uuid}``), the two will
    # diverge and ``details["email"]`` becomes worth re-adding.
    details: dict[str, Any] = {
        "role": claims.get("role", "user"),
        "token_fingerprint": fingerprint,
    }
    if jti := claims.get("jti"):
        details["jti"] = jti
        # Revoke this token server-side so its remaining TTL cannot
        # outlive the user's intent to log out (#240). Best-effort,
        # mirroring the audit write below: a denylist-write failure is
        # logged loudly but must not strand the user mid-logout. The
        # endpoint stays JWT-authenticated, so logging out proves the
        # caller controls the token being revoked.
        try:
            deny_jti(jti, claims["exp"])
        except Exception:
            logger.exception(
                "auth.logout denylist write failed for token_fingerprint=%s", fingerprint
            )

    # The other half of the session (#292). Recorded on the audit row so
    # an operator reviewing a stolen-laptop event can tell "the refresh
    # family was revoked" from "no refresh token was ever presented" —
    # the latter is the pre-#292 legacy session, not a failure.
    revoked_rows = _revoke_presented_refresh_family(
        _presented_refresh_token(request, body), fingerprint
    )
    if revoked_rows is not None:
        details["refresh_rows_revoked"] = revoked_rows

    try:
        put_audit_event(
            event_type="auth.logout",
            actor_id=actor_id,
            details=details,
        )
    except Exception:
        # Audit failures must not block the visible logout. Log loudly so
        # CloudWatch alarms surface persistent failures; the SPA's local
        # token clear runs regardless of the response status. The
        # fingerprint (not the email) is the application-log
        # correlation handle — keeps PII out of CloudWatch Logs Insights
        # while still letting an operator pair the failure to the
        # eventual audit-row repair.
        logger.exception("auth.logout audit write failed for token_fingerprint=%s", fingerprint)

    response = Response(status_code=204)
    # Unconditional: a 204 may carry ``Set-Cookie``, clearing is
    # idempotent when no cookie exists, and doing it even when the
    # server-side revoke failed still denies the browser its copy.
    clear_refresh_cookie(response)
    return response
