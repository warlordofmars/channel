# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for management auth routes and helper functions.

State storage (:mod:`starter.auth.state_store`) is patched at module
boundary so these tests do not hit DynamoDB. The fake implementation
mirrors the production contract: ``put_state`` records the state and
``consume_state`` returns the stored payload exactly once, then
``None`` thereafter.
"""

import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("STARTER_JWT_SECRET", "test-secret-for-unit-tests")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")

from channel.api.main import app  # noqa: E402
from channel.auth import state_store  # noqa: E402
from channel.auth.google import _google_client_id, _reset_allowed_emails_cache  # noqa: E402
from channel.auth.mgmt_auth import (  # noqa: E402
    _consume_pending_state,
    _create_pending_state,
    _html_redirect,
    _make_user,
    _mgmt_callback_uri,
)

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
    assert "starter_mgmt_token" in body


def test_make_user_role_user(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    user = _make_user("user@example.com", "Alice")
    assert user["email"] == "user@example.com"
    assert user["display_name"] == "Alice"
    assert user["role"] == "user"


def test_make_user_role_admin(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", '["admin@example.com"]')
    user = _make_user("admin@example.com", "Admin")
    assert user["role"] == "admin"


def test_mgmt_login_bypass_issues_html_with_token(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    with patch("channel.auth.mgmt_auth._BYPASS", True):
        resp = _client.get("/auth/login?test_email=e2e@test.com")
    assert resp.status_code == 200
    assert "starter_mgmt_token" in resp.text


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
    assert "starter_mgmt_token" in resp.text


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
    assert "starter_mgmt_token" not in resp.text


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
    assert "starter_mgmt_token" not in resp.text


def test_mgmt_callback_listed_admin_email_returns_admin_role(monkeypatch):
    """Listed email passes the gate and receives role=admin."""
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
    """The allowlist gate must not re-derive role; role still flows from
    is_admin_email().

    Today both signals read from the same ``ALLOWED_EMAILS`` set, so any
    listed email is also admin in production. This test mocks
    ``is_admin_email`` to False to assert that the new gate did not collapse
    the two checks — if the lists are ever split, role=user becomes a real
    code path and this test is the regression guard.
    """
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
                    "name": "Regular User",
                }
            ),
        ),
        patch("channel.auth.mgmt_auth.is_admin_email", return_value=False),
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
