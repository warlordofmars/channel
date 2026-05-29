# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for CSP violation report helpers."""

import json

from fastapi.testclient import TestClient

from channel.api.csp import _blocked_domain, _extract_legacy, _extract_modern, _truncate
from channel.api.main import app

_client = TestClient(app)

_FIELD_MAX_LEN = 2048


def test_truncate_short_string():
    assert _truncate("hello") == "hello"


def test_truncate_long_string():
    long = "x" * (_FIELD_MAX_LEN + 10)
    result = _truncate(long)
    assert result == "x" * _FIELD_MAX_LEN + "…"


def test_truncate_non_string_passthrough():
    assert _truncate(42) == 42
    assert _truncate(None) is None


def test_extract_legacy_missing_key():
    assert _extract_legacy({}) is None


def test_extract_legacy_non_dict_value():
    assert _extract_legacy({"csp-report": "not-a-dict"}) is None


def test_extract_legacy_valid():
    body = {
        "csp-report": {
            "violated-directive": "script-src",
            "blocked-uri": "https://evil.example.com/x.js",
            "document-uri": "https://example.com/",
        }
    }
    result = _extract_legacy(body)
    assert result is not None
    assert result["violated_directive"] == "script-src"
    assert result["blocked_uri"] == "https://evil.example.com/x.js"


def test_extract_modern_wrong_type():
    assert _extract_modern({"type": "deprecation"}) is None


def test_extract_modern_valid():
    report = {
        "type": "csp-violation",
        "url": "https://example.com/page",
        "body": {
            "effectiveDirective": "img-src",
            "blockedURL": "https://cdn.bad.com/img.png",
            "documentURL": "https://example.com/page",
            "sourceFile": "app.js",
            "lineNumber": 5,
            "columnNumber": 3,
            "disposition": "enforce",
        },
    }
    result = _extract_modern(report)
    assert result is not None
    assert result["violated_directive"] == "img-src"
    assert result["blocked_uri"] == "https://cdn.bad.com/img.png"
    assert result["disposition"] == "enforce"


def test_blocked_domain_empty_string():
    assert _blocked_domain("") == "none"


def test_blocked_domain_special_keywords():
    assert _blocked_domain("inline") == "inline"
    assert _blocked_domain("eval") == "eval"
    assert _blocked_domain("self") == "self"
    assert _blocked_domain("data") == "data"


def test_blocked_domain_full_uri():
    assert _blocked_domain("https://cdn.example.com/script.js") == "cdn.example.com"


def test_csp_report_modern_list_format_returns_204():
    payload = json.dumps(
        [
            {
                "type": "csp-violation",
                "url": "https://example.com/",
                "body": {
                    "effectiveDirective": "script-src",
                    "blockedURL": "https://evil.com/x.js",
                    "documentURL": "https://example.com/",
                },
            }
        ]
    ).encode()
    resp = _client.post(
        "/api/csp-report",
        content=payload,
        headers={"Content-Type": "application/reports+json"},
    )
    assert resp.status_code == 204


def test_csp_report_list_wrong_type_returns_204():
    payload = json.dumps([{"type": "deprecation", "url": "https://example.com/"}]).encode()
    resp = _client.post("/api/csp-report", content=payload)
    assert resp.status_code == 204


def test_csp_report_list_non_dict_entry_returns_204():
    payload = json.dumps(["not-a-dict"]).encode()
    resp = _client.post("/api/csp-report", content=payload)
    assert resp.status_code == 204
