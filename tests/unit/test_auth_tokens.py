# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for JWT token helpers."""

import os

import pytest
from jose import JWTError

os.environ.setdefault("STARTER_JWT_SECRET", "test-secret-for-unit-tests")

from channel.auth.tokens import (  # noqa: E402
    Token,
    decode_jwt,
    decode_mgmt_jwt,
    issue_jwt,
    issue_mgmt_jwt,
    make_bearer_token,
)


def test_make_bearer_token_is_valid():
    token = make_bearer_token("client-1", "read write")
    assert token.is_valid
    assert token.client_id == "client-1"
    assert token.scope == "read write"


def test_issue_and_decode_jwt_roundtrip():
    token = make_bearer_token("client-abc", "read")
    encoded = issue_jwt(token)
    claims = decode_jwt(encoded)
    assert claims["sub"] == "client-abc"
    assert claims["jti"] == token.jti


def test_decode_jwt_raises_on_tampered_token():
    token = make_bearer_token("x", "read")
    encoded = issue_jwt(token)
    tampered = encoded[:-4] + "XXXX"
    with pytest.raises(JWTError):
        decode_jwt(tampered)


def test_issue_mgmt_jwt_roundtrip():
    user = {"user_id": "u1", "email": "a@b.com", "display_name": "Alice", "role": "admin"}
    encoded = issue_mgmt_jwt(user)
    claims = decode_mgmt_jwt(encoded)
    assert claims["email"] == "a@b.com"
    assert claims["role"] == "admin"
    assert claims["typ"] == "mgmt"


def test_decode_mgmt_jwt_rejects_bearer_token():
    token = make_bearer_token("client-1", "read")
    encoded = issue_jwt(token)
    with pytest.raises(JWTError):
        decode_mgmt_jwt(encoded)


def test_issue_mgmt_jwt_includes_jti_claim():
    """Every mgmt JWT carries a unique 32-char hex jti so it can be revoked (#240)."""
    user = {"user_id": "u1", "email": "a@b.com", "display_name": "Alice", "role": "user"}
    claims = decode_mgmt_jwt(issue_mgmt_jwt(user))
    jti = claims["jti"]
    assert len(jti) == 32
    assert all(c in "0123456789abcdef" for c in jti)


def test_issue_mgmt_jwt_jti_is_unique_per_token():
    """Two tokens for the same user get distinct jtis (revoking one leaves the other)."""
    user = {"user_id": "u1", "email": "a@b.com", "display_name": "Alice", "role": "user"}
    a = decode_mgmt_jwt(issue_mgmt_jwt(user))["jti"]
    b = decode_mgmt_jwt(issue_mgmt_jwt(user))["jti"]
    assert a != b


def test_issue_mgmt_jwt_ttl_is_30_days():
    """TTL bumped from 8h to 30 days for Slack-like multi-week sessions (#240)."""
    user = {"user_id": "u1", "email": "a@b.com", "display_name": "Alice", "role": "user"}
    claims = decode_mgmt_jwt(issue_mgmt_jwt(user))
    assert claims["exp"] - claims["iat"] == 2_592_000


def test_token_dataclass_is_valid():
    token = make_bearer_token("c", "read", ttl_seconds=60)
    assert isinstance(token, Token)
    assert token.is_valid


def test_token_dataclass_expired():
    token = make_bearer_token("c", "read", ttl_seconds=-1)
    assert not token.is_valid


def test_origin_verify_secret_from_env(monkeypatch):
    from channel.auth import tokens

    tokens._origin_verify_secret.cache_clear()
    monkeypatch.setenv("STARTER_ORIGIN_VERIFY_SECRET", "my-verify-secret")
    result = tokens._origin_verify_secret()
    assert result == "my-verify-secret"
    tokens._origin_verify_secret.cache_clear()
