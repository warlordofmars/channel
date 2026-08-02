# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for ``POST /auth/refresh`` (#291, epic #241).

The unit suite mocks ``consume_refresh_token`` to pin the endpoint's
transport and CSRF contract. What it cannot prove is that the endpoint
and #290's storage layer actually compose: that a token minted by
``mint_refresh_token`` is accepted here, that the successor handed back
over the wire is itself usable, and — the property the whole rotation
design rests on — that replaying a consumed token is rejected *and*
takes the rest of the device family down with it. Those need a real
DynamoDB (``FakeTable`` accepts ``ConditionExpression`` without
evaluating it, and ignores ``IndexName`` entirely).

Each test uses a unique ``user_id``; the table is not cleaned between
tests.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from channel import storage
from channel.api.main import app
from channel.auth.refresh import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH
from channel.auth.tokens import decode_mgmt_jwt
from channel.models import RefreshConsumeOutcome

_CSRF = {"X-Channel-Refresh": "1"}


@pytest.fixture(autouse=True)
def _non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin role resolution so the suite never reaches for the SSM allowlist.

    ``make_mgmt_user`` calls ``is_admin_email``, which falls back to an
    SSM read when ``ALLOWED_EMAILS`` is unset. That read fails closed,
    but only after a network round trip against fake local credentials —
    slow and pointless here. DynamoDB Local is the only AWS boundary
    this suite means to exercise.
    """
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda _email: False)


@pytest.fixture
def client(starter_table) -> TestClient:  # type: ignore[no-untyped-def]
    """TestClient bound to the provisioned DynamoDB Local table."""
    return TestClient(app)


def _user() -> str:
    return f"itest-refresh-api-{uuid.uuid4().hex[:12]}@example.com"


def test_body_transport_rotates_against_real_dynamodb(client) -> None:  # type: ignore[no-untyped-def]
    user_id = _user()
    raw, _ = storage.mint_refresh_token(user_id=user_id, device_id="d-1")

    resp = client.post("/auth/refresh", json={"refresh_token": raw}, headers=_CSRF)

    assert resp.status_code == 200
    payload = resp.json()
    assert decode_mgmt_jwt(payload["access_token"])["sub"] == user_id
    # The successor is a genuinely different token, and the presented one
    # is now dead.
    assert payload["refresh_token"] != raw
    assert storage.consume_refresh_token(raw).outcome is not RefreshConsumeOutcome.OK


def test_the_returned_token_is_itself_refreshable(client) -> None:  # type: ignore[no-untyped-def]
    """Chained refreshes are what make a 1h access token invisible to users."""
    user_id = _user()
    raw, _ = storage.mint_refresh_token(user_id=user_id, device_id="d-1")

    first = client.post("/auth/refresh", json={"refresh_token": raw}, headers=_CSRF)
    second = client.post(
        "/auth/refresh",
        json={"refresh_token": first.json()["refresh_token"]},
        headers=_CSRF,
    )

    assert second.status_code == 200
    assert decode_mgmt_jwt(second.json()["access_token"])["sub"] == user_id


def test_replaying_a_consumed_token_is_rejected_and_kills_the_family(client) -> None:  # type: ignore[no-untyped-def]
    """OAuth 2.1 reuse detection (RFC 9700 §4.14.2), end to end."""
    user_id = _user()
    raw, _ = storage.mint_refresh_token(user_id=user_id, device_id="d-1")

    successor = client.post("/auth/refresh", json={"refresh_token": raw}, headers=_CSRF).json()[
        "refresh_token"
    ]

    replay = client.post("/auth/refresh", json={"refresh_token": raw}, headers=_CSRF)
    assert replay.status_code == 401

    # The replay took the successor down with it: the legitimate holder
    # is forced back through login too, which is the point.
    after = client.post("/auth/refresh", json={"refresh_token": successor}, headers=_CSRF)
    assert after.status_code == 401


def test_cookie_transport_rotates_and_sets_a_scoped_cookie(client) -> None:  # type: ignore[no-untyped-def]
    user_id = _user()
    raw, _ = storage.mint_refresh_token(user_id=user_id, device_id="d-web")
    client.cookies.set(REFRESH_COOKIE_NAME, raw)

    resp = client.post("/auth/refresh", headers=_CSRF)

    assert resp.status_code == 200
    assert "refresh_token" not in resp.json()
    set_cookie = resp.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert f"Path={REFRESH_COOKIE_PATH}" in set_cookie
    assert resp.cookies[REFRESH_COOKIE_NAME] != raw


def test_unknown_token_is_rejected(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post(
        "/auth/refresh", json={"refresh_token": "never-minted-" + uuid.uuid4().hex}, headers=_CSRF
    )
    assert resp.status_code == 401


def test_revoked_family_cannot_refresh(client) -> None:  # type: ignore[no-untyped-def]
    """`revoke_all_user_refresh_tokens` (sign out everywhere) closes the endpoint."""
    user_id = _user()
    raw, _ = storage.mint_refresh_token(user_id=user_id, device_id="d-1")
    assert storage.revoke_all_user_refresh_tokens(user_id) >= 1

    resp = client.post("/auth/refresh", json={"refresh_token": raw}, headers=_CSRF)
    assert resp.status_code == 401
