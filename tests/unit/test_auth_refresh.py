# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for ``POST /auth/refresh`` (#291, epic #241).

The AWS boundary mocked here is ``consume_refresh_token`` — #290's
storage primitive, which has its own unit + DynamoDB Local coverage.
These tests pin the *endpoint's* contract on top of it: the CSRF gate,
which transport each caller shape selects, what crosses the wire in each
direction, and the cookie attribute matrix.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CHANNEL_JWT_SECRET", "test-secret-for-unit-tests")

from channel.api.main import app  # noqa: E402
from channel.auth.refresh import (  # noqa: E402
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
)
from channel.auth.tokens import MGMT_JWT_TTL_SECONDS, decode_mgmt_jwt  # noqa: E402
from channel.models import (  # noqa: E402
    RefreshConsumeOutcome,
    RefreshConsumeResult,
    RefreshToken,
)

_client = TestClient(app)


def _with_cookie(value: str) -> TestClient:
    """A client whose jar already holds the refresh cookie.

    Per-request ``cookies=`` is deprecated in Starlette's TestClient
    (ambiguous persistence semantics), and a fresh client per call keeps
    one test's rotated cookie out of the next test's jar.
    """
    client = TestClient(app)
    client.cookies.set(REFRESH_COOKIE_NAME, value)
    return client


_CSRF = {"X-Channel-Refresh": "1"}
_USER = "ada@example.com"


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="microseconds")


def _row(user_id: str = _USER, device_id: str = "dev-1", **overrides: Any) -> RefreshToken:
    """A live refresh row as ``mint_refresh_token`` would have written it."""
    now = _iso(timedelta(0))
    fields: dict[str, Any] = {
        "token_hash": "a" * 64,
        "user_id": user_id,
        "device_id": device_id,
        "issued_at": now,
        "last_used_at": now,
        "absolute_expires_at": _iso(timedelta(days=30)),
        "idle_expires_at": _iso(timedelta(days=7)),
    }
    fields.update(overrides)
    return RefreshToken(**fields)


def _ok(rotated: str = "rotated-token", **row_kwargs: Any) -> RefreshConsumeResult:
    return RefreshConsumeResult(
        outcome=RefreshConsumeOutcome.OK,
        raw_token=rotated,
        token=_row(**row_kwargs),
    )


@pytest.fixture
def consumed(monkeypatch: pytest.MonkeyPatch):
    """Patch ``consume_refresh_token``; return the list of tokens it saw."""
    seen: list[str] = []
    box: dict[str, RefreshConsumeResult] = {"result": _ok()}

    def _consume(raw_token: str) -> RefreshConsumeResult:
        seen.append(raw_token)
        return box["result"]

    monkeypatch.setattr("channel.auth.refresh.consume_refresh_token", _consume)
    return seen, box


@pytest.fixture(autouse=True)
def _non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep role resolution off the SSM/allowlist path in unit tests."""
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda _email: False)


# ----------------------------------------------------------------
# CSRF gate
# ----------------------------------------------------------------


def test_missing_csrf_header_is_rejected(consumed):
    """A forged cross-site POST cannot set a custom header, so absence is fatal."""
    seen, _ = consumed
    resp = _client.post("/auth/refresh", json={"refresh_token": "whatever"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing X-Channel-Refresh header"
    # The token must not be burned by a request that failed the gate —
    # consuming it would rotate the real client out of its session.
    assert seen == []


def test_csrf_header_content_is_never_inspected(consumed):
    """Any non-empty value passes — the gate never reads what's in it."""
    resp = _client.post(
        "/auth/refresh",
        json={"refresh_token": "t"},
        headers={"X-Channel-Refresh": "anything-at-all"},
    )
    assert resp.status_code == 200


def test_empty_csrf_header_is_treated_as_absent(consumed):
    """Empty is rejected, not accepted — the deliberate stricter reading.

    Proxies and client stacks routinely strip empty headers, so an empty
    value is indistinguishable from an absent one by the time it reaches
    us. Honouring it would make a CSRF control's behaviour depend on
    whatever intermediary is in front of the app.
    """
    seen, _ = consumed
    resp = _client.post(
        "/auth/refresh", json={"refresh_token": "t"}, headers={"X-Channel-Refresh": ""}
    )
    assert resp.status_code == 403
    assert seen == []


# ----------------------------------------------------------------
# Body transport (desktop / future mobile)
# ----------------------------------------------------------------


def test_body_transport_returns_access_and_rotated_refresh_token(consumed):
    seen, box = consumed
    box["result"] = _ok(rotated="next-refresh-token")

    resp = _client.post("/auth/refresh", json={"refresh_token": "presented"}, headers=_CSRF)

    assert resp.status_code == 200
    assert seen == ["presented"]
    payload = resp.json()
    assert payload["refresh_token"] == "next-refresh-token"
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] == MGMT_JWT_TTL_SECONDS


def test_body_transport_sets_no_cookie(consumed):
    """A keychain-backed client must not also be handed a browser cookie."""
    resp = _client.post("/auth/refresh", json={"refresh_token": "presented"}, headers=_CSRF)
    assert "set-cookie" not in {k.lower() for k in resp.headers}


def test_access_token_carries_the_rows_identity(consumed):
    seen, box = consumed
    box["result"] = _ok(user_id="grace@example.com")

    resp = _client.post("/auth/refresh", json={"refresh_token": "t"}, headers=_CSRF)

    claims = decode_mgmt_jwt(resp.json()["access_token"])
    assert claims["sub"] == "grace@example.com"
    assert claims["email"] == "grace@example.com"
    assert claims["typ"] == "mgmt"
    assert claims["exp"] - claims["iat"] == MGMT_JWT_TTL_SECONDS


def test_access_token_carries_the_rows_persisted_display_name(consumed):
    """#292 put Google's real name on the row so refreshing stops degrading it."""
    box = consumed[1]
    box["result"] = _ok(user_id="grace@example.com", display_name="Grace Hopper")

    resp = _client.post("/auth/refresh", json={"refresh_token": "t"}, headers=_CSRF)

    assert decode_mgmt_jwt(resp.json()["access_token"])["display_name"] == "Grace Hopper"


def test_access_token_falls_back_to_the_local_part_for_a_nameless_row(consumed):
    """Families minted before #292 have no name; the old fallback still applies."""
    box = consumed[1]
    box["result"] = _ok(user_id="grace@example.com")  # display_name defaults to None

    resp = _client.post("/auth/refresh", json={"refresh_token": "t"}, headers=_CSRF)

    assert decode_mgmt_jwt(resp.json()["access_token"])["display_name"] == "grace"


def test_access_token_role_is_recomputed_at_refresh(consumed, monkeypatch):
    """An allowlist grant takes effect on the next refresh, not the next login."""
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda _email: True)
    resp = _client.post("/auth/refresh", json={"refresh_token": "t"}, headers=_CSRF)
    assert decode_mgmt_jwt(resp.json()["access_token"])["role"] == "admin"


def test_each_refresh_mints_a_distinct_jti(consumed):
    """Refreshed tokens stay independently revocable via the #240 denylist."""
    first = _client.post("/auth/refresh", json={"refresh_token": "t"}, headers=_CSRF)
    second = _client.post("/auth/refresh", json={"refresh_token": "t"}, headers=_CSRF)
    a = decode_mgmt_jwt(first.json()["access_token"])["jti"]
    b = decode_mgmt_jwt(second.json()["access_token"])["jti"]
    assert a != b


# ----------------------------------------------------------------
# Cookie transport (web SPA)
# ----------------------------------------------------------------


def test_cookie_transport_reads_the_cookie_and_rotates_it(consumed):
    seen, box = consumed
    box["result"] = _ok(rotated="next-cookie-token")

    resp = _with_cookie("cookie-token").post("/auth/refresh", headers=_CSRF)

    assert resp.status_code == 200
    assert seen == ["cookie-token"]
    assert resp.cookies[REFRESH_COOKIE_NAME] == "next-cookie-token"


def test_cookie_transport_never_returns_the_token_in_the_body(consumed):
    """HttpOnly is pointless if the plaintext also arrives where JS can read it."""
    resp = _with_cookie("cookie-token").post("/auth/refresh", headers=_CSRF)
    payload = resp.json()
    assert "refresh_token" not in payload
    assert payload["access_token"]


def test_rotated_cookie_carries_the_full_attribute_matrix(consumed):
    box = consumed[1]
    box["result"] = _ok(absolute_expires_at=_iso(timedelta(days=30)))

    resp = _with_cookie("cookie-token").post("/auth/refresh", headers=_CSRF)

    set_cookie = resp.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert f"Path={REFRESH_COOKIE_PATH}" in set_cookie


def test_cookie_max_age_tracks_the_absolute_deadline(consumed):
    """Rotation must not walk the cookie past the family's login-time deadline."""
    box = consumed[1]
    box["result"] = _ok(absolute_expires_at=_iso(timedelta(hours=2)))

    resp = _with_cookie("cookie-token").post("/auth/refresh", headers=_CSRF)

    max_age = int(
        next(
            part.split("=")[1]
            for part in resp.headers["set-cookie"].split("; ")
            if part.lower().startswith("max-age=")
        )
    )
    assert 7100 <= max_age <= 7200


def test_cookie_max_age_floors_at_zero(consumed):
    """A past deadline yields an immediately-expiring cookie, never a negative age."""
    box = consumed[1]
    box["result"] = _ok(absolute_expires_at=_iso(timedelta(seconds=-5)))

    resp = _with_cookie("cookie-token").post("/auth/refresh", headers=_CSRF)

    assert "Max-Age=0" in resp.headers["set-cookie"]


def test_body_token_wins_over_a_stray_cookie(consumed):
    """An explicit credential beats an incidental one."""
    seen, _ = consumed
    _with_cookie("cookie-token").post(
        "/auth/refresh", json={"refresh_token": "body-token"}, headers=_CSRF
    )
    assert seen == ["body-token"]


# ----------------------------------------------------------------
# Cacheability
# ----------------------------------------------------------------


def test_success_response_is_no_store(consumed):
    """RFC 6749 §5.1 / RFC 9700 — a token response must never be cached."""
    resp = _client.post("/auth/refresh", json={"refresh_token": "t"}, headers=_CSRF)
    assert resp.headers["cache-control"] == "no-store"


def test_cookie_success_response_is_no_store(consumed):
    resp = _with_cookie("cookie-token").post("/auth/refresh", headers=_CSRF)
    assert resp.headers["cache-control"] == "no-store"


def test_rejection_response_is_no_store(consumed):
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)
    resp = _client.post("/auth/refresh", json={"refresh_token": "t"}, headers=_CSRF)
    assert resp.headers["cache-control"] == "no-store"


# ----------------------------------------------------------------
# Rejections
# ----------------------------------------------------------------


def test_no_token_at_all_is_401(consumed):
    """The epic's migration path: a legacy session has nothing to refresh."""
    seen, _ = consumed
    resp = _client.post("/auth/refresh", headers=_CSRF)
    assert resp.status_code == 401
    assert seen == []
    assert "set-cookie" not in {k.lower() for k in resp.headers}


def test_blank_body_token_is_401(consumed):
    seen, _ = consumed
    resp = _client.post("/auth/refresh", json={"refresh_token": "   "}, headers=_CSRF)
    assert resp.status_code == 401
    assert seen == []


def test_whitespace_only_cookie_is_rejected_and_cleared(consumed):
    """A junk cookie must not sit in the jar failing every future refresh.

    ``via_cookie`` keys off the cookie's presence rather than its
    stripped value precisely so this corner still gets a clearing
    ``Set-Cookie``.
    """
    seen, _ = consumed
    resp = _with_cookie("   ").post("/auth/refresh", headers=_CSRF)
    assert resp.status_code == 401
    assert seen == []
    assert "Max-Age=0" in resp.headers["set-cookie"]


@pytest.mark.parametrize(
    "outcome",
    [
        RefreshConsumeOutcome.NOT_FOUND,
        RefreshConsumeOutcome.REVOKED,
        RefreshConsumeOutcome.REUSED,
        RefreshConsumeOutcome.EXPIRED_ABSOLUTE,
        RefreshConsumeOutcome.EXPIRED_IDLE,
    ],
)
def test_every_failed_outcome_is_an_indistinguishable_401(consumed, outcome):
    """The finer taxonomy stays server-side — it would be an attacker's oracle."""
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=outcome)

    resp = _client.post("/auth/refresh", json={"refresh_token": "t"}, headers=_CSRF)

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired refresh token"


def test_rejected_cookie_transport_clears_the_dead_cookie(consumed):
    """Otherwise the browser replays it forever, re-arming the reuse cascade."""
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.REUSED)

    resp = _with_cookie("stale").post("/auth/refresh", headers=_CSRF)

    assert resp.status_code == 401
    set_cookie = resp.headers["set-cookie"]
    assert f"{REFRESH_COOKIE_NAME}=" in set_cookie
    assert f"Path={REFRESH_COOKIE_PATH}" in set_cookie
    assert "Max-Age=0" in set_cookie


def test_rejected_body_transport_sets_no_cookie(consumed):
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)

    resp = _client.post("/auth/refresh", json={"refresh_token": "t"}, headers=_CSRF)

    assert "set-cookie" not in {k.lower() for k in resp.headers}


def test_unknown_body_field_is_rejected(consumed):
    """``extra="forbid"`` — an auth route must not silently ignore a field."""
    seen, _ = consumed
    resp = _client.post(
        "/auth/refresh", json={"refresh_token": "t", "device_id": "x"}, headers=_CSRF
    )
    assert resp.status_code == 422
    assert seen == []


def test_reuse_result_carrying_a_revoked_count_is_still_a_401(consumed):
    """The family-revoke count is #294's telemetry, not a client-visible field."""
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.REUSED, revoked_count=3)

    resp = _client.post("/auth/refresh", json={"refresh_token": "t"}, headers=_CSRF)

    assert resp.status_code == 401
    assert "revoked_count" not in resp.json()


# ----------------------------------------------------------------
# The route is unauthenticated by design
# ----------------------------------------------------------------


def test_refresh_ignores_the_authorization_header(consumed):
    """The refresh token is the credential; the access JWT is not consulted.

    Sent with an unmistakably invalid bearer token: if the route were
    gated on ``require_mgmt_user`` this would be a 401, and a client
    whose access token had merely expired could never refresh — the
    deadlock the whole flow exists to avoid.
    """
    resp = _client.post(
        "/auth/refresh",
        json={"refresh_token": "t"},
        headers={**_CSRF, "Authorization": "Bearer not.a.jwt"},
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]
