# Copyright (c) 2026 John Carter. All rights reserved.
"""Shared FastAPI auth dependencies for management API routes."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from channel.auth.tokens import decode_mgmt_jwt
from channel.logging_config import fingerprint_id, set_client_id

_bearer = HTTPBearer()


def require_mgmt_user(
    request: Request,
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

    Side effect (#111): the authenticated caller's ``client_id`` is
    published two ways so every log line of the request can carry it —
    the ContextVar (read by the JSON formatter for lines emitted inside
    this request's task) and ``request.state`` (read by the completion
    line in ``api.main._log_requests``, which runs in the outer
    middleware task where the ContextVar write is not visible). The
    value is a :func:`fingerprint_id` digest, never the raw JWT ``sub``
    — the sub is the user's email address, and log sinks are not a place
    for PII. Set only after the decode succeeds, so a rejected token
    never stamps an identity onto the request.
    """
    from jose import JWTError

    try:
        claims = decode_mgmt_jwt(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if sub := claims.get("sub"):
        client_id = fingerprint_id(str(sub))
        request.state.client_id = client_id
        set_client_id(client_id)
    return claims


def require_admin(
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Require admin role on top of a valid management JWT."""
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return claims
