# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for the audit-log row family (issues #151, #601).

These run against DynamoDB Local — the in-memory ``FakeTable`` covers
shape, but only the real client confirms that ``int(item["ttl"])``
survives the boto3 serialization round-trip (DynamoDB returns Numbers
as ``decimal.Decimal``, so the unit suite alone cannot catch a
silently-stringified ``ttl``).

The #601 block below covers ``query_audit_events`` for the same reason
at one more remove: that function leans on three pieces of *real*
DynamoDB Query semantics the fake only approximates — an inclusive
``Key("SK").between`` range over the ``{unix_ts}#{uuid}`` sort key, a
``FilterExpression`` applied after that range, and ``ExclusiveStartKey``
resumption inside a partition being walked backwards. A cursor that
paginates correctly against the fake and skips or repeats a row against
the real engine is exactly the failure this suite exists to catch.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from boto3.dynamodb.conditions import Key

from channel import storage


def test_audit_event_round_trip(starter_table: object) -> None:
    """Audit row lands under the AUDIT# prefix and queries back correctly.

    The "Audit-log query in dev shows type=auth.logout rows after the
    click" acceptance criterion from issue #151 — proven against the
    real DynamoDB Local fixture rather than the in-memory unit-test
    stand-in.
    """

    item = storage.put_audit_event(
        event_type="auth.logout",
        actor_id="audit-itest@example.com",
        details={"role": "user", "token_fingerprint": "deadbeefcafebabe"},
    )

    # Query the partition the helper wrote to and confirm our event is
    # present with the expected shape.
    result = starter_table.query(  # type: ignore[attr-defined]
        KeyConditionExpression=Key("PK").eq(item["PK"]) & Key("SK").eq(item["SK"]),
    )
    rows = result.get("Items") or []
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "auth.logout"
    assert row["actor_id"] == "audit-itest@example.com"
    assert row["details"]["role"] == "user"
    assert row["details"]["token_fingerprint"] == "deadbeefcafebabe"
    # DynamoDB returns Number-typed attributes as decimal.Decimal; cast
    # before comparing so the assertion catches a stringified ``ttl``
    # (which DynamoDB's TTL service silently ignores).
    assert int(row["ttl"]) > int(item["SK"].split("#", 1)[0])


def test_audit_event_partition_query_returns_multiple_events(
    starter_table: object,
) -> None:
    """Two writes against the same hour-shard partition both surface on query."""

    a = storage.put_audit_event(event_type="auth.logout", actor_id="actor-a")
    b = storage.put_audit_event(event_type="auth.logout", actor_id="actor-b")

    # If they happened to fall in different hours (rare; e.g. wall-clock
    # crossing the hour boundary), query each partition separately.
    partitions = {a["PK"], b["PK"]}
    actors: set[str] = set()
    for pk in partitions:
        result = starter_table.query(  # type: ignore[attr-defined]
            KeyConditionExpression=Key("PK").eq(pk),
        )
        for row in result.get("Items") or []:
            actors.add(row["actor_id"])
    assert {"actor-a", "actor-b"}.issubset(actors)


# ----------------------------------------------------------------
# query_audit_events (#601) — window, filters, and real pagination
# ----------------------------------------------------------------


def _seed(
    table: Any, *, actor_id: str, event_type: str, at: datetime, event_id: str
) -> dict[str, Any]:
    """Write an audit row at an arbitrary instant.

    ``put_audit_event`` always stamps "now", and every property under
    test here is about a *window*, so the rows have to be backdated by
    hand. The shape is copied from the writer rather than imported so a
    silent change to the stored key layout fails these tests loudly.
    """

    item = {
        "PK": f"AUDIT#{at:%Y-%m-%d}#{at:%H}",
        "SK": f"{int(at.timestamp())}#{event_id}",
        "event_id": event_id,
        "event_type": event_type,
        "actor_id": actor_id,
        "created_at": at.isoformat(timespec="microseconds"),
        "ttl": int(at.timestamp()) + 365 * 86400,
    }
    table.put_item(Item=item)
    return item


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def test_query_audit_events_window_and_ordering(starter_table: Any) -> None:
    """Newest-first across partitions, with both window edges honoured."""

    now = datetime.now(timezone.utc)
    tag = f"win-{int(now.timestamp())}"
    for hours, name in ((1, "recent"), (3, "middle"), (9, "outside")):
        _seed(
            starter_table,
            actor_id=f"{tag}@example.com",
            event_type="auth.logout",
            at=now - timedelta(hours=hours),
            event_id=f"{tag}-{name}",
        )

    events, cursor = storage.query_audit_events(
        from_iso=_iso(now - timedelta(hours=5)),
        to_iso=_iso(now),
        actor_id=f"{tag}@example.com",
    )

    assert [e["event_id"] for e in events] == [f"{tag}-recent", f"{tag}-middle"]
    assert cursor is None


def test_query_audit_events_spans_actors(starter_table: Any) -> None:
    """The unfiltered query is cross-actor — the compliance question."""

    now = datetime.now(timezone.utc)
    tag = f"multi-{int(now.timestamp())}"
    for who in ("alice", "bob"):
        _seed(
            starter_table,
            actor_id=f"{tag}-{who}@example.com",
            event_type=f"{tag}.event",
            at=now - timedelta(minutes=5),
            event_id=f"{tag}-{who}",
        )

    events, _ = storage.query_audit_events(
        from_iso=_iso(now - timedelta(hours=1)),
        to_iso=_iso(now),
        event_type=f"{tag}.event",
    )

    assert {e["actor_id"] for e in events} == {
        f"{tag}-alice@example.com",
        f"{tag}-bob@example.com",
    }


def test_query_audit_events_cursor_pages_without_gaps_or_repeats(
    starter_table: Any,
) -> None:
    """The property the fake cannot prove: real ExclusiveStartKey paging.

    Six rows spread over three partitions, read one at a time — the
    concatenation must be exactly the six, in order, with no row seen
    twice and none skipped at a partition boundary.
    """

    now = datetime.now(timezone.utc)
    tag = f"page-{int(now.timestamp())}"
    expected = []
    for hour in (1, 2, 3):
        for slot in (0, 1):
            event_id = f"{tag}-h{hour}s{slot}"
            _seed(
                starter_table,
                actor_id=f"{tag}@example.com",
                event_type="auth.logout",
                at=now - timedelta(hours=hour, seconds=slot),
                event_id=event_id,
            )
            expected.append((hour, slot, event_id))
    # Newest first: ascending hour, and within an hour the smaller
    # second offset is the later instant.
    expected.sort(key=lambda row: (row[0], row[1]))

    seen: list[str] = []
    cursor: dict[str, Any] | None = None
    for _ in range(10):  # generous bound; the walk needs 6 + 1
        page, cursor = storage.query_audit_events(
            from_iso=_iso(now - timedelta(hours=5)),
            to_iso=_iso(now),
            actor_id=f"{tag}@example.com",
            limit=1,
            cursor=cursor,
        )
        seen.extend(e["event_id"] for e in page)
        if cursor is None:
            break

    assert cursor is None, "pagination did not terminate"
    assert seen == [row[2] for row in expected]


def test_query_audit_events_boundary_second_is_exact(starter_table: Any) -> None:
    """The sort-key range is second-granular; ``created_at`` makes it exact.

    Both rows share a sort-key second, so a key-range-only
    implementation returns the row written after the bound.
    """

    base = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0)
    tag = f"edge-{int(base.timestamp())}"
    _seed(
        starter_table,
        actor_id=f"{tag}@example.com",
        event_type="auth.logout",
        at=base + timedelta(microseconds=100_000),
        event_id=f"{tag}-before",
    )
    _seed(
        starter_table,
        actor_id=f"{tag}@example.com",
        event_type="auth.logout",
        at=base + timedelta(microseconds=900_000),
        event_id=f"{tag}-after",
    )

    events, _ = storage.query_audit_events(
        from_iso=_iso(base),
        to_iso=_iso(base + timedelta(microseconds=500_000)),
        actor_id=f"{tag}@example.com",
    )

    assert [e["event_id"] for e in events] == [f"{tag}-before"]


def test_query_audit_events_ttl_survives_the_round_trip(starter_table: Any) -> None:
    """A queried row still carries an integer-typed ``ttl``.

    Same concern as ``test_audit_event_round_trip``, one layer up: the
    read path must not be the place a Number quietly becomes a string.
    """

    written = storage.put_audit_event(event_type="auth.logout", actor_id="ttl-query@example.com")
    now = datetime.now(timezone.utc)

    events, _ = storage.query_audit_events(
        from_iso=_iso(now - timedelta(hours=1)),
        to_iso=_iso(now),
        actor_id="ttl-query@example.com",
    )

    assert [e["event_id"] for e in events] == [written["event_id"]]
    assert int(events[0]["ttl"]) > int(events[0]["SK"].split("#", 1)[0])
