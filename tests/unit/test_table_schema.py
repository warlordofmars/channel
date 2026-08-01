# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the shared DDB Local table schema module."""

from __future__ import annotations

from unittest.mock import MagicMock

from channel._table_schema import (
    ATTRIBUTE_DEFINITIONS,
    GLOBAL_SECONDARY_INDEXES,
    KEY_SCHEMA,
    provision,
)


def test_attribute_definitions_cover_all_pk_sk_and_gsi_keys():
    names = {a["AttributeName"] for a in ATTRIBUTE_DEFINITIONS}
    assert names == {
        "PK",
        "SK",
        "GSI1PK",
        "GSI1SK",
        "GSI2PK",
        "GSI2SK",
        "GSI3PK",
        "GSI3SK",
        "GSI4PK",
        "GSI5PK",
        "GSI5SK",
        "owner_pk",
        "owner_sk",
    }
    # Every attribute is declared as a string — DDB rejects table creation
    # if a GSI references an attribute with a different scalar type.
    assert all(a["AttributeType"] == "S" for a in ATTRIBUTE_DEFINITIONS)


def test_key_schema_is_pk_hash_sk_range():
    assert KEY_SCHEMA == [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ]


def test_global_secondary_indexes_named_for_all_six_gsis():
    names = {gsi["IndexName"] for gsi in GLOBAL_SECONDARY_INDEXES}
    assert names == {
        "KeyIndex",
        "TagIndex",
        "UserEmailIndex",
        "ChatByIdIndex",
        "AssetOwnerIndex",
        "RefreshByUserIndex",
    }


def test_refresh_by_user_index_keyed_on_gsi5_slot():
    # #290 — the refresh-row lookup index takes the next free numbered
    # slot. GSI3/GSI4 are ChatByIdIndex/UserEmailIndex and
    # AssetOwnerIndex uses semantic names, so GSI5 was never claimed.
    gsi = next(g for g in GLOBAL_SECONDARY_INDEXES if g["IndexName"] == "RefreshByUserIndex")
    assert gsi["KeySchema"] == [
        {"AttributeName": "GSI5PK", "KeyType": "HASH"},
        {"AttributeName": "GSI5SK", "KeyType": "RANGE"},
    ]
    assert gsi["Projection"]["ProjectionType"] == "ALL"


def test_asset_owner_index_keyed_on_owner_pk_and_owner_sk():
    # #324 — the browse GSI uses the semantic ``owner_pk`` / ``owner_sk``
    # attribute names (not a GSI5PK slot) per the settled #321 design.
    gsi = next(g for g in GLOBAL_SECONDARY_INDEXES if g["IndexName"] == "AssetOwnerIndex")
    assert gsi["KeySchema"] == [
        {"AttributeName": "owner_pk", "KeyType": "HASH"},
        {"AttributeName": "owner_sk", "KeyType": "RANGE"},
    ]


def test_user_email_index_is_hash_only():
    # UserEmailIndex projects only ``GSI4PK`` (email) — no sort key, since
    # email-to-user is a 1:1 lookup. The other three GSIs are HASH+RANGE.
    gsi = next(g for g in GLOBAL_SECONDARY_INDEXES if g["IndexName"] == "UserEmailIndex")
    assert gsi["KeySchema"] == [{"AttributeName": "GSI4PK", "KeyType": "HASH"}]


def test_provision_creates_table_with_shared_schema_and_enables_ttl():
    client = MagicMock()

    provision(client, "channel-test")

    client.create_table.assert_called_once_with(
        TableName="channel-test",
        AttributeDefinitions=ATTRIBUTE_DEFINITIONS,
        KeySchema=KEY_SCHEMA,
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=GLOBAL_SECONDARY_INDEXES,
    )
    client.get_waiter.assert_called_once_with("table_exists")
    client.get_waiter.return_value.wait.assert_called_once_with(TableName="channel-test")
    client.update_time_to_live.assert_called_once_with(
        TableName="channel-test",
        TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
    )
