# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for ``POST /auth/logout`` and the audit-fingerprint helper.

The storage write is patched at the module boundary so these tests do not
touch DynamoDB. The mgmt JWT comes from the real
:func:`channel.auth.tokens.issue_mgmt_jwt` so the bearer-dependency path
exercises the real validator (mirrors the ``fastapi-route`` skill's
"don't mock auth; mock the AWS boundary" rule).
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CHANNEL_JWT_SECRET", "test-secret-for-unit-tests")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")

from channel.api.main import app  # noqa: E402
from channel.auth.logout import _token_fingerprint  # noqa: E402
from channel.auth.refresh import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH  # noqa: E402
from channel.auth.tokens import issue_mgmt_jwt  # noqa: E402

_client = TestClient(app, follow_redirects=False)


def _make_token(*, email: str = "user@example.com", role: str = "user") -> str:
    return issue_mgmt_jwt(
        {
            "user_id": email,
            "email": email,
            "display_name": email.split("@")[0],
            "role": role,
        }
    )


def _auth_headers(*, email: str = "user@example.com", role: str = "user") -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_token(email=email, role=role)}"}


@pytest.fixture
def captured_audit_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture put_audit_event calls without hitting DynamoDB."""

    captured: list[dict[str, Any]] = []

    def _fake_put(
        *, event_type: str, actor_id: str, details: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        record = {
            "event_type": event_type,
            "actor_id": actor_id,
            "details": details,
        }
        captured.append(record)
        return {"event_id": "fake-id", **record}

    monkeypatch.setattr("channel.auth.logout.put_audit_event", _fake_put)
    return captured


def test_logout_requires_bearer(captured_audit_events: list[dict[str, Any]]) -> None:
    """A missing Authorization header must return 401/403 (HTTPBearer default)."""

    resp = _client.post("/auth/logout")
    assert resp.status_code in (401, 403)
    assert captured_audit_events == []


def test_logout_rejects_invalid_token(captured_audit_events: list[dict[str, Any]]) -> None:
    """A malformed token bypasses the dependency with a 401."""

    resp = _client.post("/auth/logout", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401
    assert captured_audit_events == []


def test_logout_happy_path_returns_204(
    captured_audit_events: list[dict[str, Any]],
) -> None:
    resp = _client.post("/auth/logout", headers=_auth_headers())
    assert resp.status_code == 204
    # 204 responses must have an empty body per RFC 7230 §3.3.2.
    assert resp.content == b""


def test_logout_writes_audit_entry_with_actor_and_details(
    captured_audit_events: list[dict[str, Any]],
) -> None:
    resp = _client.post(
        "/auth/logout",
        headers=_auth_headers(email="user@example.com", role="user"),
    )
    assert resp.status_code == 204
    assert len(captured_audit_events) == 1
    event = captured_audit_events[0]
    assert event["event_type"] == "auth.logout"
    # actor_id IS the email today (mgmt JWT sub == email); the audit row
    # therefore needs only one copy of it. The handler intentionally
    # does NOT write details["email"] — see the NOTE in logout.py.
    assert event["actor_id"] == "user@example.com"
    assert event["details"] is not None
    assert "email" not in event["details"]
    assert event["details"]["role"] == "user"
    # token_fingerprint is a 16-char hex correlation handle, not the
    # raw token (and never the bearer prefix).
    fp = event["details"]["token_fingerprint"]
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)


def test_logout_audit_includes_admin_role_for_admin_token(
    captured_audit_events: list[dict[str, Any]],
) -> None:
    _client.post(
        "/auth/logout",
        headers=_auth_headers(email="admin@example.com", role="admin"),
    )
    assert captured_audit_events[-1]["details"]["role"] == "admin"


def test_logout_propagates_jti_when_present(
    monkeypatch: pytest.MonkeyPatch,
    captured_audit_events: list[dict[str, Any]],
) -> None:
    """If the JWT carries a jti claim, it lands in details for future denylist correlation."""

    def _fake_decode(_token: str) -> dict[str, Any]:
        return {
            "sub": "user@example.com",
            "email": "user@example.com",
            "role": "user",
            "iat": 1700000000,
            "exp": 1700028800,
            "typ": "mgmt",
            "jti": "tok-abc-123",
        }

    monkeypatch.setattr("channel.api._auth.decode_mgmt_jwt", _fake_decode)

    resp = _client.post("/auth/logout", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 204
    assert captured_audit_events[-1]["details"]["jti"] == "tok-abc-123"


def test_logout_swallows_audit_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A storage error must not break the visible logout — return 204 anyway."""

    def _raise(**_kwargs: Any) -> None:
        raise RuntimeError("DDB transiently unavailable")

    monkeypatch.setattr("channel.auth.logout.put_audit_event", _raise)
    resp = _client.post("/auth/logout", headers=_auth_headers())
    assert resp.status_code == 204


def test_token_fingerprint_is_stable_and_truncated() -> None:
    """Fingerprint is deterministic per-claims and 16 lowercase hex chars."""

    claims = {"sub": "u-1", "iat": 1700000000, "exp": 1700028800}
    fp1 = _token_fingerprint(claims)
    fp2 = _token_fingerprint(claims)
    assert fp1 == fp2
    assert len(fp1) == 16
    assert fp1 == fp1.lower()


def test_token_fingerprint_changes_with_iat() -> None:
    """A re-issued token (different iat) produces a different fingerprint."""

    a = _token_fingerprint({"sub": "u-1", "iat": 1700000000, "exp": 1700028800})
    b = _token_fingerprint({"sub": "u-1", "iat": 1700000001, "exp": 1700028801})
    assert a != b


def test_token_fingerprint_changes_with_subject() -> None:
    """Different actors produce different fingerprints even with same iat/exp."""

    a = _token_fingerprint({"sub": "u-1", "iat": 1700000000, "exp": 1700028800})
    b = _token_fingerprint({"sub": "u-2", "iat": 1700000000, "exp": 1700028800})
    assert a != b


def test_token_fingerprint_handles_missing_claims() -> None:
    """Defensive defaults — empty claims map produces a stable hash."""

    fp = _token_fingerprint({})
    assert len(fp) == 16


def test_logout_revokes_jti_on_denylist(
    monkeypatch: pytest.MonkeyPatch,
    captured_audit_events: list[dict[str, Any]],
) -> None:
    """Logout adds the token's jti to the denylist with its exp as the TTL (#240)."""

    denied: list[tuple[str, int]] = []

    def _fake_decode(_token: str) -> dict[str, Any]:
        return {
            "sub": "user@example.com",
            "email": "user@example.com",
            "role": "user",
            "iat": 1700000000,
            "exp": 1700028800,
            "typ": "mgmt",
            "jti": "tok-abc-123",
        }

    monkeypatch.setattr("channel.api._auth.decode_mgmt_jwt", _fake_decode)
    monkeypatch.setattr("channel.auth.logout.deny_jti", lambda jti, exp: denied.append((jti, exp)))

    resp = _client.post("/auth/logout", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 204
    assert denied == [("tok-abc-123", 1700028800)]


def test_logout_swallows_denylist_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    captured_audit_events: list[dict[str, Any]],
) -> None:
    """A denylist-write failure is logged but must not break the visible logout."""

    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("DDB transiently unavailable")

    monkeypatch.setattr("channel.auth.logout.deny_jti", _raise)
    resp = _client.post("/auth/logout", headers=_auth_headers())
    assert resp.status_code == 204


def test_second_request_with_revoked_token_is_401(
    monkeypatch: pytest.MonkeyPatch,
    captured_audit_events: list[dict[str, Any]],
) -> None:
    """End-to-end revoke-on-logout: the same token is 401 after logout (#240).

    Drives the real ``require_mgmt_user`` + real ``mgmt_logout`` through a
    shared in-memory denylist so the wiring (logout writes → next request
    reads) is proven without DynamoDB. The read side moved into
    ``decode_mgmt_jwt`` in #291, so that is where the seam is patched;
    the property under test is unchanged.
    """

    revoked: set[str] = set()
    monkeypatch.setattr("channel.auth.logout.deny_jti", lambda jti, _exp: revoked.add(jti))
    monkeypatch.setattr("channel.auth.tokens.is_jti_denied", lambda jti: jti in revoked)

    headers = _auth_headers()
    first = _client.post("/auth/logout", headers=headers)
    assert first.status_code == 204
    second = _client.post("/auth/logout", headers=headers)
    assert second.status_code == 401


# ----------------------------------------------------------------
# Refresh-token revocation on logout (#292, epic #241)
# ----------------------------------------------------------------


@pytest.fixture
def revoked_refresh_tokens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture revoke_refresh_token calls without hitting DynamoDB.

    Returns the raw tokens presented, so a test can assert *which*
    credential logout acted on — the whole point of the body-vs-cookie
    precedence rule.
    """

    presented: list[str] = []

    def _fake_revoke(raw_token: str) -> int:
        presented.append(raw_token)
        return 2  # a live row plus its rotated ancestor

    monkeypatch.setattr("channel.auth.logout.revoke_refresh_token", _fake_revoke)
    return presented


def _client_with_refresh_cookie(value: str) -> TestClient:
    """A client whose jar already holds the refresh cookie.

    A fresh client per call keeps one test's cookie out of the next
    test's jar (per-request ``cookies=`` is deprecated in Starlette).
    """

    client = TestClient(app, follow_redirects=False)
    client.cookies.set(REFRESH_COOKIE_NAME, value)
    return client


def test_logout_revokes_the_refresh_family_from_the_cookie(
    revoked_refresh_tokens: list[str],
    captured_audit_events: list[dict[str, Any]],
) -> None:
    """The web transport: the SPA cannot read the HttpOnly cookie, and need not."""

    resp = _client_with_refresh_cookie("web-refresh-token").post(
        "/auth/logout", headers=_auth_headers()
    )

    assert resp.status_code == 204
    assert revoked_refresh_tokens == ["web-refresh-token"]
    assert captured_audit_events[-1]["details"]["refresh_rows_revoked"] == 2


def test_logout_revokes_the_refresh_family_from_the_request_body(
    revoked_refresh_tokens: list[str],
    captured_audit_events: list[dict[str, Any]],
) -> None:
    """The desktop transport: the token lives in the OS keychain (#297)."""

    resp = _client.post(
        "/auth/logout",
        headers=_auth_headers(),
        json={"refresh_token": "desktop-refresh-token"},
    )

    assert resp.status_code == 204
    assert revoked_refresh_tokens == ["desktop-refresh-token"]


def test_logout_body_token_wins_over_a_stray_cookie(
    revoked_refresh_tokens: list[str],
) -> None:
    """An explicit credential beats an incidental one — matches /auth/refresh."""

    _client_with_refresh_cookie("cookie-token").post(
        "/auth/logout",
        headers=_auth_headers(),
        json={"refresh_token": "body-token"},
    )

    assert revoked_refresh_tokens == ["body-token"]


def test_logout_without_a_refresh_token_revokes_nothing(
    revoked_refresh_tokens: list[str],
    captured_audit_events: list[dict[str, Any]],
) -> None:
    """A pre-#292 session has no refresh token; that is migration, not failure."""

    resp = _client.post("/auth/logout", headers=_auth_headers())

    assert resp.status_code == 204
    assert revoked_refresh_tokens == []
    # Absent rather than zero, so an operator can tell "nothing was
    # presented" from "nothing was live".
    assert "refresh_rows_revoked" not in captured_audit_events[-1]["details"]


def test_logout_records_zero_rows_when_the_token_is_already_dead(
    monkeypatch: pytest.MonkeyPatch,
    captured_audit_events: list[dict[str, Any]],
) -> None:
    """Revoking an unknown/expired token is idempotent and leaks nothing."""

    monkeypatch.setattr("channel.auth.logout.revoke_refresh_token", lambda _raw: 0)

    resp = _client_with_refresh_cookie("stale-token").post("/auth/logout", headers=_auth_headers())

    assert resp.status_code == 204
    assert captured_audit_events[-1]["details"]["refresh_rows_revoked"] == 0


def test_logout_ignores_a_whitespace_only_cookie(
    revoked_refresh_tokens: list[str],
) -> None:
    """A blank value must never reach a storage lookup."""

    _client_with_refresh_cookie("   ").post("/auth/logout", headers=_auth_headers())

    assert revoked_refresh_tokens == []


def test_logout_swallows_refresh_revoke_failure(
    monkeypatch: pytest.MonkeyPatch,
    captured_audit_events: list[dict[str, Any]],
) -> None:
    """Best-effort, like the denylist write: a DDB blip must not strand the user."""

    def _raise(_raw: str) -> int:
        raise RuntimeError("DDB transiently unavailable")

    monkeypatch.setattr("channel.auth.logout.revoke_refresh_token", _raise)

    resp = _client_with_refresh_cookie("web-refresh-token").post(
        "/auth/logout", headers=_auth_headers()
    )

    assert resp.status_code == 204
    assert "refresh_rows_revoked" not in captured_audit_events[-1]["details"]


def test_logout_clears_the_refresh_cookie(
    revoked_refresh_tokens: list[str],
) -> None:
    """Attributes must match the mint's or the deletion lands on a different cookie."""

    resp = _client_with_refresh_cookie("web-refresh-token").post(
        "/auth/logout", headers=_auth_headers()
    )

    set_cookie = resp.headers["set-cookie"]
    assert f"{REFRESH_COOKIE_NAME}=" in set_cookie
    assert f"Path={REFRESH_COOKIE_PATH}" in set_cookie
    assert "Max-Age=0" in set_cookie


def test_logout_clears_the_cookie_even_when_the_server_side_revoke_failed(
    monkeypatch: pytest.MonkeyPatch,
    captured_audit_events: list[dict[str, Any]],
) -> None:
    """Denying the browser its copy is still worth doing when the revoke didn't land."""

    def _raise(_raw: str) -> int:
        raise RuntimeError("DDB transiently unavailable")

    monkeypatch.setattr("channel.auth.logout.revoke_refresh_token", _raise)

    resp = _client_with_refresh_cookie("web-refresh-token").post(
        "/auth/logout", headers=_auth_headers()
    )

    assert "Max-Age=0" in resp.headers["set-cookie"]


def test_logout_rejects_an_unexpected_body_field(
    revoked_refresh_tokens: list[str],
) -> None:
    """``extra="forbid"`` — a typo'd field on an auth route is an error, not a no-op."""

    resp = _client.post(
        "/auth/logout",
        headers=_auth_headers(),
        json={"refresh_token": "t", "refesh_token": "typo"},
    )

    assert resp.status_code == 422
    assert revoked_refresh_tokens == []
