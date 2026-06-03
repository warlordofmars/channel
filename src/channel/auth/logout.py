# Copyright (c) 2026 John Carter. All rights reserved.
"""
Management session logout endpoint.

Exposes ``POST /auth/logout`` for the management UI. The endpoint validates
the bearer mgmt JWT, writes an ``auth.logout`` audit entry, and returns 204.

The endpoint does **not** invalidate the JWT — there is no JTI denylist
today and mgmt tokens remain valid until their 8h ``exp``. Logout's
purpose is twofold:

1. Record operator intent in the immutable audit log so a stolen-laptop
   event has a server-side signal for remediation.
2. Pair with the SPA's local-storage clear so the user-visible UX behaves
   as "I'm signed out" even though the cookie-less bearer token cannot
   be revoked from the server side without a denylist.

A future-aware-of: a TTL'd JTI denylist (``DENY#{jti}`` with TTL = JWT
remaining lifetime) would convert this endpoint into a true revocation
point. The audit-log entry already carries a fingerprint of the
presented JWT, so a denylist add can be retrofitted without changing the
endpoint shape. Tracked in #114's bonus item (#151).
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, Response

from channel.api._auth import require_mgmt_user
from channel.logging_config import get_logger
from channel.storage import put_audit_event

router = APIRouter(tags=["mgmt-auth"])
logger = get_logger(__name__)


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

    source = f"{claims.get('sub', '')}|{claims.get('iat', 0)}|{claims.get('exp', 0)}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


@router.post("/auth/logout", status_code=204, include_in_schema=False)
async def mgmt_logout(
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Record a management-session logout and return 204.

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
    # ``mgmt_auth._make_user``). We deliberately do NOT also write
    # ``details["email"]`` — it would duplicate ``actor_id`` byte-for-byte
    # without adding any correlation value. When a future #114 schema
    # moves to opaque user ids (e.g. ``USER#u-{uuid}``), the two will
    # diverge and ``details["email"]`` becomes worth re-adding.
    details: dict[str, Any] = {
        "role": claims.get("role", "user"),
        "token_fingerprint": fingerprint,
    }
    if jti := claims.get("jti"):
        # Mgmt JWTs do not currently include a jti claim, but future
        # tokens (post-#114) may — capture it when present so the
        # forthcoming denylist can correlate logout events to issued
        # tokens directly.
        details["jti"] = jti

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

    return Response(status_code=204)
