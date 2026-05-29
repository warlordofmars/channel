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

    JWT is self-contained — no database lookup required.
    Raises HTTP 401 on invalid/expired token.
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
