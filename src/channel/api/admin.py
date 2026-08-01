# Copyright (c) 2026 John Carter. All rights reserved.
"""Admin REST API — user list + user detail (#235) + metrics (#236), epic #233.

Read-only operational visibility: who is registered, what their signs
of life look like, and how Channel is being used (CloudWatch
``Channel``-namespace counter readback for the #239 dashboard). Every
route is gated server-side by ``require_admin`` (JWT ``role ==
"admin"``) — the SPA's ``/app/admin`` route shell (#330) client-gates
for UX only; **these endpoints are the real authorization boundary**.

Pagination contract
-------------------

The #234 storage helpers return DDB-shaped resume cursors and bound
per-call read cost, but a *sorted* user list cannot be produced from an
unsorted Scan one page at a time. So the list endpoint materializes the
full user set per request (walking ``scan_users`` cursors to exhaustion
— explicitly sanctioned by the #234 handoff — and fetching the chat
aggregates in one exhaustive ``limit=None`` call), sorts in memory, and
paginates by offset. The client-facing ``cursor`` is an opaque
base64url-JSON token ``{"offset": N, "sort": "<sort>"}`` — DDB key
shapes never leak to clients. O(table)-per-request is the accepted
v0.1 trade-off (tiny user count, per epic #233); the walk is capped at
:data:`_MAX_STORAGE_WALK_CALLS` calls as a safety valve.

``last_login_at`` is read from the ``USER#META`` row when a future
login-path denormalization (epic #233 open question 5) writes it;
until then it is ``null`` for every user (nullable per design
decision 5). It is deliberately NOT derived from the audit log here —
per-user audit walks in a list view cost up to 168 Queries each.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Path, Query

from channel import storage
from channel.api._auth import require_admin
from channel.logging_config import fingerprint_id
from channel.metrics import ENVIRONMENT as _METRICS_ENVIRONMENT
from channel.metrics import NAMESPACE as _METRICS_NAMESPACE
from channel.models import Chat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Safety valve on the per-request storage walks. Each ``scan_users``
# call evaluates at most 2 500 items (25 pages x 100), so 40 calls
# bounds one request at ~100k evaluated items. At v0.1 user counts a
# single call exhausts the table; if this cap is ever hit the list is
# served truncated and a warning is logged — revisit the list-users
# access path (epic #233 open question 1) before raising it.
_MAX_STORAGE_WALK_CALLS = 40

# Detail-view caps fixed by the issue: 10 recent chats, 25 audit
# events over the last 7 days.
_RECENT_CHATS_LIMIT = 10
_RECENT_AUDIT_LIMIT = 25
_AUDIT_WINDOW_DAYS = 7

_SORTS = ("last_chat_at", "created_at", "email")
SortField = Literal["last_chat_at", "created_at", "email"]

# ----------------------------------------------------------------
# Metrics endpoints (#236) — CloudWatch Channel-namespace readback
# ----------------------------------------------------------------

# Server-side allowlist: every EMF counter Channel emits as of merge
# time. All are written by ``channel.metrics`` helpers (or ``csp.py``
# for CSPViolations) with the base ``{"Environment": <env>}`` dimension
# set, so readback pins that one dimension. Extend this tuple when a
# new counter ships — nothing here discovers metrics dynamically
# (ListMetrics is deliberately not called: static allowlist keeps the
# IAM surface to GetMetricData alone and the input validation exact).
_METRIC_ALLOWLIST = (
    "MemoryWriteSuccesses",
    "MemoryWriteFailures",
    "RecallSuccesses",
    "RecallFailures",
    # Tool-driven memory (#400): agent-callable remember/recall counters,
    # kept separate from the hook counters above so the dashboard's
    # hook-health signal stays isolated from tool usage.
    "MemoryToolWriteSuccesses",
    "MemoryToolWriteFailures",
    "MemoryToolRecallSuccesses",
    "MemoryToolRecallFailures",
    "AutoTitleSuccesses",
    "AutoTitleFailures",
    "ChatDeleteMemoryWipeSuccesses",
    "ChatDeleteMemoryWipeFailures",
    "FollowupGenSuccesses",
    "FollowupGenFailures",
    "ChatDeleteAttachmentWipeSuccesses",
    "ChatDeleteAttachmentWipeFailures",
    "ChatDeleteAssetWipeSuccesses",
    "ChatDeleteAssetWipeFailures",
    "AssetLazyExpiryReaps",
    "AssetLazyExpiryReapFailures",
    "ToolCallSuccesses",
    "ToolCallFailures",
    # Request + Bedrock SLIs (#111). Only the Sum-meaningful counters are
    # listed: this endpoint hardcodes ``Stat: "Sum"``, so adding the
    # ``RequestLatencyMs`` / ``BedrockLatencyMs`` distributions here would
    # hand the dashboard the sum of every latency sample — a number with no
    # meaning. Latency percentiles live on the CloudWatch dashboard, which
    # can name a statistic.
    #
    # ``RequestCount`` and friends are also published under a second
    # ``{Environment, Route}`` dimension set; ``_metric_data_queries`` pins
    # the ``{Environment}`` set exactly, so readback sees the aggregate and
    # the per-route breakdown never double-counts here.
    "RequestCount",
    "Request4xxCount",
    "Request5xxCount",
    "BedrockTokensIn",
    "BedrockTokensOut",
    "BedrockErrors",
    "BedrockThrottles",
    "CSPViolations",
)

# Summary windows: response key -> lookback. "today" is a rolling 24h
# window (not calendar-day) so the three cards share one semantics.
_SUMMARY_WINDOWS: tuple[tuple[str, timedelta], ...] = (
    ("today", timedelta(hours=24)),
    ("7d", timedelta(days=7)),
    ("30d", timedelta(days=30)),
)

WindowName = Literal["24h", "7d", "30d"]
BucketName = Literal["5m", "1h", "1d"]

_WINDOW_DELTAS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
# Default bucket per window: 288 / 168 / 30 points respectively —
# all under the ~300-point response cap the issue fixes.
_DEFAULT_BUCKET: dict[str, str] = {"24h": "5m", "7d": "1h", "30d": "1d"}
_BUCKET_SECONDS: dict[str, int] = {"5m": 300, "1h": 3600, "1d": 86400}
_MAX_TIMESERIES_POINTS = 300

# Module-level cache per the issue: one client per Lambda instance,
# constructed lazily on first metrics request.
_cloudwatch_client: Any = None


def _get_cloudwatch() -> Any:
    """Lazily construct and cache the CloudWatch client (module level)."""
    global _cloudwatch_client
    if _cloudwatch_client is None:
        _cloudwatch_client = boto3.client("cloudwatch")
    return _cloudwatch_client


def _metrics_unavailable(exc: Exception) -> HTTPException:
    """Map a CloudWatch failure to the 503 contract the dashboard renders.

    Throttling, region misconfig, and permission errors all collapse to
    one structured body — the SPA shows a "metrics unavailable" state
    (#239) rather than crashing. Details go to the log, not the client.
    """
    logger.warning("admin metrics: CloudWatch unavailable: %s", exc)
    return HTTPException(
        status_code=503,
        detail={
            "error": "metrics_unavailable",
            "message": "CloudWatch metrics are temporarily unavailable; retry shortly.",
        },
    )


def _metric_data_queries(names: tuple[str, ...], period_seconds: int) -> list[dict[str, Any]]:
    """Build GetMetricData queries for ``names``, one Sum series each.

    Every EMF counter carries exactly the ``Environment`` dimension
    (see ``channel.metrics.emit_metric``), and CloudWatch only matches
    a metric when the requested dimension set is exact — so the one
    dimension is pinned here.
    """
    return [
        {
            "Id": f"m{i}",
            "MetricStat": {
                "Metric": {
                    "Namespace": _METRICS_NAMESPACE,
                    "MetricName": name,
                    "Dimensions": [{"Name": "Environment", "Value": _METRICS_ENVIRONMENT}],
                },
                "Period": period_seconds,
                "Stat": "Sum",
            },
            "ReturnData": True,
        }
        for i, name in enumerate(names)
    ]


def _window_sums(start: datetime, end: datetime) -> dict[str, float]:
    """Sum every allowlisted counter over ``[start, end]`` in one call.

    The whole window is requested as a single period bucket; CloudWatch
    may still split it on internal bucket boundaries, so returned values
    are summed rather than first-item-indexed. Metrics with no data in
    the window come back with empty ``Values`` and sum to 0.0.
    """
    period = int((end - start).total_seconds())
    resp = _get_cloudwatch().get_metric_data(
        MetricDataQueries=_metric_data_queries(_METRIC_ALLOWLIST, period),
        StartTime=start,
        EndTime=end,
    )
    by_id = {r["Id"]: r for r in resp.get("MetricDataResults", [])}
    return {
        name: float(sum(by_id.get(f"m{i}", {}).get("Values") or []))
        for i, name in enumerate(_METRIC_ALLOWLIST)
    }


@router.get("/metrics/summary")
async def metrics_summary(_claims: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Rollup card data for the admin dashboard (#236, consumed by #239).

    Shape: ``{"today": {...}, "7d": {...}, "30d": {...}}`` where each
    window object is ``{"active_users": int, "metrics": {name: number}}``
    covering every allowlisted counter (0.0 when no data). "today" is a
    rolling 24h window. 503 with a structured body on CloudWatch failure.
    """
    now = datetime.now(timezone.utc)
    out: dict[str, Any] = {}
    for label, delta in _SUMMARY_WINDOWS:
        start = now - delta
        try:
            counters = _window_sums(start, now)
        except (BotoCoreError, ClientError) as exc:
            raise _metrics_unavailable(exc) from exc
        out[label] = {
            "active_users": storage.count_active_users(start.isoformat(timespec="microseconds")),
            "metrics": counters,
        }
    return out


@router.get("/metrics/timeseries")
async def metrics_timeseries(
    metric: str = Query(...),
    window: WindowName = Query(...),
    bucket: BucketName | None = Query(default=None),
    _claims: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Single-metric timeseries for the dashboard charts (#236 → #239).

    ``metric`` must be on the server-side allowlist (422 otherwise);
    ``window`` picks the lookback; ``bucket`` defaults per window
    (24h→5m, 7d→1h, 30d→1d) and may be overridden as long as the
    resulting grid stays under the ~300-point cap (422 otherwise).

    Returns ``{"metric", "window", "bucket", "start", "end",
    "points": [{"t": <ISO-8601 UTC>, "v": <number>}, ...]}`` — a dense
    ascending grid aligned to bucket boundaries, zero-filled where
    CloudWatch has no datapoint. 503 with a structured body on
    CloudWatch failure.
    """
    if metric not in _METRIC_ALLOWLIST:
        raise HTTPException(status_code=422, detail="unknown metric")
    bucket_name = bucket or _DEFAULT_BUCKET[window]
    bucket_s = _BUCKET_SECONDS[bucket_name]
    window_s = int(_WINDOW_DELTAS[window].total_seconds())
    # Aligning start down to a bucket boundary adds at most one grid
    # slot beyond window/bucket, so the cap check mirrors that +1.
    if math.ceil(window_s / bucket_s) + 1 > _MAX_TIMESERIES_POINTS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"window={window} with bucket={bucket_name} exceeds "
                f"{_MAX_TIMESERIES_POINTS} points; use a coarser bucket"
            ),
        )
    end = datetime.now(timezone.utc)
    start_epoch = int((end - _WINDOW_DELTAS[window]).timestamp() // bucket_s) * bucket_s
    start = datetime.fromtimestamp(start_epoch, tz=timezone.utc)
    try:
        resp = _get_cloudwatch().get_metric_data(
            MetricDataQueries=_metric_data_queries((metric,), bucket_s),
            StartTime=start,
            EndTime=end,
            ScanBy="TimestampAscending",
        )
    except (BotoCoreError, ClientError) as exc:
        raise _metrics_unavailable(exc) from exc
    results = resp.get("MetricDataResults") or [{}]
    observed: dict[int, float] = {
        int(ts.timestamp()): float(val)
        # strict=False: CloudWatch guarantees parallel arrays; if they
        # ever mismatch, truncating beats a 500 on the dashboard.
        for ts, val in zip(
            results[0].get("Timestamps") or [],
            results[0].get("Values") or [],
            strict=False,
        )
    }
    points = [
        {
            "t": datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(),
            "v": observed.get(epoch, 0.0),
        }
        for epoch in range(start_epoch, int(end.timestamp()), bucket_s)
    ]
    return {
        "metric": metric,
        "window": window,
        "bucket": bucket_name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "points": points,
    }


# ----------------------------------------------------------------
# Cursor codec — opaque offset tokens, never DDB key shapes
# ----------------------------------------------------------------


def _encode_cursor(offset: int, sort: str) -> str:
    payload = json.dumps({"offset": offset, "sort": sort}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(token: str, sort: str) -> int:
    """Decode an opaque list cursor; 400 on anything malformed.

    A corrupt or foreign cursor must be a client error, never a 500.
    The embedded ``sort`` must match the request's ``sort`` — an
    offset into a differently-ordered list would silently skip or
    repeat rows.
    """
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()))
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("offset"), int)
        or isinstance(payload.get("offset"), bool)
        or payload["offset"] < 0
        or payload.get("sort") != sort
    ):
        raise HTTPException(status_code=400, detail="invalid cursor")
    return int(payload["offset"])


# ----------------------------------------------------------------
# User-set materialization
# ----------------------------------------------------------------


def _walk_chat_aggregates() -> dict[str, dict[str, Any]]:
    """Full chat-index aggregation keyed by user_id — one scan.

    ``limit=None`` asks the helper for the complete aggregated list in
    a single call. Looping its cursor instead would re-run the full
    chat-index scan once per page (the helper aggregates exhaustively
    every call), turning one list request into N table scans past 100
    users — flagged by Copilot review on PR #338.
    """
    rows, _ = storage.derive_users_from_chat_index(limit=None)
    return {str(row["user_id"]): row for row in rows}


def _walk_user_meta_rows() -> list[dict[str, Any]]:
    """Collect every ``USER#META`` row.

    ``scan_users`` may legally return ``([], cursor)`` — the cursor,
    not the row count, signals exhaustion (#234 handoff) — so the loop
    keys off the cursor alone.
    """
    collected: list[dict[str, Any]] = []
    cursor: dict[str, Any] | None = None
    for _ in range(_MAX_STORAGE_WALK_CALLS):
        rows, cursor = storage.scan_users(cursor=cursor, limit=100)
        collected.extend(rows)
        if cursor is None:
            return collected
    logger.warning(
        "admin user list truncated: scan_users cursor still live after %d calls",
        _MAX_STORAGE_WALK_CALLS,
    )
    return collected


def _user_row(
    *,
    user_id: str,
    email: str | None,
    created_at: str | None,
    last_login_at: str | None,
    chat_count: int,
    last_chat_at: str | None,
) -> dict[str, Any]:
    """The one list/detail row shape (#238 consumes this verbatim)."""
    return {
        "user_id": user_id,
        # JWT sub == email today (see auth.mgmt_auth.make_mgmt_user), so the
        # derived fallback surfaces user_id as the email.
        "email": email or user_id,
        "created_at": created_at,
        "last_login_at": last_login_at,
        "chat_count": chat_count,
        "last_chat_at": last_chat_at,
    }


def _materialize_users() -> list[dict[str, Any]]:
    """Full user set as response-shaped rows.

    ``USER#META`` rows are authoritative when present (post-#110);
    chat-index aggregates fill ``chat_count`` / ``last_chat_at`` either
    way. While META is sparse (today), the aggregate set IS the user
    list — the fallback the issue prescribes.
    """
    aggregates = _walk_chat_aggregates()
    meta_rows = _walk_user_meta_rows()
    if not meta_rows:
        return [
            _user_row(
                user_id=str(agg["user_id"]),
                email=None,
                created_at=agg.get("created_at"),
                last_login_at=None,
                chat_count=int(agg.get("chat_count", 0)),
                last_chat_at=agg.get("last_chat_at"),
            )
            for agg in aggregates.values()
        ]
    users: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in meta_rows:
        # USER#META rows carry PK=USER#{user_id}; prefer the explicit
        # attribute, fall back to the key (defensive until #110 locks
        # the row shape).
        user_id = str(item.get("user_id") or str(item.get("PK", ""))[len("USER#") :])
        agg = aggregates.get(user_id, {})
        seen.add(user_id)
        users.append(
            _user_row(
                user_id=user_id,
                email=item.get("email"),
                created_at=item.get("created_at") or agg.get("created_at"),
                last_login_at=item.get("last_login_at"),
                chat_count=int(agg.get("chat_count", 0)),
                last_chat_at=agg.get("last_chat_at"),
            )
        )
    # Users visible in the chat index but lacking a META row (possible
    # mid-migration once #110 ships) must not vanish from the list.
    for user_id, agg in aggregates.items():
        if user_id not in seen:
            users.append(
                _user_row(
                    user_id=user_id,
                    email=None,
                    created_at=agg.get("created_at"),
                    last_login_at=None,
                    chat_count=int(agg.get("chat_count", 0)),
                    last_chat_at=agg.get("last_chat_at"),
                )
            )
    return users


def _sort_users(users: list[dict[str, Any]], sort: str) -> None:
    """In-place sort. Timestamps descending (nulls last), email ascending.

    The pre-pass user_id sort makes ordering deterministic across
    requests when the sort key ties — offset pagination re-derives the
    list per request, so unstable ties would skip/repeat rows across
    pages.
    """
    users.sort(key=lambda u: u["user_id"])
    if sort == "email":
        users.sort(key=lambda u: str(u["email"]))
    else:
        # reverse=True puts the "" (null) sentinel last — nulls sort
        # after every real timestamp in a descending listing.
        users.sort(key=lambda u: u[sort] or "", reverse=True)


# ----------------------------------------------------------------
# Routes
# ----------------------------------------------------------------


@router.get("/users")
async def list_users(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    sort: SortField = "last_chat_at",
    _claims: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Paginated registered-user list, sorted server-side.

    ``sort`` accepts ``last_chat_at`` (default, desc), ``created_at``
    (desc), ``email`` (asc); anything else is a 422. ``next_cursor``
    is non-null while more rows exist and is only valid for the same
    ``sort`` it was issued under.
    """
    offset = _decode_cursor(cursor, sort) if cursor else 0
    users = _materialize_users()
    _sort_users(users, sort)
    page = users[offset : offset + limit]
    has_more = offset + limit < len(users)
    return {
        "items": page,
        "next_cursor": _encode_cursor(offset + limit, sort) if has_more else None,
    }


def _walk_user_chats(user_id: str) -> list[Chat]:
    """Every chat-index row for one user, newest-created first.

    Bounded per-user Query (not a table scan); archived chats are
    included — a user with only archived chats is still a registered
    user (#234 handoff).
    """
    chats: list[Chat] = []
    cursor: Any | None = None
    for _ in range(_MAX_STORAGE_WALK_CALLS):
        rows, cursor = storage.list_chats_for_user(
            user_id, limit=100, cursor=cursor, include_archived=True
        )
        chats.extend(rows)
        if cursor is None:
            return chats
    logger.warning(
        # user_id is the user's email (JWT sub) — fingerprint it rather
        # than writing PII into the log sink (same discipline as chats.py).
        "admin user detail truncated: chat walk for user %s cursor still live after %d calls",
        fingerprint_id(user_id),
        _MAX_STORAGE_WALK_CALLS,
    )
    return chats


def _chat_summary(chat: Chat) -> dict[str, Any]:
    """Trimmed chat shape for the admin detail view.

    Deliberately excludes ``last_user_preview`` — the admin surface is
    operational visibility (epic #233), not message content.
    """
    return {
        "chat_id": chat.chat_id,
        "title": chat.title,
        "created_at": chat.created_at,
        "last_message_at": chat.last_message_at,
        "message_count": chat.message_count,
        "archived": chat.archived,
    }


def _audit_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Audit event minus DDB plumbing (PK/SK/ttl) and the redundant actor_id."""
    return {
        "event_id": item.get("event_id"),
        "event_type": item.get("event_type"),
        "created_at": item.get("created_at"),
        "details": item.get("details"),
    }


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: str = Path(...),
    _claims: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Single-user detail: list-row shape + recent chats + audit events.

    404 when the user has neither a ``USER#META`` row nor any
    chat-index row.
    """
    meta = storage.get_user_meta(user_id)
    chats = _walk_user_chats(user_id)
    if meta is None and not chats:
        raise HTTPException(status_code=404, detail="User not found")

    last_chat_at = max((c.last_message_at or c.created_at for c in chats), default=None)
    min_chat_created = min((c.created_at for c in chats), default=None)
    meta = meta or {}
    since = (datetime.now(timezone.utc) - timedelta(days=_AUDIT_WINDOW_DAYS)).isoformat(
        timespec="microseconds"
    )
    events = storage.list_audit_events_for_actor(
        user_id, since_iso=since, limit=_RECENT_AUDIT_LIMIT
    )
    return {
        "user": _user_row(
            user_id=user_id,
            email=meta.get("email"),
            created_at=meta.get("created_at") or min_chat_created,
            last_login_at=meta.get("last_login_at"),
            chat_count=len(chats),
            last_chat_at=last_chat_at,
        ),
        "recent_chats": [_chat_summary(c) for c in chats[:_RECENT_CHATS_LIMIT]],
        "recent_audit_events": [_audit_summary(e) for e in events],
    }
