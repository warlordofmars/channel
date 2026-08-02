# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for the refresh-token row family (#290, epic #241).

These run against DynamoDB Local. The in-memory ``FakeTable`` unit suite
covers item shape and control flow, but three properties only the real
client can prove:

* **Conditional-update semantics.** ``FakeTable.update_item`` accepts
  ``ConditionExpression`` without evaluating it, so the race-safety of
  ``consume_refresh_token`` — the single claim the whole rotation design
  rests on — is untested until a real DynamoDB enforces
  ``#revoked = :live``.
* **``RefreshByUserIndex`` really exists and really is queryable.** A
  wrong ``IndexName`` or a GSI missing from the shared schema surfaces
  here as a ``ValidationException``; the fake ignores ``IndexName``
  entirely and would happily pass.
* **Type round-tripping.** DynamoDB returns Numbers as
  ``decimal.Decimal`` and booleans as ``BOOL``; a silently-stringified
  ``ttl`` would leave refresh rows immortal.

Each test uses a unique ``user_id`` so rows don't cross-pollinate (the
table is not cleaned between tests).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key

from channel import storage
from channel.models import RefreshConsumeOutcome, RefreshRevokeReason


def _user() -> str:
    return f"itest-refresh-{uuid.uuid4().hex[:12]}"


def _row(starter_table: Any, token_hash: str) -> dict[str, Any]:
    resp = starter_table.get_item(
        Key={"PK": f"REFRESH#{token_hash}", "SK": "META"}, ConsistentRead=True
    )
    return resp["Item"]


def test_mint_writes_a_row_whose_ttl_and_flags_survive_the_round_trip(starter_table) -> None:  # type: ignore[no-untyped-def]
    user_id = _user()
    raw, token = storage.mint_refresh_token(user_id=user_id, device_id="d-1")

    row = _row(starter_table, token.token_hash)

    assert row["type"] == "REFRESH"
    assert row["user_id"] == user_id
    assert row["device_id"] == "d-1"
    # The raw token must appear nowhere in the persisted row.
    assert raw not in str(row)
    assert row["token_hash"] == token.token_hash
    # DynamoDB hands Numbers back as Decimal and Strings back as str, so
    # the type assertion — not the value — is what catches a ttl written
    # as a string (which the TTL service ignores, leaving the row
    # immortal). ``int("1234")`` would happily succeed.
    assert isinstance(row["ttl"], Decimal)
    assert int(row["ttl"]) == int(storage._parse_iso_utc(token.absolute_expires_at).timestamp())
    assert isinstance(row["revoked"], bool)
    assert row["revoked"] is False


def test_refresh_row_is_reachable_through_the_refresh_by_user_index(starter_table) -> None:  # type: ignore[no-untyped-def]
    """Proves the GSI is present in the provisioned schema and that the
    row carries the GSI5PK/GSI5SK projection keys."""
    user_id = _user()
    _, token = storage.mint_refresh_token(user_id=user_id, device_id="d-1")

    result = starter_table.query(
        IndexName="RefreshByUserIndex",
        KeyConditionExpression=Key("GSI5PK").eq(f"REFRESH_USER#{user_id}"),
    )

    rows = result.get("Items") or []
    assert [r["token_hash"] for r in rows] == [token.token_hash]
    assert rows[0]["GSI5SK"].startswith(token.issued_at)


def test_index_sorts_a_device_history_oldest_to_newest(starter_table) -> None:  # type: ignore[no-untyped-def]
    """``GSI5SK={issued_at}#{hash prefix}`` is what lets #293 page a
    user's sessions in time order without a second index."""
    user_id = _user()
    raw, first = storage.mint_refresh_token(user_id=user_id, device_id="d-1")
    rotated = storage.consume_refresh_token(raw)
    assert rotated.token is not None

    rows = storage._query_user_refresh_rows(user_id)

    assert [r["token_hash"] for r in rows] == [first.token_hash, rotated.token.token_hash]


def test_consume_rotates_against_real_dynamodb(starter_table) -> None:  # type: ignore[no-untyped-def]
    user_id = _user()
    raw, original = storage.mint_refresh_token(user_id=user_id, device_id="d-1")

    result = storage.consume_refresh_token(raw)

    assert result.outcome == RefreshConsumeOutcome.OK
    assert result.token is not None
    old = _row(starter_table, original.token_hash)
    assert old["revoked"] is True
    assert old["revoked_reason"] == RefreshRevokeReason.ROTATED.value
    new = _row(starter_table, result.token.token_hash)
    assert new["revoked"] is False
    # The absolute deadline — and therefore the shared ttl — is carried
    # forward unchanged, so rotation can never walk the ceiling out.
    assert new["absolute_expires_at"] == old["absolute_expires_at"]
    assert int(new["ttl"]) == int(old["ttl"])


def test_two_consumes_of_one_token_serialize_on_the_conditional_update(starter_table) -> None:  # type: ignore[no-untyped-def]
    """The race the ``ConditionExpression`` exists to win.

    The second caller cannot also rotate: DynamoDB rejects its update
    with ``ConditionalCheckFailedException`` because ``revoked`` is no
    longer ``false``. Exactly one successor row exists afterwards.

    Every assertion below goes through ``_get_refresh_row`` — a
    strongly-consistent base-table point read — and none through
    ``RefreshByUserIndex``. Inferring "no live rows remain" from a GSI
    scan would be the exact mistake this module documents: the index is
    eventually consistent, so it can omit a freshly-minted successor
    and let the test pass while a live token still exists. The winner's
    own result hands us the successor's hash, so no enumeration is
    needed.
    """
    user_id = _user()
    raw, original = storage.mint_refresh_token(user_id=user_id, device_id="d-1")

    winner = storage.consume_refresh_token(raw)
    loser = storage.consume_refresh_token(raw)

    assert winner.outcome == RefreshConsumeOutcome.OK
    assert winner.token is not None
    assert loser.outcome == RefreshConsumeOutcome.REUSED
    assert loser.raw_token is None

    # Only the winner minted a successor, so exactly these two rows can
    # exist for this device — and both must now be dead.
    first = storage._get_refresh_row(original.token_hash)
    successor = storage._get_refresh_row(winner.token.token_hash)
    assert first is not None and successor is not None
    assert first.revoked and first.revoked_reason == RefreshRevokeReason.ROTATED
    assert successor.revoked and successor.revoked_reason == RefreshRevokeReason.REUSE_DETECTED


def test_reuse_detection_revokes_every_live_row_for_that_device(starter_table) -> None:  # type: ignore[no-untyped-def]
    user_id = _user()
    raw1, _ = storage.mint_refresh_token(user_id=user_id, device_id="d-1")
    step1 = storage.consume_refresh_token(raw1)
    assert step1.raw_token is not None
    step2 = storage.consume_refresh_token(step1.raw_token)
    assert step2.outcome == RefreshConsumeOutcome.OK
    _, laptop = storage.mint_refresh_token(user_id=user_id, device_id="d-2")

    replay = storage.consume_refresh_token(raw1)

    assert replay.outcome == RefreshConsumeOutcome.REUSED
    assert replay.revoked_count == 1
    phone_rows = [r for r in storage._query_user_refresh_rows(user_id, "d-1")]
    assert len(phone_rows) == 3
    assert all(r["revoked"] for r in phone_rows)
    # The other device is a separate family and must be unaffected.
    assert _row(starter_table, laptop.token_hash)["revoked"] is False


def test_revoke_refresh_token_kills_the_presented_device_family(starter_table) -> None:  # type: ignore[no-untyped-def]
    user_id = _user()
    raw, _ = storage.mint_refresh_token(user_id=user_id, device_id="d-1")
    _, other = storage.mint_refresh_token(user_id=user_id, device_id="d-2")

    assert storage.revoke_refresh_token(raw) == 1

    assert all(r["revoked"] for r in storage._query_user_refresh_rows(user_id, "d-1"))
    assert _row(starter_table, other.token_hash)["revoked"] is False
    # Second logout is a no-op, and an unknown token never errors.
    assert storage.revoke_refresh_token(raw) == 0
    assert storage.revoke_refresh_token("not-a-real-token") == 0


def test_revoke_all_user_refresh_tokens_spans_devices_via_the_index(starter_table) -> None:  # type: ignore[no-untyped-def]
    user_id = _user()
    bystander = _user()
    for device in ("d-1", "d-2", "d-3"):
        storage.mint_refresh_token(user_id=user_id, device_id=device)
    _, untouched = storage.mint_refresh_token(user_id=bystander, device_id="d-1")

    assert storage.revoke_all_user_refresh_tokens(user_id) == 3

    rows = storage._query_user_refresh_rows(user_id)
    assert len(rows) == 3
    assert {r["revoked_reason"] for r in rows} == {RefreshRevokeReason.USER_REVOKED.value}
    assert _row(starter_table, untouched.token_hash)["revoked"] is False


def test_a_revoked_token_cannot_be_consumed(starter_table) -> None:  # type: ignore[no-untyped-def]
    user_id = _user()
    raw, _ = storage.mint_refresh_token(user_id=user_id, device_id="d-1")
    storage.revoke_refresh_token(raw)

    result = storage.consume_refresh_token(raw)

    assert result.outcome == RefreshConsumeOutcome.REVOKED
    assert result.revoked_count == 0


# ----------------------------------------------------------------
# Sessions read + per-device revoke (#293)
# ----------------------------------------------------------------


def test_list_live_refresh_tokens_reads_every_device_off_the_index(starter_table) -> None:  # type: ignore[no-untyped-def]
    """The #293 list path against the real GSI. The fake table ignores
    ``IndexName`` entirely, so only this proves the sessions list is
    actually served by ``RefreshByUserIndex`` and not by luck."""
    user_id = _user()
    bystander = _user()
    for device in ("d-1", "d-2", "d-3"):
        storage.mint_refresh_token(user_id=user_id, device_id=device)
    storage.mint_refresh_token(user_id=bystander, device_id="d-1")

    rows = storage.list_live_refresh_tokens(user_id)

    assert {r.device_id for r in rows} == {"d-1", "d-2", "d-3"}
    assert all(r.user_id == user_id for r in rows)
    # Newest-first, which the index walk alone does not give us: every
    # refresh row shares SK="META", so the ordering is the helper's.
    assert [r.issued_at for r in rows] == sorted((r.issued_at for r in rows), reverse=True)


def test_list_live_refresh_tokens_shows_only_the_surviving_row_after_a_rotation(
    starter_table,  # type: ignore[no-untyped-def]
) -> None:
    """A rotated ancestor stays in the table for reuse detection but must
    not show up as a second session for the same device."""
    user_id = _user()
    raw, first = storage.mint_refresh_token(user_id=user_id, device_id="d-1")
    rotated = storage.consume_refresh_token(raw)
    assert rotated.token is not None

    rows = storage.list_live_refresh_tokens(user_id)

    assert len(storage._query_user_refresh_rows(user_id, "d-1")) == 2
    assert [r.token_hash for r in rows] == [rotated.token.token_hash]
    assert first.token_hash not in {r.token_hash for r in rows}


def test_list_live_refresh_tokens_drops_a_row_past_its_idle_window(starter_table) -> None:  # type: ignore[no-untyped-def]
    """Liveness is the consume path's rule, so a session the next refresh
    would reject must never be advertised as active."""
    user_id = _user()
    _, token = storage.mint_refresh_token(user_id=user_id, device_id="d-1")
    starter_table.update_item(
        Key={"PK": f"REFRESH#{token.token_hash}", "SK": "META"},
        UpdateExpression="SET idle_expires_at = :past",
        ExpressionAttributeValues={
            ":past": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(
                timespec="microseconds"
            )
        },
    )

    assert storage.list_live_refresh_tokens(user_id) == []


def test_revoke_device_refresh_tokens_ends_one_session_and_leaves_the_rest(
    starter_table,  # type: ignore[no-untyped-def]
) -> None:
    user_id = _user()
    _, phone = storage.mint_refresh_token(user_id=user_id, device_id="d-1")
    storage.mint_refresh_token(user_id=user_id, device_id="d-2")

    assert storage.revoke_device_refresh_tokens(user_id, "d-1") == 1

    assert [r.device_id for r in storage.list_live_refresh_tokens(user_id)] == ["d-2"]
    revoked = _row(starter_table, phone.token_hash)
    assert revoked["revoked"] is True
    assert revoked["revoked_reason"] == RefreshRevokeReason.USER_REVOKED.value
    # Idempotent: nothing live is left on that device to revoke twice.
    assert storage.revoke_device_refresh_tokens(user_id, "d-1") == 0


def test_revoke_device_refresh_tokens_is_scoped_to_the_owning_user(starter_table) -> None:  # type: ignore[no-untyped-def]
    """Two users can independently own the same ``device_id`` string; the
    GSI partition is the tenancy boundary, and the sessions API's 404
    rests on this."""
    owner = _user()
    other = _user()
    _, theirs = storage.mint_refresh_token(user_id=owner, device_id="same-id")

    assert storage.revoke_device_refresh_tokens(other, "same-id") == 0

    assert _row(starter_table, theirs.token_hash)["revoked"] is False
    assert [r.device_id for r in storage.list_live_refresh_tokens(owner)] == ["same-id"]
