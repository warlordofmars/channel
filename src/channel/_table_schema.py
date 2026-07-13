# Copyright (c) 2026 John Carter. All rights reserved.
"""Shared DynamoDB table schema for local-dev provisioning.

The production table is created by CDK in :mod:`infra.stacks.channel_stack`.
DynamoDB Local — used by ``inv dev`` and the integration test suite — has
no CloudFormation, so each consumer would otherwise re-declare the schema
inline. This module is the single source of truth those consumers share:

* ``tests/integration/conftest.py`` — provisions ``channel-test`` for the
  integration suite (session-scoped fixture).
* ``scripts/reset_dev_table.py`` — drops and recreates ``channel`` in the
  developer's local DDB after ``inv dev`` restarts.

Both call :func:`provision`, which encapsulates the create-table +
wait-until-ready + enable-TTL sequence. The schema constants are exposed
separately for tests that need to assert on attribute names or GSI shape.
"""

from __future__ import annotations

from typing import Any

ATTRIBUTE_DEFINITIONS: list[dict[str, str]] = [
    {"AttributeName": "PK", "AttributeType": "S"},
    {"AttributeName": "SK", "AttributeType": "S"},
    {"AttributeName": "GSI1PK", "AttributeType": "S"},
    {"AttributeName": "GSI1SK", "AttributeType": "S"},
    {"AttributeName": "GSI2PK", "AttributeType": "S"},
    {"AttributeName": "GSI2SK", "AttributeType": "S"},
    {"AttributeName": "GSI3PK", "AttributeType": "S"},
    {"AttributeName": "GSI3SK", "AttributeType": "S"},
    {"AttributeName": "GSI4PK", "AttributeType": "S"},
    {"AttributeName": "owner_pk", "AttributeType": "S"},
    {"AttributeName": "owner_sk", "AttributeType": "S"},
]

KEY_SCHEMA: list[dict[str, str]] = [
    {"AttributeName": "PK", "KeyType": "HASH"},
    {"AttributeName": "SK", "KeyType": "RANGE"},
]

GLOBAL_SECONDARY_INDEXES: list[dict[str, Any]] = [
    {
        "IndexName": "KeyIndex",
        "KeySchema": [
            {"AttributeName": "GSI1PK", "KeyType": "HASH"},
            {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
        ],
        "Projection": {"ProjectionType": "ALL"},
    },
    {
        "IndexName": "TagIndex",
        "KeySchema": [
            {"AttributeName": "GSI2PK", "KeyType": "HASH"},
            {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
        ],
        "Projection": {"ProjectionType": "ALL"},
    },
    {
        "IndexName": "UserEmailIndex",
        "KeySchema": [{"AttributeName": "GSI4PK", "KeyType": "HASH"}],
        "Projection": {"ProjectionType": "ALL"},
    },
    {
        "IndexName": "ChatByIdIndex",
        "KeySchema": [
            {"AttributeName": "GSI3PK", "KeyType": "HASH"},
            {"AttributeName": "GSI3SK", "KeyType": "RANGE"},
        ],
        "Projection": {"ProjectionType": "ALL"},
    },
    {
        # #324 — cross-chat asset browsing. ``owner_pk`` / ``owner_sk``
        # (not GSI5PK/SK) match the settled #321 design: the attribute
        # names carry the ownership semantics that make the
        # workspace-tenancy migration a one-value swap.
        "IndexName": "AssetOwnerIndex",
        "KeySchema": [
            {"AttributeName": "owner_pk", "KeyType": "HASH"},
            {"AttributeName": "owner_sk", "KeyType": "RANGE"},
        ],
        "Projection": {"ProjectionType": "ALL"},
    },
]


def provision(client: Any, table_name: str) -> None:
    """Create ``table_name`` with the shared schema and enable TTL.

    Assumes any pre-existing table with that name has already been
    deleted by the caller — both consumers do their own drop step first
    because the safety semantics differ (the integration fixture has a
    hostname guard; the dev script doesn't need one).
    """
    client.create_table(
        TableName=table_name,
        AttributeDefinitions=ATTRIBUTE_DEFINITIONS,
        KeySchema=KEY_SCHEMA,
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=GLOBAL_SECONDARY_INDEXES,
    )
    client.get_waiter("table_exists").wait(TableName=table_name)
    # DynamoDB Local doesn't enforce TTL but the API call succeeds. We
    # set it for parity with the production schema.
    client.update_time_to_live(
        TableName=table_name,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
    )
