# Copyright (c) 2026 John Carter. All rights reserved.
"""Shared FastAPI auth dependencies for management API routes."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from channel.auth.tokens import decode_mgmt_jwt
from channel.storage import is_jti_denied

_bearer = HTTPBearer()


def require_mgmt_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict[str, Any]:
    """Validate a management JWT and return its claims.

    The JWT signature/claims are self-contained, but a revoked session
    must be rejected too: after the signature + ``exp`` + ``typ`` checks,
    the token's ``jti`` is checked against the ``DENY#{jti}`` denylist
    (#240) via a single point read. Raises HTTP 401 on an
    invalid/expired/revoked token.
    """
    from jose import JWTError

    try:
        claims = decode_mgmt_jwt(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    jti = claims.get("jti")
    if jti and is_jti_denied(jti):
        # A revoked (logged-out) token is rejected with 401 — the same
        # status as an expired/invalid token, so revocation fails closed
        # through the normal auth path. The detail string differs only to
        # aid client debugging and is not a security boundary. A
        # denylist-read failure propagates as a 500, consistent with how
        # every other DDB-backed endpoint surfaces storage errors.
        raise HTTPException(status_code=401, detail="Token revoked")
    return claims


def require_admin(
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Require admin role on top of a valid management JWT."""
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return claims
