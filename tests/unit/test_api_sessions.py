# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api/me/sessions router (#293, epic #241).

The storage seam is faked with a user-keyed in-memory store rather than
a blanket stub, because the property this suite most needs to prove —
that another user's ``device_id`` is a 404 and not a 403 — is only
meaningful if the fake actually scopes rows by ``user_id`` the way
``RefreshByUserIndex`` does. A stub that ignored its ``user_id``
argument would let a broken route pass.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jose import jwt

# Set before importing the app, exactly as the other token-using suites do.
# ``_jwt_secret`` is ``lru_cache``d, so a per-test ``monkeypatch.setenv``
# would populate that cache with a value that outlives its own teardown.
os.environ.setdefault("CHANNEL_JWT_SECRET", "test-secret-for-unit-tests")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")

from channel.api._auth import require_mgmt_user  # noqa: E402
from channel.api.main import app  # noqa: E402
from channel.auth.tokens import issue_mgmt_jwt  # noqa: E402
from channel.models import (  # noqa: E402
    REFRESH_ABSOLUTE_LIFETIME_SECONDS,
    REFRESH_IDLE_TIMEOUT_SECONDS,
    RefreshToken,
)

_AUTH = {"Authorization": "Bearer x"}


def _iso(delta_seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat(
        timespec="microseconds"
    )


def _token(user_id: str, device_id: str, *, age_seconds: float = 0.0) -> RefreshToken:
    """A live refresh row issued ``age_seconds`` ago."""

    issued = _iso(-age_seconds)
    return RefreshToken(
        token_hash=f"hash-{user_id}-{device_id}-{age_seconds}",
        user_id=user_id,
        device_id=device_id,
        issued_at=issued,
        last_used_at=issued,
        absolute_expires_at=_iso(REFRESH_ABSOLUTE_LIFETIME_SECONDS - age_seconds),
        idle_expires_at=_iso(REFRESH_IDLE_TIMEOUT_SECONDS),
    )


class _Store:
    """Stands in for the storage helpers the router calls.

    Refresh rows are held per ``user_id`` and every helper reads only its
    own caller's bucket — the fake's whole job is to reproduce the
    tenancy scoping of ``GSI5PK=REFRESH_USER#{user_id}``. ``deny_jti``
    and ``put_audit_event`` are captured rather than modelled; the
    assertions are about *whether and with what* they were called.
    """

    def __init__(self) -> None:
        self.rows: dict[str, list[RefreshToken]] = {}
        self.revoked_calls: list[tuple[str, str | None]] = []
        self.denied_jtis: list[tuple[str, int]] = []
        self.audit_events: list[dict[str, Any]] = []
        self.audit_raises = False

    def seed(self, *tokens: RefreshToken) -> None:
        for token in tokens:
            self.rows.setdefault(token.user_id, []).append(token)

    # --- the patched storage surface ---------------------------------

    def list_live_refresh_tokens(self, user_id: str) -> list[RefreshToken]:
        # Real helper returns newest-first; mirror that contract.
        return sorted(
            self.rows.get(user_id, []),
            key=lambda row: (row.issued_at, row.token_hash),
            reverse=True,
        )

    def revoke_device_refresh_tokens(self, user_id: str, device_id: str) -> int:
        self.revoked_calls.append((user_id, device_id))
        return self._drop(user_id, lambda row: row.device_id == device_id)

    def revoke_all_user_refresh_tokens(self, user_id: str) -> int:
        self.revoked_calls.append((user_id, None))
        return self._drop(user_id, lambda _row: True)

    def deny_jti(self, jti: str, exp: int) -> None:
        self.denied_jtis.append((jti, exp))

    def put_audit_event(
        self, *, event_type: str, actor_id: str, details: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.audit_raises:
            raise RuntimeError("audit table unavailable")
        event = {"event_type": event_type, "actor_id": actor_id, "details": details or {}}
        self.audit_events.append(event)
        return event

    def _drop(self, user_id: str, predicate: Callable[[RefreshToken], bool]) -> int:
        kept = [row for row in self.rows.get(user_id, []) if not predicate(row)]
        dropped = len(self.rows.get(user_id, [])) - len(kept)
        self.rows[user_id] = kept
        return dropped


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    fake = _Store()
    for name in (
        "list_live_refresh_tokens",
        "revoke_device_refresh_tokens",
        "revoke_all_user_refresh_tokens",
        "deny_jti",
        "put_audit_event",
    ):
        monkeypatch.setattr(f"channel.api.sessions.storage.{name}", getattr(fake, name))
    return fake


@pytest.fixture
def as_user() -> Iterator[Callable[..., TestClient]]:
    """Build a ``TestClient`` whose mgmt JWT resolves to ``sub``."""

    def _make(sub: str = "u-1", *, jti: str | None = None, exp: int = 1_900_000_000) -> TestClient:
        def _stub_user() -> dict[str, Any]:
            claims: dict[str, Any] = {"sub": sub, "role": "user"}
            if jti is not None:
                claims |= {"jti": jti, "exp": exp}
            return claims

        app.dependency_overrides[require_mgmt_user] = _stub_user
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


# ----------------------------------------------------------------
# GET /api/me/sessions
# ----------------------------------------------------------------


def test_list_sessions_returns_one_entry_per_device_newest_first(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    store.seed(
        _token("u-1", "laptop", age_seconds=600),
        _token("u-1", "phone", age_seconds=5),
    )

    r = as_user("u-1").get("/api/me/sessions", headers=_AUTH)

    assert r.status_code == 200
    sessions = r.json()["sessions"]
    assert [s["device_id"] for s in sessions] == ["phone", "laptop"]
    assert set(sessions[0]) == {
        "device_id",
        "issued_at",
        "last_used_at",
        "absolute_expires_at",
        "idle_expires_at",
    }


def test_list_sessions_never_exposes_the_token_hash(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    """The digest is not a usable credential, but it is a stable
    per-token correlation handle and has no business in a response."""
    store.seed(_token("u-1", "laptop"))

    r = as_user("u-1").get("/api/me/sessions", headers=_AUTH)

    assert "hash-" not in r.text
    assert "token_hash" not in r.text


def test_list_sessions_collapses_a_mid_rotation_device_to_its_newest_row(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    """A refresh racing the read can leave two live rows for one device;
    the session list must still show one device, at its newest row."""
    store.seed(
        _token("u-1", "laptop", age_seconds=900),
        _token("u-1", "laptop", age_seconds=1),
    )

    sessions = as_user("u-1").get("/api/me/sessions", headers=_AUTH).json()["sessions"]

    assert len(sessions) == 1
    assert sessions[0]["issued_at"] == store.rows["u-1"][1].issued_at


def test_list_sessions_is_empty_for_a_user_with_no_refresh_rows(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    r = as_user("u-1").get("/api/me/sessions", headers=_AUTH)

    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_list_sessions_only_returns_the_callers_own_devices(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    store.seed(_token("u-1", "laptop"), _token("u-2", "intruder-device"))

    sessions = as_user("u-2").get("/api/me/sessions", headers=_AUTH).json()["sessions"]

    assert [s["device_id"] for s in sessions] == ["intruder-device"]


def test_list_sessions_sets_no_store_cache_header(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    """Without it Chromium heuristic-caches the response and a device the
    user just signed out of keeps showing up in the list."""
    r = as_user("u-1").get("/api/me/sessions", headers=_AUTH)

    assert r.headers.get("cache-control") == "no-store"


def test_list_sessions_requires_auth(store: _Store) -> None:
    # No dependency override — the request is unauthenticated. HTTPBearer
    # answers a missing Authorization header with 401 on some versions and
    # 403 on others; either is "rejected for missing auth".
    assert TestClient(app).get("/api/me/sessions").status_code in (401, 403)


# ----------------------------------------------------------------
# DELETE /api/me/sessions/{device_id}
# ----------------------------------------------------------------


def test_revoke_one_device_revokes_only_that_device(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    store.seed(_token("u-1", "laptop"), _token("u-1", "phone"))
    client = as_user("u-1")

    r = client.delete("/api/me/sessions/laptop", headers=_AUTH)

    assert r.status_code == 204
    assert store.revoked_calls == [("u-1", "laptop")]
    remaining = client.get("/api/me/sessions", headers=_AUTH).json()["sessions"]
    assert [s["device_id"] for s in remaining] == ["phone"]


def test_revoke_another_users_device_is_404_and_revokes_nothing(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    """The ownership boundary: 404 rather than 403, so the endpoint can't
    be used to confirm that another user's device id exists."""
    store.seed(_token("u-1", "victim-laptop"))

    r = as_user("u-2").delete("/api/me/sessions/victim-laptop", headers=_AUTH)

    assert r.status_code == 404
    assert r.json()["detail"] == "Session not found"
    assert store.revoked_calls == []
    assert [row.device_id for row in store.rows["u-1"]] == ["victim-laptop"]


def test_revoke_unknown_device_is_404(store: _Store, as_user: Callable[..., TestClient]) -> None:
    store.seed(_token("u-1", "laptop"))

    r = as_user("u-1").delete("/api/me/sessions/never-existed", headers=_AUTH)

    assert r.status_code == 404
    assert store.revoked_calls == []


def test_revoking_the_same_device_twice_is_404_the_second_time(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    """Once the family is dead the device has no session left to end, so
    the repeat delete looks exactly like deleting an unknown device."""
    store.seed(_token("u-1", "laptop"))
    client = as_user("u-1")

    assert client.delete("/api/me/sessions/laptop", headers=_AUTH).status_code == 204
    assert client.delete("/api/me/sessions/laptop", headers=_AUTH).status_code == 404


def test_revoke_one_device_requires_auth(store: _Store) -> None:
    assert TestClient(app).delete("/api/me/sessions/laptop").status_code in (401, 403)


# ----------------------------------------------------------------
# DELETE /api/me/sessions
# ----------------------------------------------------------------


def test_revoke_all_sessions_clears_every_device_for_the_caller(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    store.seed(
        _token("u-1", "laptop"),
        _token("u-1", "phone"),
        _token("u-2", "bystander"),
    )
    client = as_user("u-1")

    r = client.delete("/api/me/sessions", headers=_AUTH)

    assert r.status_code == 204
    assert store.revoked_calls == [("u-1", None)]
    assert client.get("/api/me/sessions", headers=_AUTH).json() == {"sessions": []}
    # Another user's sessions are untouched.
    assert [row.device_id for row in store.rows["u-2"]] == ["bystander"]


def test_revoke_all_sessions_is_idempotent_when_there_are_none(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    r = as_user("u-1").delete("/api/me/sessions", headers=_AUTH)

    assert r.status_code == 204
    assert store.revoked_calls == [("u-1", None)]


def test_revoke_all_sessions_requires_auth(store: _Store) -> None:
    assert TestClient(app).delete("/api/me/sessions").status_code in (401, 403)


# ----------------------------------------------------------------
# Access-token denylist + audit trail
# ----------------------------------------------------------------


def test_revoke_all_sessions_denylists_the_callers_own_access_token(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    """Without this the user who pressed "sign out everywhere" stays
    signed in on the device they pressed it from for the rest of that
    access token's lifetime."""
    store.seed(_token("u-1", "laptop"))

    r = as_user("u-1", jti="jti-abc", exp=1_800_000_000).delete("/api/me/sessions", headers=_AUTH)

    assert r.status_code == 204
    assert store.denied_jtis == [("jti-abc", 1_800_000_000)]


def test_revoke_all_sessions_revokes_refresh_families_before_the_denylist_write(
    store: _Store, as_user: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering is retry-safety: if the denylist write blows up, the
    caller must still hold a working token to retry the whole call."""
    order: list[str] = []
    monkeypatch.setattr(
        "channel.api.sessions.storage.revoke_all_user_refresh_tokens",
        lambda user_id: order.append("revoke") or 0,
    )
    monkeypatch.setattr(
        "channel.api.sessions.storage.deny_jti",
        lambda jti, exp: order.append("deny"),
    )

    as_user("u-1", jti="jti-abc").delete("/api/me/sessions", headers=_AUTH)

    assert order == ["revoke", "deny"]


def test_revoke_all_sessions_surfaces_a_denylist_failure_as_a_server_error(
    store: _Store, as_user: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike the audit write, this one is not best-effort: swallowing it
    would report a successful sign-out while leaving the calling device
    authenticated."""

    def _boom(jti: str, exp: int) -> None:
        raise RuntimeError("denylist table unavailable")

    monkeypatch.setattr("channel.api.sessions.storage.deny_jti", _boom)
    client = as_user("u-1", jti="jti-abc")

    with pytest.raises(RuntimeError):
        client.delete("/api/me/sessions", headers=_AUTH)


def test_revoke_all_sessions_skips_the_denylist_for_a_pre_240_token(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    """Tokens minted before the jti claim existed cannot be denylisted;
    they just expire. Must not blow up."""
    r = as_user("u-1").delete("/api/me/sessions", headers=_AUTH)

    assert r.status_code == 204
    assert store.denied_jtis == []
    assert store.audit_events[0]["details"]["access_token_denied"] is False


def test_revoke_one_device_does_not_denylist_anything(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    """There is no device_id claim on the mgmt JWT, so a per-device
    revoke cannot identify the access token that device holds."""
    store.seed(_token("u-1", "laptop"))

    as_user("u-1", jti="jti-abc").delete("/api/me/sessions/laptop", headers=_AUTH)

    assert store.denied_jtis == []


def test_revoke_one_device_writes_an_audit_event(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    """These routes are the stolen-laptop remediation surface, so the
    intent has to leave a server-side signal like /auth/logout does."""
    store.seed(_token("u-1", "laptop"))

    as_user("u-1").delete("/api/me/sessions/laptop", headers=_AUTH)

    assert store.audit_events == [
        {
            "event_type": "auth.session_revoke",
            "actor_id": "u-1",
            "details": {"role": "user", "device_id": "laptop", "revoked_rows": 1},
        }
    ]


def test_revoke_all_sessions_writes_an_audit_event(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    store.seed(_token("u-1", "laptop"), _token("u-1", "phone"))

    as_user("u-1", jti="jti-abc").delete("/api/me/sessions", headers=_AUTH)

    assert store.audit_events == [
        {
            "event_type": "auth.session_revoke_all",
            "actor_id": "u-1",
            "details": {
                "role": "user",
                "revoked_rows": 2,
                "access_token_denied": True,
                # Pairs the row with the DENY#{jti} row it caused, the
                # same correlation handle /auth/logout records.
                "jti": "jti-abc",
            },
        }
    ]


def test_a_failed_denylist_write_still_leaves_an_audit_row(
    store: _Store, as_user: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refresh families are already gone by the time the deny runs, so the
    half-applied state is exactly the one the audit trail must show —
    honestly, as access_token_denied: false."""

    def _boom(jti: str, exp: int) -> None:
        raise RuntimeError("denylist table unavailable")

    monkeypatch.setattr("channel.api.sessions.storage.deny_jti", _boom)
    store.seed(_token("u-1", "laptop"))
    client = as_user("u-1", jti="jti-abc")

    with pytest.raises(RuntimeError):
        client.delete("/api/me/sessions", headers=_AUTH)

    assert store.audit_events == [
        {
            "event_type": "auth.session_revoke_all",
            "actor_id": "u-1",
            "details": {
                "role": "user",
                "revoked_rows": 1,
                "access_token_denied": False,
                "jti": "jti-abc",
            },
        }
    ]


def test_a_failed_audit_write_does_not_fail_the_revocation(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    """The revoke has already been applied by then — failing the response
    would tell the user the sign-out did not happen when it did."""
    store.seed(_token("u-1", "laptop"))
    store.audit_raises = True

    r = as_user("u-1").delete("/api/me/sessions/laptop", headers=_AUTH)

    assert r.status_code == 204
    assert store.revoked_calls == [("u-1", "laptop")]


def test_a_404_revoke_writes_no_audit_event(
    store: _Store, as_user: Callable[..., TestClient]
) -> None:
    """Nothing happened, so nothing is recorded — the audit trail must
    not fill with probes for device ids that were never the caller's."""
    store.seed(_token("u-1", "laptop"))

    assert as_user("u-2").delete("/api/me/sessions/laptop", headers=_AUTH).status_code == 404
    assert store.audit_events == []


# ----------------------------------------------------------------
# The ownership boundary through the real auth path
# ----------------------------------------------------------------
#
# Every test above overrides ``require_mgmt_user`` (as the five sibling
# /api suites do) to vary ``sub`` cheaply. The 404-not-403 rule is the
# security property of this router, though, so it also gets proven end
# to end through the real validator with real signed tokens — no
# dependency override — per the ``fastapi-route`` skill's "don't mock
# auth; mock the AWS boundary" rule.


def _real_bearer(email: str) -> dict[str, str]:
    """A real signed mgmt JWT for ``email``, as the login flow would mint."""

    token = issue_mgmt_jwt(
        {
            "user_id": email,
            "email": email,
            "display_name": email.split("@")[0],
            "role": "user",
        }
    )
    return {"Authorization": f"Bearer {token}"}


def test_cross_user_revoke_is_404_with_a_real_signed_token(store: _Store) -> None:
    store.seed(_token("victim@example.com", "victim-laptop"))

    r = TestClient(app).delete(
        "/api/me/sessions/victim-laptop", headers=_real_bearer("attacker@example.com")
    )

    assert r.status_code == 404
    assert store.revoked_calls == []
    assert [row.device_id for row in store.rows["victim@example.com"]] == ["victim-laptop"]


def test_a_real_token_only_lists_its_own_subjects_sessions(store: _Store) -> None:
    store.seed(
        _token("victim@example.com", "victim-laptop"),
        _token("attacker@example.com", "attacker-phone"),
    )

    body = (
        TestClient(app).get("/api/me/sessions", headers=_real_bearer("attacker@example.com")).json()
    )

    assert [s["device_id"] for s in body["sessions"]] == ["attacker-phone"]


def test_a_real_token_denylists_its_own_jti_on_sign_out_everywhere(store: _Store) -> None:
    """End-to-end through the real validator: the jti that gets denylisted
    is the one the issuer actually baked into the caller's token."""
    store.seed(_token("user@example.com", "laptop"))
    headers = _real_bearer("user@example.com")

    r = TestClient(app).delete("/api/me/sessions", headers=headers)

    assert r.status_code == 204
    assert len(store.denied_jtis) == 1
    denied_jti, denied_exp = store.denied_jtis[0]
    claims = jwt.get_unverified_claims(headers["Authorization"].removeprefix("Bearer "))
    assert denied_jti == claims["jti"]
    assert denied_exp == claims["exp"]
    assert store.audit_events[0]["details"]["jti"] == claims["jti"]
