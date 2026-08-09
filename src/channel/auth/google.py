# Copyright (c) 2026 John Carter. All rights reserved.
"""
Google OAuth 2.0 integration.

Uses Google as an identity provider for human-facing management UI login.

Configuration (env vars or SSM parameters):
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_ID_PARAM
  GOOGLE_CLIENT_SECRET / GOOGLE_CLIENT_SECRET_PARAM
  ALLOWED_EMAILS / ALLOWED_EMAILS_PARAM  (JSON array; empty = deny all)
  ADMIN_ALLOWED_EMAILS / ADMIN_ALLOWED_EMAILS_PARAM
      (JSON array; empty = nobody gets the admin role)

The two allowlists are deliberately **separate sources** (#600). Sign-in
answers "may this person use Channel at all"; admin answers "may this
person reach ``/api/admin/*``". Reading one list for both made every
permitted user an admin, which stayed invisible only while the sign-in
list was tiny.
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

# Short TTL on the allowlists so a single login doesn't double-read SSM
# (gate + role each fetch their own list) while still letting operators
# populate a parameter post-deploy without forcing a Lambda cold-start.
_ALLOWED_EMAILS_TTL_SECONDS = 60

# One cache slot per allowlist, keyed by the loader's own name. Separate
# slots are the point: the sign-in list and the admin list must never be
# able to serve each other's value, which is the #600 bug in miniature.
_email_allowlist_cache: dict[str, tuple[float, frozenset[str]]] = {}

_SIGNIN_ALLOWLIST = "signin"
_ADMIN_ALLOWLIST = "admin"

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


def _parse_allowlist(raw: str, label: str) -> frozenset[str]:
    """Parse a JSON-array allowlist value, or raise.

    Every raise here is a fail-closed signal — the caller turns it into an
    empty set, never into a permissive default.
    """
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError(f"{label} must be a JSON array")
    return frozenset(parsed)


def _load_email_allowlist(
    cache_key: str,
    env_var: str,
    param_env_var: str,
    default_param: str,
    failure_effect: str,
) -> frozenset[str]:
    """Load one email allowlist from env or SSM, with a short TTL cache.

    **Fails closed (empty set) on every path that isn't an explicit, valid
    list**: env var unset with no readable parameter, an SSM read error,
    malformed JSON, a non-array JSON value, or an array holding unhashable
    values. A misconfigured parameter must never silently widen access —
    for the admin list in particular, "we couldn't tell" has to mean "no
    admins", not "everyone". Errors are logged so an operator can see why
    the list came back empty.

    ``failure_effect`` names what an empty result means for this list, so
    the warning reads as a consequence rather than a bare parse error.
    """
    now = time.monotonic()
    entry = _email_allowlist_cache.get(cache_key)
    if entry is not None:
        cached_at, cached = entry
        if now - cached_at < _ALLOWED_EMAILS_TTL_SECONDS:
            return cached

    value: frozenset[str]
    if val := os.environ.get(env_var):
        try:
            value = _parse_allowlist(val, env_var)
        except Exception as exc:
            logger.warning(
                "Failed to parse %s env var (%s); %s. Expected a JSON array of email strings.",
                env_var,
                exc,
                failure_effect,
            )
            value = frozenset()
    else:
        try:
            raw = _ssm_param(os.environ.get(param_env_var, default_param))
            value = _parse_allowlist(raw, f"{env_var} SSM value")
        except Exception as exc:
            logger.warning(
                "Failed to load %s from SSM (%s); %s. "
                "Check the SSM parameter exists and contains a valid JSON array.",
                env_var,
                exc,
                failure_effect,
            )
            value = frozenset()

    _email_allowlist_cache[cache_key] = (now, value)
    return value


def _allowed_emails() -> frozenset[str]:
    """The sign-in allowlist — who may use Channel at all."""
    return _load_email_allowlist(
        _SIGNIN_ALLOWLIST,
        "ALLOWED_EMAILS",
        "ALLOWED_EMAILS_PARAM",
        "/channel/allowed-emails",
        "denying all logins",
    )


def _admin_allowed_emails() -> frozenset[str]:
    """The admin allowlist — who additionally gets ``role=admin`` (#600).

    A separate source from :func:`_allowed_emails` on purpose. Populating
    it is a deploy step: until then the parameter holds ``[]`` and nobody
    reaches ``/api/admin/*``.
    """
    return _load_email_allowlist(
        _ADMIN_ALLOWLIST,
        "ADMIN_ALLOWED_EMAILS",
        "ADMIN_ALLOWED_EMAILS_PARAM",
        "/channel/admin-allowed-emails",
        "granting the admin role to nobody",
    )


def _reset_allowed_emails_cache() -> None:
    """Clear both allowlist TTL caches; for use in tests."""
    _email_allowlist_cache.clear()


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

    Reads ADMIN_ALLOWED_EMAILS / ADMIN_ALLOWED_EMAILS_PARAM — its **own**
    list, not the sign-in allowlist. Until #600 this read
    ``_allowed_emails()``, which made every user who could sign in an
    admin; the failure was masked only by the sign-in list being tiny, and
    would have surfaced the moment it widened (an invited user, a demo
    account, a workspace member).

    An empty or unreadable list means *no admins* — never 'everyone'. See
    :func:`_load_email_allowlist` for the fail-closed contract.
    """
    return email in _admin_allowed_emails()
