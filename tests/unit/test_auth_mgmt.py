# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for management auth routes and helper functions.

State storage (:mod:`starter.auth.state_store`) is patched at module
boundary so these tests do not hit DynamoDB. The fake implementation
mirrors the production contract: ``put_state`` records the state and
``consume_state`` returns the stored payload exactly once, then
``None`` thereafter.
"""

import importlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CHANNEL_JWT_SECRET", "test-secret-for-unit-tests")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")

from channel.api.main import app  # noqa: E402
from channel.auth import mgmt_auth, state_store  # noqa: E402
from channel.auth.google import _google_client_id, _reset_allowed_emails_cache  # noqa: E402
from channel.auth.mgmt_auth import (  # noqa: E402
    _consume_pending_state,
    _create_pending_state,
    _html_redirect,
    _mgmt_callback_uri,
    make_mgmt_user,
)
from channel.auth.refresh import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH  # noqa: E402
from channel.models import RefreshToken  # noqa: E402

_client = TestClient(app, follow_redirects=False)


def _clear_google_caches():
    _google_client_id.cache_clear()
    _reset_allowed_emails_cache()


@pytest.fixture(autouse=True)
def _fake_state_store(monkeypatch):
    """In-memory fake for state_store; mirrors single-use semantics.

    Production state_store hits DynamoDB. The unit tests want to verify
    the mgmt_auth control flow (state created, callback consumes it
    exactly once) without spinning up DynamoDB Local — this fake
    captures that behaviour. Atomic-consume / expiry edge cases are
    covered separately in tests/unit/test_auth_state_store.py.
    """
    store: dict[str, dict[str, Any]] = {}

    def _put(state: str, payload: dict[str, Any] | None = None, ttl_seconds: int = 600) -> None:
        store[state] = dict(payload or {})

    def _consume(state: str) -> dict[str, Any] | None:
        return store.pop(state, None)

    monkeypatch.setattr(state_store, "put_state", _put)
    monkeypatch.setattr(state_store, "consume_state", _consume)
    yield store


@pytest.fixture(autouse=True)
def minted_refresh_tokens(monkeypatch):
    """In-memory fake for ``mint_refresh_token``; records what it was called with.

    Autouse because #292 made *every* successful callback mint a refresh
    row. Without this the real helper would reach for DynamoDB on each
    login test — and, since minting is deliberately fail-soft, would
    swallow the resulting error and pass anyway, quietly turning the
    whole suite into a slow no-op. The yielded list is the assertion
    surface: what the callback bound the token to.
    """

    calls: list[dict[str, Any]] = []

    def _fake_mint(
        *,
        user_id: str,
        device_id: str,
        absolute_expires_at: str | None = None,
        display_name: str | None = None,
    ) -> tuple[str, RefreshToken]:
        calls.append({"user_id": user_id, "device_id": device_id, "display_name": display_name})
        now = datetime.now(timezone.utc)
        absolute = absolute_expires_at or (now + timedelta(days=30)).isoformat(
            timespec="microseconds"
        )
        return f"raw-refresh-{len(calls)}", RefreshToken(
            token_hash="h" * 64,
            user_id=user_id,
            device_id=device_id,
            issued_at=now.isoformat(timespec="microseconds"),
            last_used_at=now.isoformat(timespec="microseconds"),
            absolute_expires_at=absolute,
            idle_expires_at=(now + timedelta(days=7)).isoformat(timespec="microseconds"),
            display_name=display_name,
        )

    monkeypatch.setattr(mgmt_auth, "mint_refresh_token", _fake_mint)
    return calls


@pytest.fixture
def failing_refresh_mint(monkeypatch):
    """Make minting blow up, to exercise the fail-soft login path."""

    def _boom(**_kwargs: Any) -> tuple[str, RefreshToken]:
        raise RuntimeError("dynamodb is having a day")

    monkeypatch.setattr(mgmt_auth, "mint_refresh_token", _boom)


def setup_function():
    _clear_google_caches()


def teardown_function():
    _clear_google_caches()


def test_mgmt_callback_uri_ends_with_auth_callback():
    uri = _mgmt_callback_uri()
    assert uri.endswith("/auth/callback")


def test_create_pending_state_returns_string():
    state = _create_pending_state()
    assert isinstance(state, str)
    assert len(state) > 10


def test_consume_pending_state_valid():
    state = _create_pending_state()
    assert _consume_pending_state(state) is True


def test_consume_pending_state_unknown():
    assert _consume_pending_state("totally-bogus-state-xyz") is False


def test_consume_pending_state_only_once():
    state = _create_pending_state()
    assert _consume_pending_state(state) is True
    assert _consume_pending_state(state) is False


def test_html_redirect_sets_token_in_localstorage():
    resp = _html_redirect("my.jwt.token")
    body = resp.body.decode()
    assert "my.jwt.token" in body
    assert "localStorage.setItem" in body
    assert "channel_mgmt_token" in body


def test_html_redirect_targets_app_not_marketing_root():
    # After Google OAuth success the user should land in the chat app
    # (/app), not the marketing landing page (/).
    resp = _html_redirect("t")
    body = resp.body.decode()
    assert "location.replace('/app')" in body


def test_make_user_role_user(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "[]")
    user = make_mgmt_user("user@example.com", "Alice")
    assert user["email"] == "user@example.com"
    assert user["display_name"] == "Alice"
    assert user["role"] == "user"


def test_make_user_role_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", '["admin@example.com"]')
    user = make_mgmt_user("admin@example.com", "Admin")
    assert user["role"] == "admin"


def test_make_user_signin_allowlist_does_not_confer_admin(monkeypatch):
    """Role resolution reads the admin list, never the sign-in list (#600).

    Until the lists were split, being on ``ALLOWED_EMAILS`` — the only
    way to sign in at all — was sufficient for ``role=admin``, so every
    user was an admin.
    """
    monkeypatch.setenv("ALLOWED_EMAILS", '["someone@example.com"]')
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "[]")
    assert make_mgmt_user("someone@example.com", "Someone")["role"] == "user"


def test_make_user_admin_allowlist_unset_denies_admin(monkeypatch):
    """No admin list configured is 'no admins', not 'everyone'."""
    monkeypatch.setenv("ALLOWED_EMAILS", '["someone@example.com"]')
    monkeypatch.delenv("ADMIN_ALLOWED_EMAILS", raising=False)
    assert make_mgmt_user("someone@example.com", "Someone")["role"] == "user"


def test_mgmt_login_bypass_issues_html_with_token(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    with patch("channel.auth.mgmt_auth._BYPASS", True):
        resp = _client.get("/auth/login?test_email=e2e@test.com")
    assert resp.status_code == 200
    assert "channel_mgmt_token" in resp.text


def test_mgmt_login_no_bypass_redirects_to_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-google-id")
    with patch("channel.auth.mgmt_auth._BYPASS", False):
        resp = _client.get("/auth/login")
    assert resp.status_code == 302
    assert "accounts.google.com" in resp.headers["location"]


def test_mgmt_callback_error_param():
    resp = _client.get("/auth/callback?error=access_denied")
    assert resp.status_code == 400


def test_mgmt_callback_missing_code_and_state():
    resp = _client.get("/auth/callback")
    assert resp.status_code == 400


def test_mgmt_callback_invalid_state():
    resp = _client.get("/auth/callback?code=abc&state=invalid-bogus-state")
    assert resp.status_code == 400


def test_mgmt_callback_success(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", '["user@example.com"]')
    state = _create_pending_state()
    with (
        patch(
            "channel.auth.mgmt_auth.exchange_google_code",
            new=AsyncMock(return_value="fake-id-token"),
        ),
        patch(
            "channel.auth.mgmt_auth.verify_google_id_token",
            new=AsyncMock(
                return_value={
                    "email": "user@example.com",
                    "email_verified": True,
                    "name": "Test User",
                }
            ),
        ),
    ):
        resp = _client.get(f"/auth/callback?code=authcode&state={state}")
    assert resp.status_code == 200
    assert "channel_mgmt_token" in resp.text


def test_mgmt_callback_unlisted_email_with_populated_allowlist_returns_403(monkeypatch):
    """Verified email not in a populated allowlist must be rejected with 403."""
    monkeypatch.setenv("ALLOWED_EMAILS", '["admin@example.com"]')
    state = _create_pending_state()
    with (
        patch(
            "channel.auth.mgmt_auth.exchange_google_code",
            new=AsyncMock(return_value="fake-id-token"),
        ),
        patch(
            "channel.auth.mgmt_auth.verify_google_id_token",
            new=AsyncMock(
                return_value={
                    "email": "stranger@example.com",
                    "email_verified": True,
                    "name": "Stranger",
                }
            ),
        ),
    ):
        resp = _client.get(f"/auth/callback?code=authcode&state={state}")
    assert resp.status_code == 403
    assert "channel_mgmt_token" not in resp.text


def test_mgmt_callback_unlisted_email_with_empty_allowlist_returns_403(monkeypatch):
    """Empty allowlist must deny all verified emails (deny-all default).

    Regression for SEC-1: a freshly-deployed stack ships with
    ALLOWED_EMAILS="[]" and must not mint a JWT to any verified Google
    account until the deployer explicitly populates the list.
    """
    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    state = _create_pending_state()
    with (
        patch(
            "channel.auth.mgmt_auth.exchange_google_code",
            new=AsyncMock(return_value="fake-id-token"),
        ),
        patch(
            "channel.auth.mgmt_auth.verify_google_id_token",
            new=AsyncMock(
                return_value={
                    "email": "anyone@example.com",
                    "email_verified": True,
                    "name": "Anyone",
                }
            ),
        ),
    ):
        resp = _client.get(f"/auth/callback?code=authcode&state={state}")
    assert resp.status_code == 403
    assert "channel_mgmt_token" not in resp.text


def test_mgmt_callback_listed_admin_email_returns_admin_role(monkeypatch):
    """Listed email passes the gate and receives role=admin.

    Two lists since #600: ``ALLOWED_EMAILS`` opens the gate,
    ``ADMIN_ALLOWED_EMAILS`` grants the role. This email is on both.
    """
    monkeypatch.setenv("ALLOWED_EMAILS", '["admin@example.com"]')
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", '["admin@example.com"]')
    state = _create_pending_state()
    with (
        patch(
            "channel.auth.mgmt_auth.exchange_google_code",
            new=AsyncMock(return_value="fake-id-token"),
        ),
        patch(
            "channel.auth.mgmt_auth.verify_google_id_token",
            new=AsyncMock(
                return_value={
                    "email": "admin@example.com",
                    "email_verified": True,
                    "name": "Admin",
                }
            ),
        ),
        patch("channel.auth.mgmt_auth.issue_mgmt_jwt") as mock_issue,
    ):
        mock_issue.return_value = "stub-jwt"
        resp = _client.get(f"/auth/callback?code=authcode&state={state}")
    assert resp.status_code == 200
    assert mock_issue.call_args.args[0]["role"] == "admin"


def test_mgmt_callback_listed_non_admin_email_returns_user_role(monkeypatch):
    """The allowlist gate must not re-derive role; role flows from
    is_admin_email().

    The lists were split in #600, so this is no longer a hypothetical
    guarded by a mock: the email below is on the sign-in list and absent
    from the admin list, which is the ordinary production shape. It
    drives the real ``is_admin_email`` end to end through the callback.
    """
    monkeypatch.setenv("ALLOWED_EMAILS", '["user@example.com"]')
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", '["someone-else@example.com"]')
    state = _create_pending_state()
    with (
        patch(
            "channel.auth.mgmt_auth.exchange_google_code",
            new=AsyncMock(return_value="fake-id-token"),
        ),
        patch(
            "channel.auth.mgmt_auth.verify_google_id_token",
            new=AsyncMock(
                return_value={
                    "email": "user@example.com",
                    "email_verified": True,
                    "name": "Regular User",
                }
            ),
        ),
        patch("channel.auth.mgmt_auth.issue_mgmt_jwt") as mock_issue,
    ):
        mock_issue.return_value = "stub-jwt"
        resp = _client.get(f"/auth/callback?code=authcode&state={state}")
    assert resp.status_code == 200
    assert mock_issue.call_args.args[0]["role"] == "user"


def test_mgmt_callback_unverified_email(monkeypatch):
    state = _create_pending_state()
    with (
        patch(
            "channel.auth.mgmt_auth.exchange_google_code",
            new=AsyncMock(return_value="fake-id-token"),
        ),
        patch(
            "channel.auth.mgmt_auth.verify_google_id_token",
            new=AsyncMock(return_value={"email": "user@example.com", "email_verified": False}),
        ),
    ):
        resp = _client.get(f"/auth/callback?code=authcode&state={state}")
    assert resp.status_code == 400


def test_mgmt_callback_google_exchange_error(monkeypatch):
    state = _create_pending_state()
    with patch(
        "channel.auth.mgmt_auth.exchange_google_code",
        new=AsyncMock(side_effect=Exception("network error")),
    ):
        resp = _client.get(f"/auth/callback?code=authcode&state={state}")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Desktop-callback test cases for the Electron OAuth loopback flow.
#
# When the Electron desktop app starts an OAuth flow, it passes its own
# random state and a loopback callback URL. These tests verify the
# validator rejects non-loopback callbacks and that the happy path stores
# the desktop_callback in the state record's payload.
# ---------------------------------------------------------------------------

DESKTOP_CALLBACK_OK = "http://127.0.0.1:54321/callback"
VALID_STATE = "A" * 43  # 43 base64url chars


@pytest.mark.parametrize(
    "bad_callback",
    [
        "https://evil.example.com/callback",  # external host
        "https://127.0.0.1:54321/callback",  # https not allowed
        "http://127.0.0.1:54321/other",  # wrong path
        "http://127.0.0.1/callback",  # missing port
        "http://0.0.0.0:54321/callback",  # not loopback
        "http://[::1]:54321/callback",  # IPv6 loopback rejected
        "http://127.0.0.1:54321/callback?foo=bar",  # query injection
        "http://127.0.0.1:54321/callback#frag",  # fragment injection
        "http://127.0.0.1:99999/callback",  # port out of range
    ],
)
def test_desktop_callback_rejects_bad_urls(bad_callback, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    resp = _client.get(
        "/auth/login",
        params={"desktop_callback": bad_callback, "state": VALID_STATE},
    )
    assert resp.status_code == 400


def test_desktop_callback_rejects_short_state(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    resp = _client.get(
        "/auth/login",
        params={"desktop_callback": DESKTOP_CALLBACK_OK, "state": "too-short"},
    )
    assert resp.status_code == 400


def test_desktop_callback_happy_path(monkeypatch, _fake_state_store):
    """Caller-supplied state is reused as the DynamoDB key + Google's state."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    resp = _client.get(
        "/auth/login",
        params={"desktop_callback": DESKTOP_CALLBACK_OK, "state": VALID_STATE},
    )
    assert resp.status_code == 302
    assert _fake_state_store[VALID_STATE]["desktop_callback"] == DESKTOP_CALLBACK_OK
    assert "accounts.google.com" in resp.headers["location"]
    assert f"state={VALID_STATE}" in resp.headers["location"]


def _async_return(value):
    async def f(*a, **kw):
        return value

    return f


def test_callback_redirects_to_desktop_callback_when_set(monkeypatch):
    """When the state record carries desktop_callback, redirect to the loopback URL."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    state = "B" * 43
    desktop_callback = "http://127.0.0.1:54321/callback"
    monkeypatch.setattr(
        "channel.auth.state_store.consume_state",
        lambda s: {"PK": f"MGMT_STATE#{s}", "SK": "META", "desktop_callback": desktop_callback},
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.exchange_google_code", _async_return("id_token"))
    monkeypatch.setattr(
        "channel.auth.mgmt_auth.verify_google_id_token",
        _async_return({"email": "user@example.com", "email_verified": True, "name": "User"}),
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.is_email_allowed", lambda e: True)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda e: False)

    client = TestClient(app)
    resp = client.get(
        "/auth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("http://127.0.0.1:54321/callback?")
    assert f"state={state}" in location
    assert "token=" in location


def test_callback_still_returns_html_redirect_when_no_desktop_callback(monkeypatch):
    """Existing web flow (no desktop_callback) returns the HTML redirect page."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    state = "C" * 43
    monkeypatch.setattr(
        "channel.auth.state_store.consume_state",
        lambda s: {"PK": f"MGMT_STATE#{s}", "SK": "META"},  # no desktop_callback
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.exchange_google_code", _async_return("id_token"))
    monkeypatch.setattr(
        "channel.auth.mgmt_auth.verify_google_id_token",
        _async_return({"email": "user@example.com", "email_verified": True, "name": "User"}),
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.is_email_allowed", lambda e: True)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda e: False)

    client = TestClient(app)
    resp = client.get(
        "/auth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert resp.status_code == 200
    assert b"localStorage.setItem" in resp.content


def test_callback_rejects_tampered_desktop_callback(monkeypatch):
    """Defense in depth: if the stored desktop_callback is invalid (e.g. tampered
    DynamoDB record), the callback site re-validates and returns 400."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    state = "E" * 43
    monkeypatch.setattr(
        "channel.auth.state_store.consume_state",
        lambda s: {
            "PK": f"MGMT_STATE#{s}",
            "SK": "META",
            "desktop_callback": "https://evil.example.com/callback",
        },
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.exchange_google_code", _async_return("id_token"))
    monkeypatch.setattr(
        "channel.auth.mgmt_auth.verify_google_id_token",
        _async_return({"email": "user@example.com", "email_verified": True, "name": "User"}),
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.is_email_allowed", lambda e: True)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda e: False)

    client = TestClient(app)
    resp = client.get(
        "/auth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert resp.status_code == 400


# ─── Desktop bypass branch ──────────────────────────────────────────────────


def test_mgmt_login_desktop_bypass_mints_jwt_and_redirects_to_loopback(monkeypatch):
    """When _BYPASS=1 AND CHANNEL_DESKTOP_DEV_EMAIL is set, /auth/login skips
    Google entirely and redirects to the loopback URL with ?token=<jwt>&state=<S>.
    """
    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    monkeypatch.setenv("CHANNEL_DESKTOP_DEV_EMAIL", "dev@channel.local")
    state = "F" * 43
    desktop_callback = "http://127.0.0.1:60123/callback"
    with patch("channel.auth.mgmt_auth._BYPASS", True):
        resp = _client.get(
            "/auth/login",
            params={"desktop_callback": desktop_callback, "state": state},
        )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(f"{desktop_callback}?")
    assert f"state={state}" in location
    assert "token=" in location


def test_mgmt_login_desktop_bypass_honours_CHANNEL_DESKTOP_DEV_EMAIL(monkeypatch):
    """The minted JWT's email is the value of CHANNEL_DESKTOP_DEV_EMAIL."""
    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    monkeypatch.setenv("CHANNEL_DESKTOP_DEV_EMAIL", "custom@example.test")
    state = "G" * 43
    desktop_callback = "http://127.0.0.1:60124/callback"
    captured: dict[str, Any] = {}

    def fake_issue(user):
        captured["email"] = user["email"]
        return "tok"

    monkeypatch.setattr("channel.auth.mgmt_auth.issue_mgmt_jwt", fake_issue)
    with patch("channel.auth.mgmt_auth._BYPASS", True):
        resp = _client.get(
            "/auth/login",
            params={"desktop_callback": desktop_callback, "state": state},
        )
    assert resp.status_code == 302
    assert captured["email"] == "custom@example.test"


def test_mgmt_login_desktop_bypass_skipped_when_CHANNEL_DESKTOP_DEV_EMAIL_unset(monkeypatch):
    """When _BYPASS=1 but CHANNEL_DESKTOP_DEV_EMAIL is NOT set (deployed dev
    environment shape), /auth/login does NOT silently auto-mint a synthetic
    JWT — it falls through to the real Google OAuth flow.

    Without this gating, every desktop sign-in on the deployed dev environment
    would auto-log-in as a placeholder account regardless of who actually
    clicked the button — the bug reported when the user's desktop app on the
    dev domain was auto-logged-in as 'dev@channel.local'.
    """
    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    monkeypatch.delenv("CHANNEL_DESKTOP_DEV_EMAIL", raising=False)
    state = "H" * 43
    desktop_callback = "http://127.0.0.1:60125/callback"

    fake_put_state = MagicMock()
    monkeypatch.setattr("channel.auth.state_store.put_state", fake_put_state)
    monkeypatch.setattr(
        "channel.auth.mgmt_auth.google_authorization_url",
        lambda *args, **kwargs: "https://accounts.google.com/o/oauth2/v2/auth?...",
    )

    with patch("channel.auth.mgmt_auth._BYPASS", True):
        resp = _client.get(
            "/auth/login",
            params={"desktop_callback": desktop_callback, "state": state},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    # The bypass did NOT fire — we got redirected to Google, not the loopback.
    assert resp.headers["location"].startswith("https://accounts.google.com/")
    assert "127.0.0.1" not in resp.headers["location"]
    # And the state was persisted for the real callback to consume.
    assert fake_put_state.called


# ---------------------------------------------------------------------------
# CHANNEL_BYPASS_GOOGLE_AUTH parsing (issue #446)
#
# ``_BYPASS`` is evaluated once at module-import time, so flipping the env var
# requires ``importlib.reload`` — the same technique test_debug_api.py uses for
# CHANNEL_ENABLE_DEBUG_ENDPOINTS. Reload re-executes the module body in the
# *existing* module dict, so the route functions already registered on ``app``
# (whose ``__globals__`` is that dict) observe the new value — which is what
# lets the route-level tests below exercise the real request path.
#
# Two consequences of reloading a module that ``app`` already mounted:
#   - ``mgmt_auth.router`` is rebound to a *fresh* APIRouter while ``app``
#     keeps routes from the pre-reload object. Harmless here (nothing
#     re-includes the router), but a future test that re-includes it or
#     asserts route identity must not assume the two are the same object.
#   - ``mgmt_auth`` imports state_store as a *module*
#     (``from channel.auth import state_store``), so reload rebinds to the
#     same object the autouse ``_fake_state_store`` fixture patched and the
#     fall-through tests stay offline. If that import is ever narrowed to
#     ``from channel.auth.state_store import put_state``, reload would pick
#     up the real function and these tests would reach DynamoDB — hence the
#     explicit "state was persisted" assertion below, which fails loudly
#     rather than silently going live.
#
# These tests deliberately pin the module-level expression rather than a
# helper: the regression being guarded is precisely that line reverting to
# ``bool(os.environ.get(...))``, under which "0" and "false" would silently
# re-enable the auth bypass.
# ---------------------------------------------------------------------------


@pytest.fixture
def bypass_env():
    """Set CHANNEL_BYPASS_GOOGLE_AUTH and re-evaluate the module-level flag.

    Yields a callable taking the env value (``None`` means unset) and
    returning the freshly computed ``_BYPASS``. Restores the original
    environment and reloads once more on teardown so no later test inherits
    a mutated bypass state.
    """
    original = os.environ.get("CHANNEL_BYPASS_GOOGLE_AUTH")

    def _apply(value: str | None) -> bool:
        if value is None:
            os.environ.pop("CHANNEL_BYPASS_GOOGLE_AUTH", None)
        else:
            os.environ["CHANNEL_BYPASS_GOOGLE_AUTH"] = value
        importlib.reload(mgmt_auth)
        return mgmt_auth._BYPASS

    yield _apply

    if original is None:
        os.environ.pop("CHANNEL_BYPASS_GOOGLE_AUTH", None)
    else:
        os.environ["CHANNEL_BYPASS_GOOGLE_AUTH"] = original
    importlib.reload(mgmt_auth)


def test_bypass_enabled_only_by_exact_string_one(bypass_env):
    """The documented contract: CHANNEL_BYPASS_GOOGLE_AUTH=1 enables the bypass."""
    assert bypass_env("1") is True


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("0", id="zero"),
        pytest.param("false", id="false"),
        pytest.param("False", id="False-capitalised"),
        pytest.param("no", id="no"),
        pytest.param("true", id="true-not-one"),
        pytest.param("yes", id="yes-not-one"),
        pytest.param(" 1 ", id="padded-one"),
        pytest.param("", id="empty"),
        pytest.param(None, id="unset"),
    ],
)
def test_bypass_disabled_for_every_value_other_than_one(value, bypass_env):
    """Fail closed: anything the flag doesn't explicitly recognise leaves it off.

    Before #446 this used ``bool(os.environ.get(...))``, so "0" and "false" —
    the obvious ways to turn a flag *off* — ENABLED the Google-auth bypass.
    """
    assert bypass_env(value) is False


def test_login_with_test_email_is_refused_when_flag_is_zero(
    bypass_env, monkeypatch, _fake_state_store
):
    """?test_email= must NOT mint a synthetic JWT when the flag is "0"."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-google-id")
    bypass_env("0")

    resp = _client.get("/auth/login?test_email=e2e@test.com")

    assert resp.status_code == 302
    assert "accounts.google.com" in resp.headers["location"]
    # The request fell through to the real Google path far enough to persist
    # OAuth state — and it landed in the fake store, confirming the reloaded
    # module still writes through the patched state_store rather than DynamoDB.
    assert len(_fake_state_store) == 1


def test_login_with_test_email_still_works_when_flag_is_one(bypass_env, monkeypatch):
    """The local dev shortcut (`inv dev` sets the flag to "1") keeps working."""
    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    bypass_env("1")

    resp = _client.get("/auth/login?test_email=e2e@test.com")

    assert resp.status_code == 200
    assert "channel_mgmt_token" in resp.text


# ----------------------------------------------------------------
# Refresh-token minting on the Google callback (#292, epic #241)
# ----------------------------------------------------------------


def _google_login(monkeypatch, *, name="Ada Lovelace", email="user@example.com", record=None):
    """Drive one successful /auth/callback, returning the response.

    ``record`` overrides what the state store hands back, which is how a
    test selects the desktop-loopback transport over the web one.
    ``name=None`` *omits* the claim rather than sending a null — Google
    leaves ``name`` out of an ID token it has no name for, and the
    callback's fallback keys off absence.
    """

    state = "D" * 43
    monkeypatch.setattr(
        "channel.auth.state_store.consume_state",
        lambda s: {"PK": f"MGMT_STATE#{s}", "SK": "META", **(record or {})},
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.exchange_google_code", _async_return("id_token"))
    id_claims = {"email": email, "email_verified": True}
    if name is not None:
        id_claims["name"] = name
    monkeypatch.setattr(
        "channel.auth.mgmt_auth.verify_google_id_token",
        _async_return(id_claims),
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.is_email_allowed", lambda e: True)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda e: False)

    return TestClient(app).get(
        "/auth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )


def test_callback_mints_a_refresh_token_for_the_google_identity(monkeypatch, minted_refresh_tokens):
    """Without this, #291's 1h access token means an hourly trip through Google."""

    _google_login(monkeypatch)

    assert len(minted_refresh_tokens) == 1
    assert minted_refresh_tokens[0]["user_id"] == "user@example.com"


def test_callback_persists_googles_display_name_on_the_refresh_row(
    monkeypatch, minted_refresh_tokens
):
    """The callback is the only place the real name exists (#292's seam)."""

    _google_login(monkeypatch, name="Ada Lovelace")

    assert minted_refresh_tokens[0]["display_name"] == "Ada Lovelace"


def test_callback_falls_back_to_the_local_part_when_google_sends_no_name(
    monkeypatch, minted_refresh_tokens
):
    """Mirrors the access token's own fallback rather than storing nothing."""

    _google_login(monkeypatch, name=None, email="ada@example.com")

    assert minted_refresh_tokens[0]["display_name"] == "ada"


def test_callback_device_id_is_opaque_and_fresh_per_login(monkeypatch, minted_refresh_tokens):
    """Server-side opaque, per epic #241 Q2 — never client-supplied or reused."""

    _google_login(monkeypatch)
    _google_login(monkeypatch)

    first, second = (call["device_id"] for call in minted_refresh_tokens)
    assert first != second
    # Opaque: no user-identifying substring leaks into the id.
    assert "user@example.com" not in first
    assert len(first) >= 20


def test_callback_sets_the_refresh_cookie_with_the_full_attribute_matrix(monkeypatch):
    """A mint/rotate attribute mismatch silently breaks the web flow."""

    resp = _google_login(monkeypatch)

    set_cookie = resp.headers["set-cookie"]
    assert f"{REFRESH_COOKIE_NAME}=raw-refresh-1" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert f"Path={REFRESH_COOKIE_PATH}" in set_cookie


def test_callback_cookie_max_age_tracks_the_absolute_deadline(monkeypatch):
    """Pinned to the family's 30-day ceiling, not the idle window."""

    resp = _google_login(monkeypatch)

    max_age = int(
        next(
            part.split("=")[1]
            for part in resp.headers["set-cookie"].split("; ")
            if part.lower().startswith("max-age=")
        )
    )
    # 30 days, allowing a couple of seconds of test-execution drift.
    assert 30 * 24 * 3600 - 10 <= max_age <= 30 * 24 * 3600


def test_callback_never_puts_the_refresh_token_where_javascript_can_read_it(monkeypatch):
    """HttpOnly is pointless if the page body also carries the plaintext."""

    resp = _google_login(monkeypatch)

    assert "raw-refresh-1" not in resp.text


def test_desktop_callback_carries_the_refresh_token_in_the_loopback_query(
    monkeypatch, minted_refresh_tokens
):
    """Electron has no cookie jar to speak of; it persists via safeStorage (#297)."""

    resp = _google_login(
        monkeypatch, record={"desktop_callback": "http://127.0.0.1:54321/callback"}
    )

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "refresh_token=raw-refresh-1" in location
    assert "token=" in location
    # The cookie transport is the web flow's alone — and any cookie a
    # previous web login left in this browser is cleared, not inherited.
    assert _cleared(resp)


def test_callback_survives_a_refresh_mint_failure(monkeypatch, failing_refresh_mint):
    """Fail-soft: a storage blip must not become a total login outage."""

    resp = _google_login(monkeypatch)

    assert resp.status_code == 200
    assert "localStorage.setItem" in resp.text
    # No cookie is set — and a stale one is actively cleared, see
    # test_failed_mint_clears_a_stale_refresh_cookie.
    assert "raw-refresh" not in resp.headers.get("set-cookie", "")


def test_desktop_callback_survives_a_refresh_mint_failure(monkeypatch, failing_refresh_mint):
    """Same fail-soft posture on the loopback transport."""

    resp = _google_login(
        monkeypatch, record={"desktop_callback": "http://127.0.0.1:54321/callback"}
    )

    assert resp.status_code == 302
    assert "refresh_token=" not in resp.headers["location"]
    assert "token=" in resp.headers["location"]


def test_rejected_login_mints_no_refresh_token(monkeypatch, minted_refresh_tokens):
    """A 403 must not strand a live row in the user's token partition."""

    state = "E" * 43
    monkeypatch.setattr(
        "channel.auth.state_store.consume_state",
        lambda s: {"PK": f"MGMT_STATE#{s}", "SK": "META"},
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.exchange_google_code", _async_return("id_token"))
    monkeypatch.setattr(
        "channel.auth.mgmt_auth.verify_google_id_token",
        _async_return({"email": "stranger@example.com", "email_verified": True, "name": "S"}),
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.is_email_allowed", lambda e: False)

    resp = TestClient(app).get("/auth/callback", params={"code": "c", "state": state})

    assert resp.status_code == 403
    assert minted_refresh_tokens == []


def test_tampered_desktop_callback_mints_no_refresh_token(monkeypatch, minted_refresh_tokens):
    """Minting sits after every rejection path, including this one."""

    resp = _google_login(
        monkeypatch, record={"desktop_callback": "https://evil.example.com/callback"}
    )

    assert resp.status_code == 400
    assert minted_refresh_tokens == []


def test_test_email_bypass_mints_no_refresh_token(bypass_env, monkeypatch, minted_refresh_tokens):
    """Deliberate: the e2e shortcut's blast radius stays at the 1h access token.

    ``CHANNEL_BYPASS_GOOGLE_AUTH=1`` is set on every deployed non-prod
    stack, so this path hands a token to anyone who asks. #292 scopes
    minting to ``/auth/callback`` — the real Google flow — rather than
    extending a bypass credential to a 30-day refresh family. The
    cost is that bypass logins re-auth hourly; the fix, if that
    friction bites, is a deliberate follow-up rather than a silent
    widening here.
    """

    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    bypass_env("1")

    resp = _client.get("/auth/login?test_email=e2e@test.com")

    assert resp.status_code == 200
    assert minted_refresh_tokens == []
    # Mints nothing — but still clears, so it cannot inherit the
    # previous account's family (test_test_email_bypass_clears_a_stale_
    # refresh_cookie pins that half).
    assert "raw-refresh" not in resp.headers.get("set-cookie", "")


# ----------------------------------------------------------------
# Invariant: a login-completing response sets OR clears the refresh
# cookie — never leaves a previous account's behind (#292 review)
# ----------------------------------------------------------------


def _cleared(resp) -> bool:
    """True if the response expires the refresh cookie."""
    set_cookie = resp.headers.get("set-cookie", "")
    return f"{REFRESH_COOKIE_NAME}=" in set_cookie and "Max-Age=0" in set_cookie


def test_failed_mint_clears_a_stale_refresh_cookie(monkeypatch, failing_refresh_mint):
    """Otherwise B's browser silently keeps A's live refresh family.

    ``/auth/refresh`` is unauthenticated by design, so identity comes
    from whichever cookie is presented — leaving a stale one is a
    cross-account session leak, not just untidiness.
    """

    resp = _google_login(monkeypatch)

    assert resp.status_code == 200
    assert _cleared(resp)


def test_test_email_bypass_clears_a_stale_refresh_cookie(bypass_env, monkeypatch):
    """The bypass mints nothing, so it must clear rather than leave A's cookie."""

    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    bypass_env("1")

    resp = _client.get("/auth/login?test_email=e2e@test.com")

    assert resp.status_code == 200
    assert _cleared(resp)


def test_desktop_callback_clears_the_web_transports_cookie(monkeypatch):
    """Desktop carries its token in the query string; the two must not cross."""

    resp = _google_login(
        monkeypatch, record={"desktop_callback": "http://127.0.0.1:54321/callback"}
    )

    assert resp.status_code == 302
    assert _cleared(resp)


def test_starting_a_new_login_does_not_disturb_an_existing_session(monkeypatch):
    """The redirect *to* Google is not a login completion — an abandoned
    sign-in attempt must not sign the user out of the session they have."""

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")

    resp = _client.get("/auth/login")

    assert resp.status_code == 302
    assert "accounts.google.com" in resp.headers["location"]
    assert "set-cookie" not in resp.headers


def test_bypass_login_after_a_real_login_cannot_inherit_the_first_session(
    bypass_env, monkeypatch, minted_refresh_tokens
):
    """The concrete cross-identity repro, end to end in one client.

    Sign in as A through Google (jar now holds A's refresh family), then
    take the ``?test_email=`` shortcut as B. Before the fix the cookie
    survived, so the next ``/auth/refresh`` would have handed back an
    access token for **A** while localStorage held B's.
    """

    client = TestClient(app, base_url="https://testserver", follow_redirects=False)

    monkeypatch.setattr(
        "channel.auth.state_store.consume_state",
        lambda s: {"PK": f"MGMT_STATE#{s}", "SK": "META"},
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.exchange_google_code", _async_return("id_token"))
    monkeypatch.setattr(
        "channel.auth.mgmt_auth.verify_google_id_token",
        _async_return({"email": "a@example.com", "email_verified": True, "name": "A"}),
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.is_email_allowed", lambda e: True)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda e: False)

    client.get("/auth/callback", params={"code": "c", "state": "F" * 43})
    assert client.cookies.get(REFRESH_COOKIE_NAME)  # A's family is in the jar

    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    bypass_env("1")
    client.get("/auth/login?test_email=b@example.com")

    assert client.cookies.get(REFRESH_COOKIE_NAME) is None
