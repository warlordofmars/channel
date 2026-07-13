# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for the #234 admin read helpers against DynamoDB Local.

The in-memory ``FakeTable`` unit suite covers shape and branch logic;
these prove the pagination contracts against the real wire protocol —
``LastEvaluatedKey`` round-trips, FilterExpression-after-Limit
semantics, and synthesized mid-page Scan cursors that DynamoDB must
accept as ``ExclusiveStartKey``.

The session-scoped table is shared with the rest of the integration
suite (no per-test row cleanup), so every assertion filters to rows
tagged with a per-test unique id rather than asserting global totals.
The one exception is :func:`test_count_active_users_distinct_and_windowed`,
which isolates by using a far-future activity window no other test's
``last_message_at`` values can reach.

``_ADMIN_SCAN_PAGE_LIMIT`` is monkeypatched small in each test so the
internal Scan / Query loops are forced through real multi-page
pagination even at integration-suite data volumes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from channel import storage


def test_scan_users_pagination_round_trip(
    starter_table: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every seeded USER#META row surfaces exactly once across cursor hops.

    Page limit 2 forces the helper through real ``LastEvaluatedKey``
    pagination and (with the shared table's other row families in the
    scan path) through the empty-page-with-cursor case the docstring
    warns #235 about.
    """

    monkeypatch.setattr(storage, "_ADMIN_SCAN_PAGE_LIMIT", 2)
    tag = f"scanu-{uuid.uuid4().hex[:8]}"
    mine = {f"{tag}-{i}" for i in range(5)}
    for uid in sorted(mine):
        starter_table.put_item(
            Item={
                "PK": f"USER#{uid}",
                "SK": "META",
                "user_id": uid,
                "email": f"{uid}@example.com",
            }
        )

    seen: list[str] = []
    cursor: dict[str, Any] | None = None
    for _ in range(500):  # hard bound — the shared table is finite but not ours alone
        rows, cursor = storage.scan_users(cursor=cursor, limit=2)
        assert len(rows) <= 2
        seen.extend(str(r["user_id"]) for r in rows if str(r["user_id"]).startswith(tag))
        if cursor is None:
            break
    else:
        pytest.fail("scan_users never exhausted the table")

    assert sorted(seen) == sorted(mine)
    assert len(seen) == len(set(seen))  # no duplicates across cursor boundaries


def test_derive_users_aggregates_against_ddb_local(
    starter_table: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage, "_ADMIN_SCAN_PAGE_LIMIT", 3)
    tag = f"derive-{uuid.uuid4().hex[:8]}"
    ua, ub = f"{tag}-a", f"{tag}-b"
    first = storage.create_chat(user_id=ua, title=None, model_default="m")
    second = storage.create_chat(user_id=ua, title=None, model_default="m")
    other = storage.create_chat(user_id=ub, title=None, model_default="m")
    bumped = "2999-01-01T00:00:00.000000+00:00"
    storage.update_chat_index(
        user_id=ua,
        chat=first,
        last_user_preview="x",
        delta_count=1,
        last_message_at=bumped,
    )

    rows: list[dict[str, Any]] = []
    cursor: dict[str, Any] | None = None
    while True:
        page, cursor = storage.derive_users_from_chat_index(cursor=cursor, limit=2)
        rows.extend(r for r in page if str(r["user_id"]).startswith(tag))
        if cursor is None:
            break

    by_id = {r["user_id"]: r for r in rows}
    assert set(by_id) == {ua, ub}
    assert int(by_id[ua]["chat_count"]) == 2
    assert by_id[ua]["created_at"] == min(first.created_at, second.created_at)
    assert by_id[ua]["last_chat_at"] == bumped
    assert int(by_id[ub]["chat_count"]) == 1
    assert by_id[ub]["created_at"] == other.created_at
    assert by_id[ub]["last_chat_at"] == other.last_message_at


def test_list_audit_events_actor_timeline(
    starter_table: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-shard walk, event_type filter, and since bound — real shards.

    Page limit 1 forces the per-shard Query loop through real
    ``LastEvaluatedKey`` pagination (filter-thinned pages must not
    truncate the shard read).
    """

    monkeypatch.setattr(storage, "_ADMIN_SCAN_PAGE_LIMIT", 1)
    actor = f"audit-{uuid.uuid4().hex[:8]}@example.com"
    e1 = storage.put_audit_event(event_type="auth.login", actor_id=actor)
    e2 = storage.put_audit_event(event_type="auth.logout", actor_id=actor)
    # Backdated event two hours ago — lands in an older hourly shard.
    old_at = datetime.now(timezone.utc) - timedelta(hours=2)
    old_id = f"evt-old-{uuid.uuid4().hex[:8]}"
    starter_table.put_item(
        Item={
            "PK": f"AUDIT#{old_at:%Y-%m-%d}#{old_at:%H}",
            "SK": f"{int(old_at.timestamp())}#{old_id}",
            "event_id": old_id,
            "event_type": "auth.login",
            "actor_id": actor,
            "created_at": old_at.isoformat(timespec="microseconds"),
        }
    )

    all_events = storage.list_audit_events_for_actor(actor)
    ids = [e["event_id"] for e in all_events]
    assert set(ids) == {e1["event_id"], e2["event_id"], old_id}
    assert ids[-1] == old_id  # older shard ordered after current-hour events

    logins = storage.list_audit_events_for_actor(actor, event_type="auth.login")
    assert {e["event_id"] for e in logins} == {e1["event_id"], old_id}

    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="microseconds")
    recent = storage.list_audit_events_for_actor(actor, since_iso=since)
    assert {e["event_id"] for e in recent} == {e1["event_id"], e2["event_id"]}


def test_count_active_users_distinct_and_windowed(
    starter_table: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distinct count over the real table, isolated by a far-future window.

    Window year 3000 — every other test's ``last_message_at`` is either
    "now" (2026) or the derive test's 2999 bump, so only the rows bumped
    here can satisfy the filter and the count is exact, not >=.
    """

    monkeypatch.setattr(storage, "_ADMIN_SCAN_PAGE_LIMIT", 3)
    tag = f"active-{uuid.uuid4().hex[:8]}"
    window = "3000-01-01T00:00:00.000000+00:00"
    active_at = "3000-06-01T00:00:00.000000+00:00"
    u1, u2, u3 = (f"{tag}-{i}" for i in range(3))
    for uid, chats in ((u1, 2), (u2, 1)):
        for _ in range(chats):
            chat = storage.create_chat(user_id=uid, title=None, model_default="m")
            storage.update_chat_index(
                user_id=uid,
                chat=chat,
                last_user_preview="x",
                delta_count=1,
                last_message_at=active_at,
            )
    # u3 stays at last_message_at = now → outside the window.
    storage.create_chat(user_id=u3, title=None, model_default="m")

    assert storage.count_active_users(window) == 2
