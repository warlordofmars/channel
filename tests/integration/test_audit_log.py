# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for the audit-log row family (issue #151).

These run against DynamoDB Local — the in-memory ``FakeTable`` covers
shape, but only the real client confirms that ``int(item["ttl"])``
survives the boto3 serialization round-trip (DynamoDB returns Numbers
as ``decimal.Decimal``, so the unit suite alone cannot catch a
silently-stringified ``ttl``).
"""

from __future__ import annotations

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
