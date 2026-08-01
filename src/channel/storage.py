# Copyright (c) 2026 John Carter. All rights reserved.
"""DynamoDB persistence for chats and messages.

Single-table design with these row families:
- Chat-index row  ``PK=USER#{u}, SK=CHAT#{created_at}#{chat_id}``
- Message row     ``PK=CHAT#{chat_id}, SK=MSG#{created_at}#{msg_id}``
- Refresh row     ``PK=REFRESH#{sha256(token)}, SK=META``

The chat-index row also projects onto the ``ChatByIdIndex`` GSI
(``GSI3PK=CHAT_ID#{chat_id}, GSI3SK=META``) so direct chat-id lookups
don't have to know the original ``created_at``. Refresh rows project
onto ``RefreshByUserIndex`` (``GSI5PK=REFRESH_USER#{user_id}``) so a
user's whole session set is one Query.

Tests inject a fake table via the module-level ``_get_table`` symbol; in
production it returns the real boto3 ``Table`` resource.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, ConditionBase, Key
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from channel.logging_config import fingerprint_id
from channel.models import (
    REFRESH_ABSOLUTE_LIFETIME_SECONDS,
    REFRESH_IDLE_TIMEOUT_SECONDS,
    Asset,
    Attachment,
    Chat,
    ChatMCPMode,
    ChatMCPSettings,
    Feedback,
    FeedbackKind,
    MCPServer,
    MCPServerAuthStatus,
    MCPServerAuthType,
    MCPToken,
    Message,
    MessageRole,
    Prefs,
    RefreshConsumeOutcome,
    RefreshConsumeResult,
    RefreshRevokeReason,
    RefreshToken,
)

logger = logging.getLogger(__name__)

_CHAT_INDEX_GSI = "ChatByIdIndex"

# Race guard for UpdateItem against rows that may have been deleted
# concurrently. Without this, DynamoDB silently upserts a partial
# ghost item containing only the touched fields. Callers wrap the
# update in try/except and swallow ``ConditionalCheckFailedException``.
_ATTRIBUTE_EXISTS_PK = "attribute_exists(PK)"
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


def _get_s3_client() -> Any:
    """Return a boto3 S3 client pinned to SigV4 (#196).

    SSE-KMS PUTs ``REQUIRE`` SigV4 — S3 rejects SigV2-signed presigned URLs
    with ``InvalidArgument: Requests specifying Server Side Encryption with
    AWS KMS managed keys require AWS Signature Version 4``. The bucket
    enforces SSE-KMS by default (per #173), so every presigned PUT we mint
    must use SigV4. ``Config(signature_version="s3v4")`` applies that
    pinning to every operation this client performs — presigned URLs,
    HEAD checks, deletes, tagging.
    """

    return boto3.client(
        "s3",
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(signature_version="s3v4"),
    )


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


def list_messages_page(
    chat_id: str, *, limit: int, before: Any | None = None
) -> tuple[list[Message], Any | None]:
    """Return one page of messages newest-first-paginated but returned in
    chronological order, for the UI conversation view (#270).

    Unlike ``list_messages`` (oldest-first forward paging), this reads from
    the NEWEST end so the initial conversation load always includes the
    most recent turns — ``list_messages`` + ``Limit`` returns the oldest N,
    which left long chats (>``limit`` messages) showing only their oldest
    window while the recent turns were unreachable from the SPA.

    ``before`` is the opaque cursor returned by a prior call; passing it
    fetches the next OLDER page (the SPA prepends each older page above the
    current head). The returned cursor is ``None`` once the start of the
    chat is reached.
    """
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": (Key("PK").eq(f"CHAT#{chat_id}") & Key("SK").begins_with("MSG#")),
        "Limit": limit,
        "ScanIndexForward": False,  # newest first
    }
    if before:
        kwargs["ExclusiveStartKey"] = before
    result = _get_table().query(**kwargs)
    msgs = [_message_from_item(item) for item in (result.get("Items") or [])]
    msgs.reverse()  # newest-first page → chronological for display
    return msgs, result.get("LastEvaluatedKey")


def list_recent_messages(chat_id: str, *, limit: int) -> list[Message]:
    """Return the most recent ``limit`` messages for a chat, in chronological order.

    Used by the agent-streaming path to seed ``Strands.Agent(messages=...)``
    and by the regenerate path to find the newest user/assistant turns.
    The cursorless newest-N read — the first page of ``list_messages_page``
    — distinct from ``list_messages``, which pages from the OLDEST end and
    combining that with ``Limit`` silently dropped the recent half of long
    chats (#244).
    """

    msgs, _ = list_messages_page(chat_id, limit=limit)
    return msgs


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

    # Read from the NEWEST end — ``list_messages`` + Limit returns the
    # oldest window, which in long chats never contains the last
    # assistant turn (#244).
    msgs = list_recent_messages(chat_id, limit=50)
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


_FEEDBACK_NOTE_MAX_CHARS = 1000


def put_message_feedback(
    *, chat_id: str, msg_id: str, kind: FeedbackKind, note: str | None
) -> Feedback | None:
    """Persist a thumbs-up / thumbs-down feedback record on a message row.

    Returns the persisted ``Feedback`` on success, or ``None`` when the
    message row does not exist under ``chat_id`` OR is not an assistant
    turn — the API layer turns that into a 404 so user-message-feedback
    attempts surface the same as msg_id-never-existed. Overwrites any
    prior feedback for the same message (issue #146).

    The message SK is ``MSG#{created_at}#{msg_id}`` and we don't know
    ``created_at`` from ``msg_id`` alone, so this paginates the chat's
    message rows to find the one whose ``msg_id`` matches. The lookup
    query uses ``ProjectionExpression`` to fetch only the PK/SK/msg_id/
    role attributes — message ``text`` and ``attachments`` can be large
    and we don't need them to locate the row.

    ``note`` is capped at ``_FEEDBACK_NOTE_MAX_CHARS`` here as
    defence-in-depth — the API layer's ``FeedbackRequest`` already
    rejects longer values with 422, but capping again ensures the
    storage helper stays safe if it's ever called from a non-HTTP
    path (e.g. an internal backfill or admin script).
    """

    table = _get_table()
    capped_note = note[:_FEEDBACK_NOTE_MAX_CHARS] if note is not None else None
    last_evaluated_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": (
                Key("PK").eq(f"CHAT#{chat_id}") & Key("SK").begins_with("MSG#")
            ),
            # Project only the attrs the lookup needs — message text /
            # attachments can be large and DDB charges for fetched bytes.
            # The role projection lets us reject user-message feedback
            # without a second round trip.
            "ProjectionExpression": "PK, SK, msg_id, #r",
            "ExpressionAttributeNames": {"#r": "role"},
            "Limit": 50,
        }
        if last_evaluated_key:
            kwargs["ExclusiveStartKey"] = last_evaluated_key
        page = table.query(**kwargs)
        for item in page.get("Items") or []:
            if item.get("msg_id") != msg_id:
                continue
            # Feedback is for assistant turns only — silently surface
            # user-message attempts as "not found" so the API stays at
            # 404 and chat/message existence isn't leaked.
            if item.get("role") != MessageRole.ASSISTANT.value:
                return None
            feedback = Feedback(
                kind=kind,
                note=capped_note,
                created_at=_now_iso(),
            )
            try:
                table.update_item(
                    Key={"PK": item["PK"], "SK": item["SK"]},
                    UpdateExpression="SET feedback = :f",
                    # Guard against a race where the message row is
                    # deleted between the query above and this update
                    # — DDB's UpdateItem is otherwise an upsert and
                    # would create a partial row containing only
                    # PK/SK/feedback, corrupting list_messages.
                    ConditionExpression=_ATTRIBUTE_EXISTS_PK,
                    ExpressionAttributeValues={":f": feedback.model_dump(mode="json")},
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code == "ConditionalCheckFailedException":
                    # Row was deleted after the query — caller sees
                    # the same surface as msg_id-never-existed.
                    return None
                raise
            return feedback
        last_evaluated_key = page.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
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
    feedback_raw = item.get("feedback")
    feedback: Feedback | None
    if feedback_raw:
        # Defensive hydration: a stale or partial schema (e.g. missing
        # required field, unknown kind value) should NOT take down the
        # whole list_messages call — surface as no-feedback instead so
        # the rest of the chat is still readable. Narrowly catch
        # validation / type errors so genuine programmer errors elsewhere
        # in this function still propagate up.
        try:
            feedback = Feedback(**feedback_raw)
        except (ValidationError, TypeError):
            feedback = None
    else:
        feedback = None
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
        feedback=feedback,
    )


# ----------------------------------------------------------------
# Attachments (#174) — file attachments + vision (epic #109)
# ----------------------------------------------------------------


def _attachment_sk(att_id: str) -> str:
    return f"ATTACHMENT#{att_id}"


def _attachment_item(att: Attachment) -> dict[str, Any]:
    """Serialise an Attachment to its DDB row shape.

    ``referenced_at`` is dropped when None — keeps the item slim and
    mirrors the put_message pattern of stripping null attrs.
    """

    item = {
        "PK": f"USER#{att.user_id}",
        "SK": _attachment_sk(att.id),
        "id": att.id,
        "user_id": att.user_id,
        "name": att.name,
        "mime": att.mime,
        "size_bytes": att.size_bytes,
        "s3_key": att.s3_key,
        "s3_bucket": att.s3_bucket,
        "checksum_sha256": att.checksum_sha256,
        "created_at": att.created_at,
        "referenced_at": att.referenced_at,
    }
    return {k: v for k, v in item.items() if v is not None}


def _attachment_from_item(item: dict[str, Any]) -> Attachment:
    return Attachment(
        id=item["id"],
        user_id=item["user_id"],
        name=item["name"],
        mime=item["mime"],
        size_bytes=int(item["size_bytes"]),
        s3_key=item["s3_key"],
        s3_bucket=item["s3_bucket"],
        checksum_sha256=item["checksum_sha256"],
        created_at=item["created_at"],
        referenced_at=item.get("referenced_at"),
    )


def put_attachment(att: Attachment) -> None:
    """Persist the canonical ATTACHMENT row at PK=USER#{u}, SK=ATTACHMENT#{id}."""

    _get_table().put_item(Item=_attachment_item(att))


def get_attachment(*, user_id: str, att_id: str) -> Attachment | None:
    """Look up an Attachment by (user_id, att_id). Returns None on miss."""

    result = _get_table().get_item(Key={"PK": f"USER#{user_id}", "SK": _attachment_sk(att_id)})
    item = result.get("Item")
    if not item:
        return None
    return _attachment_from_item(item)


def delete_attachment(*, user_id: str, att_id: str) -> None:
    """Delete the ATTACHMENT row. Idempotent (no-op if already gone)."""

    _get_table().delete_item(Key={"PK": f"USER#{user_id}", "SK": _attachment_sk(att_id)})


def mark_attachment_referenced(*, user_id: str, att_id: str) -> None:
    """Stamp ``referenced_at = now`` on the canonical ATTACHMENT row (#176).

    Called from the send path AFTER ``get_attachment_bytes`` returns
    success — the lifecycle rule on the attachments bucket targets
    objects still tagged ``unreferenced=1``; this marker is the
    DDB-side counterpart that ties an S3 object to at least one
    message reference. Doesn't touch other attributes (name, mime,
    etc.) — UpdateItem on a single attribute keeps the row's prior
    state intact.

    ``ConditionExpression=_ATTRIBUTE_EXISTS_PK`` guards the narrow
    window where a concurrent chat-delete cascade could remove the
    canonical row between the send-path ``get_attachment`` lookup and
    this stamp — without the condition, DynamoDB silently creates a
    ghost item with only ``PK``/``SK``/``referenced_at``. The race
    loss is harmless (the attachment is gone), so we swallow the
    conditional failure.
    """

    try:
        _get_table().update_item(
            Key={"PK": f"USER#{user_id}", "SK": _attachment_sk(att_id)},
            UpdateExpression="SET referenced_at = :now",
            ExpressionAttributeValues={":now": _now_iso()},
            ConditionExpression=_ATTRIBUTE_EXISTS_PK,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.warning(
                "attachment.mark_referenced_lost_race user_id=%s att_id=%s",
                user_id,
                att_id,
            )
            return
        raise


def get_attachment_bytes(att: Attachment) -> tuple[bytes | None, str | None]:
    """Fetch the raw S3 object bytes for ``att`` (#201).

    Returns ``(bytes, None)`` on success and ``(None, "<reason>")`` on
    any error. The dedicated ``"S3 object not found"`` reason for HTTP
    404 lets the structured-failure-block builder in #176 surface a
    user-actionable marker (re-upload) distinct from a generic
    permissions / network failure.

    Bedrock's Converse API supports ``s3Location`` references only for
    select models — Claude Opus 4.6 rejects them with
    ``ValidationException: This model doesn't support the s3Uri
    field``. The send path therefore fetches each attachment's bytes
    inline and passes ``{"bytes": ...}`` to Strands instead.

    Memory footprint is bounded by the #173 hard caps
    (5 × 20 MB = 100 MB max per turn), which fits inside the Lambda's
    512 MB. A single ``GetObject`` per attachment also subsumes the
    prior HEAD-verify roundtrip — 404 surfaces here just as it did from
    HeadObject before.
    """

    return _fetch_s3_object_bytes(bucket=att.s3_bucket, key=att.s3_key)


def _fetch_s3_object_bytes(*, bucket: str, key: str) -> tuple[bytes | None, str | None]:
    """Shared S3 GetObject → ``(bytes, None) | (None, reason)`` core.

    Extracted from :func:`get_attachment_bytes` so :func:`get_asset_bytes`
    (#324) reuses the identical failure vocabulary — the dedicated
    ``"S3 object not found"`` reason, the ``S3 error: <code>`` shape,
    and the mid-read ``StreamingBody`` failure conversion.
    """

    try:
        response = _get_s3_client().get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None, "S3 object not found"
        return None, f"S3 error: {code}" if code else "S3 error"

    # StreamingBody wraps an HTTP connection. A mid-read ``ReadTimeoutError``
    # / ``IncompleteReadError`` / socket failure must convert into the same
    # ``(None, reason)`` failure shape so the SSE generator can render a
    # labeled marker instead of 500-ing the stream. ``finally: close()``
    # releases the pooled connection on both success and failure (a
    # successful read leaves it benign-to-close anyway).
    body = response["Body"]
    try:
        return body.read(), None
    except (BotoCoreError, OSError) as exc:
        return None, f"S3 read error: {type(exc).__name__}"
    finally:
        with contextlib.suppress(Exception):
            body.close()


def delete_chat_attachments(*, chat_id: str, user_id: str) -> tuple[int, int]:
    """Cascade-delete the S3 objects + ATTACHMENT rows referenced by a chat.

    Returns ``(deleted_count, failed_count)``. Per-attachment failures
    are caught + logged so one bad attachment doesn't abort the rest
    of the cascade. Total failure (e.g. DDB query for messages raises)
    propagates to the caller — the chats API layer wraps the whole
    cascade in try/except + emits a CloudWatch counter
    (``ChatDeleteAttachmentWipeFailures``) per the chat-delete pattern.

    Deduplicates: an attachment referenced by N messages in the chat
    is deleted exactly once.

    S3 deletion runs BEFORE the DDB row delete so a partial failure
    leaves the canonical row in place — gives a future orphan-recovery
    job something to work with.
    """

    table = _get_table()
    s3 = _get_s3_client()
    att_ids: set[str] = set()
    last_evaluated_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": (
                Key("PK").eq(f"CHAT#{chat_id}") & Key("SK").begins_with("MSG#")
            ),
            "ProjectionExpression": "attachments",
        }
        if last_evaluated_key:
            kwargs["ExclusiveStartKey"] = last_evaluated_key
        page = table.query(**kwargs)
        for item in page.get("Items") or []:
            for snap in item.get("attachments") or []:
                if isinstance(snap, dict) and "id" in snap:
                    att_ids.add(snap["id"])
        last_evaluated_key = page.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    deleted = 0
    failed = 0
    for att_id in sorted(att_ids):
        att = get_attachment(user_id=user_id, att_id=att_id)
        if att is None:
            logger.warning(
                "attachment.cascade_missing_canonical chat_id=%s att_id=%s",
                chat_id,
                att_id,
            )
            failed += 1
            continue
        try:
            s3.delete_object(Bucket=att.s3_bucket, Key=att.s3_key)
        except ClientError as exc:
            logger.warning(
                "attachment.cascade_s3_delete_failed chat_id=%s att_id=%s",
                chat_id,
                att_id,
                extra={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=True,
            )
            failed += 1
            continue
        try:
            delete_attachment(user_id=user_id, att_id=att_id)
        except ClientError as exc:
            logger.warning(
                "attachment.cascade_ddb_delete_failed chat_id=%s att_id=%s",
                chat_id,
                att_id,
                extra={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=True,
            )
            failed += 1
            continue
        deleted += 1
    return deleted, failed


# ----------------------------------------------------------------
# Assets (#324) — unified chat assets (epic #321)
# ----------------------------------------------------------------

# Owner-GSI name + page bounds for ``list_assets_by_owner``. Same
# clamp policy as the #234 admin helpers: default 50, never more
# than 100.
_ASSET_OWNER_GSI = "AssetOwnerIndex"
_ASSET_PAGE_MAX = 100
_ASSET_DEFAULT_PAGE = 50

# Include-list projection for the owner-GSI browse query — every Asset
# attribute EXCEPT ``content`` (inline payloads are up to 100 KB each;
# a browse page must not fetch megabytes of text it will never render).
# All names are aliased through ExpressionAttributeNames because
# several (``owner``, ``source``) collide with DynamoDB reserved words.
_ASSET_LIST_PROJECTION_NAMES = {
    f"#a{i}": name
    for i, name in enumerate(
        (
            "PK",
            "SK",
            "asset_id",
            "chat_id",
            "owner",
            "kind",
            "title",
            "mime",
            "size_bytes",
            "origin",
            "source",
            "s3_bucket",
            "s3_key",
            "created_at",
            "updated_at",
        )
    )
}
_ASSET_LIST_PROJECTION = ", ".join(_ASSET_LIST_PROJECTION_NAMES)


def _asset_sk(created_at: str, asset_id: str) -> str:
    return f"ASSET#{created_at}#{asset_id}"


def _asset_item(asset: Asset) -> dict[str, Any]:
    """Serialise an Asset to its DDB row shape (#324).

    ``owner_pk`` / ``owner_sk`` project the row onto ``AssetOwnerIndex``
    for the cross-chat browse view. ``owner`` feeds the GSI key as a
    single attribute so the workspace-tenancy migration is one value
    swap (``user_id`` → ``{workspace_id}/{user_id}``), never a second
    tenancy field. None-valued payload attrs are dropped, mirroring
    ``_attachment_item``.
    """

    item = {
        "PK": f"CHAT#{asset.chat_id}",
        "SK": _asset_sk(asset.created_at, asset.asset_id),
        "owner_pk": f"ASSETOWNER#{asset.owner}",
        "owner_sk": f"{asset.created_at}#{asset.asset_id}",
        "asset_id": asset.asset_id,
        "chat_id": asset.chat_id,
        "owner": asset.owner,
        "kind": asset.kind,
        "title": asset.title,
        "mime": asset.mime,
        "size_bytes": asset.size_bytes,
        "origin": asset.origin,
        "source": asset.source,
        "content": asset.content,
        "s3_bucket": asset.s3_bucket,
        "s3_key": asset.s3_key,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }
    return {k: v for k, v in item.items() if v is not None}


def _asset_from_item(item: dict[str, Any]) -> Asset:
    return Asset(
        asset_id=item["asset_id"],
        chat_id=item["chat_id"],
        owner=item["owner"],
        kind=item["kind"],
        title=item["title"],
        mime=item["mime"],
        size_bytes=int(item["size_bytes"]),
        origin=item["origin"],
        source=dict(item["source"]),
        content=item.get("content"),
        s3_bucket=item.get("s3_bucket"),
        s3_key=item.get("s3_key"),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
    )


def _iter_chat_asset_items(chat_id: str) -> list[dict[str, Any]]:
    """Collect every ASSET row in a chat partition (paginated Query).

    Full items (no projection): every caller needs the payload
    coordinates — ``list_chat_assets`` hydrates models, the cascade
    needs ``s3_key`` for the S3 side, ``get_asset`` returns the whole
    asset. Per-chat asset counts are small (issue #324), so collecting
    the partition into memory is fine.
    """

    table = _get_table()
    items: list[dict[str, Any]] = []
    last_evaluated_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": (
                Key("PK").eq(f"CHAT#{chat_id}") & Key("SK").begins_with("ASSET#")
            ),
        }
        if last_evaluated_key:
            kwargs["ExclusiveStartKey"] = last_evaluated_key
        page = table.query(**kwargs)
        items.extend(page.get("Items") or [])
        last_evaluated_key = page.get("LastEvaluatedKey")
        if not last_evaluated_key:
            return items


def put_asset(asset: Asset) -> None:
    """Persist the ASSET row at ``PK=CHAT#{chat_id}, SK=ASSET#{created_at}#{asset_id}``.

    The producer (#326) constructs the :class:`~channel.models.Asset` —
    including ``asset_id`` (uuid4) and ``created_at`` / ``updated_at``
    timestamps — mirroring how the attachments finalize endpoint owns
    Attachment construction.
    """

    _get_table().put_item(Item=_asset_item(asset))


def put_asset_bytes(*, chat_id: str, asset_id: str, data: bytes, mime: str) -> tuple[str, str]:
    """Write an asset's payload bytes to S3, returning ``(bucket, key)`` (#326).

    Server-side producer counterpart to the presigned-PUT ingestion
    path: generated payloads (code-exec images, oversized fence
    bodies) land under the ``assets/chat/{chat_id}/{asset_id}`` prefix
    of the existing attachments bucket (epic #321 decision Q2 — no new
    bucket; prefix-scoped IAM grants landed with #324).
    ``ServerSideEncryption="aws:kms"`` matches the presign params so
    every object in the bucket rides the same CMK. No lifecycle
    ``Tagging`` — assets are chat-scoped rows reaped by the delete
    cascade / lazy expiry, not by the unreferenced-upload GC rule.

    Errors propagate — callers (``channel.agents.asset_producers``)
    wrap per-asset for fail-soft isolation.
    """

    bucket = os.environ["STARTER_ATTACHMENTS_BUCKET"]
    key = f"assets/chat/{chat_id}/{asset_id}"
    _get_s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=mime,
        ServerSideEncryption="aws:kms",
    )
    return bucket, key


def delete_asset_object(*, bucket: str, key: str) -> None:
    """Delete one produced S3 object by raw coordinates (#326).

    Compensating cleanup for the producer path: when
    :func:`put_asset_bytes` succeeded but the subsequent ASSET row
    write failed, the object has no row — which makes it invisible to
    both the chat-delete cascade and the lazy-expiry reap (each
    discovers objects via rows), and the ``assets/chat/*`` prefix has
    no lifecycle GC tag. Without this delete, transient DynamoDB
    errors would grow S3 unboundedly. Errors propagate; the caller
    (``channel.agents.asset_producers``) wraps best-effort.
    """

    _get_s3_client().delete_object(Bucket=bucket, Key=key)


def get_asset(*, chat_id: str, asset_id: str) -> Asset | None:
    """Look up one asset by ``(chat_id, asset_id)``. Returns None on miss.

    Queries the chat partition (``begins_with SK, "ASSET#"``) and
    matches on id rather than requiring ``created_at`` — per-chat asset
    counts are small, so the partition walk beats carrying a second
    lookup key through every caller (#324 design).
    """

    for item in _iter_chat_asset_items(chat_id):
        if item.get("asset_id") == asset_id:
            return _asset_from_item(item)
    return None


def list_chat_assets(chat_id: str) -> list[Asset]:
    """All assets in a chat, oldest first (SK order = chronological).

    Full-fidelity rows (inline ``content`` included) — the per-chat
    surface is small and #325's content endpoint reads through this
    path. The cross-chat browse view must use
    :func:`list_assets_by_owner` instead, which projects ``content``
    away.
    """

    return [_asset_from_item(item) for item in _iter_chat_asset_items(chat_id)]


def list_assets_by_owner(
    owner: str,
    *,
    limit: int = _ASSET_DEFAULT_PAGE,
    cursor: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Page of an owner's assets across chats, newest first (#324).

    Queries ``AssetOwnerIndex`` (``owner_pk = ASSETOWNER#{owner}``,
    ``ScanIndexForward=False``). Returns ``(rows, next_cursor)`` where
    ``rows`` are raw item dicts *without* the inline ``content``
    attribute (ProjectionExpression keeps up-to-100 KB payloads off the
    browse hot path — fetch content per asset via :func:`get_asset`)
    and ``next_cursor`` is the response's ``LastEvaluatedKey`` to feed
    back in opaquely. Raw dicts rather than models, following the #234
    admin-helper precedent: the #325 browse endpoint maps these onto
    card descriptors, and a content-less row cannot hydrate an
    :class:`~channel.models.Asset` (the model requires exactly one
    payload form). ``limit`` is clamped to ``[1, _ASSET_PAGE_MAX]``.
    """

    limit = max(1, min(limit, _ASSET_PAGE_MAX))
    kwargs: dict[str, Any] = {
        "IndexName": _ASSET_OWNER_GSI,
        "KeyConditionExpression": Key("owner_pk").eq(f"ASSETOWNER#{owner}"),
        "ScanIndexForward": False,
        "Limit": limit,
        "ProjectionExpression": _ASSET_LIST_PROJECTION,
        "ExpressionAttributeNames": dict(_ASSET_LIST_PROJECTION_NAMES),
    }
    if cursor:
        kwargs["ExclusiveStartKey"] = cursor
    page = _get_table().query(**kwargs)
    return list(page.get("Items") or []), page.get("LastEvaluatedKey")


def delete_asset(asset: Asset) -> None:
    """Delete one asset: the S3 object (when S3-backed) then the DDB row.

    S3 deletion runs BEFORE the row delete so a partial failure leaves
    the row in place for the lazy-expiry reap to retry — same ordering
    rationale as :func:`delete_chat_attachments`. Errors propagate;
    callers that need best-effort semantics (the cascade, the reap)
    wrap per-asset.

    Both S3 coordinates are checked (not just ``s3_key``) so an
    unvalidated instance — ``model_construct``-built or mutated after
    construction — can never reach boto3 with ``Bucket=None``, which
    would raise a ``ParamValidationError`` the callers' per-asset
    ``except ClientError`` isolation doesn't cover. Mirrors the
    payload-coordinates guard in :func:`get_asset_bytes`.
    """

    if asset.s3_bucket is not None and asset.s3_key is not None:
        _get_s3_client().delete_object(Bucket=asset.s3_bucket, Key=asset.s3_key)
    _get_table().delete_item(
        Key={
            "PK": f"CHAT#{asset.chat_id}",
            "SK": _asset_sk(asset.created_at, asset.asset_id),
        }
    )


def get_asset_bytes(asset: Asset) -> tuple[bytes | None, str | None]:
    """Fetch an asset's payload bytes (#324), mirroring ``get_attachment_bytes``.

    Inline assets return their ``content`` UTF-8-encoded without
    touching S3. S3-backed assets share the exact failure vocabulary
    of :func:`get_attachment_bytes` via :func:`_fetch_s3_object_bytes`
    — ``(bytes, None)`` on success, ``(None, "<reason>")`` on failure,
    with the dedicated ``"S3 object not found"`` reason for 404s.
    """

    if asset.content is not None:
        return asset.content.encode("utf-8"), None
    if asset.s3_bucket is None or asset.s3_key is None:
        # The model's exactly-one-payload validator makes this
        # unreachable for validated instances; guard anyway so a
        # ``model_construct``-built or manually-mutated instance fails
        # loudly in the established (None, reason) shape.
        return None, "asset has no payload coordinates"
    return _fetch_s3_object_bytes(bucket=asset.s3_bucket, key=asset.s3_key)


def delete_chat_assets(*, chat_id: str) -> tuple[int, int]:
    """Cascade-delete a chat's ASSET rows + their S3 objects (#324).

    Returns ``(deleted_count, failed_count)``. Mirrors
    :func:`delete_chat_attachments`: per-asset failures are caught +
    logged so one bad asset doesn't abort the rest; total failure
    (the partition Query raising) propagates to the API layer, which
    wraps the cascade and emits ``ChatDeleteAssetWipeFailures``.
    Unlike the attachments cascade there is no snapshot indirection —
    ASSET rows live on the chat partition itself, so the row set IS
    the delete set. No ``user_id`` parameter for the same reason.
    """

    deleted = 0
    failed = 0
    for item in _iter_chat_asset_items(chat_id):
        try:
            asset = _asset_from_item(item)
        except (ValidationError, KeyError):
            logger.warning(
                "asset.cascade_malformed_row chat_id=%s sk=%s",
                chat_id,
                item.get("SK"),
                exc_info=True,
            )
            failed += 1
            continue
        try:
            delete_asset(asset)
        except ClientError as exc:
            logger.warning(
                "asset.cascade_delete_failed chat_id=%s asset_id=%s",
                chat_id,
                asset.asset_id,
                extra={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=True,
            )
            failed += 1
            continue
        deleted += 1
    return deleted, failed


def reap_orphaned_assets(rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Best-effort reap of assets whose chat no longer exists (#324, Q5).

    ``rows`` are raw asset item dicts — typically a page from
    :func:`list_assets_by_owner` that the browse endpoint (#325 / #328)
    suspects of orphan-hood. Each candidate's chat liveness is
    (re)verified here via ``ChatByIdIndex`` (:func:`get_chat_by_id`,
    cached per distinct ``chat_id``) so a caller bug can never reap a
    living chat's assets. Orphans get the S3-object-then-row delete via
    :func:`delete_asset`; rows whose chat still lives are skipped and
    counted in neither bucket.

    Returns ``(reaped, failed)``. Callers report via
    ``record_asset_lazy_expiry_reaps`` — this helper emits nothing
    itself, matching the cascade's metrics-at-the-API-layer split.
    There is no scheduled sweeper in v1; this browse-time reap is the
    only expiry mechanism unless EMF shows sustained cascade failures.
    """

    reaped = 0
    failed = 0
    liveness: dict[str, bool] = {}
    for row in rows:
        try:
            asset = _asset_from_item(_restore_reap_row(row))
        except (ValidationError, KeyError):
            logger.warning("asset.reap_malformed_row sk=%s", row.get("SK"), exc_info=True)
            failed += 1
            continue
        alive = liveness.get(asset.chat_id)
        if alive is None:
            alive = get_chat_by_id(asset.chat_id) is not None
            liveness[asset.chat_id] = alive
        if alive:
            continue
        try:
            delete_asset(asset)
        except ClientError as exc:
            logger.warning(
                "asset.reap_delete_failed chat_id=%s asset_id=%s",
                asset.chat_id,
                asset.asset_id,
                extra={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=True,
            )
            failed += 1
            continue
        reaped += 1
    return reaped, failed


def _restore_reap_row(row: dict[str, Any]) -> dict[str, Any]:
    """Make a content-projected browse row hydratable for the reap.

    :func:`list_assets_by_owner` strips ``content``, so an inline
    asset's row arrives with neither payload form and would fail the
    Asset model's exactly-one-payload validation. The reap only needs
    the row key + S3 coordinates; substituting an empty ``content``
    for rows without S3 coordinates satisfies the model without
    fetching the real payload. Rows that already carry a payload form
    pass through untouched.
    """

    if "content" in row or "s3_key" in row:
        return row
    return {**row, "content": ""}


# ----------------------------------------------------------------
# MCP servers / tokens (#207) — registry + per-chat override
# ----------------------------------------------------------------


def _mcp_server_sk(server_id: str) -> str:
    return f"MCPSERVER#{server_id}"


def _mcp_token_sk(server_id: str) -> str:
    return f"MCPTOKEN#{server_id}"


def _mcp_chat_override_sk() -> str:
    return "MCPSERVERS#META"


def _mcp_server_item(server: MCPServer) -> dict[str, Any]:
    item: dict[str, Any] = {
        "PK": f"USER#{server.user_id}",
        "SK": _mcp_server_sk(server.server_id),
        "server_id": server.server_id,
        "user_id": server.user_id,
        "name": server.name,
        "url": server.url,
        "tool_prefix": server.tool_prefix,
        "auth_type": server.auth_type.value,
        "auth_status": server.auth_status.value,
        "globally_enabled": server.globally_enabled,
        "created_at": server.created_at,
        "updated_at": server.updated_at,
    }
    # Keep the item sparse: a static-token server has no DCR client, so
    # omit client_id entirely rather than storing DynamoDB's NULL type for
    # it. The read path uses ``item.get("client_id")``, which yields None
    # for an absent attribute — no NULL round-trip needed.
    if server.client_id is not None:
        item["client_id"] = server.client_id
    return item


def _mcp_server_from_item(item: dict[str, Any]) -> MCPServer:
    return MCPServer(
        server_id=item["server_id"],
        user_id=item["user_id"],
        name=item["name"],
        url=item["url"],
        # ``.get`` — a static-token row has no client_id attribute.
        client_id=item.get("client_id"),
        tool_prefix=item["tool_prefix"],
        # Default to oauth_dcr so pre-#375 rows (no auth_type attribute)
        # read back with today's behaviour.
        auth_type=MCPServerAuthType(item.get("auth_type", MCPServerAuthType.OAUTH_DCR.value)),
        auth_status=MCPServerAuthStatus(item["auth_status"]),
        globally_enabled=bool(item.get("globally_enabled", True)),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
    )


def create_mcp_server(
    *,
    user_id: str,
    name: str,
    url: str,
    tool_prefix: str,
    client_id: str | None = None,
    auth_type: MCPServerAuthType = MCPServerAuthType.OAUTH_DCR,
    auth_status: MCPServerAuthStatus = MCPServerAuthStatus.NEVER_AUTHED,
    globally_enabled: bool = True,
) -> MCPServer:
    """Persist a freshly-registered MCP server row.

    Caller supplies a normalized ``tool_prefix``. For the OAuth-DCR flow
    (the default) the caller passes the DCR-issued ``client_id`` and the
    row starts ``NEVER_AUTHED`` — the user completes the auth-code flow
    next and the callback handler flips this to ``ACTIVE``. For a
    static-token (PAT) server the caller passes
    ``auth_type=STATIC_TOKEN``, ``client_id=None``, and
    ``auth_status=ACTIVE`` (there is no auth-code step to complete).

    ``globally_enabled`` defaults to ``True`` (the historical registry
    behaviour). A featured-server registration of a read-write surface
    (GitHub, #277) passes ``False`` so write hands need an explicit
    per-chat enable.

    Enforces the data-model invariant at the storage boundary: an
    ``oauth_dcr`` server must carry a DCR-issued ``client_id``. Persisting
    an oauth_dcr row with ``client_id=None`` would later surface as 500s
    on the auth/refresh path, so reject it here. See #375 / Copilot.
    """
    if auth_type == MCPServerAuthType.OAUTH_DCR and client_id is None:
        raise ValueError("oauth_dcr MCP server requires a client_id")
    now = _now_iso()
    server = MCPServer(
        server_id=str(uuid.uuid4()),
        user_id=user_id,
        name=name,
        url=url,
        client_id=client_id,
        tool_prefix=tool_prefix,
        auth_type=auth_type,
        auth_status=auth_status,
        globally_enabled=globally_enabled,
        created_at=now,
        updated_at=now,
    )
    _get_table().put_item(Item=_mcp_server_item(server))
    return server


def get_mcp_server(*, user_id: str, server_id: str) -> MCPServer | None:
    result = _get_table().get_item(Key={"PK": f"USER#{user_id}", "SK": _mcp_server_sk(server_id)})
    item = result.get("Item")
    return _mcp_server_from_item(item) if item else None


def list_mcp_servers_for_user(user_id: str) -> list[MCPServer]:
    """List all registered MCP servers for one user. Newest first."""
    result = _get_table().query(
        KeyConditionExpression=(
            Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with("MCPSERVER#")
        ),
    )
    items = result.get("Items") or []
    servers = [_mcp_server_from_item(it) for it in items]
    # Sort newest first by created_at — list query has no implicit order.
    servers.sort(key=lambda s: s.created_at, reverse=True)
    return servers


def update_mcp_server(
    *,
    user_id: str,
    server_id: str,
    name: str | None = None,
    globally_enabled: bool | None = None,
) -> None:
    """Update an MCPSERVER row's caller-facing fields.

    ``ConditionExpression=_ATTRIBUTE_EXISTS_PK`` guards the race
    window where ``delete_mcp_server`` runs between the route's
    existence check and this update — without it, DynamoDB silently
    upserts and creates a ghost row containing only the touched
    fields, which crashes ``_mcp_server_from_item`` later. We swallow
    the conditional failure (the row is gone; the update is moot).
    See ``mark_attachment_referenced`` for the same pattern.
    """
    sets: list[str] = ["updated_at = :u"]
    values: dict[str, Any] = {":u": _now_iso()}
    if name is not None:
        sets.append("#n = :n")
        values[":n"] = name
    if globally_enabled is not None:
        sets.append("globally_enabled = :g")
        values[":g"] = globally_enabled
    kwargs: dict[str, Any] = {
        "Key": {"PK": f"USER#{user_id}", "SK": _mcp_server_sk(server_id)},
        "UpdateExpression": "SET " + ", ".join(sets),
        "ExpressionAttributeValues": values,
        "ConditionExpression": _ATTRIBUTE_EXISTS_PK,
    }
    # ``name`` is a DynamoDB reserved word; alias when we touch it.
    if name is not None:
        kwargs["ExpressionAttributeNames"] = {"#n": "name"}
    try:
        _get_table().update_item(**kwargs)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            # `extra={}` keeps Sonar's taint engine off the format-string
            # sink — user_id is server-validated (JWT sub) and server_id
            # is a DDB UUID, but both came in via the request.
            logger.warning(
                "mcp_server.update_lost_race",
                extra={
                    "user_id_hash": fingerprint_id(user_id),
                    "server_id_hash": fingerprint_id(server_id),
                },
            )
            return
        raise


def set_mcp_server_auth_status(
    *,
    user_id: str,
    server_id: str,
    status: MCPServerAuthStatus,
) -> None:
    """Promote / demote an MCPSERVER row's auth_status (e.g. EXPIRED
    after a token-resolution failure, ACTIVE after a successful
    callback).

    Same ``attribute_exists(PK)`` race guard as
    :func:`update_mcp_server` — a concurrent delete must not be able
    to resurrect the row as a partial ghost item containing only
    auth_status / updated_at.
    """
    try:
        _get_table().update_item(
            Key={"PK": f"USER#{user_id}", "SK": _mcp_server_sk(server_id)},
            UpdateExpression="SET auth_status = :s, updated_at = :u",
            ExpressionAttributeValues={":s": status.value, ":u": _now_iso()},
            ConditionExpression=_ATTRIBUTE_EXISTS_PK,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.warning(
                "mcp_server.auth_status_lost_race",
                extra={
                    "user_id_hash": fingerprint_id(user_id),
                    "server_id_hash": fingerprint_id(server_id),
                },
            )
            return
        raise


def delete_mcp_server(*, user_id: str, server_id: str) -> None:
    """Delete the MCPSERVER row + its sibling MCPTOKEN row.

    Idempotent — each delete_item no-ops if the row is already gone.
    Caller (API layer) is responsible for the best-effort revoke at the
    MCP server's token endpoint BEFORE this — but the revoke is not a
    correctness condition for this helper.

    Per-chat override rows (PK=CHAT#{chat_id}, SK=MCPSERVERS#META) that
    reference this server are NOT cleaned up — they degrade gracefully
    when the chassis resolver filters out unknown server IDs at
    list-build time (#207 chassis wiring, Task 8).
    """
    table = _get_table()
    table.delete_item(Key={"PK": f"USER#{user_id}", "SK": _mcp_server_sk(server_id)})
    table.delete_item(Key={"PK": f"USER#{user_id}", "SK": _mcp_token_sk(server_id)})


def put_mcp_token(
    *,
    user_id: str,
    server_id: str,
    access_token_ciphertext: bytes,
    refresh_token_ciphertext: bytes | None,
    expires_at: int,
    granted_scope: str,
) -> None:
    """Persist a token bundle. Ciphertext supplied by the caller.

    TTL is set to ``expires_at + 30 days`` as a hard upper bound for the
    orphan-row case where DELETE lost a race; the application's own
    expires_at check still drives refresh."""
    item: dict[str, Any] = {
        "PK": f"USER#{user_id}",
        "SK": _mcp_token_sk(server_id),
        "user_id": user_id,
        "server_id": server_id,
        "access_token_ciphertext": access_token_ciphertext,
        "expires_at": expires_at,
        "granted_scope": granted_scope,
        "updated_at": _now_iso(),
        "ttl": expires_at + (30 * 86400),
    }
    if refresh_token_ciphertext is not None:
        item["refresh_token_ciphertext"] = refresh_token_ciphertext
    _get_table().put_item(Item=item)


def get_mcp_token(*, user_id: str, server_id: str) -> MCPToken | None:
    result = _get_table().get_item(Key={"PK": f"USER#{user_id}", "SK": _mcp_token_sk(server_id)})
    item = result.get("Item")
    if not item:
        return None
    return MCPToken(
        server_id=item["server_id"],
        user_id=item["user_id"],
        access_token_ciphertext=bytes(item["access_token_ciphertext"]),
        refresh_token_ciphertext=(
            bytes(item["refresh_token_ciphertext"])
            if item.get("refresh_token_ciphertext") is not None
            else None
        ),
        expires_at=int(item["expires_at"]),
        granted_scope=item.get("granted_scope", ""),
        updated_at=item["updated_at"],
    )


def get_chat_mcp_settings(chat_id: str) -> ChatMCPSettings:
    """Read the chat's MCP override. Returns defaults if no row exists."""
    result = _get_table().get_item(Key={"PK": f"CHAT#{chat_id}", "SK": _mcp_chat_override_sk()})
    item = result.get("Item")
    if not item:
        return ChatMCPSettings(chat_id=chat_id)
    return ChatMCPSettings(
        chat_id=chat_id,
        mode=ChatMCPMode(item.get("mode", "inherit")),
        explicit_server_ids=list(item.get("explicit_server_ids") or []),
    )


def put_chat_mcp_settings(settings: ChatMCPSettings) -> None:
    _get_table().put_item(
        Item={
            "PK": f"CHAT#{settings.chat_id}",
            "SK": _mcp_chat_override_sk(),
            "mode": settings.mode.value,
            "explicit_server_ids": settings.explicit_server_ids,
            "updated_at": _now_iso(),
        }
    )


def deny_jti(jti: str, exp: int) -> None:
    """Add a management-JWT ``jti`` to the revocation denylist (#240).

    Writes a ``PK=DENY#{jti}, SK=META`` row whose DynamoDB ``ttl`` is the
    denied token's own ``exp``: the row self-prunes exactly when the token
    would have expired anyway, so the denylist stays bounded by the set of
    currently-live tokens rather than growing unbounded. A plain
    ``PutItem`` is naturally idempotent — re-revoking the same ``jti``
    (e.g. a logout retry) overwrites the row harmlessly.
    """

    _get_table().put_item(
        Item={
            "PK": f"DENY#{jti}",
            "SK": "META",
            "type": "DENY",
            "revoked_at": _now_iso(),
            "ttl": exp,
        }
    )


def is_jti_denied(jti: str) -> bool:
    """Return ``True`` if ``jti`` has been revoked via :func:`deny_jti`.

    A single **strongly-consistent** point ``GetItem``: revocation must
    observe the ``DENY`` row written by the *immediately* preceding logout,
    so an eventually-consistent read's replication window cannot briefly
    admit a just-revoked token (the same read-after-write reason
    :func:`get_prefs` reads consistently).

    ``require_mgmt_user`` only reaches this for tokens that already passed
    ``exp`` validation, so a row whose ``ttl`` has lapsed but which
    DynamoDB has not yet swept is never consulted here — the JWT itself
    would have been rejected first.
    """

    resp = _get_table().get_item(
        Key={"PK": f"DENY#{jti}", "SK": "META"},
        ConsistentRead=True,
    )
    return "Item" in resp


# ----------------------------------------------------------------
# Refresh-token rows (#290) — foundation of the epic-#241 rework
# ----------------------------------------------------------------

_REFRESH_USER_GSI = "RefreshByUserIndex"

# 32 bytes = 256 bits of entropy, per the epic's "opaque 256-bit random
# string (NOT a JWT)" decision. ``token_urlsafe`` renders that as 43
# URL-safe characters, so the token survives a cookie / JSON body / URL
# fragment untouched.
_REFRESH_TOKEN_BYTES = 32

# How much of the SHA-256 digest disambiguates the GSI sort key. The
# sort key is ``{issued_at}#{prefix}``; ``issued_at`` already has
# microsecond resolution, so 64 bits of hash is far more than enough to
# keep two same-instant mints from colliding.
_REFRESH_HASH_PREFIX_LEN = 16

# Safety valve on the per-user GSI walk. A user's refresh rows are
# bounded (one family per device, one row per rotation, all pruned by
# the shared 30-day ttl), so hitting this means something is wrong —
# log loudly rather than spin.
_REFRESH_QUERY_MAX_PAGES = 25

# Sweeps the reuse-detection cascade makes over a device's family.
# ``RefreshByUserIndex`` is eventually consistent (no ConsistentRead on
# a GSI), so a single pass can miss a row minted moments earlier. Two
# passes force a concurrent mint to hide inside the propagation window
# twice; see _revoke_refresh_family for what this does and does not
# guarantee. Only the breach path pays for the extra Query.
_REFRESH_REUSE_SWEEPS = 2

# Revoke only rows that are still live. ``revoked`` is aliased because
# expression attribute names are the cheap way to stay clear of
# DynamoDB's reserved-word list without having to audit it.
_REFRESH_STILL_LIVE = "attribute_exists(PK) AND #revoked = :live"


def _refresh_token_hash(raw_token: str) -> str:
    """SHA-256 hex digest of a raw refresh token — the row's identity.

    This is the ONLY form of the token that ever reaches storage. Plain
    SHA-256 (not a password KDF) is correct here: the input is 256 bits
    of CSPRNG output, so there is no dictionary to attack and stretching
    would only add latency to every refresh.
    """

    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _refresh_pk(token_hash: str) -> str:
    return f"REFRESH#{token_hash}"


def _refresh_gsi_pk(user_id: str) -> str:
    return f"REFRESH_USER#{user_id}"


def _refresh_gsi_sk(issued_at: str, token_hash: str) -> str:
    return f"{issued_at}#{token_hash[:_REFRESH_HASH_PREFIX_LEN]}"


def _refresh_item(token: RefreshToken) -> dict[str, Any]:
    """Render a :class:`RefreshToken` as its DynamoDB row.

    ``ttl`` is the absolute expiry as integer Unix seconds (DynamoDB's
    TTL service ignores anything else), so every row in a family —
    including the revoked ancestors kept around for reuse detection —
    disappears at the same moment the session could no longer have been
    refreshed anyway.
    """

    item: dict[str, Any] = {
        "PK": _refresh_pk(token.token_hash),
        "SK": "META",
        "GSI5PK": _refresh_gsi_pk(token.user_id),
        "GSI5SK": _refresh_gsi_sk(token.issued_at, token.token_hash),
        "type": "REFRESH",
        "token_hash": token.token_hash,
        "user_id": token.user_id,
        "device_id": token.device_id,
        "issued_at": token.issued_at,
        "last_used_at": token.last_used_at,
        "absolute_expires_at": token.absolute_expires_at,
        "idle_expires_at": token.idle_expires_at,
        "revoked": token.revoked,
        "ttl": int(_parse_iso_utc(token.absolute_expires_at).timestamp()),
    }
    if token.revoked_reason is not None:
        item["revoked_reason"] = token.revoked_reason.value
    if token.revoked_at is not None:
        item["revoked_at"] = token.revoked_at
    return item


def _refresh_from_item(item: dict[str, Any]) -> RefreshToken:
    reason = item.get("revoked_reason")
    return RefreshToken(
        token_hash=item["token_hash"],
        user_id=item["user_id"],
        device_id=item["device_id"],
        issued_at=item["issued_at"],
        last_used_at=item["last_used_at"],
        absolute_expires_at=item["absolute_expires_at"],
        idle_expires_at=item["idle_expires_at"],
        revoked=bool(item.get("revoked", False)),
        revoked_reason=RefreshRevokeReason(reason) if reason else None,
        revoked_at=item.get("revoked_at"),
    )


def _get_refresh_row(token_hash: str) -> RefreshToken | None:
    """Strongly-consistent point-read of one refresh row.

    ``ConsistentRead`` for the same reason :func:`is_jti_denied` uses it:
    a rotation that just wrote ``revoked=True`` must be visible to the
    very next presentation of that token, or an eventually-consistent
    replica would silently hand an attacker the replay window that
    rotation exists to close.
    """

    resp = _get_table().get_item(
        Key={"PK": _refresh_pk(token_hash), "SK": "META"},
        ConsistentRead=True,
    )
    item = resp.get("Item")
    return _refresh_from_item(item) if item else None


def _mark_refresh_revoked(token_hash: str, reason: RefreshRevokeReason) -> bool:
    """Atomically flip one live refresh row to revoked. Returns success.

    The conditional ``update_item`` is what makes consume race-safe:
    ``#revoked = :live`` means exactly one concurrent caller can win the
    transition out of the live state, and the losers get
    ``ConditionalCheckFailedException`` rather than both believing they
    rotated the same token. ``attribute_exists(PK)`` additionally stops
    DynamoDB from resurrecting a TTL-swept row as a ghost item holding
    only the attributes this expression touches.

    ``False`` means "the row was already not-live" (gone, or someone
    else won) — never an error the caller needs to handle.
    """

    try:
        _get_table().update_item(
            Key={"PK": _refresh_pk(token_hash), "SK": "META"},
            UpdateExpression=(
                "SET #revoked = :revoked, revoked_reason = :reason, revoked_at = :now"
            ),
            ExpressionAttributeNames={"#revoked": "revoked"},
            ExpressionAttributeValues={
                ":revoked": True,
                ":reason": reason.value,
                ":now": _now_iso(),
                ":live": False,
            },
            ConditionExpression=_REFRESH_STILL_LIVE,
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def _query_user_refresh_rows(user_id: str, device_id: str | None = None) -> list[dict[str, Any]]:
    """All refresh rows for a user (optionally one device) via the GSI.

    ``device_id`` narrows with a ``FilterExpression`` rather than a
    sharper key: the epic pins the sort key to ``{issued_at}#{prefix}``
    so the per-user session list (#293) reads newest-first without a
    second index. A user's partition holds at most a few hundred rows,
    so filtering server-side costs a negligible amount of read capacity
    and keeps the index single-purpose.

    **This read is eventually consistent and cannot be made otherwise.**
    DynamoDB rejects ``ConsistentRead=True`` on a global secondary
    index, so — unlike :func:`_get_refresh_row`, which reads the base
    table strongly — a row written moments ago may not be projected
    onto the index yet and will be absent here. Every caller revokes
    what it finds, which makes family revocation **best-effort with
    respect to concurrent mints**. See :func:`_revoke_refresh_family`
    for how that window is narrowed and what remains open.
    """

    kwargs: dict[str, Any] = {
        "IndexName": _REFRESH_USER_GSI,
        "KeyConditionExpression": Key("GSI5PK").eq(_refresh_gsi_pk(user_id)),
    }
    if device_id is not None:
        kwargs["FilterExpression"] = Attr("device_id").eq(device_id)

    rows: list[dict[str, Any]] = []
    cursor: dict[str, Any] | None = None
    for _ in range(_REFRESH_QUERY_MAX_PAGES):
        page = _get_table().query(**({**kwargs, "ExclusiveStartKey": cursor} if cursor else kwargs))
        rows.extend(page.get("Items") or [])
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return rows
    logger.warning(
        "refresh.user_query_truncated user=%s pages=%d",
        fingerprint_id(user_id),
        _REFRESH_QUERY_MAX_PAGES,
    )
    return rows


def _revoke_refresh_rows(rows: list[dict[str, Any]], reason: RefreshRevokeReason) -> int:
    """Revoke every still-live row in ``rows``; return how many flipped.

    Rows already revoked are skipped without a write, and a row that
    loses the conditional race simply doesn't count — either way the
    post-condition is the same: none of these rows is usable afterwards.
    """

    revoked = 0
    for row in rows:
        if row.get("revoked"):
            continue
        if _mark_refresh_revoked(row["token_hash"], reason):
            revoked += 1
    return revoked


def _revoke_refresh_family(
    user_id: str,
    device_id: str | None,
    reason: RefreshRevokeReason,
    *,
    sweeps: int = 1,
) -> int:
    """Query a user's (or one device's) refresh rows and revoke the live ones.

    Returns the number of rows that flipped from live to revoked.

    **Known window — read this before relying on the post-condition.**
    The row set comes from ``RefreshByUserIndex``, and GSI reads are
    eventually consistent by construction (DynamoDB does not accept
    ``ConsistentRead`` on an index). A row minted between the last index
    propagation and this query is invisible here and therefore survives.
    Concretely: a rotation racing a revoke can leave the successor row
    live, because its rotated ancestor is skipped (already revoked) and
    the successor hasn't reached the index yet.

    ``sweeps`` narrows that window for the security-critical caller —
    a second pass runs after the first pass's writes have completed, so
    a concurrent mint has to land inside the propagation window *twice*
    to survive. It does not close the window, and no amount of sweeping
    would; closing it needs a strongly-consistent family marker read on
    the consume path, which belongs with the per-device session row
    #293 introduces. Until then the honest contract is **best-effort
    with respect to concurrent mints**, and the bound on the damage is
    that any surviving row still expires at the family's unchanged
    ``absolute_expires_at``.
    """

    revoked = 0
    for _ in range(sweeps):
        revoked += _revoke_refresh_rows(_query_user_refresh_rows(user_id, device_id), reason)
    return revoked


def mint_refresh_token(
    *,
    user_id: str,
    device_id: str,
    absolute_expires_at: str | None = None,
) -> tuple[str, RefreshToken]:
    """Create a refresh row for ``(user_id, device_id)``.

    Returns ``(raw_token, row)``. The raw token is returned and never
    stored — the row keys off its SHA-256 digest — so this return value
    is the single point in the system where the plaintext exists.
    Callers must hand it straight to the client and drop it.

    ``absolute_expires_at`` carries a family's original deadline across
    a rotation. Left ``None`` (the fresh-login case) it defaults to
    :data:`REFRESH_ABSOLUTE_LIFETIME_SECONDS` from now. Rotation passes
    the previous row's value so refreshing can never walk the absolute
    lifetime forward — that property is the whole point of having an
    absolute window alongside the idle one.
    """

    raw_token = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="microseconds")
    absolute = absolute_expires_at or (
        now + timedelta(seconds=REFRESH_ABSOLUTE_LIFETIME_SECONDS)
    ).isoformat(timespec="microseconds")
    token = RefreshToken(
        token_hash=_refresh_token_hash(raw_token),
        user_id=user_id,
        device_id=device_id,
        issued_at=now_iso,
        last_used_at=now_iso,
        absolute_expires_at=absolute,
        idle_expires_at=(now + timedelta(seconds=REFRESH_IDLE_TIMEOUT_SECONDS)).isoformat(
            timespec="microseconds"
        ),
    )
    _get_table().put_item(Item=_refresh_item(token))
    return raw_token, token


def consume_refresh_token(raw_token: str) -> RefreshConsumeResult:
    """Exchange a refresh token for its successor — hard rotation (#290).

    A token is consumable exactly once. On success the presented row is
    flipped to ``revoked`` with reason
    :attr:`~channel.models.RefreshRevokeReason.ROTATED` via a conditional
    ``update_item`` and a fresh row is minted carrying the family's
    unchanged ``absolute_expires_at`` and a renewed idle window.

    **Reuse detection.** Presenting a token that was already rotated is
    the OAuth 2.1 breach signal (RFC 9700 §4.14.2): either the client
    replayed it or an attacker captured it, and the server cannot tell
    which. The response is to revoke the entire device token-family, so
    both the legitimate holder and the attacker are forced back through
    login. Rows revoked for any *other* reason (logout, an earlier reuse
    cascade) return :attr:`RefreshConsumeOutcome.REVOKED` instead — that
    family is already dead and re-flagging it would inflate the #294
    breach counter with noise.

    The conditional-update loser lands on the same reuse path
    deliberately. Two simultaneous consumes of one token are
    indistinguishable at the server from a replay microseconds apart,
    and epic #241 Q1 settles the trade-off in favour of hard rotation on
    the server with single-flight in the SPA (#295) preventing the race
    client-side. Treating the loser as a rejection would leave a real
    replay silently tolerated whenever it arrived fast enough.

    Both expiry columns are enforced, absolute before idle, so a session
    that blew through its 30-day ceiling reports that rather than
    whichever bound happened to be checked first.
    """

    token_hash = _refresh_token_hash(raw_token)
    row = _get_refresh_row(token_hash)
    if row is None:
        return RefreshConsumeResult(outcome=RefreshConsumeOutcome.NOT_FOUND)

    if row.revoked:
        if row.revoked_reason != RefreshRevokeReason.ROTATED:
            return RefreshConsumeResult(outcome=RefreshConsumeOutcome.REVOKED)
        return _refresh_reuse_response(row)

    now = datetime.now(timezone.utc)
    if now >= _parse_iso_utc(row.absolute_expires_at):
        return RefreshConsumeResult(outcome=RefreshConsumeOutcome.EXPIRED_ABSOLUTE)
    if now >= _parse_iso_utc(row.idle_expires_at):
        return RefreshConsumeResult(outcome=RefreshConsumeOutcome.EXPIRED_IDLE)

    if not _mark_refresh_revoked(token_hash, RefreshRevokeReason.ROTATED):
        # Lost the race to a concurrent consume of the same token — see
        # the docstring: indistinguishable from a replay, so it takes
        # the reuse path.
        return _refresh_reuse_response(row)

    new_raw, new_row = mint_refresh_token(
        user_id=row.user_id,
        device_id=row.device_id,
        absolute_expires_at=row.absolute_expires_at,
    )
    return RefreshConsumeResult(
        outcome=RefreshConsumeOutcome.OK,
        raw_token=new_raw,
        token=new_row,
    )


def _refresh_reuse_response(row: RefreshToken) -> RefreshConsumeResult:
    """Revoke the presented row's device family and report the reuse.

    Uses :data:`_REFRESH_REUSE_SWEEPS` passes rather than one: this is
    the breach path, so it is worth an extra Query on a small partition
    to narrow the eventually-consistent gap described in
    :func:`_revoke_refresh_family`.
    """

    revoked = _revoke_refresh_family(
        row.user_id,
        row.device_id,
        RefreshRevokeReason.REUSE_DETECTED,
        sweeps=_REFRESH_REUSE_SWEEPS,
    )
    logger.warning(
        "refresh.reuse_detected user=%s device=%s revoked_rows=%d",
        fingerprint_id(row.user_id),
        fingerprint_id(row.device_id),
        revoked,
    )
    return RefreshConsumeResult(
        outcome=RefreshConsumeOutcome.REUSED,
        revoked_count=revoked,
    )


def revoke_refresh_token(raw_token: str) -> int:
    """Revoke the presented token's whole device token-family (logout).

    Returns the number of rows that flipped from live to revoked — ``0``
    for an unknown token, which keeps logout idempotent and leaks
    nothing about whether the token ever existed.

    Family-wide rather than row-wide on purpose: a device's session is
    the family, not any single row, and revoking only the presented row
    would leave a concurrently-rotated successor alive and the user
    still signed in on a device they just signed out of.

    Subject to the eventually-consistent index window documented on
    :func:`_revoke_refresh_family` — a single sweep here, because a
    logout racing a rotation from the same device is a self-inflicted
    ordering problem, not an adversarial one.
    """

    row = _get_refresh_row(_refresh_token_hash(raw_token))
    if row is None:
        return 0
    return _revoke_refresh_family(row.user_id, row.device_id, RefreshRevokeReason.LOGOUT)


def revoke_all_user_refresh_tokens(user_id: str) -> int:
    """Revoke every live refresh row for ``user_id`` ("sign out everywhere").

    Reads the user's rows off ``RefreshByUserIndex`` and returns how
    many were still live. Already-revoked rows are left untouched so
    their original reason (and the reuse-detection signal that depends
    on it) survives.

    Subject to the eventually-consistent index window documented on
    :func:`_revoke_refresh_family`: a device that mints a token in the
    same instant may not appear in the sweep.
    """

    return _revoke_refresh_family(user_id, None, RefreshRevokeReason.USER_REVOKED)


# ----------------------------------------------------------------
# Admin read helpers (#234) — minimum-viable admin UI (epic #233)
# ----------------------------------------------------------------

# Hard cap on caller-supplied page sizes for the admin read helpers.
# v0.1 soft-cap decision from #234: default 50, never more than 100.
_ADMIN_PAGE_MAX = 100
_ADMIN_DEFAULT_PAGE = 50

# Items *evaluated* per DDB request inside the internal Scan / Query
# loops below. DynamoDB applies ``FilterExpression`` AFTER ``Limit``,
# so this bounds read cost per round trip, not rows returned.
# Module-level (not a default arg) so integration tests can shrink it
# to force real LastEvaluatedKey pagination against DynamoDB Local.
_ADMIN_SCAN_PAGE_LIMIT = 100

# Per-call bound on the number of Scan round trips ``scan_users`` makes
# while hunting for sparse USER#META rows. With the page limit above
# this caps one call at 2 500 evaluated items; the returned cursor lets
# callers continue. ``derive_users_from_chat_index`` and
# ``count_active_users`` are exempt — they aggregate, and a partial
# walk would return silently-wrong numbers (documented per helper).
_ADMIN_SCAN_MAX_PAGES = 25

# ``list_audit_events_for_actor`` walks at most this many hourly
# AUDIT#{date}#{hour} shards (168 = 7 days), newest first.
_AUDIT_SHARD_WALK_MAX_HOURS = 168


def _clamp_admin_limit(limit: int) -> int:
    return max(1, min(limit, _ADMIN_PAGE_MAX))


def _parse_iso_utc(value: str) -> datetime:
    """Parse an ISO-8601 string into an aware UTC datetime.

    Accepts the trailing-``Z`` form (``fromisoformat`` only understands
    it from Python 3.11; this repo's floor is 3.10) and treats naive
    timestamps as UTC — matching how every ``created_at`` /
    ``last_message_at`` in this table is written (``_now_iso``).
    """

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_iso_bound(value: str) -> str:
    """Normalize a caller-supplied ISO bound to the stored-string shape.

    Timestamps in this table are written by ``_now_iso`` as
    ``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``. Lexicographic ``>=`` against
    that shape is only correct when the bound uses the same offset
    format — a raw ``...Z`` suffix or a non-UTC offset would compare
    wrongly — so bounds are round-tripped through ``datetime`` first.
    """

    return _parse_iso_utc(value).isoformat(timespec="microseconds")


def get_user_meta(user_id: str) -> dict[str, Any] | None:
    """Point-read the ``PK=USER#{user_id}, SK=META`` row (#235).

    Returns the raw item dict, or ``None`` when the row is absent —
    which is every user until the #110 writer ships. The #235 admin
    detail endpoint combines this with the caller's chat-index rows to
    decide 404 (a user with chats but no META row still exists).

    ``ConsistentRead=True`` so a detail request issued immediately
    after the META row is written can't observe a stale miss and 404 a
    real (chat-less) user — same read-after-write discipline as
    :func:`get_prefs` and :func:`is_jti_denied`.
    """

    result = _get_table().get_item(
        Key={"PK": f"USER#{user_id}", "SK": "META"},
        ConsistentRead=True,
    )
    item: dict[str, Any] | None = result.get("Item")
    return item


def scan_users(
    *,
    cursor: dict[str, Any] | None = None,
    limit: int = _ADMIN_DEFAULT_PAGE,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Paginated Scan over ``PK=USER#*, SK=META`` user rows (#234).

    Returns ``(rows, next_cursor)`` where ``rows`` are raw item dicts
    (there is no ``User`` model yet — the #110 writer defines one; the
    #235 endpoint maps these to its response shape) and ``next_cursor``
    is an ``ExclusiveStartKey``-shaped dict to feed back in.

    Semantics the #235 endpoint must respect:

    - ``limit`` is clamped to ``[1, _ADMIN_PAGE_MAX]`` (default 50).
    - **Sparse-tolerant**: until #110 ships the USER#META writer this
      returns ``[]`` — callers fall back to
      :func:`derive_users_from_chat_index`.
    - **The cursor, not the row count, is the exhaustion signal.** A
      call may return ``([], cursor)``: each call walks at most
      ``_ADMIN_SCAN_MAX_PAGES`` Scan pages (bounding read cost on a
      table dominated by non-user rows) and hands back the resume key
      when the bound is hit before ``limit`` matching rows were found.
    - When ``limit`` is reached mid-page the cursor is synthesized from
      the last *returned* row's key — using the response's
      ``LastEvaluatedKey`` there would silently skip the matching rows
      in the rest of the evaluated page.
    """

    limit = _clamp_admin_limit(limit)
    table = _get_table()
    rows: list[dict[str, Any]] = []
    start_key = cursor
    for _ in range(_ADMIN_SCAN_MAX_PAGES):
        kwargs: dict[str, Any] = {
            "FilterExpression": Attr("PK").begins_with("USER#") & Attr("SK").eq("META"),
            "Limit": _ADMIN_SCAN_PAGE_LIMIT,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        page = table.scan(**kwargs)
        for item in page.get("Items") or []:
            rows.append(item)
            if len(rows) == limit:
                return rows, {"PK": item["PK"], "SK": item["SK"]}
        start_key = page.get("LastEvaluatedKey")
        if not start_key:
            return rows, None
    return rows, start_key


def derive_users_from_chat_index(
    *,
    cursor: dict[str, Any] | None = None,
    limit: int | None = _ADMIN_DEFAULT_PAGE,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Derive a user list from chat-index rows while USER#META is sparse (#234).

    Fallback path for the #235 admin user list until the #110 writer
    ships. Scans ``PK=USER#*, SK=CHAT#...`` chat-index rows and folds
    them into one row per distinct ``user_id``:

    - ``created_at`` — min of the user's chat ``created_at`` values
      (earliest sign of life; a floor on the real signup date)
    - ``chat_count`` — number of chat-index rows (archived included —
      a user with only archived chats is still a registered user)
    - ``last_chat_at`` — max ``last_message_at`` across the user's
      chats (falling back per row to ``created_at`` for legacy rows)

    Returns ``(rows, next_cursor)`` with rows sorted by ``user_id``
    ascending and ``next_cursor`` of shape ``{"user_id": <last>}``.
    Pagination is applied to the *aggregated* list in memory: each call
    walks the chat-index scan to exhaustion, because a user's chats can
    span Scan pages and a partial walk would return silently-wrong
    aggregates. O(table) per call is an accepted v0.1 trade-off (tiny
    user count, per epic #233); ``ProjectionExpression`` keeps the
    fetched bytes to the three attributes the fold needs.

    ``limit=None`` disables the in-memory pagination and returns the
    complete aggregated list with ``next_cursor=None``. Callers that
    need *every* row (the #235 list endpoint sorts globally before
    paginating) should use this rather than looping the cursor —
    each cursor hop re-runs the full chat-index scan, so an N-page
    cursor walk costs N scans for data this function already had in
    memory on the first call.
    """

    if limit is not None:
        limit = _clamp_admin_limit(limit)
    table = _get_table()
    aggregates: dict[str, dict[str, Any]] = {}
    start_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            "FilterExpression": (Attr("PK").begins_with("USER#") & Attr("SK").begins_with("CHAT#")),
            "ProjectionExpression": "user_id, created_at, last_message_at",
            "Limit": _ADMIN_SCAN_PAGE_LIMIT,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        page = table.scan(**kwargs)
        for item in page.get("Items") or []:
            user_id = item.get("user_id")
            created_at = item.get("created_at")
            if not user_id or not created_at:
                # Defensive: a chat-index row is never written without
                # these (see _chat_index_item), but a malformed row must
                # not corrupt the whole admin list.
                continue
            last_chat_at = item.get("last_message_at") or created_at
            agg = aggregates.get(user_id)
            if agg is None:
                aggregates[user_id] = {
                    "user_id": user_id,
                    "created_at": created_at,
                    "chat_count": 1,
                    "last_chat_at": last_chat_at,
                }
            else:
                agg["created_at"] = min(agg["created_at"], created_at)
                agg["chat_count"] += 1
                agg["last_chat_at"] = max(agg["last_chat_at"], last_chat_at)
        start_key = page.get("LastEvaluatedKey")
        if not start_key:
            break
    users = sorted(aggregates.values(), key=lambda u: str(u["user_id"]))
    if cursor is not None:
        after = str(cursor.get("user_id", ""))
        users = [u for u in users if str(u["user_id"]) > after]
    if limit is None:
        return users, None
    rows = users[:limit]
    next_cursor = {"user_id": rows[-1]["user_id"]} if len(users) > limit else None
    return rows, next_cursor


def list_audit_events_for_actor(
    actor_id: str,
    *,
    event_type: str | None = None,
    since_iso: str | None = None,
    limit: int = _ADMIN_DEFAULT_PAGE,
) -> list[dict[str, Any]]:
    """Read audit events for one actor, newest first (#234).

    Walks the hour-sharded ``AUDIT#{date}#{hour}`` partitions from the
    current hour backwards — one Query per shard, per the fan-out rule
    in the dynamodb-item skill §4 — capped at
    ``_AUDIT_SHARD_WALK_MAX_HOURS`` (168 = 7 days). ``actor_id`` and
    the optional ``event_type`` / ``since_iso`` filters are applied
    server-side via ``FilterExpression``; each shard's Query loop
    follows ``LastEvaluatedKey`` so a filter-thinned page can't
    truncate results. Returns raw item dicts, newest first, at most
    ``limit`` (clamped to ``[1, _ADMIN_PAGE_MAX]``).

    ``since_iso`` bounds both the row filter (``created_at >= since``)
    and the shard walk itself — pass it whenever the caller has a
    window (e.g. #236's ``last_login_at`` derivation), because an
    unbounded miss (actor with no events) costs 168 empty Queries.
    """

    limit = _clamp_admin_limit(limit)
    table = _get_table()
    now = datetime.now(timezone.utc)
    oldest = now - timedelta(hours=_AUDIT_SHARD_WALK_MAX_HOURS - 1)
    since_norm: str | None = None
    if since_iso is not None:
        since_dt = _parse_iso_utc(since_iso)
        since_norm = since_dt.isoformat(timespec="microseconds")
        oldest = max(oldest, since_dt)

    filter_expr: ConditionBase = Attr("actor_id").eq(actor_id)
    if event_type is not None:
        filter_expr = filter_expr & Attr("event_type").eq(event_type)
    if since_norm is not None:
        filter_expr = filter_expr & Attr("created_at").gte(since_norm)

    events: list[dict[str, Any]] = []
    shard_hour = now.replace(minute=0, second=0, microsecond=0)
    oldest_hour = oldest.replace(minute=0, second=0, microsecond=0)
    while shard_hour >= oldest_hour:
        start_key: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": Key("PK").eq(
                    f"AUDIT#{shard_hour:%Y-%m-%d}#{shard_hour:%H}"
                ),
                "FilterExpression": filter_expr,
                # Newest first within the shard. Lexicographic SK order is
                # chronological because the ``{unix_ts}#{uuid}`` prefix stays
                # fixed-width (10 digits) until year 2286.
                "ScanIndexForward": False,
                "Limit": _ADMIN_SCAN_PAGE_LIMIT,
            }
            if start_key:
                kwargs["ExclusiveStartKey"] = start_key
            page = table.query(**kwargs)
            for item in page.get("Items") or []:
                events.append(item)
                if len(events) == limit:
                    return events
            start_key = page.get("LastEvaluatedKey")
            if not start_key:
                break
        shard_hour -= timedelta(hours=1)
    return events


def count_active_users(window_start_iso: str) -> int:
    """Count distinct users with chat activity since ``window_start_iso`` (#234).

    "Active" = owns at least one chat-index row whose
    ``last_message_at >= window_start_iso`` (the boundary itself
    counts). The caller picks the window (24h / 7d / 30d for the #236
    dashboard cards). Full-table Scan folded into a distinct-``user_id``
    set — like :func:`derive_users_from_chat_index`, the walk runs to
    exhaustion because a page-capped count would be silently wrong;
    ``ProjectionExpression`` limits the fetch to ``user_id``.
    """

    window_norm = _normalize_iso_bound(window_start_iso)
    table = _get_table()
    user_ids: set[str] = set()
    start_key: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            "FilterExpression": (
                Attr("PK").begins_with("USER#")
                & Attr("SK").begins_with("CHAT#")
                & Attr("last_message_at").gte(window_norm)
            ),
            "ProjectionExpression": "user_id",
            "Limit": _ADMIN_SCAN_PAGE_LIMIT,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        page = table.scan(**kwargs)
        for item in page.get("Items") or []:
            user_id = item.get("user_id")
            if user_id:
                user_ids.add(str(user_id))
        start_key = page.get("LastEvaluatedKey")
        if not start_key:
            break
    return len(user_ids)
