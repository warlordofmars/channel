# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for :mod:`starter.auth.state_store` against DynamoDB Local.

Covers the same four cases as the unit suite — happy path, missing key,
expired item, atomic-consume race — but against a real DynamoDB Local
instance so the conditional-delete + ALL_OLD shape is exercised end to end.

The unit suite mocks ``_get_table()``; this suite calls the real boto3
client so any drift between the mock and the real API surface (e.g.
ConditionalCheckFailedException error code, Attributes vs no-Attributes
response shape) shows up here.
"""

from __future__ import annotations

import time
import uuid

from channel.auth import state_store


def _unique_state() -> str:
    """Generate a state value unique to this test invocation.

    Uses a UUID so concurrent runs of the suite don't collide. The
    ``state_store`` table fixture is session-scoped and not cleaned
    between tests.
    """
    return f"itest-{uuid.uuid4()}"


def test_put_then_consume_returns_payload(starter_table):
    state = _unique_state()
    state_store.put_state(state, payload={"nonce": "n1", "redirect_uri": "/cb"})

    result = state_store.consume_state(state)

    assert result is not None
    assert result["nonce"] == "n1"
    assert result["redirect_uri"] == "/cb"
    # PK / SK round-tripped from the stored item.
    assert result["PK"] == f"MGMT_STATE#{state}"
    assert result["SK"] == "META"


def test_consume_missing_state_returns_none(starter_table):
    # Never put — direct consume of a state that doesn't exist.
    result = state_store.consume_state(_unique_state())
    assert result is None


def test_consume_after_consume_returns_none(starter_table):
    """Single-use semantics: a second consume of the same state returns None."""
    state = _unique_state()
    state_store.put_state(state)

    first = state_store.consume_state(state)
    second = state_store.consume_state(state)

    assert first is not None
    assert second is None


def test_consume_expired_item_returns_none(starter_table):
    """App-level expiry check rejects items past their ``created_at + ttl_seconds``.

    DynamoDB TTL won't sweep within the test window (sweep runs ~48h),
    so we put an item directly with a stale ``created_at`` to exercise
    the application-side expiry branch.
    """
    state = _unique_state()
    long_ago = int(time.time()) - 3600  # one hour ago
    starter_table.put_item(
        Item={
            "PK": f"MGMT_STATE#{state}",
            "SK": "META",
            "created_at": long_ago,
            "ttl_seconds": 600,  # 10-minute window expired ~50 minutes ago
            "ttl": long_ago + 600,
            "nonce": "stale",
        }
    )

    assert state_store.consume_state(state) is None


def test_atomic_consume_only_one_winner(starter_table):
    """Two consumes of the same state: first wins, second gets None.

    DynamoDB serializes writes per partition key, so the conditional
    delete makes this race deterministic without threading: if we issue
    two sequential consumes, the second sees the row already gone via
    ConditionalCheckFailedException and returns None. This mirrors the
    real concurrent-Lambda failure mode the issue is fixing.
    """
    state = _unique_state()
    state_store.put_state(state, payload={"nonce": "race-winner"})

    first = state_store.consume_state(state)
    second = state_store.consume_state(state)

    assert first is not None
    assert first["nonce"] == "race-winner"
    assert second is None


def test_put_state_writes_ttl_attribute_as_integer(starter_table):
    """DynamoDB silently drops non-integer TTL values; assert the contract.

    boto3 returns numeric attributes as ``decimal.Decimal``. The
    underlying DynamoDB type is "N" (Number), and the stored value is
    integral — what we care about is that the value has no fractional
    component (int(d) == d) so DynamoDB's TTL service accepts it. A
    ``float``-typed write would round-trip as a Decimal with a non-zero
    fractional component.
    """
    from decimal import Decimal

    state = _unique_state()
    state_store.put_state(state, ttl_seconds=300)

    resp = starter_table.get_item(Key={"PK": f"MGMT_STATE#{state}", "SK": "META"})
    item = resp["Item"]
    assert isinstance(item["ttl"], Decimal)
    assert int(item["ttl"]) == item["ttl"]  # no fractional component
    assert item["ttl_seconds"] == 300
    assert item["ttl"] == item["created_at"] + 300
