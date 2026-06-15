# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for the JTI revocation denylist row family (#240).

These run against DynamoDB Local. The in-memory ``FakeTable`` unit suite
covers item shape, but only the real client confirms that ``int(ttl)``
survives the boto3 number round-trip (DynamoDB returns Numbers as
``decimal.Decimal``) and that ``is_jti_denied`` reads back a row written
by ``deny_jti`` through the real ``GetItem`` path. Unique ``jti`` values
per test avoid cross-pollution (the table is not cleaned between tests).
"""

from __future__ import annotations

from boto3.dynamodb.conditions import Key

from channel import storage


def test_is_jti_denied_false_before_any_deny(starter_table: object) -> None:
    assert storage.is_jti_denied("itest-never-revoked") is False


def test_deny_jti_then_is_denied_round_trip(starter_table: object) -> None:
    storage.deny_jti("itest-jti-1", exp=1_900_000_000)
    assert storage.is_jti_denied("itest-jti-1") is True


def test_deny_jti_row_shape_and_ttl_survive_round_trip(starter_table) -> None:  # type: ignore[no-untyped-def]
    exp = 1_900_000_001
    storage.deny_jti("itest-jti-2", exp=exp)

    result = starter_table.query(
        KeyConditionExpression=Key("PK").eq("DENY#itest-jti-2") & Key("SK").eq("META"),
    )
    rows = result.get("Items") or []
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "DENY"
    assert row["revoked_at"]
    # DynamoDB returns Number attributes as decimal.Decimal; cast so the
    # assertion catches a silently-stringified ttl (which the TTL service
    # would ignore, leaving the denylist row to live forever).
    assert int(row["ttl"]) == exp


def test_deny_jti_is_idempotent(starter_table: object) -> None:
    storage.deny_jti("itest-jti-3", exp=1_900_000_000)
    storage.deny_jti("itest-jti-3", exp=1_900_000_000)  # re-revocation must not raise
    assert storage.is_jti_denied("itest-jti-3") is True
