# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for logout revoking *both* credentials (#292, epic #241).

The unit suite proves logout calls the right storage helpers with the
right token, mocking the boundary. What it cannot prove is the property
that actually matters to a user pressing "sign out": that afterwards
**neither** credential still works. That needs the real composition —
a refresh row minted by the Google callback, a ``DENY`` row written by
logout, and both read back through the real endpoints against a real
DynamoDB (``FakeTable`` evaluates neither ``ConditionExpression`` nor
``IndexName``, so the family-revoke query is meaningless there).

The pre-#292 shape of this bug is what the suite pins: denying the
access JTI alone left a 30-day refresh token live, so "log out" on a
shared browser was cosmetic — the next visitor could trade the surviving
cookie straight back for a fresh access token.

Google is the only mocked boundary; the OAuth *state* round trip runs
through DynamoDB Local like everything else. Each test uses a unique
email; the table is not cleaned between tests.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from channel import storage
from channel.api.main import app
from channel.auth.google import _google_client_id
from channel.auth.refresh import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH

_CSRF = {"X-Channel-Refresh": "1"}


@pytest.fixture(autouse=True)
def _google(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for Google's token endpoint, client id, and allowlist lookup.

    Every one of these otherwise falls back to an SSM read against the
    fake local credentials — a slow failure, and DynamoDB Local is the
    only AWS boundary this suite means to exercise. ``GOOGLE_CLIENT_ID``
    is memoised behind an ``lru_cache``, so the cache is cleared on both
    sides of the test rather than trusting ``monkeypatch`` alone to undo
    a value that was captured, not read.
    """

    async def _exchange(*_args: Any, **_kwargs: Any) -> str:
        return "fake-id-token"

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "integration-test-google-client-id")
    _google_client_id.cache_clear()
    monkeypatch.setattr("channel.auth.mgmt_auth.exchange_google_code", _exchange)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_email_allowed", lambda _e: True)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda _e: False)
    yield
    _google_client_id.cache_clear()


def _browser(**kwargs: Any) -> TestClient:
    """A TestClient that will actually round-trip the refresh cookie.

    ``base_url`` is **https** on purpose. The cookie is minted with
    ``Secure``, and httpx's jar — correctly — refuses to send a Secure
    cookie back over plain http, so against the default
    ``http://testserver`` every request here would silently arrive
    cookie-less and the suite would "pass" while proving nothing. Real
    clients never hit that: production is https, and browsers treat
    ``http://localhost`` as a secure context for cookie purposes.
    """

    return TestClient(app, base_url="https://testserver", **kwargs)


@pytest.fixture
def client(starter_table) -> TestClient:  # type: ignore[no-untyped-def]
    """TestClient bound to the provisioned DynamoDB Local table."""
    return _browser(follow_redirects=False)


def _user() -> str:
    return f"itest-logout-{uuid.uuid4().hex[:12]}@example.com"


def _login(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    email: str,
    *,
    name: str = "Ada Lovelace",
) -> str:
    """Drive a full /auth/login → /auth/callback round trip; return the access token.

    The refresh cookie lands in ``client``'s jar as a side effect, which
    is exactly how a browser would carry it into the logout call.
    """

    async def _verify(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"email": email, "email_verified": True, "name": name}

    monkeypatch.setattr("channel.auth.mgmt_auth.verify_google_id_token", _verify)

    started = client.get("/auth/login")
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    landed = client.get("/auth/callback", params={"code": "authcode", "state": state})
    assert landed.status_code == 200
    # The page hands the access token to the SPA via localStorage; the
    # refresh token never appears in it (HttpOnly cookie only).
    return landed.text.split("setItem('starter_mgmt_token', '")[1].split("'")[0]


def test_login_mints_a_real_refresh_row_and_sets_the_cookie(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """#292's first two acceptance criteria, against real storage."""

    email = _user()
    _login(client, monkeypatch, email)

    raw_cookie = client.cookies.get(REFRESH_COOKIE_NAME)
    assert raw_cookie
    row = storage._get_refresh_row(storage._refresh_token_hash(raw_cookie))
    assert row is not None
    assert row.user_id == email
    assert row.revoked is False
    # Bound to a server-side opaque device id, not anything client-supplied.
    assert row.device_id and email not in row.device_id
    # And carrying the name only the callback ever sees.
    assert row.display_name == "Ada Lovelace"


def test_the_minted_cookie_is_immediately_usable_for_refresh(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Mint and rotate must agree on the cookie's attributes or web login is stuck."""

    _login(client, monkeypatch, _user())

    resp = client.post("/auth/refresh", headers=_CSRF)

    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_logout_revokes_the_refresh_token_so_refresh_stops_working(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The hole this issue closes: logout must not leave a live 30-day credential."""

    access_token = _login(client, monkeypatch, _user())
    raw_cookie = client.cookies.get(REFRESH_COOKIE_NAME)

    logout = client.post("/auth/logout", headers={"Authorization": f"Bearer {access_token}"})
    assert logout.status_code == 204

    # Server-side: the row is dead, whatever the browser still holds.
    row = storage._get_refresh_row(storage._refresh_token_hash(raw_cookie))
    assert row is not None
    assert row.revoked is True

    # And the endpoint agrees — replaying the exfiltrated copy gets nothing.
    replay = _browser()
    replay.cookies.set(REFRESH_COOKIE_NAME, raw_cookie)
    assert replay.post("/auth/refresh", headers=_CSRF).status_code == 401


def test_logout_clears_the_browsers_copy_of_the_cookie(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Cosmetic next to the server-side revoke, but still worth doing."""

    access_token = _login(client, monkeypatch, _user())

    logout = client.post("/auth/logout", headers={"Authorization": f"Bearer {access_token}"})

    set_cookie = logout.headers["set-cookie"]
    assert f"{REFRESH_COOKIE_NAME}=" in set_cookie
    assert f"Path={REFRESH_COOKIE_PATH}" in set_cookie
    assert "Max-Age=0" in set_cookie


def test_logout_denies_the_access_jti_as_well(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Both credentials, not either — #240's denylist still does its half."""

    access_token = _login(client, monkeypatch, _user())

    client.post("/auth/logout", headers={"Authorization": f"Bearer {access_token}"})

    reused = client.get("/api/models", headers={"Authorization": f"Bearer {access_token}"})
    assert reused.status_code == 401


def test_logout_kills_the_whole_family_including_a_rotated_successor(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Revoking one row would leave the successor a rotation just minted alive."""

    access_token = _login(client, monkeypatch, _user())
    # Rotate once, so the family holds a revoked ancestor plus a live successor.
    assert client.post("/auth/refresh", headers=_CSRF).status_code == 200
    successor = client.cookies.get(REFRESH_COOKIE_NAME)

    client.post("/auth/logout", headers={"Authorization": f"Bearer {access_token}"})

    row = storage._get_refresh_row(storage._refresh_token_hash(successor))
    assert row is not None
    assert row.revoked is True


def test_logout_accepts_the_refresh_token_in_the_body_for_desktop(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Electron keeps its token in the OS keychain (#297), not a cookie jar."""

    email = _user()
    raw, _ = storage.mint_refresh_token(user_id=email, device_id="d-desktop")
    access_token = _login(client, monkeypatch, email)
    # Drop the cookie the login left behind so only the body is presented.
    client.cookies.delete(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)

    resp = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"refresh_token": raw},
    )

    assert resp.status_code == 204
    row = storage._get_refresh_row(storage._refresh_token_hash(raw))
    assert row is not None
    assert row.revoked is True


def test_logout_without_any_refresh_token_still_succeeds(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A session predating #292 has nothing to revoke — migration, not failure."""

    access_token = _login(client, monkeypatch, _user())
    client.cookies.delete(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)

    resp = client.post("/auth/logout", headers={"Authorization": f"Bearer {access_token}"})

    assert resp.status_code == 204
