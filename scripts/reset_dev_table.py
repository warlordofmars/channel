#!/usr/bin/env python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Drop and recreate the local DDB table with the current schema.

DynamoDB Local (used by ``inv dev``) runs in-memory, so the table
disappears on every container restart. ``inv dev --seed`` doesn't
provision the table yet (the flag is a no-op stub). Run this script
after starting ``inv dev`` to (re)create ``channel`` with all four
GSIs. The schema itself lives in :mod:`channel._table_schema` and is
shared with the integration test fixture so the dev table stays in
lock-step with what the integration tests rely on.

DDB Local partitions data by AWS access key, so this script does NOT
override credentials — boto3 will pick up the same credentials FastAPI
uses (real credentials from ``~/.aws/credentials`` or env vars). If
the dev API can talk to DDB Local, this script can talk to the same
data store.

Usage::

    uv run python scripts/reset_dev_table.py
"""

from __future__ import annotations

import os

import boto3

from channel._table_schema import provision

TABLE = "channel"
ENDPOINT = "http://localhost:8000"


def main() -> None:
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get(
        "AWS_REGION", "us-east-1"
    )
    ddb = boto3.client("dynamodb", region_name=region, endpoint_url=ENDPOINT)

    try:
        ddb.delete_table(TableName=TABLE)
        ddb.get_waiter("table_not_exists").wait(TableName=TABLE)
        print(f"dropped {TABLE}")
    except ddb.exceptions.ResourceNotFoundException:
        print(f"{TABLE} did not exist; creating fresh")

    provision(ddb, TABLE)
    print(f"recreated {TABLE} with 4 GSIs (incl. ChatByIdIndex)")


if __name__ == "__main__":
    main()
