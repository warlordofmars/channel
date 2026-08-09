# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for Google OAuth helper functions."""

import os

os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("ALLOWED_EMAILS", '["admin@test.com"]')

import pytest  # noqa: E402

from channel.auth.google import (  # noqa: E402
    _admin_allowed_emails,
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
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", '["admin@test.com"]')
    assert is_admin_email("admin@test.com") is True


def test_is_admin_email_when_not_listed(monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", '["admin@test.com"]')
    assert is_admin_email("user@test.com") is False


# ---------------------------------------------------------------------------
# #600 — admin is its own allowlist, and every failure path denies
# ---------------------------------------------------------------------------


def test_signing_in_does_not_confer_admin(monkeypatch):
    """The #600 regression test.

    Before the split, ``is_admin_email`` read the sign-in allowlist, so
    every email permitted to log in was an admin. A user on the sign-in
    list and absent from the admin list must be allowed in as ``user``.
    """
    monkeypatch.setenv("ALLOWED_EMAILS", '["user@test.com","admin@test.com"]')
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", '["admin@test.com"]')

    assert is_email_allowed("user@test.com") is True
    assert is_admin_email("user@test.com") is False
    assert is_admin_email("admin@test.com") is True


def test_admin_allowlist_unset_denies_everyone(monkeypatch):
    """Default-deny path 1 — nothing configured at all.

    Neither the env var nor a populated parameter exists. The autouse
    ``_no_ssm_reads`` fixture makes the SSM fallback raise, which is the
    same shape as a missing parameter in production.
    """
    monkeypatch.delenv("ADMIN_ALLOWED_EMAILS", raising=False)
    monkeypatch.setenv("ALLOWED_EMAILS", '["admin@test.com"]')

    assert _admin_allowed_emails() == frozenset()
    assert is_admin_email("admin@test.com") is False


def test_admin_allowlist_empty_denies_everyone(monkeypatch):
    """Default-deny path 2 — the parameter exists but is still ``[]``.

    This is the shipped CDK default, and therefore the state of every
    stack between the deploy that lands #600 and the deploy step that
    populates the parameter.
    """
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "[]")
    monkeypatch.setenv("ALLOWED_EMAILS", '["admin@test.com"]')

    assert _admin_allowed_emails() == frozenset()
    assert is_admin_email("admin@test.com") is False


def test_admin_allowlist_malformed_json_denies_everyone(monkeypatch):
    """Default-deny path 3 — unparseable value."""
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", "not-json{")
    monkeypatch.setenv("ALLOWED_EMAILS", '["admin@test.com"]')

    assert _admin_allowed_emails() == frozenset()
    assert is_admin_email("admin@test.com") is False


def test_admin_allowlist_ssm_read_error_denies_everyone(monkeypatch):
    """Default-deny path 4 — SSM itself fails.

    A throttle, a permissions gap or an outage must not be readable as
    "grant admin to everyone"; it has to land on the same empty set as a
    parameter that was never populated.
    """
    monkeypatch.delenv("ADMIN_ALLOWED_EMAILS", raising=False)
    monkeypatch.setenv("ALLOWED_EMAILS", '["admin@test.com"]')

    def _boom(_name):
        raise RuntimeError("ssm unavailable")

    monkeypatch.setattr("channel.auth.google._ssm_param", _boom)

    assert _admin_allowed_emails() == frozenset()
    assert is_admin_email("admin@test.com") is False


def test_admin_allowlist_non_array_json_denies_everyone(monkeypatch):
    """Valid JSON that isn't a list is still a misconfiguration."""
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", '{"admin": "admin@test.com"}')

    assert _admin_allowed_emails() == frozenset()
    assert is_admin_email("admin@test.com") is False


def test_admin_allowlist_unhashable_members_deny_everyone(monkeypatch):
    """A JSON array of objects can't build a frozenset — deny, don't raise."""
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", '[{"email": "admin@test.com"}]')

    assert _admin_allowed_emails() == frozenset()
    assert is_admin_email("admin@test.com") is False


def test_admin_allowlist_from_ssm_parameter(monkeypatch):
    """The SSM transport works when the parameter is readable."""
    monkeypatch.delenv("ADMIN_ALLOWED_EMAILS", raising=False)
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS_PARAM", "/channel/dev/admin-allowed-emails")

    seen = []

    def _fake_ssm(name):
        seen.append(name)
        return '["admin@test.com"]'

    monkeypatch.setattr("channel.auth.google._ssm_param", _fake_ssm)

    assert is_admin_email("admin@test.com") is True
    assert seen == ["/channel/dev/admin-allowed-emails"]


def test_admin_allowlist_ssm_default_parameter_path(monkeypatch):
    """With no ``*_PARAM`` override, the default path mirrors sign-in's."""
    monkeypatch.delenv("ADMIN_ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("ADMIN_ALLOWED_EMAILS_PARAM", raising=False)

    seen = []

    def _fake_ssm(name):
        seen.append(name)
        return "[]"

    monkeypatch.setattr("channel.auth.google._ssm_param", _fake_ssm)

    assert _admin_allowed_emails() == frozenset()
    assert seen == ["/channel/admin-allowed-emails"]


def test_admin_allowlist_ssm_malformed_value_denies_everyone(monkeypatch):
    """Malformed JSON over the SSM transport fails closed too."""
    monkeypatch.delenv("ADMIN_ALLOWED_EMAILS", raising=False)
    monkeypatch.setattr("channel.auth.google._ssm_param", lambda _name: "not-json{")

    assert _admin_allowed_emails() == frozenset()


def test_admin_allowlist_ssm_non_array_denies_everyone(monkeypatch):
    """A non-array SSM value fails closed — covers the SSM-label raise."""
    monkeypatch.delenv("ADMIN_ALLOWED_EMAILS", raising=False)
    monkeypatch.setattr("channel.auth.google._ssm_param", lambda _name: '"admin@test.com"')

    assert _admin_allowed_emails() == frozenset()


def test_admin_and_signin_caches_are_independent(monkeypatch):
    """Two lists, two cache slots — neither may serve the other's value."""
    monkeypatch.setenv("ALLOWED_EMAILS", '["user@test.com"]')
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", '["admin@test.com"]')

    assert _allowed_emails() == frozenset({"user@test.com"})
    assert _admin_allowed_emails() == frozenset({"admin@test.com"})
    # Re-read within the TTL window: still distinct, still not swapped.
    assert _allowed_emails() == frozenset({"user@test.com"})
    assert _admin_allowed_emails() == frozenset({"admin@test.com"})


def test_admin_allowlist_ttl_cache_returns_cached_value(monkeypatch):
    """The admin list gets the same ~60s cache as the sign-in list."""
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", '["admin@test.com"]')
    first = _admin_allowed_emails()
    monkeypatch.setenv("ADMIN_ALLOWED_EMAILS", '["other@test.com"]')
    second = _admin_allowed_emails()
    assert first == second == frozenset({"admin@test.com"})


def test_unit_tests_may_not_read_ssm():
    """The autouse guard in conftest is itself load-bearing — pin it.

    Without it a suite that forgets to set an allowlist env var makes a
    real boto3 call, which is both slow and outside the unit contract.
    """
    from channel.auth.google import _ssm_param

    with pytest.raises(AssertionError, match="must not read SSM"):
        _ssm_param("/channel/whatever")


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
