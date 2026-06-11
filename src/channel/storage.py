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

import contextlib
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from channel.models import (
    Attachment,
    Chat,
    ChatMCPMode,
    ChatMCPSettings,
    Feedback,
    FeedbackKind,
    MCPServer,
    MCPServerAuthStatus,
    MCPToken,
    Message,
    MessageRole,
    Prefs,
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

    try:
        response = _get_s3_client().get_object(Bucket=att.s3_bucket, Key=att.s3_key)
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
# MCP servers / tokens (#207) — registry + per-chat override
# ----------------------------------------------------------------


def _mcp_server_sk(server_id: str) -> str:
    return f"MCPSERVER#{server_id}"


def _mcp_token_sk(server_id: str) -> str:
    return f"MCPTOKEN#{server_id}"


def _mcp_chat_override_sk() -> str:
    return "MCPSERVERS#META"


def _mcp_server_item(server: MCPServer) -> dict[str, Any]:
    return {
        "PK": f"USER#{server.user_id}",
        "SK": _mcp_server_sk(server.server_id),
        "server_id": server.server_id,
        "user_id": server.user_id,
        "name": server.name,
        "url": server.url,
        "client_id": server.client_id,
        "tool_prefix": server.tool_prefix,
        "auth_status": server.auth_status.value,
        "globally_enabled": server.globally_enabled,
        "created_at": server.created_at,
        "updated_at": server.updated_at,
    }


def _mcp_server_from_item(item: dict[str, Any]) -> MCPServer:
    return MCPServer(
        server_id=item["server_id"],
        user_id=item["user_id"],
        name=item["name"],
        url=item["url"],
        client_id=item["client_id"],
        tool_prefix=item["tool_prefix"],
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
    client_id: str,
    tool_prefix: str,
) -> MCPServer:
    """Persist a freshly-registered MCP server row.

    Caller supplies the DCR-issued ``client_id`` and a normalized
    ``tool_prefix``. Auth status starts ``NEVER_AUTHED`` — the user
    completes the auth-code flow next and the callback handler flips
    this to ``ACTIVE``.
    """
    now = _now_iso()
    server = MCPServer(
        server_id=str(uuid.uuid4()),
        user_id=user_id,
        name=name,
        url=url,
        client_id=client_id,
        tool_prefix=tool_prefix,
        auth_status=MCPServerAuthStatus.NEVER_AUTHED,
        globally_enabled=True,
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
                    "user_id_hash": str(hash(user_id)),
                    "server_id_hash": str(hash(server_id)),
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
                    "user_id_hash": str(hash(user_id)),
                    "server_id_hash": str(hash(server_id)),
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
