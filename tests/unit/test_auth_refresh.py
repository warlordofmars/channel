# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for ``POST /auth/refresh`` (#291, #294, epic #241).

The AWS boundary mocked here is ``consume_refresh_token`` — #290's
storage primitive, which has its own unit + DynamoDB Local coverage.
These tests pin the *endpoint's* contract on top of it: the CSRF gate,
which transport each caller shape selects, what crosses the wire in each
direction, the cookie attribute matrix, and (#294) the per-token-family
rate limit and the EMF counters every exit path emits.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CHANNEL_JWT_SECRET", "test-secret-for-unit-tests")

from channel.api.main import app  # noqa: E402
from channel.auth import refresh as refresh_module  # noqa: E402
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
from channel.rate_limit import FixedWindowRateLimiter  # noqa: E402

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


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Isolate each test from the module-level limiter's window (#294).

    The limiter is process-global by design, so without this a suite that
    replays the same token across a dozen assertions would start 429-ing
    partway through — and which test tipped it over would depend on
    collection order.
    """
    refresh_module._refresh_limiter.clear()
    yield
    refresh_module._refresh_limiter.clear()


class _Clock:
    """Manually advanced monotonic clock for the limiter."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def limiter(monkeypatch: pytest.MonkeyPatch):
    """Swap in a limiter on a fake clock; return ``(limiter, clock)``."""
    clock = _Clock()
    replacement = FixedWindowRateLimiter(limit=5, window_seconds=60.0, clock=clock)
    monkeypatch.setattr(refresh_module, "_refresh_limiter", replacement)
    return replacement, clock


@pytest.fixture
def counters(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Capture ``record_refresh_outcome`` calls made by the route."""
    recorder = AsyncMock()
    monkeypatch.setattr(refresh_module, "record_refresh_outcome", recorder)
    return recorder


def _outcomes(recorder: AsyncMock) -> list[tuple[bool, str | None]]:
    """Flatten recorded calls to ``(success, reason)`` pairs."""
    return [
        (call.kwargs["success"], call.kwargs.get("reason")) for call in recorder.await_args_list
    ]


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


# ----------------------------------------------------------------
# Rate limiting (#294)
# ----------------------------------------------------------------


def _post(token: str) -> Any:
    return _client.post("/auth/refresh", json={"refresh_token": token}, headers=_CSRF)


def test_a_sixth_attempt_in_the_window_is_rejected(consumed, limiter):
    """5/min per token-family. Driven on the failure path so no rotation
    intervenes — a bad token is exactly the flood shape worth bounding."""
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)

    assert [_post("junk").status_code for _ in range(5)] == [401] * 5

    sixth = _post("junk")
    assert sixth.status_code == 429
    assert sixth.json()["detail"] == "Too many refresh attempts; retry shortly"


def test_the_429_advertises_how_long_to_wait(consumed, limiter):
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)
    _, clock = limiter
    for _ in range(5):
        _post("junk")

    clock.advance(20.0)

    assert _post("junk").headers["retry-after"] == "40"


def test_the_window_reopens(consumed, limiter):
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)
    _, clock = limiter
    for _ in range(5):
        _post("junk")
    assert _post("junk").status_code == 429

    clock.advance(60.0)

    assert _post("junk").status_code == 401


def test_the_429_is_no_store(consumed, limiter):
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)
    for _ in range(5):
        _post("junk")

    assert _post("junk").headers["cache-control"] == "no-store"


def test_one_familys_flood_does_not_shed_another_familys_traffic(consumed, limiter):
    """The reason the key is the credential and not the client IP: carrier
    NAT and corporate egress share one address across unrelated users."""
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)
    for _ in range(6):
        _post("noisy-device")

    assert _post("quiet-device").status_code == 401  # rejected on merit, not throttled


def test_the_family_budget_follows_the_rotated_token(consumed, limiter):
    """Hard rotation hands the client a new credential on every success,
    so a limiter keyed on the credential is only meaningful if the window
    is carried forward. Without the hand-off this loop never trips."""
    seen, box = consumed
    presented = "chain-0"
    for i in range(1, 6):
        box["result"] = _ok(rotated=f"chain-{i}")
        assert _post(presented).status_code == 200
        presented = f"chain-{i}"

    box["result"] = _ok(rotated="chain-6")
    assert _post(presented).status_code == 429
    # The sixth call was shed before storage saw it.
    assert seen == [f"chain-{i}" for i in range(5)]


def test_a_rate_limited_request_never_reaches_storage(consumed, limiter):
    """The property that keeps throttling from manufacturing the breach
    signal: a shed request leaves the presented token live and unrotated,
    so the client's retry is a first use, not a replay."""
    seen, box = consumed
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)
    for _ in range(5):
        _post("still-good")
    seen.clear()

    assert _post("still-good").status_code == 429
    assert seen == []


def test_a_throttled_client_can_still_refresh_once_the_window_reopens(consumed, limiter):
    """End-to-end statement of the anti-self-DoS property: a burst costs
    the client a wait, never its session."""
    seen, box = consumed
    _, clock = limiter
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.EXPIRED_IDLE)
    for _ in range(5):
        _post("live-token")
    assert _post("live-token").status_code == 429

    clock.advance(60.0)
    box["result"] = _ok(rotated="successor")
    resp = _post("live-token")

    # The token survived the throttle intact, so this is an ordinary
    # rotation — not the reuse cascade a post-consumption rejection would
    # have produced.
    assert resp.status_code == 200
    assert resp.json()["refresh_token"] == "successor"


def test_a_429_does_not_clear_the_refresh_cookie(consumed, limiter):
    """Clearing would turn a sub-minute throttle into a full sign-out —
    the exact self-inflicted DoS the per-family scoping avoids."""
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)
    for _ in range(5):
        _with_cookie("web-token").post("/auth/refresh", headers=_CSRF)

    resp = _with_cookie("web-token").post("/auth/refresh", headers=_CSRF)

    assert resp.status_code == 429
    assert "set-cookie" not in {k.lower() for k in resp.headers}


def test_a_request_with_no_token_is_not_bucketed(consumed, limiter):
    """There is no credential to key on, so bucketing these would put
    every tokenless caller in one shared bucket — a global limit by the
    back door."""
    for _ in range(10):
        assert _client.post("/auth/refresh", headers=_CSRF).status_code == 401

    assert len(limiter[0]._buckets) == 0


def test_a_missing_csrf_header_is_not_bucketed(consumed, limiter):
    """Rejected before the body is even read, so nothing is charged."""
    for _ in range(10):
        assert _client.post("/auth/refresh", json={"refresh_token": "t"}).status_code == 403

    assert len(limiter[0]._buckets) == 0


def test_the_limit_is_configurable_off(consumed, monkeypatch):
    """``CHANNEL_REFRESH_RATE_LIMIT=0`` is the operator's escape hatch if
    the ceiling turns out to be wrong in production.

    Driven through the real env → :func:`_build_refresh_limiter` wiring
    rather than by handing the route a pre-built ``limit=0`` limiter, so
    the escape hatch is exercised the way an operator actually reaches
    for it.
    """
    monkeypatch.setenv("CHANNEL_REFRESH_RATE_LIMIT", "0")
    monkeypatch.setattr(refresh_module, "_refresh_limiter", refresh_module._build_refresh_limiter())
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)

    assert [_post("junk").status_code for _ in range(20)] == [401] * 20


def test_the_limit_and_window_are_configurable_from_the_environment(consumed, monkeypatch):
    monkeypatch.setenv("CHANNEL_REFRESH_RATE_LIMIT", "2")
    monkeypatch.setenv("CHANNEL_REFRESH_RATE_LIMIT_WINDOW_SECONDS", "120")
    monkeypatch.setattr(refresh_module, "_refresh_limiter", refresh_module._build_refresh_limiter())
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)

    assert [_post("junk").status_code for _ in range(3)] == [401, 401, 429]
    assert _post("junk").headers["retry-after"] == "120"


def test_a_malformed_limit_falls_back_instead_of_crashing_the_lambda(monkeypatch):
    """The parse runs at import, so raising here would turn a typo in the
    rate-limit knob into a total outage rather than a wrong ceiling."""
    monkeypatch.setenv("CHANNEL_REFRESH_RATE_LIMIT", "five")
    monkeypatch.setenv("CHANNEL_REFRESH_RATE_LIMIT_WINDOW_SECONDS", "a while")

    built = refresh_module._build_refresh_limiter()

    assert built.enabled is True
    assert built._limit == 5
    assert built._window == 60.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 7), ("11", 11), ("nonsense", 7), ("", 7), ("9" * 400, 7)],
)
def test_env_number_parses_or_falls_back(monkeypatch, raw, expected):
    """The 400-digit case parses as an ``int`` but overflows the finite
    check — caught as malformed rather than raised out of an import."""
    if raw is None:
        monkeypatch.delenv("CHANNEL_TEST_KNOB", raising=False)
    else:
        monkeypatch.setenv("CHANNEL_TEST_KNOB", raw)

    assert refresh_module._env_number("CHANNEL_TEST_KNOB", 7, int) == expected


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "1e999"])
def test_env_number_rejects_values_that_parse_but_are_not_finite(monkeypatch, raw):
    """``float("nan")`` / ``float("inf")`` are legal ``float`` calls, so a
    bare ``except ValueError`` would let them through — and they only
    detonate later, in the limiter's ``math.ceil``. Rejecting them here
    keeps a misconfigured knob from turning every throttled refresh into
    a 500."""
    monkeypatch.setenv("CHANNEL_TEST_KNOB", raw)

    assert refresh_module._env_number("CHANNEL_TEST_KNOB", 60.0, float) == 60.0


@pytest.mark.parametrize("raw", ["0", "-10", "0.0", "0.0001", "1e-9", "0.999"])
def test_a_sub_second_window_is_corrected_rather_than_honoured(consumed, monkeypatch, raw):
    """Such a window rolls between consecutive calls, so the limiter would
    admit everything while still reporting ``enabled`` and leaving
    ``RefreshRateLimited`` flat — a silently-off damper indistinguishable
    from "no abuse". Disabling is legible via the limit, not the window.

    Parametrised past ``0`` deliberately: ``0.0001`` has the same shape
    and is the likelier operator slip (unit confusion), so a bare
    ``> 0`` guard would leave the footgun loaded."""
    monkeypatch.setenv("CHANNEL_REFRESH_RATE_LIMIT", "1")
    monkeypatch.setenv("CHANNEL_REFRESH_RATE_LIMIT_WINDOW_SECONDS", raw)
    monkeypatch.setattr(refresh_module, "_refresh_limiter", refresh_module._build_refresh_limiter())
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)

    assert _post("junk").status_code == 401
    throttled = _post("junk")
    assert throttled.status_code == 429
    assert throttled.headers["retry-after"] == "60"


@pytest.mark.parametrize("raw", ["nan", "inf"])
def test_a_non_finite_window_never_reaches_the_limiter(consumed, monkeypatch, raw):
    """End-to-end statement of the above: the throttled path still
    answers 429 rather than 500."""
    monkeypatch.setenv("CHANNEL_REFRESH_RATE_LIMIT", "1")
    monkeypatch.setenv("CHANNEL_REFRESH_RATE_LIMIT_WINDOW_SECONDS", raw)
    monkeypatch.setattr(refresh_module, "_refresh_limiter", refresh_module._build_refresh_limiter())
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)

    assert _post("junk").status_code == 401
    throttled = _post("junk")
    assert throttled.status_code == 429
    assert throttled.headers["retry-after"] == "60"


def test_the_bucket_key_is_neither_the_token_nor_its_storage_digest(limiter, consumed):
    """The in-memory key must be inert: not a credential, and not the
    ``PK=REFRESH#{sha256}`` row identifier."""
    import hashlib

    _post("secret-token")

    keys = set(limiter[0]._buckets)
    assert "secret-token" not in keys
    assert hashlib.sha256(b"secret-token").hexdigest() not in keys
    assert len(keys) == 1


# ----------------------------------------------------------------
# EMF counters (#294)
# ----------------------------------------------------------------


def test_a_successful_refresh_counts_a_success(consumed, counters):
    _post("t")
    assert _outcomes(counters) == [(True, None)]


def test_a_missing_csrf_header_counts_a_failure(consumed, counters):
    _client.post("/auth/refresh", json={"refresh_token": "t"})
    assert _outcomes(counters) == [(False, "csrf_missing")]


def test_a_tokenless_request_counts_a_failure(consumed, counters):
    _client.post("/auth/refresh", headers=_CSRF)
    assert _outcomes(counters) == [(False, "no_token")]


def test_a_rate_limited_request_counts_a_failure_tagged_rate_limited(consumed, counters, limiter):
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)
    for _ in range(5):
        _post("junk")
    counters.reset_mock()

    _post("junk")

    assert _outcomes(counters) == [(False, "rate_limited")]


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
def test_each_rejected_outcome_counts_a_failure_carrying_its_reason(consumed, counters, outcome):
    """The reason is a branch selector, never a dimension — the endpoint
    forwards the full taxonomy and ``metrics.py`` decides which of them
    earn a subset counter."""
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=outcome)

    _post("t")

    assert _outcomes(counters) == [(False, outcome.value)]


def test_reuse_detection_reaches_the_counter_as_its_own_reason(consumed, counters):
    """``RefreshReuseDetected`` is the one counter here worth alarming
    on, so it must not arrive indistinguishable from a routine expiry."""
    box = consumed[1]
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.REUSED, revoked_count=3)

    _post("t")

    assert _outcomes(counters) == [(False, "reused")]


def test_every_request_records_exactly_one_outcome(consumed, counters, limiter):
    """The invariant that makes ``RefreshFailure / (RefreshSuccess +
    RefreshFailure)`` a real ratio rather than a count against an unknown
    denominator."""
    box = consumed[1]
    _client.post("/auth/refresh", json={"refresh_token": "t"})  # 403
    _client.post("/auth/refresh", headers=_CSRF)  # 401, no token
    _post("t")  # 200
    box["result"] = RefreshConsumeResult(outcome=RefreshConsumeOutcome.REVOKED)
    _post("dead")  # 401

    assert counters.await_count == 4
