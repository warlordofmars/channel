# Copyright (c) 2026 John Carter. All rights reserved.
"""
Reference example for the ``dynamodb-item`` skill.

This file is documentation, not application code — it lives under
``.claude/skills/`` and is not imported by the running app or tests.
Copy the relevant block into ``src/channel/storage.py`` (or
``src/channel/models.py`` for the Pydantic side) and adapt the names
when introducing a new item type.

Mirrors every convention captured in ``SKILL.md``:

1. PK/SK pattern — ``TYPE#id`` prefix, ``META`` or composite SK
2. Prefix taxonomy — picks a non-colliding prefix and notes the
   CLAUDE.md update obligation
3. TTL semantics — ``ttl`` attribute, Unix timestamp integer
4. Hour-shard pattern — for the time-series / log-style item
5. GSI naming — sets ``GSIxPK`` to surface the item on the GSI
6. Table-name source — ``os.environ["CHANNEL_TABLE_NAME"]``,
   never hardcoded
7. Update CLAUDE.md — see the docstring banner near the bottom
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import boto3


# ---------------------------------------------------------------------------
# Table-name resolution (§6)
# ---------------------------------------------------------------------------
#
# The project contract is ``CHANNEL_TABLE_NAME`` (the ``CHANNEL_*`` prefix
# scopes config across the codebase). The Lambda runtime sets it via
# ``infra/stacks/channel_stack.py``; the integration suite sets it via
# ``tests/integration/conftest.py``; local dev provisions the ``channel``
# table via ``scripts/reset_dev_table.py``. Production ``_get_table()`` in
# ``src/channel/storage.py`` reads the var as *required* (no silent
# default) so a misconfigured environment fails loudly rather than
# writing to the wrong table.


def _get_table() -> Any:
    # ``CHANNEL_TABLE_NAME`` is required — mirrors ``_get_table()`` in
    # ``src/channel/storage.py``, which raises ``KeyError`` if it is unset
    # rather than falling back to a default table.
    table_name = os.environ["CHANNEL_TABLE_NAME"]
    endpoint_url = os.environ.get("DYNAMODB_ENDPOINT")  # set for DynamoDB Local
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    dynamodb = boto3.resource("dynamodb", region_name=region, endpoint_url=endpoint_url)
    return dynamodb.Table(table_name)


# ---------------------------------------------------------------------------
# Example A — Singleton item with TTL (§§1, 3)
# ---------------------------------------------------------------------------
#
# Pattern: a single record per entity, keyed ``TYPE#id`` / ``META``, with
# automatic expiry via the ``ttl`` attribute. Mirrors how ``DENY#`` and
# ``MGMT_STATE#`` are stored.


def put_session(session_id: str, user_id: str, ttl_seconds: int = 3600) -> None:
    """Persist a short-lived session record that expires automatically."""
    table = _get_table()
    table.put_item(
        Item={
            "PK": f"SESSION#{session_id}",  # new prefix — must be added to CLAUDE.md
            "SK": "META",
            "user_id": user_id,
            "created_at": int(time.time()),
            # TTL is the *absolute* Unix timestamp at which DynamoDB should
            # delete the row. Always an integer; never an ISO string.
            "ttl": int(time.time()) + ttl_seconds,
        }
    )


# ---------------------------------------------------------------------------
# Example B — Hour-sharded log item (§§1, 4)
# ---------------------------------------------------------------------------
#
# Pattern: time-series data partitioned by ``{date}#{hour}`` to spread writes
# across 24 partitions per day, with a composite SK that orders events
# lexicographically inside each partition. Mirrors ``LOG#`` and ``AUDIT#``.


def put_event(event_id: str, payload: dict[str, Any]) -> None:
    """Append an event to the activity log under the current hour partition."""
    table = _get_table()
    now = datetime.now(timezone.utc)
    table.put_item(
        Item={
            "PK": f"EVENT#{now:%Y-%m-%d}#{now:%H}",  # hour-sharded
            "SK": f"{int(now.timestamp())}#{event_id}",
            "event_id": event_id,
            "payload": payload,
        }
    )


# ---------------------------------------------------------------------------
# Example C — Item indexed on a GSI (§5)
# ---------------------------------------------------------------------------
#
# Pattern: set the ``GSIxPK`` attribute matching the GSI's slot. The item
# appears on that GSI; items without the attribute are omitted (sparse
# index). Adapted from how ``USER#`` rows surface on ``UserEmailIndex``
# via ``GSI4PK=EMAIL#{email}``.


def put_user(user_id: str, email: str, display_name: str) -> None:
    """Store a user record indexed by email on UserEmailIndex (GSI4)."""
    table = _get_table()
    table.put_item(
        Item={
            "PK": f"USER#{user_id}",
            "SK": "META",
            "GSI4PK": f"EMAIL#{email}",  # surfaces this item on UserEmailIndex
            "email": email,
            "display_name": display_name,
            "created_at": int(time.time()),
        }
    )


def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Look up a user by email via the UserEmailIndex GSI."""
    table = _get_table()
    resp = table.query(
        IndexName="UserEmailIndex",
        KeyConditionExpression="GSI4PK = :pk",
        ExpressionAttributeValues={":pk": f"EMAIL#{email}"},
    )
    items = resp.get("Items", [])
    return items[0] if items else None


# ---------------------------------------------------------------------------
# Example D — Retention env-var pattern (§3)
# ---------------------------------------------------------------------------
#
# Pattern: TTL window is operator-tunable via ``CHANNEL_<ITEM>_RETENTION_DAYS``
# with a sensible default. Mirrors ``CHANNEL_AUDIT_RETENTION_DAYS`` (default
# 365) for the audit-log item type.


def put_audit(event_id: str, actor_id: str, action: str) -> None:
    """Append an immutable audit entry with operator-configurable retention."""
    table = _get_table()
    now = datetime.now(timezone.utc)
    retention_days = int(os.environ.get("CHANNEL_AUDIT_RETENTION_DAYS", "365"))
    table.put_item(
        Item={
            "PK": f"AUDIT#{now:%Y-%m-%d}#{now:%H}",  # hour-sharded
            "SK": f"{int(now.timestamp())}#{event_id}",
            "actor_id": actor_id,
            "action": action,
            "ttl": int(now.timestamp()) + retention_days * 86400,
        }
    )


# ---------------------------------------------------------------------------
# Update CLAUDE.md too! (§7)
# ---------------------------------------------------------------------------
#
# Every new prefix introduced above (``SESSION#``, ``EVENT#``, ...) must be
# added to:
#
#   1. CLAUDE.md §"DynamoDB single table design"
#   2. .claude/skills/dynamodb-item/SKILL.md §2 prefix taxonomy
#
# These two tables are the discovery contract for every future agent. A
# stale entry means the next agent that adds an item won't see your prefix
# and may collide with it. Same PR, same diff, no follow-up.
