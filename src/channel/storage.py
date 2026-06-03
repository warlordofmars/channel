# Copyright (c) 2026 John Carter. All rights reserved.
"""DynamoDB persistence for chats and messages.

Single-table design with these row families:
- Chat-index row  ``PK=USER#{u}, SK=CHAT#{created_at}#{chat_id}``
- Message row     ``PK=CHAT#{chat_id}, SK=MSG#{created_at}#{msg_id}``

The chat-index row also projects onto the ``ChatByIdIndex`` GSI
(``GSI3PK=CHAT_ID#{chat_id}, GSI3SK=META``) so direct chat-id lookups
don't have to know the original ``created_at``.

Tests inject a fake table via the module-level ``_get_table`` symbol; in
production it returns the real boto3 ``Table`` resource.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from channel.models import Chat, Message, MessageRole, Prefs

_CHAT_INDEX_GSI = "ChatByIdIndex"
_DEFAULT_TITLE = "New chat"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _get_table() -> Any:  # pragma: no cover - tests replace this seam
    table_name = os.environ["STARTER_TABLE_NAME"]
    endpoint = os.environ.get("DYNAMODB_ENDPOINT")
    kwargs: dict[str, Any] = {"region_name": os.environ.get("AWS_DEFAULT_REGION", "us-east-1")}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.resource("dynamodb", **kwargs).Table(table_name)


def _chat_index_sk(created_at: str, chat_id: str) -> str:
    return f"CHAT#{created_at}#{chat_id}"


def _chat_index_item(chat: Chat) -> dict[str, Any]:
    return {
        "PK": f"USER#{chat.user_id}",
        "SK": _chat_index_sk(chat.created_at, chat.chat_id),
        "GSI3PK": f"CHAT_ID#{chat.chat_id}",
        "GSI3SK": "META",
        "chat_id": chat.chat_id,
        "user_id": chat.user_id,
        "title": chat.title,
        "created_at": chat.created_at,
        "last_message_at": chat.last_message_at,
        "last_user_preview": chat.last_user_preview,
        "model_default": chat.model_default,
        "message_count": chat.message_count,
        "archived": chat.archived,
    }


def create_chat(*, user_id: str, title: str | None, model_default: str) -> Chat:
    """Persist a new chat-index row and return the model."""

    now = _now_iso()
    chat = Chat(
        chat_id=str(uuid.uuid4()),
        user_id=user_id,
        title=title or _DEFAULT_TITLE,
        created_at=now,
        last_message_at=now,
        last_user_preview="",
        model_default=model_default,
        message_count=0,
        archived=False,
    )
    _get_table().put_item(Item=_chat_index_item(chat))
    return chat


def get_chat_by_id(chat_id: str) -> Chat | None:
    """Look up a chat-index row through the ``ChatByIdIndex`` GSI."""

    result = _get_table().query(
        IndexName=_CHAT_INDEX_GSI,
        KeyConditionExpression=Key("GSI3PK").eq(f"CHAT_ID#{chat_id}") & Key("GSI3SK").eq("META"),
    )
    items = result.get("Items") or []
    if not items:
        return None
    return _chat_from_item(items[0])


def list_chats_for_user(
    user_id: str,
    *,
    limit: int,
    cursor: Any | None,
    include_archived: bool = False,
) -> tuple[list[Chat], Any | None]:
    """List chats for a user, newest first, paginated by SK cursor.

    When ``include_archived`` is ``False`` (the default), a DDB
    ``FilterExpression`` drops archived rows server-side. The filter
    matches rows whose ``archived`` attribute is missing
    (older rows that pre-date the field) OR explicitly ``False``.

    Trade-off: DynamoDB applies ``FilterExpression`` *after* ``Limit``,
    so a page may return fewer than ``limit`` rows when archived chats
    sit in the queried window. We deliberately do NOT over-fetch and
    trim here — that would silently skip items between trimmed pages.
    Instead, the response stays a true "up to ``limit`` rows, here's
    the cursor"; callers that need exactly N visible rows should poll
    ``next_cursor`` until they've gathered enough. The SPA sidebar
    requests 50 rows by default which is well over the typical
    archived-ratio for active users.
    """

    kwargs: dict[str, Any] = {
        "KeyConditionExpression": (
            Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with("CHAT#")
        ),
        "Limit": limit,
        "ScanIndexForward": False,
    }
    if not include_archived:
        kwargs["FilterExpression"] = Attr("archived").not_exists() | Attr("archived").eq(False)
    if cursor:
        kwargs["ExclusiveStartKey"] = cursor
    result = _get_table().query(**kwargs)
    chats = [_chat_from_item(item) for item in (result.get("Items") or [])]
    next_cursor = result.get("LastEvaluatedKey")
    return chats, next_cursor


def put_message(
    *,
    chat_id: str,
    role: MessageRole,
    text: str,
    model: str | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> Message:
    """Persist a message row.  Returns the resulting Message model."""

    now = _now_iso()
    msg = Message(
        chat_id=chat_id,
        msg_id=str(uuid.uuid4()),
        role=role,
        text=text,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        artifacts=artifacts,
        attachments=attachments,
        created_at=now,
    )
    # SK = MSG#{created_at}#{msg_id}: microsecond-resolution timestamp
    # sorts chat turns chronologically; UUID suffix is a uniqueness
    # tiebreaker that's stable across concurrent Lambda containers (a
    # per-process counter would silently collide across warm instances).
    item = {
        "PK": f"CHAT#{chat_id}",
        "SK": f"MSG#{now}#{msg.msg_id}",
        "chat_id": chat_id,
        "msg_id": msg.msg_id,
        "role": msg.role.value,
        "text": msg.text,
        "model": msg.model,
        "input_tokens": msg.input_tokens,
        "output_tokens": msg.output_tokens,
        "artifacts": msg.artifacts,
        "attachments": msg.attachments,
        "created_at": msg.created_at,
    }
    _get_table().put_item(Item={k: v for k, v in item.items() if v is not None})
    return msg


def list_messages(
    chat_id: str, *, limit: int, cursor: Any | None
) -> tuple[list[Message], Any | None]:
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": (Key("PK").eq(f"CHAT#{chat_id}") & Key("SK").begins_with("MSG#")),
        "Limit": limit,
        "ScanIndexForward": True,
    }
    if cursor:
        kwargs["ExclusiveStartKey"] = cursor
    result = _get_table().query(**kwargs)
    msgs = [_message_from_item(item) for item in (result.get("Items") or [])]
    return msgs, result.get("LastEvaluatedKey")


def update_chat_index(
    *,
    user_id: str,
    chat: Chat,
    last_user_preview: str,
    delta_count: int,
    last_message_at: str,
) -> None:
    """Atomic-ish update to the chat-index row: preview + count + ts."""

    _get_table().update_item(
        Key={
            "PK": f"USER#{user_id}",
            "SK": _chat_index_sk(chat.created_at, chat.chat_id),
        },
        UpdateExpression=("SET last_user_preview = :p, last_message_at = :t ADD message_count :c"),
        ExpressionAttributeValues={
            # Cap on the write path. The model validator caps at construct time but
            # direct dict-passing bypasses the model, so this is the load-bearing cap
            # for callers that don't construct a full Chat first.
            ":p": last_user_preview[:120],
            ":t": last_message_at,
            ":c": delta_count,
        },
    )


def patch_chat(
    *,
    user_id: str,
    chat: Chat,
    title: str | None,
    archived: bool | None,
) -> None:
    sets: list[str] = []
    values: dict[str, Any] = {}
    if title is not None:
        sets.append("title = :title")
        values[":title"] = title
    if archived is not None:
        sets.append("archived = :archived")
        values[":archived"] = archived
    if not sets:
        return
    _get_table().update_item(
        Key={
            "PK": f"USER#{user_id}",
            "SK": _chat_index_sk(chat.created_at, chat.chat_id),
        },
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeValues=values,
    )


def delete_chat(*, user_id: str, chat: Chat) -> None:
    """Permanently delete a chat: all its message rows + the chat-index row.

    Message rows live at ``PK=CHAT#{chat_id}, SK begins_with MSG#`` and
    are paginated + batch-deleted in chunks of 25 (DDB batch-write
    limit). The chat-index row lives at
    ``PK=USER#{user_id}, SK=CHAT#{created_at}#{chat_id}`` and is a
    single delete.

    DDB ``DeleteItem`` is naturally idempotent — calling this twice for
    the same chat is safe (the second call finds nothing to delete and
    no-ops). The API layer should still call this only once per user
    action; idempotency is a defence-in-depth guarantee, not a feature
    to lean on.
    """
    table = _get_table()
    # 1. Paginate + batch-delete all message rows.
    last_evaluated_key: dict[str, Any] | None = None
    while True:
        query_kwargs: dict[str, Any] = {
            "KeyConditionExpression": (
                Key("PK").eq(f"CHAT#{chat.chat_id}") & Key("SK").begins_with("MSG#")
            ),
            "ProjectionExpression": "PK, SK",  # don't fetch payloads we'll just throw away
            "Limit": 25,  # bound memory per page; drives pagination in the while-loop
        }
        if last_evaluated_key:
            query_kwargs["ExclusiveStartKey"] = last_evaluated_key
        page = table.query(**query_kwargs)
        items = page.get("Items", [])
        if items:
            with table.batch_writer() as bw:
                for it in items:
                    bw.delete_item(Key={"PK": it["PK"], "SK": it["SK"]})
        last_evaluated_key = page.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    # 2. Delete the chat-index row.
    table.delete_item(
        Key={
            "PK": f"USER#{user_id}",
            "SK": _chat_index_sk(chat.created_at, chat.chat_id),
        }
    )


def reserve_idempotency_key(*, user_id: str, key: str) -> dict[str, Any] | None:
    """Reserve a key via ConditionalPut.  Returns stored payload if already used.

    Returns None on a fresh reservation (caller proceeds to produce the
    reply).  Returns the existing DDB item if the key has been used
    within the TTL window — the caller can short-circuit and replay
    the stored result.
    """

    now = int(time.time())
    ttl = now + 3600
    try:
        _get_table().put_item(
            Item={
                "PK": f"IDEMP#{user_id}",
                "SK": key,
                "reserved_at": now,
                "ttl": ttl,
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
        return None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code != "ConditionalCheckFailedException":
            raise
        return _get_table().get_item(Key={"PK": f"IDEMP#{user_id}", "SK": key}).get("Item")


def store_idempotency_result(*, user_id: str, key: str, payload: dict[str, Any]) -> None:
    """Store the produced result on the previously-reserved idempotency key."""

    _get_table().update_item(
        Key={"PK": f"IDEMP#{user_id}", "SK": key},
        # ``result`` is a DynamoDB reserved keyword; alias it via
        # ExpressionAttributeNames so the UpdateExpression doesn't 400.
        UpdateExpression="SET #r = :r",
        ExpressionAttributeNames={"#r": "result"},
        ExpressionAttributeValues={":r": payload},
    )


def delete_last_assistant_message(chat_id: str) -> Message | None:
    """Drop the most recent assistant message in a chat.

    Returns the deleted message, or None if the chat had no assistant
    messages.  Used by ``POST /api/chats/{id}/regenerate``.
    """

    msgs, _ = list_messages(chat_id, limit=50, cursor=None)
    for msg in reversed(msgs):
        if msg.role == MessageRole.ASSISTANT:
            _get_table().delete_item(
                Key={
                    "PK": f"CHAT#{chat_id}",
                    "SK": f"MSG#{msg.created_at}#{msg.msg_id}",
                }
            )
            return msg
    return None


def get_prefs(user_id: str) -> Prefs:
    """Return the user's prefs, or default Prefs if no row exists.

    Uses ``ConsistentRead=True`` so a GET issued immediately after a PUT
    from the same actor (typical cross-device sync flow: phone PUTs,
    laptop refreshes) returns the just-written value rather than a
    stale eventual-consistency replica.
    """

    result = _get_table().get_item(
        Key={"PK": f"USER#{user_id}", "SK": "PREFS"},
        ConsistentRead=True,
    )
    item = result.get("Item")
    if not item:
        return Prefs()
    return Prefs(**(item.get("prefs") or {}))


def put_prefs(user_id: str, updates: dict[str, Any]) -> Prefs:
    """Merge ``updates`` into the user's prefs row and return the merged Prefs.

    Validates the merged result against ``Prefs`` (so unknown keys raise).
    Initialises with defaults when no row exists.
    """

    current = get_prefs(user_id).model_dump()
    current.update(updates)
    merged = Prefs(**current)  # raises ValidationError on unknown / bad type
    _get_table().put_item(
        Item={
            "PK": f"USER#{user_id}",
            "SK": "PREFS",
            "prefs": merged.model_dump(),
        }
    )
    return merged


def _audit_retention_seconds() -> int:
    """Resolve the audit-log TTL from ``STARTER_AUDIT_RETENTION_DAYS``.

    Default 365 days, matching CLAUDE.md §"DynamoDB single table design".
    The env var is read at call time (not import time) so tests can vary
    retention windows without re-importing the module.
    """

    return int(os.environ.get("STARTER_AUDIT_RETENTION_DAYS", "365")) * 86400


def put_audit_event(
    *,
    event_type: str,
    actor_id: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append an immutable audit-log entry to the single table.

    Hour-sharded ``PK=AUDIT#{date}#{hour}`` with ``SK={timestamp}#{uuid}``
    per CLAUDE.md §"DynamoDB single table design" and the dynamodb-item
    skill. TTL comes from ``STARTER_AUDIT_RETENTION_DAYS`` (default 365);
    DynamoDB's TTL service requires an integer Unix timestamp under the
    table-configured ``ttl`` attribute.

    Returns the persisted item dict so callers can log / assert on the
    generated ``event_id`` and ``created_at``.
    """

    now = datetime.now(timezone.utc)
    event_id = str(uuid.uuid4())
    item: dict[str, Any] = {
        "PK": f"AUDIT#{now:%Y-%m-%d}#{now:%H}",
        "SK": f"{int(now.timestamp())}#{event_id}",
        "event_id": event_id,
        "event_type": event_type,
        "actor_id": actor_id,
        "created_at": now.isoformat(timespec="microseconds"),
        "ttl": int(now.timestamp()) + _audit_retention_seconds(),
    }
    if details:
        item["details"] = details
    _get_table().put_item(Item=item)
    return item


def _chat_from_item(item: dict[str, Any]) -> Chat:
    return Chat(
        chat_id=item["chat_id"],
        user_id=item["user_id"],
        title=item["title"],
        created_at=item["created_at"],
        last_message_at=item["last_message_at"],
        last_user_preview=item.get("last_user_preview", ""),
        model_default=item["model_default"],
        message_count=int(item.get("message_count", 0)),
        archived=bool(item.get("archived", False)),
    )


def _message_from_item(item: dict[str, Any]) -> Message:
    return Message(
        chat_id=item["chat_id"],
        msg_id=item["msg_id"],
        role=MessageRole(item["role"]),
        text=item["text"],
        model=item.get("model"),
        input_tokens=item.get("input_tokens"),
        output_tokens=item.get("output_tokens"),
        artifacts=item.get("artifacts"),
        attachments=item.get("attachments"),
        created_at=item["created_at"],
    )
