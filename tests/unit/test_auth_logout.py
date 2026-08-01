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
