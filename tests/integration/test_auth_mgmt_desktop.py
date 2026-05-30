# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration test for the desktop OAuth callback redirect, against DynamoDB Local."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from channel.api.main import app
from channel.auth import state_store


@pytest.fixture
def state_record(starter_table):
    """Put a real state record into DynamoDB Local with desktop_callback set."""
    state = "D" * 43
    state_store.put_state(
        state, payload={"desktop_callback": "http://127.0.0.1:50000/callback"}, ttl_seconds=600
    )
    return state


def test_callback_redirects_to_loopback_against_real_dynamo(state_record, monkeypatch):
    """End-to-end: state record in DynamoDB Local + stubbed Google → loopback redirect."""

    async def fake_exchange(_code, _redirect):
        return "id_token"

    async def fake_verify(_id_token):
        return {
            "email": "user@example.com",
            "email_verified": True,
            "name": "User",
        }

    monkeypatch.setattr("channel.auth.mgmt_auth.exchange_google_code", fake_exchange)
    monkeypatch.setattr("channel.auth.mgmt_auth.verify_google_id_token", fake_verify)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_email_allowed", lambda e: True)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda e: False)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")

    client = TestClient(app)
    resp = client.get(
        "/auth/callback", params={"code": "c", "state": state_record}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("http://127.0.0.1:50000/callback?")
    assert f"state={state_record}" in resp.headers["location"]
