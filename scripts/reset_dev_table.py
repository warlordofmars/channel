#!/usr/bin/env python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Drop and recreate the local DDB table with the current schema.

DynamoDB Local (used by ``inv dev``) runs in-memory, so the table
disappears on every container restart. ``inv dev --seed`` doesn't
provision the table yet (the flag is a no-op stub). Run this script
after starting ``inv dev`` to (re)create ``channel`` with all four
GSIs — the schema must mirror ``tests/integration/conftest.py`` so the
dev table stays in lock-step with what the integration tests rely on.

DDB Local partitions data by AWS access key, so this script does NOT
override credentials — boto3 will pick up the same credentials FastAPI
uses (real credentials from ``~/.aws/credentials`` or env vars). If
the dev API can talk to DDB Local, this script can talk to the same
data store.

Usage::

    uv run python scripts/reset_dev_table.py
"""

from __future__ import annotations

import boto3

TABLE = "channel"
ENDPOINT = "http://localhost:8000"


def main() -> None:
    ddb = boto3.client(
        "dynamodb", region_name="us-east-1", endpoint_url=ENDPOINT
    )

    try:
        ddb.delete_table(TableName=TABLE)
        ddb.get_waiter("table_not_exists").wait(TableName=TABLE)
        print(f"dropped {TABLE}")
    except ddb.exceptions.ResourceNotFoundException:
        print(f"{TABLE} did not exist; creating fresh")

    ddb.create_table(
        TableName=TABLE,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"},
            {"AttributeName": "GSI3PK", "AttributeType": "S"},
            {"AttributeName": "GSI3SK", "AttributeType": "S"},
            {"AttributeName": "GSI4PK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
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
        ],
    )
    ddb.get_waiter("table_exists").wait(TableName=TABLE)
    ddb.update_time_to_live(
        TableName=TABLE,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
    )
    print(f"recreated {TABLE} with 4 GSIs (incl. ChatByIdIndex)")


if __name__ == "__main__":
    main()
