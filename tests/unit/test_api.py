# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the management API."""

import pytest
from fastapi.testclient import TestClient

from channel.api.main import app

client = TestClient(app)


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_includes_version():
    resp = client.get("/health")
    assert "version" in resp.json()


def test_csp_report_empty_body_returns_204():
    resp = client.post("/api/csp-report", content=b"")
    assert resp.status_code == 204


def test_csp_report_malformed_json_returns_204():
    resp = client.post(
        "/api/csp-report",
        content=b"not-json",
        headers={"Content-Type": "application/csp-report"},
    )
    assert resp.status_code == 204


def test_app_version_from_env(monkeypatch):
    from channel.api import main

    monkeypatch.setenv("APP_VERSION", "9.9.9")
    assert main._app_version() == "9.9.9"


def test_origin_verify_middleware_rejects_missing_header(monkeypatch):
    from channel.auth import tokens

    tokens._origin_verify_secret.cache_clear()
    monkeypatch.setenv("STARTER_ORIGIN_VERIFY_SECRET", "super-secret")
    resp = client.get("/health")
    assert resp.status_code == 403
    tokens._origin_verify_secret.cache_clear()


def test_origin_verify_middleware_accepts_matching_header(monkeypatch):
    from channel.auth import tokens

    tokens._origin_verify_secret.cache_clear()
    monkeypatch.setenv("STARTER_ORIGIN_VERIFY_SECRET", "super-secret")
    resp = client.get("/health", headers={"x-origin-verify": "super-secret"})
    assert resp.status_code == 200
    tokens._origin_verify_secret.cache_clear()


@pytest.mark.asyncio
async def test_csp_report_legacy_format_returns_204():
    import json

    payload = json.dumps(
        {
            "csp-report": {
                "violated-directive": "script-src",
                "blocked-uri": "https://evil.example.com/script.js",
                "document-uri": "https://example.com/",
            }
        }
    ).encode()
    resp = client.post(
        "/api/csp-report",
        content=payload,
        headers={"Content-Type": "application/csp-report"},
    )
    assert resp.status_code == 204
