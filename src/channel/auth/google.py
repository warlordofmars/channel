# Copyright (c) 2026 John Carter. All rights reserved.
"""
Google OAuth 2.0 integration.

Uses Google as an identity provider for human-facing management UI login.

Configuration (env vars or SSM parameters):
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_ID_PARAM
  GOOGLE_CLIENT_SECRET / GOOGLE_CLIENT_SECRET_PARAM
  ALLOWED_EMAILS / ALLOWED_EMAILS_PARAM  (JSON array; empty = deny all)
"""

from __future__ import annotations

import functools
import json
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from jose import jwt as jose_jwt

from channel.logging_config import get_logger

logger = get_logger(__name__)

# Short TTL on the allowlist so a single login doesn't double-read SSM
# (gate + role both fetch) while still letting operators populate the
# parameter post-deploy without forcing a Lambda cold-start.
_ALLOWED_EMAILS_TTL_SECONDS = 60
_allowed_emails_cache: tuple[float, frozenset[str]] | None = None

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUER = "https://accounts.google.com"


def _ssm_param(name: str) -> str:  # pragma: no cover
    import boto3

    ssm = boto3.client("ssm")
    resp = ssm.get_parameter(Name=name, WithDecryption=True)
    return resp["Parameter"]["Value"]


@functools.lru_cache(maxsize=1)
def _google_client_id() -> str:
    if val := os.environ.get("GOOGLE_CLIENT_ID"):
        return val
    return _ssm_param(  # pragma: no cover
        os.environ.get("GOOGLE_CLIENT_ID_PARAM", "/channel/google-client-id")
    )


@functools.lru_cache(maxsize=1)
def _google_client_secret() -> str:
    if val := os.environ.get("GOOGLE_CLIENT_SECRET"):
        return val
    return _ssm_param(  # pragma: no cover
        os.environ.get("GOOGLE_CLIENT_SECRET_PARAM", "/channel/google-client-secret")
    )


def _allowed_emails() -> frozenset[str]:
    """Load the allowlist from env or SSM, with a short TTL cache.

    Fails closed (deny-all) on any load or parse error so a misconfigured
    parameter never silently grants access. Errors are logged so the
    operator can see why logins are being rejected.
    """
    global _allowed_emails_cache
    now = time.monotonic()
    if _allowed_emails_cache is not None:
        cached_at, cached = _allowed_emails_cache
        if now - cached_at < _ALLOWED_EMAILS_TTL_SECONDS:
            return cached

    value: frozenset[str]
    if val := os.environ.get("ALLOWED_EMAILS"):
        try:
            parsed = json.loads(val)
            if not isinstance(parsed, list):
                raise ValueError("ALLOWED_EMAILS must be a JSON array")
            value = frozenset(parsed)
        except Exception as exc:
            logger.warning(
                "Failed to parse ALLOWED_EMAILS env var (%s); denying all logins. "
                "Expected a JSON array of email strings.",
                exc,
            )
            value = frozenset()
    else:
        try:  # pragma: no cover
            raw = _ssm_param(os.environ.get("ALLOWED_EMAILS_PARAM", "/channel/allowed-emails"))
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError("ALLOWED_EMAILS SSM value must be a JSON array")
            value = frozenset(parsed)
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Failed to load ALLOWED_EMAILS from SSM (%s); denying all logins. "
                "Check the SSM parameter exists and contains a valid JSON array.",
                exc,
            )
            value = frozenset()

    _allowed_emails_cache = (now, value)
    return value


def _reset_allowed_emails_cache() -> None:
    """Clear the TTL cache; for use in tests."""
    global _allowed_emails_cache
    _allowed_emails_cache = None


def google_authorization_url(state: str, callback_uri: str) -> str:
    """Build the Google OAuth authorization URL to redirect the user to."""
    params = {
        "client_id": _google_client_id(),
        "response_type": "code",
        # `profile` is required for Google to return the `name` claim — the
        # full display name we use for the chat-app greeting + sidebar.
        "scope": "openid email profile",
        "redirect_uri": callback_uri,
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_google_code(code: str, callback_uri: str) -> str:  # pragma: no cover
    """Exchange a Google authorization code for an ID token string."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": _google_client_id(),
                "client_secret": _google_client_secret(),
                "redirect_uri": callback_uri,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return str(resp.json()["id_token"])


async def fetch_google_jwks() -> dict[str, Any]:  # pragma: no cover
    """Fetch Google's current public keys (JWKS)."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(GOOGLE_JWKS_URL)
        resp.raise_for_status()
        return dict(resp.json())


async def verify_google_id_token(id_token: str) -> dict[str, Any]:  # pragma: no cover
    """Decode and verify a Google ID token; return its claims.

    Raises jose.JWTError on verification failure.
    """
    jwks = await fetch_google_jwks()
    claims: dict[str, Any] = jose_jwt.decode(
        id_token,
        jwks,
        algorithms=["RS256"],
        audience=_google_client_id(),
        issuer=GOOGLE_ISSUER,
        options={"verify_at_hash": False},
    )
    return claims


def is_email_allowed(email: str) -> bool:
    """Return True if the email is permitted to access the application.

    An empty allowlist denies all access. This is the safer default: a
    freshly-deployed stack ships with ``ALLOWED_EMAILS="[]"`` and must
    not grant management access to any verified Google account until the
    deployer explicitly populates the list.
    """
    return email in _allowed_emails()


def is_admin_email(email: str) -> bool:
    """Return True if this email gets the admin role.

    Only emails explicitly listed in ALLOWED_EMAILS / ALLOWED_EMAILS_PARAM
    receive admin.  An empty allowlist means no admins (not 'everyone is admin').
    """
    return email in _allowed_emails()
