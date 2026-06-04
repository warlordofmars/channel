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

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from pydantic import ValidationError

from channel.models import (
    Attachment,
    Chat,
    Feedback,
    FeedbackKind,
    Message,
    MessageRole,
    Prefs,
)

logger = logging.getLogger(__name__)

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


def _get_s3_client() -> Any:  # pragma: no cover - tests replace this seam
    return boto3.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))


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
                    ConditionExpression="attribute_exists(PK)",
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

    Called from the send path AFTER ``verify_attachment_object`` returns
    success — the lifecycle rule on the attachments bucket targets
    objects still tagged ``unreferenced=1``; this marker is the
    DDB-side counterpart that ties an S3 object to at least one
    message reference. Doesn't touch other attributes (name, mime,
    etc.) — UpdateItem on a single attribute keeps the row's prior
    state intact.
    """

    _get_table().update_item(
        Key={"PK": f"USER#{user_id}", "SK": _attachment_sk(att_id)},
        UpdateExpression="SET referenced_at = :now",
        ExpressionAttributeValues={":now": _now_iso()},
    )


def verify_attachment_object(att: Attachment) -> tuple[bool, str | None]:
    """HEAD the S3 object to confirm it still exists at turn time.

    Returns ``(True, None)`` on success and ``(False, "<reason>")`` on
    any error. The dedicated ``"S3 object not found"`` reason for HTTP
    404 lets the structured-failure-block builder in #176 surface a
    user-actionable marker (re-upload) distinct from a generic
    permissions / network failure.
    """

    client = _get_s3_client()
    try:
        client.head_object(Bucket=att.s3_bucket, Key=att.s3_key)
        return True, None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False, "S3 object not found"
        return False, f"S3 error: {code}" if code else "S3 error"


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
