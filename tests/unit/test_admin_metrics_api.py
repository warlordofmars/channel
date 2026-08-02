# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api/admin/metrics endpoints (#236).

Auth-gating tests drive the REAL ``require_admin`` dependency with real
mgmt JWTs (repo rule: don't mock auth; mock the AWS boundary). The AWS
boundary here is the CloudWatch client, stubbed via
``botocore.stub.Stubber`` per the issue spec — expected request params
are asserted so the query construction (namespace, Environment
dimension pin, Stat=Sum, period) is locked, not just the response
mapping. DynamoDB is patched at the ``channel.api.admin.storage`` seam,
following the test_admin_api.py pattern.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import pytest
from botocore.stub import ANY, Stubber
from fastapi.testclient import TestClient

os.environ.setdefault("CHANNEL_JWT_SECRET", "test-secret-for-unit-tests")

from channel.api.admin import _METRIC_ALLOWLIST  # noqa: E402
from channel.api.main import app  # noqa: E402
from channel.auth.tokens import issue_mgmt_jwt  # noqa: E402
from channel.metrics import ENVIRONMENT as _ENV  # noqa: E402

client = TestClient(app)

_SUMMARY_PATH = "/api/admin/metrics/summary"
_TIMESERIES_PATH = "/api/admin/metrics/timeseries"
_VALID_TS_QS = {"metric": "ToolCallSuccesses", "window": "24h"}

# Fixed clock exactly on a 1-day bucket boundary so every grid the
# handler derives from it is deterministic (no floor-alignment jitter).
_FROZEN_NOW = datetime(2026, 7, 10, 0, 0, 0, tzinfo=timezone.utc)


def _admin_headers() -> dict[str, str]:
    token = issue_mgmt_jwt(
        {"user_id": "admin@test.com", "email": "admin@test.com", "role": "admin"}
    )
    return {"Authorization": f"Bearer {token}"}


def _user_headers() -> dict[str, str]:
    token = issue_mgmt_jwt({"user_id": "user@test.com", "email": "user@test.com", "role": "user"})
    return {"Authorization": f"Bearer {token}"}


class _FrozenDatetime(datetime):
    """now() pinned to _FROZEN_NOW; everything else is real datetime."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return _FROZEN_NOW


@pytest.fixture()
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr("channel.api.admin.datetime", _FrozenDatetime)
    return _FROZEN_NOW


@pytest.fixture()
def cloudwatch_stub(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A stubbed CloudWatch client injected at the module-level cache."""
    cw = boto3.client("cloudwatch", region_name="us-east-1")
    stubber = Stubber(cw)
    monkeypatch.setattr("channel.api.admin._cloudwatch_client", cw)
    stubber.activate()
    yield stubber
    stubber.deactivate()


def _expected_queries(names: tuple[str, ...], period: int) -> list[dict[str, Any]]:
    """Mirror of the query shape the endpoints must construct."""
    return [
        {
            "Id": f"m{i}",
            "MetricStat": {
                "Metric": {
                    # Literal namespace on purpose — locks the readback to
                    # the same namespace channel.metrics writes to.
                    "Namespace": "Channel",
                    "MetricName": name,
                    "Dimensions": [{"Name": "Environment", "Value": _ENV}],
                },
                "Period": period,
                "Stat": "Sum",
            },
            "ReturnData": True,
        }
        for i, name in enumerate(names)
    ]


def _result(id_: str, values: list[float], timestamps: list[datetime] | None = None) -> dict:
    return {
        "Id": id_,
        "Label": id_,
        "StatusCode": "Complete",
        "Timestamps": timestamps or [],
        "Values": values,
    }


# ----------------------------------------------------------------
# Auth boundary
# ----------------------------------------------------------------


@pytest.mark.parametrize("path,qs", [(_SUMMARY_PATH, {}), (_TIMESERIES_PATH, _VALID_TS_QS)])
def test_metrics_routes_reject_missing_auth(path: str, qs: dict[str, str]) -> None:
    # HTTPBearer returns 403 (not 401) when no Authorization header is
    # supplied; either is "rejected for missing auth" so accept both.
    resp = client.get(path, params=qs)
    assert resp.status_code in (401, 403)


@pytest.mark.parametrize("path,qs", [(_SUMMARY_PATH, {}), (_TIMESERIES_PATH, _VALID_TS_QS)])
def test_metrics_routes_reject_invalid_token_with_401(path: str, qs: dict[str, str]) -> None:
    resp = client.get(path, params=qs, headers={"Authorization": "Bearer totally.invalid.jwt"})
    assert resp.status_code == 401


@pytest.mark.parametrize("path,qs", [(_SUMMARY_PATH, {}), (_TIMESERIES_PATH, _VALID_TS_QS)])
def test_metrics_routes_reject_non_admin_with_403(path: str, qs: dict[str, str]) -> None:
    """A valid mgmt JWT with role=user must be refused server-side."""
    resp = client.get(path, params=qs, headers=_user_headers())
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin role required"


# ----------------------------------------------------------------
# Timeseries validation — allowlist, window/bucket literals, point cap
# ----------------------------------------------------------------


def test_memory_tool_counters_are_allowlisted() -> None:
    """#400: the tool-driven memory counters must be readable from the
    dashboard, and must be distinct names from the hook counters so the two
    signals stay isolated."""
    tool_counters = {
        "MemoryToolWriteSuccesses",
        "MemoryToolWriteFailures",
        "MemoryToolRecallSuccesses",
        "MemoryToolRecallFailures",
    }
    assert tool_counters.issubset(set(_METRIC_ALLOWLIST))
    # The hook counters remain, distinct, so hook health is not folded in.
    assert {"MemoryWriteSuccesses", "RecallSuccesses"}.issubset(set(_METRIC_ALLOWLIST))
    assert tool_counters.isdisjoint({"MemoryWriteSuccesses", "MemoryWriteFailures"})


def test_request_and_bedrock_counters_are_allowlisted() -> None:
    """#111: the request-level and Bedrock SLIs must be readable from the
    admin dashboard, so it reflects real traffic rather than only
    agent-internal hook activity."""
    assert {
        "RequestCount",
        "Request4xxCount",
        "Request5xxCount",
        "BedrockTokensIn",
        "BedrockTokensOut",
        "BedrockErrors",
        "BedrockThrottles",
    }.issubset(set(_METRIC_ALLOWLIST))


def test_latency_distributions_are_not_allowlisted() -> None:
    """This endpoint hardcodes ``Stat: "Sum"``. Summing a latency
    distribution yields a number with no meaning, so the ``*LatencyMs``
    metrics deliberately stay off the allowlist — percentiles live on the
    CloudWatch dashboard, which can name a statistic."""
    assert "RequestLatencyMs" not in _METRIC_ALLOWLIST
    assert "BedrockLatencyMs" not in _METRIC_ALLOWLIST


def test_timeseries_accepts_memory_tool_metric(cloudwatch_stub: Any, frozen_now: datetime) -> None:
    """#400: a tool-driven counter is a valid timeseries metric (not a 422)."""
    cloudwatch_stub.add_response(
        "get_metric_data",
        {"MetricDataResults": []},
        {
            "MetricDataQueries": _expected_queries(("MemoryToolWriteSuccesses",), 300),
            "StartTime": ANY,
            "EndTime": ANY,
            "ScanBy": "TimestampAscending",
        },
    )
    resp = client.get(
        _TIMESERIES_PATH,
        params={"metric": "MemoryToolWriteSuccesses", "window": "24h"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["metric"] == "MemoryToolWriteSuccesses"
    cloudwatch_stub.assert_no_pending_responses()


def test_timeseries_rejects_unknown_metric_with_422() -> None:
    resp = client.get(
        _TIMESERIES_PATH,
        params={"metric": "TotallyMadeUp", "window": "24h"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "unknown metric"


def test_timeseries_rejects_unknown_window_with_422() -> None:
    resp = client.get(
        _TIMESERIES_PATH,
        params={"metric": "ToolCallSuccesses", "window": "90d"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 422


def test_timeseries_rejects_unknown_bucket_with_422() -> None:
    resp = client.get(
        _TIMESERIES_PATH,
        params={"metric": "ToolCallSuccesses", "window": "24h", "bucket": "30s"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("window,bucket", [("30d", "1h"), ("7d", "5m"), ("30d", "5m")])
def test_timeseries_rejects_over_cap_grid_with_422(window: str, bucket: str) -> None:
    """Fine buckets over long windows would exceed the ~300-point cap."""
    resp = client.get(
        _TIMESERIES_PATH,
        params={"metric": "ToolCallSuccesses", "window": window, "bucket": bucket},
        headers=_admin_headers(),
    )
    assert resp.status_code == 422
    assert "exceeds" in resp.json()["detail"]


# ----------------------------------------------------------------
# Summary
# ----------------------------------------------------------------


def _stub_summary_windows(stub: Any, results_per_window: list[list[dict]]) -> None:
    """Queue the three per-window GetMetricData responses in order."""
    for period, results in zip((86400, 604800, 2592000), results_per_window, strict=True):
        stub.add_response(
            "get_metric_data",
            {"MetricDataResults": results},
            {
                "MetricDataQueries": _expected_queries(_METRIC_ALLOWLIST, period),
                "StartTime": ANY,
                "EndTime": ANY,
            },
        )


def test_summary_shape_and_sums(
    monkeypatch: pytest.MonkeyPatch, cloudwatch_stub: Any, frozen_now: datetime
) -> None:
    """Three windows, each: active_users + every allowlisted counter.

    CloudWatch may split a whole-window period request on internal
    bucket boundaries, so multi-value results must be summed; metrics
    absent from the response must surface as 0.0.
    """
    active_calls: list[str] = []
    active_returns = iter([5, 4, 3])

    def _fake_count(window_start_iso: str) -> int:
        active_calls.append(window_start_iso)
        return next(active_returns)

    monkeypatch.setattr("channel.api.admin.storage.count_active_users", _fake_count)
    _stub_summary_windows(
        cloudwatch_stub,
        [
            # today: split datapoints for m0 must sum; m1 single value.
            [_result("m0", [2.0, 3.0]), _result("m1", [1.0])],
            # 7d: empty Values sums to 0.0.
            [_result("m0", [])],
            # 30d: no results at all — every metric 0.0.
            [],
        ],
    )

    resp = client.get(_SUMMARY_PATH, headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"today", "7d", "30d"}

    today = body["today"]
    assert today["active_users"] == 5
    assert set(today["metrics"].keys()) == set(_METRIC_ALLOWLIST)
    assert today["metrics"]["MemoryWriteSuccesses"] == pytest.approx(5.0)  # m0: 2.0 + 3.0
    assert today["metrics"]["MemoryWriteFailures"] == pytest.approx(1.0)  # m1
    assert today["metrics"]["ToolCallSuccesses"] == pytest.approx(0.0)  # absent from response

    assert body["7d"]["active_users"] == 4
    assert body["7d"]["metrics"]["MemoryWriteSuccesses"] == pytest.approx(0.0)
    assert body["30d"]["active_users"] == 3
    assert all(v == pytest.approx(0.0) for v in body["30d"]["metrics"].values())

    # count_active_users receives each window's start; lookbacks grow,
    # so the ISO bounds must be strictly decreasing (24h > 7d > 30d).
    assert active_calls == [
        (frozen_now - timedelta(hours=24)).isoformat(timespec="microseconds"),
        (frozen_now - timedelta(days=7)).isoformat(timespec="microseconds"),
        (frozen_now - timedelta(days=30)).isoformat(timespec="microseconds"),
    ]
    cloudwatch_stub.assert_no_pending_responses()


def test_summary_returns_503_on_cloudwatch_error(
    monkeypatch: pytest.MonkeyPatch, cloudwatch_stub: Any
) -> None:
    """Throttling (or any CW failure) maps to the structured 503 the
    dashboard renders as "metrics unavailable" — never a 500."""
    monkeypatch.setattr(
        "channel.api.admin.storage.count_active_users",
        lambda _iso: pytest.fail("count_active_users must not run when CloudWatch fails"),
    )
    cloudwatch_stub.add_client_error(
        "get_metric_data", service_error_code="Throttling", http_status_code=400
    )
    resp = client.get(_SUMMARY_PATH, headers=_admin_headers())
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "metrics_unavailable"
    assert "unavailable" in resp.json()["detail"]["message"]


# ----------------------------------------------------------------
# Timeseries
# ----------------------------------------------------------------


def test_timeseries_zero_filled_grid_default_bucket(
    cloudwatch_stub: Any, frozen_now: datetime
) -> None:
    """24h window defaults to 5m buckets: a dense 288-point ascending
    grid, observed values in their slots, zeros elsewhere."""
    start = frozen_now - timedelta(hours=24)
    cloudwatch_stub.add_response(
        "get_metric_data",
        {
            "MetricDataResults": [
                _result(
                    "m0",
                    [4.0, 7.0],
                    [start, start + timedelta(seconds=300 * 10)],
                )
            ]
        },
        {
            "MetricDataQueries": _expected_queries(("ToolCallSuccesses",), 300),
            "StartTime": ANY,
            "EndTime": ANY,
            "ScanBy": "TimestampAscending",
        },
    )
    resp = client.get(_TIMESERIES_PATH, params=_VALID_TS_QS, headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "ToolCallSuccesses"
    assert body["window"] == "24h"
    assert body["bucket"] == "5m"
    assert body["start"] == start.isoformat()
    assert body["end"] == frozen_now.isoformat()

    points = body["points"]
    assert len(points) == 288  # 24h / 5m, end-exclusive
    assert points[0]["t"] == start.isoformat()
    assert points[0]["v"] == pytest.approx(4.0)
    assert points[10]["v"] == pytest.approx(7.0)
    assert sum(p["v"] for p in points) == pytest.approx(11.0)  # everything else zero-filled
    ts = [p["t"] for p in points]
    assert ts == sorted(ts)  # ascending grid
    cloudwatch_stub.assert_no_pending_responses()


def test_timeseries_bucket_override_and_empty_results(
    cloudwatch_stub: Any, frozen_now: datetime
) -> None:
    """Explicit coarser bucket is honoured; a response with no
    MetricDataResults still yields the full zero-filled grid."""
    cloudwatch_stub.add_response(
        "get_metric_data",
        {"MetricDataResults": []},
        {
            "MetricDataQueries": _expected_queries(("CSPViolations",), 3600),
            "StartTime": ANY,
            "EndTime": ANY,
            "ScanBy": "TimestampAscending",
        },
    )
    resp = client.get(
        _TIMESERIES_PATH,
        params={"metric": "CSPViolations", "window": "24h", "bucket": "1h"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bucket"] == "1h"
    assert len(body["points"]) == 24
    assert all(p["v"] == pytest.approx(0.0) for p in body["points"])
    cloudwatch_stub.assert_no_pending_responses()


def test_timeseries_returns_503_on_cloudwatch_error(cloudwatch_stub: Any) -> None:
    cloudwatch_stub.add_client_error(
        "get_metric_data", service_error_code="AccessDenied", http_status_code=403
    )
    resp = client.get(_TIMESERIES_PATH, params=_VALID_TS_QS, headers=_admin_headers())
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "metrics_unavailable"


# ----------------------------------------------------------------
# Client cache
# ----------------------------------------------------------------


def test_get_cloudwatch_constructs_once_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.api import admin

    constructed: list[str] = []

    def _fake_client(service: str) -> object:
        constructed.append(service)
        return object()

    monkeypatch.setattr("channel.api.admin._cloudwatch_client", None)
    monkeypatch.setattr("channel.api.admin.boto3.client", _fake_client)
    first = admin._get_cloudwatch()
    second = admin._get_cloudwatch()
    assert first is second
    assert constructed == ["cloudwatch"]
