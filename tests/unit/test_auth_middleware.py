# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for FastAPI auth middleware dependencies."""

import os

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("CHANNEL_JWT_SECRET", "test-secret-for-unit-tests")

from channel.api._auth import require_admin, require_mgmt_user  # noqa: E402
from channel.auth.tokens import issue_mgmt_jwt  # noqa: E402


def _admin_token() -> str:
    return issue_mgmt_jwt(
        {"user_id": "u1", "email": "admin@test.com", "display_name": "Admin", "role": "admin"}
    )


def _user_token() -> str:
    return issue_mgmt_jwt(
        {"user_id": "u2", "email": "user@test.com", "display_name": "User", "role": "user"}
    )


# Minimal test app wiring the dependencies
_app = FastAPI()


@_app.get("/me")
def me(claims: dict = Depends(require_mgmt_user)):
    return claims


@_app.get("/admin-only")
def admin_only(claims: dict = Depends(require_admin)):
    return claims


_client = TestClient(_app)


def test_require_mgmt_user_valid_token():
    token = _admin_token()
    resp = _client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@test.com"


def test_require_mgmt_user_missing_token():
    resp = _client.get("/me")
    assert resp.status_code in (401, 403)


def test_require_mgmt_user_invalid_token():
    resp = _client.get("/me", headers={"Authorization": "Bearer totally.invalid.jwt"})
    assert resp.status_code == 401


def test_require_admin_grants_access_to_admin():
    token = _admin_token()
    resp = _client.get("/admin-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_require_admin_rejects_non_admin():
    token = _user_token()
    resp = _client.get("/admin-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_require_mgmt_user_rejects_revoked_jti(monkeypatch):
    """A token whose jti was revoked at logout is rejected with 401 (#240).

    The denylist read itself lives in ``decode_mgmt_jwt`` since #291, so
    the seam is patched there; what this test pins is the *dependency's*
    contract — revocation surfaces as a 401, not a 500 or a pass.
    """
    monkeypatch.setattr("channel.auth.tokens.is_jti_denied", lambda _jti: True)
    resp = _client.get("/me", headers={"Authorization": f"Bearer {_user_token()}"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token revoked"


def test_require_mgmt_user_propagates_denylist_read_failure(monkeypatch):
    """A denylist read error is not a JWTError, so it must not become a 401 (#291).

    Failing open (or degrading to "invalid token") would make revocation
    bypassable by inducing DynamoDB errors; the storage failure surfaces
    like any other DDB-backed endpoint's would.
    """

    def _boom(_jti: str) -> bool:
        raise RuntimeError("dynamodb unavailable")

    monkeypatch.setattr("channel.auth.tokens.is_jti_denied", _boom)
    with pytest.raises(RuntimeError, match="dynamodb unavailable"):
        _client.get("/me", headers={"Authorization": f"Bearer {_user_token()}"})
