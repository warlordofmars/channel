# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for Google OAuth helper functions."""

import os

os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("ALLOWED_EMAILS", '["admin@test.com"]')

from channel.auth.google import (  # noqa: E402
    _allowed_emails,
    _google_client_id,
    _google_client_secret,
    _reset_allowed_emails_cache,
    google_authorization_url,
    is_admin_email,
    is_email_allowed,
)


def _clear_caches():
    _google_client_id.cache_clear()
    _google_client_secret.cache_clear()
    _reset_allowed_emails_cache()


def setup_function():
    _clear_caches()


def teardown_function():
    _clear_caches()


def test_google_client_id_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "my-client-id")
    assert _google_client_id() == "my-client-id"


def test_google_client_secret_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "my-secret")
    assert _google_client_secret() == "my-secret"


def test_allowed_emails_from_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", '["alice@example.com","bob@example.com"]')
    result = _allowed_emails()
    assert "alice@example.com" in result
    assert "bob@example.com" in result


def test_google_authorization_url(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-oauth-id")
    url = google_authorization_url("test-state-123", "https://example.com/auth/callback")
    assert "accounts.google.com" in url
    assert "test-state-123" in url
    assert "test-oauth-id" in url
    assert "response_type=code" in url


def test_google_authorization_url_requests_profile_scope(monkeypatch):
    # `profile` is required for Google to return the `name` claim, which the
    # backend maps to display_name → chat-app greeting. Without it Google
    # only returns email, and display_name falls back to the email's
    # local-part.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-oauth-id")
    url = google_authorization_url("s", "https://example.com/auth/callback")
    assert "scope=openid+email+profile" in url or "scope=openid%20email%20profile" in url


def test_is_email_allowed_when_in_list(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", '["allowed@test.com"]')
    assert is_email_allowed("allowed@test.com") is True


def test_is_email_allowed_when_not_in_list(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", '["allowed@test.com"]')
    assert is_email_allowed("other@test.com") is False


def test_is_email_allowed_empty_list_denies_all(monkeypatch):
    """Empty allowlist denies all — safer default for freshly-deployed stacks."""
    monkeypatch.setenv("ALLOWED_EMAILS", "[]")
    assert is_email_allowed("anyone@example.com") is False


def test_is_admin_email_when_listed(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", '["admin@test.com"]')
    assert is_admin_email("admin@test.com") is True


def test_is_admin_email_when_not_listed(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", '["admin@test.com"]')
    assert is_admin_email("user@test.com") is False


def test_allowed_emails_invalid_json_denies_all(monkeypatch):
    """Malformed JSON in ALLOWED_EMAILS env must fail closed (deny all)."""
    monkeypatch.setenv("ALLOWED_EMAILS", "not-json{")
    assert _allowed_emails() == frozenset()


def test_allowed_emails_non_array_denies_all(monkeypatch):
    """Non-array JSON in ALLOWED_EMAILS env must fail closed (deny all)."""
    monkeypatch.setenv("ALLOWED_EMAILS", '{"admin": "alice@example.com"}')
    assert _allowed_emails() == frozenset()


def test_allowed_emails_ttl_cache_returns_cached_value(monkeypatch):
    """A second call within the TTL window returns the cached set even if
    the env var changes — the cache absorbs intra-window churn so a single
    login doesn't double-fetch.
    """
    monkeypatch.setenv("ALLOWED_EMAILS", '["alice@example.com"]')
    first = _allowed_emails()
    monkeypatch.setenv("ALLOWED_EMAILS", '["bob@example.com"]')
    second = _allowed_emails()
    assert first == second == frozenset({"alice@example.com"})
