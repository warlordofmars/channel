# Copyright (c) 2026 John Carter. All rights reserved.
"""Shared FastAPI auth dependencies for management API routes."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from channel.auth.tokens import decode_mgmt_jwt

_bearer = HTTPBearer()


def require_mgmt_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict[str, Any]:
    """Validate a management JWT and return its claims.

    Every rejection reason — bad signature, wrong issuer, expired,
    ``typ != mgmt``, or a ``jti`` on the ``DENY#{jti}`` revocation
    denylist (#240) — arrives as a ``JWTError`` from
    :func:`~channel.auth.tokens.decode_mgmt_jwt` and maps to HTTP 401.
    Revocation lives inside the decode path (#291) so it cannot be
    forgotten by a future consumer; a revoked token therefore fails
    closed with the same status as an expired one, its ``detail``
    differing only to aid client debugging.

    A *denylist read failure* is not a ``JWTError`` and so is not caught
    here: it propagates as a 500, consistent with how every other
    DDB-backed endpoint surfaces storage errors, and deliberately never
    degrades to "not revoked".
    """
    from jose import JWTError

    try:
        return decode_mgmt_jwt(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_admin(
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Require admin role on top of a valid management JWT."""
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return claims
