# Copyright (c) 2026 John Carter. All rights reserved.
"""
JWT issuance and validation for OAuth 2.1 and management sessions.

Tokens are signed with HS256 using a secret resolved from:
  1. ``CHANNEL_JWT_SECRET`` env var (tests / local dev)
  2. SSM parameter named by ``CHANNEL_JWT_SECRET_PARAM`` (Lambda runtime;
     per-environment in non-prod, e.g. ``/channel/<env>/jwt-secret``;
     the legacy ``/channel/jwt-secret`` path is kept as a
     fallback default for environments where the env var isn't set)

There is no random fallback — Lambda startup is gated by
:func:`channel.startup.validate_secrets_or_die`, which fails closed if
the SSM parameter is unrotated, missing, or unreadable. Local execution
without ``CHANNEL_JWT_SECRET`` set will surface the underlying SSM error
rather than silently issuing tokens that no other process can validate.
"""

from __future__ import annotations

import dataclasses
import functools
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

JWT_ALGORITHM = "HS256"
ISSUER = os.environ.get("CHANNEL_ISSUER", "https://channel.example.com")


@dataclasses.dataclass
class Token:
    """Minimal token record returned by validation helpers."""

    jti: str
    client_id: str
    scope: str
    issued_at: datetime
    expires_at: datetime

    @property
    def is_valid(self) -> bool:
        return datetime.now(timezone.utc) < self.expires_at


@functools.lru_cache(maxsize=1)
def _jwt_secret() -> str:
    """Return the JWT signing secret.

    Priority:
    1. ``CHANNEL_JWT_SECRET`` env var (tests / local dev)
    2. SSM parameter whose path is supplied by ``CHANNEL_JWT_SECRET_PARAM``
       (Lambda runtime; per-environment in non-prod, e.g.
       ``/channel/<env>/jwt-secret``). The literal default
       below (``/channel/jwt-secret``) is kept only as a legacy
       fallback for environments where the env var isn't wired.

    SSM exceptions propagate — there is no random fallback. The
    fail-closed startup check (:mod:`channel.startup`) ensures Lambda
    cold start fails if the SSM parameter is unreadable, so a successful
    return here means every Lambda warm pool sees the same secret.
    """
    if secret := os.environ.get("CHANNEL_JWT_SECRET"):
        return secret
    import boto3  # pragma: no cover

    param_name = os.environ.get(  # pragma: no cover
        "CHANNEL_JWT_SECRET_PARAM", "/channel/jwt-secret"
    )
    ssm = boto3.client("ssm")  # pragma: no cover
    resp = ssm.get_parameter(Name=param_name, WithDecryption=True)  # pragma: no cover
    return resp["Parameter"]["Value"]  # pragma: no cover


@functools.lru_cache(maxsize=1)
def _origin_verify_secret() -> str | None:
    """Return the expected X-Origin-Verify header value, or None if not configured."""
    if secret := os.environ.get("CHANNEL_ORIGIN_VERIFY_SECRET"):
        return secret
    param_name = os.environ.get("CHANNEL_ORIGIN_VERIFY_PARAM")
    if not param_name:
        return None
    try:  # pragma: no cover
        import boto3

        ssm = boto3.client("ssm")
        resp = ssm.get_parameter(Name=param_name, WithDecryption=False)
        return resp["Parameter"]["Value"]
    except Exception:  # pragma: no cover
        return None


def issue_jwt(token: Token) -> str:
    """Encode a Token record as a signed JWT."""
    payload = {
        "iss": ISSUER,
        "sub": token.client_id,
        "jti": token.jti,
        "scope": token.scope,
        "iat": int(token.issued_at.timestamp()),
        "exp": int(token.expires_at.timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_jwt(token_str: str) -> dict[str, Any]:
    """Decode and verify a JWT. Raises JWTError on failure."""
    return jwt.decode(token_str, _jwt_secret(), algorithms=[JWT_ALGORITHM], issuer=ISSUER)


MGMT_JWT_TTL_SECONDS = 2_592_000  # 30 days (revocable via the #240 jti denylist)


def issue_mgmt_jwt(user: Any) -> str:
    """Issue a short-lived management session JWT for a human user.

    Uses typ=mgmt to distinguish from API access tokens so neither can be
    replayed as the other.
    """
    import time

    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "sub": getattr(user, "user_id", "")
        if not isinstance(user, dict)
        else user.get("user_id", ""),
        "email": getattr(user, "email", "")
        if not isinstance(user, dict)
        else user.get("email", ""),
        "display_name": getattr(user, "display_name", "")
        if not isinstance(user, dict)
        else user.get("display_name", ""),
        "role": getattr(user, "role", "user")
        if not isinstance(user, dict)
        else user.get("role", "user"),
        "typ": "mgmt",
        # Unique per-token id so an individual session can be revoked
        # (logout / stolen-laptop) via the DENY#{jti} denylist — the
        # safety valve the 30-day TTL relies on (#240).
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + MGMT_JWT_TTL_SECONDS,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_mgmt_jwt(token_str: str) -> dict[str, Any]:
    """Decode a management JWT and enforce typ=mgmt.

    Raises JWTError if the token is invalid, expired, or not a management token.
    """
    claims = jwt.decode(token_str, _jwt_secret(), algorithms=[JWT_ALGORITHM], issuer=ISSUER)
    if claims.get("typ") != "mgmt":
        raise JWTError("Not a management token")
    return claims


def make_bearer_token(client_id: str, scope: str, ttl_seconds: int = 3600) -> Token:
    """Create a short-lived bearer Token (for testing / stub use)."""
    now = datetime.now(timezone.utc)
    return Token(
        jti=secrets.token_hex(16),
        client_id=client_id,
        scope=scope,
        issued_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
