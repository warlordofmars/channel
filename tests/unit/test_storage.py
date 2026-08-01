# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for channel.storage chat persistence helpers.

These run against an in-memory fake table to verify item shapes,
ordering, and pagination without DDB Local.  See
``tests/integration/test_chat_persistence.py`` for the DDB-Local
counterpart.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from boto3.dynamodb.conditions import (
    And,
    AttributeNotExists,
    BeginsWith,
    ConditionBase,
    Equals,
    GreaterThanEquals,
    Or,
)
from botocore.exceptions import ClientError
from pydantic import ValidationError

from channel.logging_config import fingerprint_id
from channel.models import (
    REFRESH_ABSOLUTE_LIFETIME_SECONDS,
    REFRESH_IDLE_TIMEOUT_SECONDS,
    MessageRole,
    RefreshConsumeOutcome,
    RefreshConsumeResult,
    RefreshRevokeReason,
    RefreshToken,
)
from channel.storage import (
    consume_refresh_token,
    count_active_users,
    create_chat,
    delete_last_assistant_message,
    deny_jti,
    derive_users_from_chat_index,
    get_chat_by_id,
    get_user_meta,
    is_jti_denied,
    list_audit_events_for_actor,
    list_chats_for_user,
    list_messages,
    list_messages_page,
    list_recent_messages,
    mint_refresh_token,
    patch_chat,
    put_message,
    revoke_all_user_refresh_tokens,
    revoke_refresh_token,
    scan_users,
    update_chat_index,
)


def _extract_conditions(expr: ConditionBase) -> dict[str, tuple[str, Any]]:
    """Walk a boto3 condition tree, returning ``{attr_name: (op, value)}``.

    Mirrors enough of the real ``KeyConditionExpression`` shape that
    ``FakeTable.query`` can filter on real condition objects produced by
    ``Key(...).eq(...) & Key(...).begins_with(...)`` — the same form
    used against real DynamoDB by ``channel.storage``.
    """

    result: dict[str, tuple[str, Any]] = {}

    def walk(node: ConditionBase) -> None:
        if isinstance(node, And):
            for child in node._values:
                walk(child)
        elif isinstance(node, Equals):
            attr, value = node._values
            result[attr.name] = ("eq", value)
        elif isinstance(node, BeginsWith):
            attr, value = node._values
            result[attr.name] = ("begins_with", value)

    walk(expr)
    return result


def _evaluate_filter(expr: ConditionBase | None, item: dict[str, Any]) -> bool:
    """Evaluate a boto3 ``FilterExpression`` against a single fake-table item.

    Handles the small set of operators ``channel.storage`` actually
    emits: ``Attr(name).not_exists()``, ``Attr(name).eq(value)``, and
    their ``|`` (Or) / ``&`` (And) combinations. Anything else is a
    test bug; raise so the test author notices.
    """

    if expr is None:
        return True
    if isinstance(expr, Or):
        return any(_evaluate_filter(child, item) for child in expr._values)
    if isinstance(expr, And):
        return all(_evaluate_filter(child, item) for child in expr._values)
    if isinstance(expr, AttributeNotExists):
        (attr,) = expr._values
        return attr.name not in item
    if isinstance(expr, Equals):
        attr, value = expr._values
        return item.get(attr.name) == value
    if isinstance(expr, BeginsWith):
        attr, value = expr._values
        actual = item.get(attr.name)
        return isinstance(actual, str) and actual.startswith(value)
    if isinstance(expr, GreaterThanEquals):
        attr, value = expr._values
        actual = item.get(attr.name)
        return actual is not None and actual >= value
    raise NotImplementedError(f"FakeTable filter does not understand {type(expr).__name__}")


class FakeTable:
    """Minimal in-memory stand-in for ``boto3.resource('dynamodb').Table``."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, Item: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        # Extra kwargs (e.g. ``ConditionExpression``) are accepted but not
        # enforced — Task 11's integration test against DDB Local covers
        # the real ConditionalCheck semantics.
        self.items[(Item["PK"], Item["SK"])] = dict(Item)
        return {}

    def get_item(self, Key: dict[str, str], **kwargs: Any) -> dict[str, Any]:
        # Record kwargs (e.g. ``ConsistentRead``) so tests can assert on them.
        self.last_get_item_kwargs = kwargs
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        # IndexName is ignored: chat-index items carry both their primary
        # (PK/SK) and GSI (GSI3PK/GSI3SK) keys on the same item, so the
        # condition tree alone is sufficient to find them whether the
        # caller meant the main table or the GSI.
        conds = _extract_conditions(kwargs["KeyConditionExpression"])
        filter_expr = kwargs.get("FilterExpression")
        limit = kwargs.get("Limit")
        scan_forward = kwargs.get("ScanIndexForward", True)
        exclusive_start = kwargs.get("ExclusiveStartKey")

        def matches(item: dict[str, Any]) -> bool:
            for attr, (op, value) in conds.items():
                actual = item.get(attr)
                if op == "eq" and actual != value:
                    return False
                if op == "begins_with" and not (
                    isinstance(actual, str) and actual.startswith(value)
                ):
                    return False
            return True

        # Sort by SK for main-table queries; the GSI lookup is single-row
        # and unaffected by sort direction in this fake.
        items = sorted(
            (i for i in self.items.values() if matches(i)),
            key=lambda i: i["SK"],
            reverse=not scan_forward,
        )
        if exclusive_start is not None:
            # Drop everything up to AND including the item whose
            # (PK, SK) matches the cursor — mirrors DynamoDB pagination.
            # If the cursor item has been deleted (e.g. during a
            # paginated-delete loop), fall back to SK-based position so
            # pages after the cursor are still reachable.
            cursor_pk = exclusive_start.get("PK")
            cursor_sk = exclusive_start.get("SK")
            for idx, item in enumerate(items):
                if item["PK"] == cursor_pk and item["SK"] == cursor_sk:
                    items = items[idx + 1 :]
                    break
            else:
                # Cursor item not found (e.g. already deleted) — skip
                # all items whose SK is <= cursor_sk to preserve ordering.
                if cursor_sk is not None:
                    items = [i for i in items if i.get("SK", "") > cursor_sk]
                else:
                    items = []
        # DynamoDB applies FilterExpression AFTER Limit on the wire.
        # Mirror that ordering here: limit first, then filter — so a
        # page that contains archived rows surfaces as "fewer than
        # ``limit`` items, still has a cursor" exactly like production.
        # See ``list_chats_for_user``'s docstring for the trade-off.
        result: dict[str, Any] = {}
        if limit is not None and len(items) > limit:
            returned = items[:limit]
            last = returned[-1]
            result["LastEvaluatedKey"] = {"PK": last["PK"], "SK": last["SK"]}
            items = returned
        elif limit is not None:
            items = items[:limit]
        if filter_expr is not None:
            items = [i for i in items if _evaluate_filter(filter_expr, i)]
        result["Items"] = items
        return result

    def scan(self, **kwargs: Any) -> dict[str, Any]:
        """Mirror DynamoDB Scan semantics closely enough for #234.

        - Deterministic iteration order (sorted by ``(PK, SK)``) stands
          in for DynamoDB's stable-but-arbitrary hash order, so cursor
          round-trips behave like production.
        - ``Limit`` bounds items *evaluated*; ``FilterExpression`` is
          applied AFTER the limit window (the wire order), so a page can
          legitimately return zero matching items plus a cursor.
        - ``ExclusiveStartKey`` resumes strictly after the given key,
          including keys synthesized from a mid-page stop.
        - ``ProjectionExpression`` trims returned attributes (plain
          comma-separated names only — no ``#alias`` support needed).
        """

        filter_expr = kwargs.get("FilterExpression")
        limit = kwargs.get("Limit")
        exclusive_start = kwargs.get("ExclusiveStartKey")
        projection = kwargs.get("ProjectionExpression")

        items = sorted(self.items.values(), key=lambda i: (i["PK"], i["SK"]))
        if exclusive_start is not None:
            cursor_key = (exclusive_start.get("PK"), exclusive_start.get("SK"))
            items = [i for i in items if (i["PK"], i["SK"]) > cursor_key]

        result: dict[str, Any] = {}
        if limit is not None and len(items) > limit:
            items = items[:limit]
            last = items[-1]
            result["LastEvaluatedKey"] = {"PK": last["PK"], "SK": last["SK"]}
        if filter_expr is not None:
            items = [i for i in items if _evaluate_filter(filter_expr, i)]
        if projection is not None:
            attrs = {a.strip() for a in projection.split(",")}
            items = [{k: v for k, v in i.items() if k in attrs} for i in items]
        result["Items"] = items
        return result

    def delete_item(self, Key: dict[str, str]) -> dict[str, Any]:
        self.items.pop((Key["PK"], Key["SK"]), None)
        return {}

    def batch_writer(self) -> _FakeBatchWriter:
        return _FakeBatchWriter(self)

    def update_item(self, Key: dict[str, str], **kwargs: Any) -> dict[str, Any]:
        item = self.items.setdefault((Key["PK"], Key["SK"]), {**Key})
        values = kwargs.get("ExpressionAttributeValues") or {}
        names = kwargs.get("ExpressionAttributeNames") or {}
        expression = kwargs.get("UpdateExpression", "")

        def resolve(attr: str) -> str:
            # Map ``#alias`` → real attribute name when present.
            return names.get(attr, attr)

        # Parse "SET a = :p, b = :q" and "ADD c :r" clauses just well
        # enough for the storage-helper UpdateExpressions in this module.
        set_clause = ""
        add_clause = ""
        if "ADD" in expression:
            set_part, _, add_part = expression.partition("ADD")
            add_clause = add_part.strip()
            set_clause = set_part.replace("SET", "", 1).strip()
        elif expression.startswith("SET"):
            set_clause = expression[3:].strip()
        for assignment in [s.strip() for s in set_clause.split(",") if s.strip()]:
            attr, _, placeholder = assignment.partition("=")
            item[resolve(attr.strip())] = values[placeholder.strip()]
        for addition in [s.strip() for s in add_clause.split(",") if s.strip()]:
            attr, _, placeholder = addition.partition(" ")
            item[resolve(attr.strip())] = (
                item.get(resolve(attr.strip()), 0) + values[placeholder.strip()]
            )
        return {"Attributes": item}


class _FakeBatchWriter:
    """Context manager that proxies delete_item calls back to a FakeTable."""

    def __init__(self, fake_table: FakeTable) -> None:
        self._table = fake_table

    def __enter__(self) -> _FakeBatchWriter:
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def delete_item(self, Key: dict[str, str]) -> None:
        self._table.delete_item(Key)


@pytest.fixture
def table(monkeypatch: pytest.MonkeyPatch) -> FakeTable:
    fake = FakeTable()
    monkeypatch.setattr("channel.storage._get_table", lambda: fake)
    return fake


def test_create_chat_writes_index_row_with_correct_keys(table: FakeTable) -> None:
    chat = create_chat(
        user_id="u-1",
        title=None,
        model_default="canned-stream-v1",
    )

    assert chat.title == "New chat"
    pk = "USER#u-1"
    sk_prefix = "CHAT#"
    stored = next(
        item
        for item in table.items.values()
        if item["PK"] == pk and item["SK"].startswith(sk_prefix)
    )
    assert stored["chat_id"] == chat.chat_id
    assert stored["GSI3PK"] == f"CHAT_ID#{chat.chat_id}"
    assert stored["GSI3SK"] == "META"


def test_get_chat_by_id_returns_chat_via_gsi(table: FakeTable) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    fetched = get_chat_by_id(chat.chat_id)
    assert fetched is not None
    assert fetched.chat_id == chat.chat_id


def test_get_chat_by_id_returns_none_for_unknown(table: FakeTable) -> None:
    assert get_chat_by_id("does-not-exist") is None


def test_list_chats_returns_newest_first(table: FakeTable) -> None:
    a = create_chat(user_id="u-1", title=None, model_default="m")
    b = create_chat(user_id="u-1", title=None, model_default="m")
    chats, cursor = list_chats_for_user("u-1", limit=10, cursor=None)
    assert [c.chat_id for c in chats] == [b.chat_id, a.chat_id]
    assert cursor is None


def test_list_chats_excludes_archived_by_default(table: FakeTable) -> None:
    """Default ``include_archived=False`` filters archived rows server-side."""

    keeper = create_chat(user_id="u-1", title=None, model_default="m")
    hidden = create_chat(user_id="u-1", title=None, model_default="m")
    patch_chat(user_id="u-1", chat=hidden, title=None, archived=True)

    chats, _ = list_chats_for_user("u-1", limit=10, cursor=None)
    assert [c.chat_id for c in chats] == [keeper.chat_id]


def test_list_chats_includes_archived_when_flag_set(table: FakeTable) -> None:
    """Explicit ``include_archived=True`` returns archived rows too."""

    a = create_chat(user_id="u-1", title=None, model_default="m")
    b = create_chat(user_id="u-1", title=None, model_default="m")
    patch_chat(user_id="u-1", chat=b, title=None, archived=True)

    chats, _ = list_chats_for_user("u-1", limit=10, cursor=None, include_archived=True)
    # Newest-first: b (archived) comes before a.
    assert [c.chat_id for c in chats] == [b.chat_id, a.chat_id]


def test_list_chats_keeps_legacy_rows_lacking_archived_attribute(
    table: FakeTable,
) -> None:
    """Pre-archived-field rows (no ``archived`` attribute) still appear in the default list.

    The filter uses ``attribute_not_exists(archived) OR archived = false``
    so old chats migrated in before the field existed are still visible.
    """

    legacy = create_chat(user_id="u-1", title=None, model_default="m")
    # Reach into the fake table and delete the attribute to simulate
    # a row written before the ``archived`` column existed.
    for item in table.items.values():
        if item.get("chat_id") == legacy.chat_id and item["SK"].startswith("CHAT#"):
            item.pop("archived", None)

    chats, _ = list_chats_for_user("u-1", limit=10, cursor=None)
    assert [c.chat_id for c in chats] == [legacy.chat_id]


def test_list_chats_paginates_with_cursor(table: FakeTable) -> None:
    """Walk through 5 chats two-at-a-time, verifying cursors round-trip.

    The fake table now honors ``ExclusiveStartKey`` end-to-end, so each
    page must return distinct items and ``LastEvaluatedKey`` must point
    at the page's tail item.
    """
    created = [create_chat(user_id="u-1", title=None, model_default="m") for _ in range(5)]
    # Newest-first: reversed creation order.
    expected_order = list(reversed([c.chat_id for c in created]))

    page1, cursor1 = list_chats_for_user("u-1", limit=2, cursor=None)
    assert [c.chat_id for c in page1] == expected_order[:2]
    assert cursor1 is not None

    page2, cursor2 = list_chats_for_user("u-1", limit=2, cursor=cursor1)
    assert [c.chat_id for c in page2] == expected_order[2:4]
    assert cursor2 is not None

    page3, cursor3 = list_chats_for_user("u-1", limit=2, cursor=cursor2)
    assert [c.chat_id for c in page3] == expected_order[4:]
    assert cursor3 is None


def test_put_message_writes_row_with_msg_id_suffix(table: FakeTable) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    msg = put_message(
        chat_id=chat.chat_id,
        role=MessageRole.USER,
        text="hello",
        model=None,
    )
    stored = next(item for item in table.items.values() if item["PK"] == f"CHAT#{chat.chat_id}")
    assert stored["text"] == "hello"
    assert stored["role"] == "user"
    assert stored["SK"].startswith("MSG#")
    # The SK now uses msg_id as the uniqueness tiebreaker instead of a
    # per-process counter — pin the new format so a regression to the
    # old `seq:05d` shape is caught.
    assert msg.msg_id in stored["SK"]
    assert msg.role is MessageRole.USER


def test_put_message_persists_artifacts(table: FakeTable) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    artifacts = [{"kind": "code", "title": "demo"}]
    attachments = [{"kind": "file", "name": "x.txt"}]
    msg = put_message(
        chat_id=chat.chat_id,
        role=MessageRole.ASSISTANT,
        text="ok",
        model="m",
        input_tokens=4,
        output_tokens=2,
        artifacts=artifacts,
        attachments=attachments,
    )
    stored = next(
        item
        for item in table.items.values()
        if item["PK"] == f"CHAT#{chat.chat_id}" and item.get("role") == "assistant"
    )
    assert stored["artifacts"] == artifacts
    assert stored["attachments"] == attachments
    assert stored["input_tokens"] == 4
    assert stored["output_tokens"] == 2
    assert msg.artifacts == artifacts


def test_list_messages_returns_chronological_order(table: FakeTable) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="one", model=None)
    put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="two", model="m")
    msgs, _ = list_messages(chat.chat_id, limit=10, cursor=None)
    assert [m.text for m in msgs] == ["one", "two"]


def test_list_recent_messages_returns_newest_n_in_chronological_order(
    table: FakeTable,
) -> None:
    """The agent-history feed must get the NEWEST N messages (#244).

    ``list_messages`` + ``Limit`` returns the oldest N; the helper under
    test reads from the newest end and re-sorts chronologically.
    """
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    for i in range(7):
        put_message(chat_id=chat.chat_id, role=MessageRole.USER, text=f"msg-{i}", model=None)

    msgs = list_recent_messages(chat.chat_id, limit=3)

    assert [m.text for m in msgs] == ["msg-4", "msg-5", "msg-6"]


def test_list_recent_messages_returns_all_when_chat_is_shorter_than_limit(
    table: FakeTable,
) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="one", model=None)
    put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="two", model="m")

    msgs = list_recent_messages(chat.chat_id, limit=10)

    assert [m.text for m in msgs] == ["one", "two"]


def test_list_messages_page_returns_newest_limit_chronologically(table: FakeTable) -> None:
    """The conversation view must load the NEWEST page (#270).

    ``list_messages`` + ``Limit`` returns the OLDEST N (which silently
    dropped the recent half of long chats); ``list_messages_page`` reads
    from the newest end and re-sorts chronologically for display. The
    returned cursor is non-None when older messages remain.
    """
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    for i in range(7):
        put_message(chat_id=chat.chat_id, role=MessageRole.USER, text=f"msg-{i}", model=None)

    msgs, older = list_messages_page(chat.chat_id, limit=3)

    assert [m.text for m in msgs] == ["msg-4", "msg-5", "msg-6"]
    assert older is not None  # 4 older messages remain


def test_list_messages_page_no_cursor_when_chat_fits_one_page(table: FakeTable) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="one", model=None)
    put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="two", model="m")

    msgs, older = list_messages_page(chat.chat_id, limit=10)

    assert [m.text for m in msgs] == ["one", "two"]
    assert older is None


def test_list_messages_page_cursor_chains_backward_to_start(table: FakeTable) -> None:
    """Paging with the returned ``before`` cursor walks toward the chat
    start; reassembling the pages reconstructs the full chat oldest→newest,
    and the cursor goes ``None`` once the first message is reached."""
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    for i in range(5):
        put_message(chat_id=chat.chat_id, role=MessageRole.USER, text=f"m{i}", model=None)

    page1, c1 = list_messages_page(chat.chat_id, limit=2)
    page2, c2 = list_messages_page(chat.chat_id, limit=2, before=c1)
    page3, c3 = list_messages_page(chat.chat_id, limit=2, before=c2)

    assert [m.text for m in page1] == ["m3", "m4"]  # newest page, chronological
    assert [m.text for m in page2] == ["m1", "m2"]  # next older page
    assert [m.text for m in page3] == ["m0"]  # oldest remaining
    assert c3 is None
    assert [m.text for m in (page3 + page2 + page1)] == ["m0", "m1", "m2", "m3", "m4"]


def test_list_recent_messages_matches_first_page_of_list_messages_page(
    table: FakeTable,
) -> None:
    """``list_recent_messages`` is the cursorless newest-N read; it must
    agree with ``list_messages_page``'s first page so the agent feed and
    the UI's initial load see the same recent window (#270)."""
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    for i in range(6):
        put_message(chat_id=chat.chat_id, role=MessageRole.USER, text=f"r{i}", model=None)

    recent = list_recent_messages(chat.chat_id, limit=4)
    page, _ = list_messages_page(chat.chat_id, limit=4)

    assert [m.text for m in recent] == [m.text for m in page]


def test_list_messages_paginates_with_cursor(table: FakeTable) -> None:
    """Walk through 5 messages two-at-a-time, verifying cursor round-trips.

    Mirrors the chats pagination test — same end-to-end assertion shape
    over the messages SK (``MSG#{created_at}#{msg_id}``).
    """
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    texts = ["one", "two", "three", "four", "five"]
    for text in texts:
        put_message(chat_id=chat.chat_id, role=MessageRole.USER, text=text, model=None)

    page1, cursor1 = list_messages(chat.chat_id, limit=2, cursor=None)
    assert [m.text for m in page1] == ["one", "two"]
    assert cursor1 is not None

    page2, cursor2 = list_messages(chat.chat_id, limit=2, cursor=cursor1)
    assert [m.text for m in page2] == ["three", "four"]
    assert cursor2 is not None

    page3, cursor3 = list_messages(chat.chat_id, limit=2, cursor=cursor2)
    assert [m.text for m in page3] == ["five"]
    assert cursor3 is None


def test_update_chat_index_bumps_count_and_preview(table: FakeTable) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    update_chat_index(
        user_id="u-1",
        chat=chat,
        last_user_preview="hi there",
        delta_count=2,
        last_message_at="2026-05-30T01:00:00Z",
    )
    pk = "USER#u-1"
    sk_prefix = "CHAT#"
    stored = next(
        item
        for item in table.items.values()
        if item["PK"] == pk and item["SK"].startswith(sk_prefix)
    )
    assert stored["message_count"] == 2
    assert stored["last_user_preview"] == "hi there"
    assert stored["last_message_at"] == "2026-05-30T01:00:00Z"


def test_patch_chat_updates_title_and_archived(table: FakeTable) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    patch_chat(user_id="u-1", chat=chat, title="renamed", archived=True)
    pk = "USER#u-1"
    sk = f"CHAT#{chat.created_at}#{chat.chat_id}"
    stored = table.items[(pk, sk)]
    assert stored["title"] == "renamed"
    assert stored["archived"] is True


def test_idempotency_reserve_then_replay(table: FakeTable) -> None:
    from channel import storage as st

    first = st.reserve_idempotency_key(user_id="u-1", key="k1")
    assert first is None

    st.store_idempotency_result(user_id="u-1", key="k1", payload={"text": "hello"})

    # FakeTable doesn't simulate ConditionExpression, so we mimic the
    # "already exists" path by exercising the get directly.
    existing = table.get_item(Key={"PK": "IDEMP#u-1", "SK": "k1"}).get("Item")
    assert existing is not None
    assert existing["result"] == {"text": "hello"}


def test_idempotency_reserve_returns_existing_on_conflict(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    from botocore.exceptions import ClientError

    from channel import storage as st

    # Pre-populate the IDEMP item so get_item returns it on the replay path.
    table.put_item(
        Item={
            "PK": "IDEMP#u-1",
            "SK": "k2",
            "reserved_at": 0,
            "ttl": 9999,
            "result": {"text": "prior"},
        }
    )

    def fake_put_item(**_kwargs: Any) -> dict[str, Any]:
        raise ClientError(
            error_response={"Error": {"Code": "ConditionalCheckFailedException"}},
            operation_name="PutItem",
        )

    monkeypatch.setattr(table, "put_item", fake_put_item)

    result = st.reserve_idempotency_key(user_id="u-1", key="k2")
    assert result is not None
    assert result["result"] == {"text": "prior"}


def test_idempotency_reserve_re_raises_other_client_errors(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    from botocore.exceptions import ClientError

    from channel import storage as st

    def fake_put_item(**_kwargs: Any) -> dict[str, Any]:
        raise ClientError(
            error_response={"Error": {"Code": "ThrottlingException"}},
            operation_name="PutItem",
        )

    monkeypatch.setattr(table, "put_item", fake_put_item)

    with pytest.raises(ClientError):
        st.reserve_idempotency_key(user_id="u-1", key="k3")


def test_delete_last_assistant_message_drops_only_the_assistant_row(
    table: FakeTable,
) -> None:
    chat = create_chat(user_id="u", title=None, model_default="m")
    put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="hi", model=None)
    put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="hello", model="m")
    deleted = delete_last_assistant_message(chat.chat_id)
    assert deleted is not None and deleted.text == "hello"

    msgs, _ = list_messages(chat.chat_id, limit=10, cursor=None)
    assert [m.role for m in msgs] == [MessageRole.USER]


def test_delete_last_assistant_message_targets_true_last_in_long_chat(
    table: FakeTable,
) -> None:
    """In a chat longer than the helper's read window, the ACTUAL newest
    assistant turn must be deleted — not the newest within the oldest-50
    window (#244).
    """
    chat = create_chat(user_id="u", title=None, model_default="m")
    for i in range(30):
        put_message(chat_id=chat.chat_id, role=MessageRole.USER, text=f"u-{i}", model=None)
        put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text=f"a-{i}", model="m")

    deleted = delete_last_assistant_message(chat.chat_id)

    assert deleted is not None and deleted.text == "a-29"
    remaining_texts = {
        item["text"]
        for item in table.items.values()
        if item["PK"] == f"CHAT#{chat.chat_id}" and item.get("role") == "assistant"
    }
    assert "a-29" not in remaining_texts and len(remaining_texts) == 29


def test_delete_last_assistant_message_returns_none_when_no_assistant_rows(
    table: FakeTable,
) -> None:
    chat = create_chat(user_id="u-no-asst", title=None, model_default="m")
    put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="hi", model=None)
    assert delete_last_assistant_message(chat.chat_id) is None


def test_patch_chat_with_no_fields_is_noop(table: FakeTable) -> None:
    """Empty PATCH payloads silently no-op — deliberate, not a bug.

    PATCH /api/chats/{id} with `{}` should not produce a useless
    UpdateItem call.  The API layer is responsible for rejecting
    empty payloads if it cares to (it doesn't, in 7a).
    """
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    pk = "USER#u-1"
    sk = f"CHAT#{chat.created_at}#{chat.chat_id}"
    before = dict(table.items[(pk, sk)])
    patch_chat(user_id="u-1", chat=chat, title=None, archived=None)
    assert table.items[(pk, sk)] == before


def test_delete_chat_removes_message_rows_and_chat_index_row(table: FakeTable) -> None:
    """delete_chat removes EVERY message row plus the chat-index row.

    Uses a chat with 26 messages to cross the 25-row DDB batch boundary
    — guards against off-by-one in the batched-delete loop.
    """
    from channel import storage

    chat = create_chat(user_id="u1", title="t", model_default="claude-sonnet-4-6")
    # Plant 26 fake message rows.
    for i in range(26):
        table.put_item(
            Item={
                "PK": f"CHAT#{chat.chat_id}",
                "SK": f"MSG#2026-06-01T00:00:00.{i:06d}#m{i}",
                "msg_id": f"m{i}",
                "text": f"hello {i}",
                "role": "user",
            }
        )

    storage.delete_chat(user_id="u1", chat=chat)

    # All message rows gone.
    remaining_msgs = [
        item
        for item in table.items.values()
        if item["PK"] == f"CHAT#{chat.chat_id}" and item["SK"].startswith("MSG#")
    ]
    assert len(remaining_msgs) == 0, (
        f"expected 0 message rows after delete; got {len(remaining_msgs)}"
    )

    # Chat-index row gone.
    idx = table.get_item(
        Key={
            "PK": "USER#u1",
            "SK": storage._chat_index_sk(chat.created_at, chat.chat_id),
        }
    )
    assert "Item" not in idx, "chat-index row still present after delete"


def test_delete_chat_is_idempotent(table: FakeTable) -> None:
    """Calling delete_chat twice doesn't raise — DDB delete is naturally idempotent."""
    from channel import storage

    chat = create_chat(user_id="u1", title="t", model_default="claude-sonnet-4-6")
    storage.delete_chat(user_id="u1", chat=chat)
    storage.delete_chat(user_id="u1", chat=chat)  # MUST NOT raise


def test_get_prefs_returns_defaults_when_no_row(table: FakeTable) -> None:
    from channel import storage

    prefs = storage.get_prefs("user-1")
    assert prefs.theme == "dark"
    assert prefs.send_on_enter is True
    assert prefs.suggest_followups is True


def test_get_prefs_uses_strong_consistency(table: FakeTable) -> None:
    """Cross-device sync requires `ConsistentRead=True` — without it, a
    GET issued right after a PUT can return a stale eventual-consistency
    replica and the second device sees the old value."""
    from channel import storage

    storage.get_prefs("user-1")
    assert table.last_get_item_kwargs.get("ConsistentRead") is True


def test_put_prefs_persists_and_returns_merged(table: FakeTable) -> None:
    from channel import storage

    merged = storage.put_prefs("user-1", {"theme": "light", "send_on_enter": False})
    assert merged.theme == "light"
    assert merged.send_on_enter is False
    # Other fields fall back to defaults.
    assert merged.accent == "42"
    # Round-trip via a fresh read.
    assert storage.get_prefs("user-1").theme == "light"


def test_put_prefs_unknown_key_raises(table: FakeTable) -> None:
    import pydantic

    from channel import storage

    with pytest.raises(pydantic.ValidationError):
        storage.put_prefs("user-1", {"made_up_key": "x"})


def test_put_prefs_partial_update_preserves_other_keys(table: FakeTable) -> None:
    from channel import storage

    storage.put_prefs("user-1", {"theme": "light"})
    storage.put_prefs("user-1", {"accent": "150"})
    prefs = storage.get_prefs("user-1")
    assert prefs.theme == "light"
    assert prefs.accent == "150"


# ---------------------------------------------------------------------------
# Audit log (issue #151)
# ---------------------------------------------------------------------------


def test_put_audit_event_writes_hour_sharded_row(table: FakeTable) -> None:
    from channel import storage

    item = storage.put_audit_event(
        event_type="auth.logout",
        actor_id="user@example.com",
        details={"role": "user"},
    )

    # PK shape: AUDIT#YYYY-MM-DD#HH (hour-sharded per CLAUDE.md §"DynamoDB
    # single table design" and the dynamodb-item skill §4).
    assert item["PK"].startswith("AUDIT#")
    parts = item["PK"].split("#")
    assert len(parts) == 3
    # parts[1] is the date, parts[2] the zero-padded hour.
    assert len(parts[1]) == 10  # "YYYY-MM-DD"
    assert len(parts[2]) == 2 and parts[2].isdigit()

    # SK shape: {unix_timestamp}#{event_id}; both pieces non-empty.
    sk_ts, _, sk_event_id = item["SK"].partition("#")
    assert sk_ts.isdigit()
    assert sk_event_id == item["event_id"]
    assert item["event_type"] == "auth.logout"
    assert item["actor_id"] == "user@example.com"
    assert item["details"] == {"role": "user"}

    # Row is actually persisted to the fake table under the same key.
    stored = table.items[(item["PK"], item["SK"])]
    assert stored["event_type"] == "auth.logout"


def test_put_audit_event_sets_ttl_from_retention_default(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel import storage

    monkeypatch.delenv("STARTER_AUDIT_RETENTION_DAYS", raising=False)
    item = storage.put_audit_event(event_type="auth.logout", actor_id="u-1")
    # SK encodes the issuance timestamp; ttl - created_ts should equal
    # 365 * 86400 seconds (the default retention window).
    created_ts = int(item["SK"].split("#", 1)[0])
    assert item["ttl"] - created_ts == 365 * 86400


def test_put_audit_event_honours_retention_override(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel import storage

    monkeypatch.setenv("STARTER_AUDIT_RETENTION_DAYS", "7")
    item = storage.put_audit_event(event_type="auth.logout", actor_id="u-1")
    created_ts = int(item["SK"].split("#", 1)[0])
    assert item["ttl"] - created_ts == 7 * 86400


def test_put_audit_event_omits_details_when_none(table: FakeTable) -> None:
    """A bare audit row (no caller-supplied details) skips the details key."""

    from channel import storage

    item = storage.put_audit_event(event_type="auth.logout", actor_id="u-1")
    assert "details" not in item
    stored = table.items[(item["PK"], item["SK"])]
    assert "details" not in stored


def test_put_audit_event_unique_event_id_per_call(table: FakeTable) -> None:
    """Two writes in the same partition land under distinct SKs."""

    from channel import storage

    a = storage.put_audit_event(event_type="auth.logout", actor_id="u-1")
    b = storage.put_audit_event(event_type="auth.logout", actor_id="u-1")
    assert a["event_id"] != b["event_id"]
    assert (a["PK"], a["SK"]) != (b["PK"], b["SK"])


# ---------------------------------------------------------------------------
# put_message_feedback (issue #146)
# ---------------------------------------------------------------------------


def test_put_message_feedback_writes_feedback_attribute_on_message_row(
    table: FakeTable,
) -> None:
    """Happy path: feedback is persisted on the matching MSG# row."""
    from channel import storage
    from channel.models import FeedbackKind

    chat = create_chat(user_id="u-1", title=None, model_default="m")
    msg = put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="reply", model="m")

    result = storage.put_message_feedback(
        chat_id=chat.chat_id,
        msg_id=msg.msg_id,
        kind=FeedbackKind.UP,
        note=None,
    )

    assert result is not None
    assert result.kind is FeedbackKind.UP
    assert result.note is None
    assert result.created_at  # ISO-8601 timestamp populated by storage

    stored = next(
        item
        for item in table.items.values()
        if item["PK"] == f"CHAT#{chat.chat_id}" and item.get("msg_id") == msg.msg_id
    )
    assert stored["feedback"]["kind"] == "up"
    assert stored["feedback"]["note"] is None


def test_put_message_feedback_persists_note_when_supplied(table: FakeTable) -> None:
    """The optional `note` round-trips into the stored feedback attribute."""
    from channel import storage
    from channel.models import FeedbackKind

    chat = create_chat(user_id="u-1", title=None, model_default="m")
    msg = put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="reply", model="m")

    result = storage.put_message_feedback(
        chat_id=chat.chat_id,
        msg_id=msg.msg_id,
        kind=FeedbackKind.DOWN,
        note="this answer was wrong",
    )

    assert result is not None
    assert result.kind is FeedbackKind.DOWN
    assert result.note == "this answer was wrong"

    stored = next(
        item
        for item in table.items.values()
        if item["PK"] == f"CHAT#{chat.chat_id}" and item.get("msg_id") == msg.msg_id
    )
    assert stored["feedback"]["kind"] == "down"
    assert stored["feedback"]["note"] == "this answer was wrong"


def test_put_message_feedback_overwrites_prior_feedback(table: FakeTable) -> None:
    """Submitting feedback twice keeps only the latest record on the row."""
    from channel import storage
    from channel.models import FeedbackKind

    chat = create_chat(user_id="u-1", title=None, model_default="m")
    msg = put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="reply", model="m")

    first = storage.put_message_feedback(
        chat_id=chat.chat_id, msg_id=msg.msg_id, kind=FeedbackKind.UP, note=None
    )
    second = storage.put_message_feedback(
        chat_id=chat.chat_id, msg_id=msg.msg_id, kind=FeedbackKind.DOWN, note="bad"
    )

    assert first is not None
    assert second is not None
    stored = next(
        item
        for item in table.items.values()
        if item["PK"] == f"CHAT#{chat.chat_id}" and item.get("msg_id") == msg.msg_id
    )
    assert stored["feedback"]["kind"] == "down"
    assert stored["feedback"]["note"] == "bad"


def test_put_message_feedback_returns_none_when_message_unknown(
    table: FakeTable,
) -> None:
    """An unknown msg_id surfaces as None (the API layer turns into 404)."""
    from channel import storage
    from channel.models import FeedbackKind

    chat = create_chat(user_id="u-1", title=None, model_default="m")
    # Plant a message so the chat partition isn't empty; ask for a different msg_id.
    put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="reply", model="m")

    result = storage.put_message_feedback(
        chat_id=chat.chat_id,
        msg_id="does-not-exist",
        kind=FeedbackKind.UP,
        note=None,
    )

    assert result is None


def test_put_message_feedback_paginates_across_50_row_pages(
    table: FakeTable,
) -> None:
    """The lookup loop walks the message rows past the 50-row page boundary.

    Plant 60 messages and request feedback on the last one — the loop
    must consume the first page (cursor returned), then find the target
    on the second page. Guards against an off-by-one in the
    LastEvaluatedKey handling.
    """
    from channel import storage
    from channel.models import FeedbackKind

    chat = create_chat(user_id="u-1", title=None, model_default="m")
    target_msg = None
    for i in range(60):
        msg = put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text=f"r{i}", model="m")
        if i == 59:
            target_msg = msg
    assert target_msg is not None

    result = storage.put_message_feedback(
        chat_id=chat.chat_id,
        msg_id=target_msg.msg_id,
        kind=FeedbackKind.UP,
        note=None,
    )

    assert result is not None
    stored = next(
        item
        for item in table.items.values()
        if item["PK"] == f"CHAT#{chat.chat_id}" and item.get("msg_id") == target_msg.msg_id
    )
    assert stored["feedback"]["kind"] == "up"


def test_message_from_item_hydrates_feedback_attribute(table: FakeTable) -> None:
    """list_messages returns Message objects with feedback populated when present."""
    from channel import storage
    from channel.models import FeedbackKind

    chat = create_chat(user_id="u-1", title=None, model_default="m")
    msg = put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="reply", model="m")
    storage.put_message_feedback(
        chat_id=chat.chat_id, msg_id=msg.msg_id, kind=FeedbackKind.UP, note=None
    )

    msgs, _ = list_messages(chat.chat_id, limit=10, cursor=None)
    assert len(msgs) == 1
    assert msgs[0].feedback is not None
    assert msgs[0].feedback.kind is FeedbackKind.UP


def test_message_from_item_leaves_feedback_none_when_absent(table: FakeTable) -> None:
    """A message with no feedback attribute reads back as feedback=None."""
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="reply", model="m")

    msgs, _ = list_messages(chat.chat_id, limit=10, cursor=None)
    assert len(msgs) == 1
    assert msgs[0].feedback is None


def test_message_from_item_treats_malformed_feedback_as_none(table: FakeTable) -> None:
    """Stale schema or partial feedback payload should not break list_messages."""
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    msg = put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="reply", model="m")
    # Tamper the stored row to simulate a stale schema: missing
    # ``created_at`` field would fail pydantic validation.
    stored_key = next(
        key
        for key, item in table.items.items()
        if item["PK"] == f"CHAT#{chat.chat_id}" and item.get("msg_id") == msg.msg_id
    )
    table.items[stored_key]["feedback"] = {"kind": "up"}  # missing created_at

    msgs, _ = list_messages(chat.chat_id, limit=10, cursor=None)
    assert len(msgs) == 1
    assert msgs[0].feedback is None  # gracefully degraded
    assert msgs[0].text == "reply"  # rest of the message still hydrates


def test_put_message_feedback_caps_note_at_max_length(table: FakeTable) -> None:
    """Storage truncates oversized notes as defence-in-depth (API caps too)."""
    from channel import storage
    from channel.models import FeedbackKind
    from channel.storage import _FEEDBACK_NOTE_MAX_CHARS

    chat = create_chat(user_id="u-1", title=None, model_default="m")
    msg = put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="reply", model="m")

    long_note = "x" * (_FEEDBACK_NOTE_MAX_CHARS + 500)
    result = storage.put_message_feedback(
        chat_id=chat.chat_id,
        msg_id=msg.msg_id,
        kind=FeedbackKind.UP,
        note=long_note,
    )
    assert result is not None
    assert result.note is not None
    assert len(result.note) == _FEEDBACK_NOTE_MAX_CHARS

    stored = next(
        item
        for item in table.items.values()
        if item["PK"] == f"CHAT#{chat.chat_id}" and item.get("msg_id") == msg.msg_id
    )
    assert len(stored["feedback"]["note"]) == _FEEDBACK_NOTE_MAX_CHARS


def test_put_message_feedback_returns_none_on_concurrent_delete(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Race: the message row is deleted between query and update_item.

    The ConditionExpression on update_item refuses the upsert and
    raises ConditionalCheckFailedException; the helper surfaces the
    same None-return as a never-existed msg_id so the API layer can
    still 404 cleanly without inventing a phantom feedback row.
    """
    from channel import storage
    from channel.models import FeedbackKind

    chat = create_chat(user_id="u-1", title=None, model_default="m")
    msg = put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="reply", model="m")

    original_update = table.update_item

    def fake_update(*args: Any, **kwargs: Any) -> Any:
        # Simulate the row vanishing between query and update.
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "gone"}},
            "UpdateItem",
        )

    monkeypatch.setattr(table, "update_item", fake_update)
    result = storage.put_message_feedback(
        chat_id=chat.chat_id,
        msg_id=msg.msg_id,
        kind=FeedbackKind.UP,
        note=None,
    )
    assert result is None

    # Restore so any subsequent table interaction is unaffected.
    monkeypatch.setattr(table, "update_item", original_update)


def test_put_message_feedback_rejects_user_message_rows(table: FakeTable) -> None:
    """Feedback is for assistant turns only — user-message msg_id → None.

    Surfaces the same as a missing msg_id so the API layer can 404
    without leaking which msg_ids exist. Guards against data pollution
    (e.g. self-feedback on user messages skewing evaluation data).
    """
    from channel import storage
    from channel.models import FeedbackKind

    chat = create_chat(user_id="u-1", title=None, model_default="m")
    user_msg = put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="hi", model=None)

    result = storage.put_message_feedback(
        chat_id=chat.chat_id,
        msg_id=user_msg.msg_id,
        kind=FeedbackKind.UP,
        note=None,
    )

    assert result is None
    # And the user row stays free of any feedback attribute.
    stored = next(
        item
        for item in table.items.values()
        if item["PK"] == f"CHAT#{chat.chat_id}" and item.get("msg_id") == user_msg.msg_id
    )
    assert "feedback" not in stored


def test_put_message_feedback_propagates_unexpected_client_errors(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-Conditional error from DDB (e.g. throttling) is re-raised."""
    from channel import storage
    from channel.models import FeedbackKind

    chat = create_chat(user_id="u-1", title=None, model_default="m")
    msg = put_message(chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="reply", model="m")

    def fake_update(*args: Any, **kwargs: Any) -> Any:
        raise ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "slow down",
                }
            },
            "UpdateItem",
        )

    monkeypatch.setattr(table, "update_item", fake_update)
    with pytest.raises(ClientError):
        storage.put_message_feedback(
            chat_id=chat.chat_id,
            msg_id=msg.msg_id,
            kind=FeedbackKind.UP,
            note=None,
        )


# ----------------------------------------------------------------
# Attachments (#174) — file attachments + vision (epic #109)
# ----------------------------------------------------------------


def _attachment(**overrides: Any) -> Any:
    """Build an Attachment for storage tests.

    ``s3_key`` derives from ``id`` (and ``user_id``) by default — the
    production presign code follows the same shape, and tests that
    construct multiple attachments need distinct keys.
    """

    from channel.models import Attachment

    att_id = overrides.get("id", "att-1")
    user_id = overrides.get("user_id", "u-1")
    base = {
        "id": att_id,
        "user_id": user_id,
        "name": "spec.pdf",
        "mime": "application/pdf",
        "size_bytes": 12345,
        "s3_key": f"attachments/user/{user_id}/{att_id}",
        "s3_bucket": "channel-attachments-dev",
        "checksum_sha256": "abc123",
        "created_at": "2026-06-03T00:00:00Z",
    }
    base.update(overrides)
    return Attachment(**base)


def test_put_attachment_writes_user_partitioned_row(table: FakeTable) -> None:
    from channel import storage

    att = _attachment()
    storage.put_attachment(att)

    stored = table.items[("USER#u-1", "ATTACHMENT#att-1")]
    assert stored["PK"] == "USER#u-1"
    assert stored["SK"] == "ATTACHMENT#att-1"
    assert stored["id"] == "att-1"
    assert stored["name"] == "spec.pdf"
    assert stored["mime"] == "application/pdf"
    assert stored["size_bytes"] == 12345
    assert stored["s3_key"] == "attachments/user/u-1/att-1"
    assert stored["s3_bucket"] == "channel-attachments-dev"
    assert stored["checksum_sha256"] == "abc123"


def test_put_attachment_omits_referenced_at_when_none(table: FakeTable) -> None:
    """``referenced_at`` defaults to None — don't write a NULL attribute."""

    from channel import storage

    storage.put_attachment(_attachment())
    stored = table.items[("USER#u-1", "ATTACHMENT#att-1")]
    assert "referenced_at" not in stored


def test_put_attachment_writes_referenced_at_when_set(table: FakeTable) -> None:
    from channel import storage

    storage.put_attachment(_attachment(referenced_at="2026-06-03T00:01:00Z"))
    stored = table.items[("USER#u-1", "ATTACHMENT#att-1")]
    assert stored["referenced_at"] == "2026-06-03T00:01:00Z"


def test_get_attachment_returns_model_on_hit(table: FakeTable) -> None:
    from channel import storage

    storage.put_attachment(_attachment())
    fetched = storage.get_attachment(user_id="u-1", att_id="att-1")
    assert fetched is not None
    assert fetched.id == "att-1"
    assert fetched.s3_key == "attachments/user/u-1/att-1"


def test_get_attachment_returns_none_on_miss(table: FakeTable) -> None:
    from channel import storage

    assert storage.get_attachment(user_id="u-1", att_id="nope") is None


def test_delete_attachment_removes_row(table: FakeTable) -> None:
    from channel import storage

    storage.put_attachment(_attachment())
    storage.delete_attachment(user_id="u-1", att_id="att-1")
    assert ("USER#u-1", "ATTACHMENT#att-1") not in table.items


def test_delete_attachment_is_idempotent(table: FakeTable) -> None:
    """DDB DeleteItem is naturally idempotent — the second call is a no-op."""

    from channel import storage

    storage.delete_attachment(user_id="u-1", att_id="never-existed")  # no raise
    storage.delete_attachment(user_id="u-1", att_id="never-existed")


def test_mark_attachment_referenced_swallows_conditional_check_failure(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the canonical row was deleted between get_attachment and the
    update (concurrent chat-delete cascade race), DynamoDB raises
    ConditionalCheckFailedException — swallow it instead of creating a
    ghost item with just PK/SK/referenced_at."""

    from channel import storage

    def fake_update(**kwargs: Any) -> Any:
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no row"}},
            "UpdateItem",
        )

    monkeypatch.setattr(table, "update_item", fake_update)
    # Should NOT raise — race losses are harmless.
    storage.mark_attachment_referenced(user_id="u-1", att_id="att-1")


def test_mark_attachment_referenced_re_raises_other_client_errors(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unrelated DDB errors (throttling, etc.) must still propagate so the
    caller sees the real failure instead of a silent drop."""

    from channel import storage

    def fake_update(**kwargs: Any) -> Any:
        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}},
            "UpdateItem",
        )

    monkeypatch.setattr(table, "update_item", fake_update)
    with pytest.raises(ClientError):
        storage.mark_attachment_referenced(user_id="u-1", att_id="att-1")


def test_mark_attachment_referenced_stamps_iso_timestamp(table: FakeTable) -> None:
    """#176 — when a message references an attachment, stamp
    ``referenced_at`` so the lifecycle rule stops eyeing the S3
    object for GC. Issued via UpdateItem so the existing row's other
    attributes (name, mime, etc.) stay intact."""

    from channel import storage

    storage.put_attachment(_attachment())
    storage.mark_attachment_referenced(user_id="u-1", att_id="att-1")

    stored = table.items[("USER#u-1", "ATTACHMENT#att-1")]
    assert "referenced_at" in stored
    # ISO-8601 with microseconds — matches _now_iso()'s shape
    assert stored["referenced_at"].endswith("+00:00") or stored["referenced_at"].endswith("Z")
    # Other fields preserved
    assert stored["name"] == "spec.pdf"


# ---- S3 verify helper -------------------------------------------


class _BytesBody:
    """Stand-in for the StreamingBody returned by ``s3.get_object``.

    boto3's real ``Body`` is a streaming response with a blocking
    ``read()`` — the helper needs ``read()`` plus ``close()`` for the
    finalizer path in :func:`channel.storage.get_attachment_bytes`.
    A test can pin a ``read_error`` to simulate a mid-stream failure.
    """

    def __init__(self, data: bytes, read_error: Exception | None = None) -> None:
        self._data = data
        self._read_error = read_error
        self.closed = False

    def read(self) -> bytes:
        if self._read_error is not None:
            raise self._read_error
        return self._data

    def close(self) -> None:
        self.closed = True


class _FakeS3:
    """Minimal stand-in for the boto3 S3 client used by storage helpers."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.bodies: dict[tuple[str, str], bytes] = {}
        self.deleted: list[tuple[str, str]] = []
        # Lets a test pin a specific ClientError code on a key.
        self.get_errors: dict[tuple[str, str], str] = {}
        # Per-key mid-read failures: raised from the returned ``Body.read()``.
        self.body_read_errors: dict[tuple[str, str], Exception] = {}
        self.delete_errors: dict[tuple[str, str], str] = {}
        # Track every body the fake handed out so a test can assert it was
        # closed even on the failure path.
        self.handed_out_bodies: list[_BytesBody] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        code = self.get_errors.get((Bucket, Key))
        if code is not None:
            raise ClientError(
                {"Error": {"Code": code, "Message": code}},
                "GetObject",
            )
        if (Bucket, Key) not in self.bodies:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}},
                "GetObject",
            )
        body = _BytesBody(
            self.bodies[(Bucket, Key)],
            read_error=self.body_read_errors.get((Bucket, Key)),
        )
        self.handed_out_bodies.append(body)
        return {"Body": body}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        code = self.delete_errors.get((Bucket, Key))
        if code is not None:
            raise ClientError(
                {"Error": {"Code": code, "Message": code}},
                "DeleteObject",
            )
        self.deleted.append((Bucket, Key))
        self.objects.pop((Bucket, Key), None)
        return {}


@pytest.fixture
def s3_client(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    fake = _FakeS3()
    monkeypatch.setattr("channel.storage._get_s3_client", lambda: fake)
    return fake


def test_get_attachment_bytes_returns_payload_on_success(
    s3_client: _FakeS3,
) -> None:
    from channel import storage

    att = _attachment()
    payload = b"%PDF-1.4 fake pdf bytes"
    s3_client.bodies[(att.s3_bucket, att.s3_key)] = payload
    data, reason = storage.get_attachment_bytes(att)
    assert data == payload
    assert reason is None


def test_get_attachment_bytes_returns_not_found_on_404(
    s3_client: _FakeS3,
) -> None:
    from channel import storage

    data, reason = storage.get_attachment_bytes(_attachment())
    assert data is None
    assert reason == "S3 object not found"


def test_get_attachment_bytes_surfaces_other_error_codes(
    s3_client: _FakeS3,
) -> None:
    """Permissions and other failures must surface a non-empty reason so
    the structured failure block in #176 can render a useful marker."""

    from channel import storage

    att = _attachment()
    s3_client.get_errors[(att.s3_bucket, att.s3_key)] = "AccessDenied"
    data, reason = storage.get_attachment_bytes(att)
    assert data is None
    assert reason is not None
    assert "AccessDenied" in reason


def test_get_attachment_bytes_converts_streaming_read_failure(
    s3_client: _FakeS3,
) -> None:
    """A mid-read ``ReadTimeoutError`` (or any boto/OS-level failure) must
    surface as the ``(None, reason)`` shape so the SSE generator can
    render a labeled marker — otherwise the exception would crash the
    in-flight stream."""

    from botocore.exceptions import ReadTimeoutError

    from channel import storage

    att = _attachment()
    s3_client.bodies[(att.s3_bucket, att.s3_key)] = b"would-be-payload"
    s3_client.body_read_errors[(att.s3_bucket, att.s3_key)] = ReadTimeoutError(
        endpoint_url="https://example.com"
    )
    data, reason = storage.get_attachment_bytes(att)
    assert data is None
    assert reason is not None
    assert "ReadTimeoutError" in reason


def test_get_attachment_bytes_closes_body_on_success(
    s3_client: _FakeS3,
) -> None:
    """Hygiene: the StreamingBody is closed even on the happy path so
    the underlying HTTP connection returns to the pool."""

    from channel import storage

    att = _attachment()
    s3_client.bodies[(att.s3_bucket, att.s3_key)] = b"payload"
    data, _ = storage.get_attachment_bytes(att)
    assert data == b"payload"
    assert all(body.closed for body in s3_client.handed_out_bodies)


def test_get_attachment_bytes_closes_body_on_read_failure(
    s3_client: _FakeS3,
) -> None:
    """Hygiene under failure: the body still closes after a mid-read
    exception so a transient timeout doesn't leak a pooled connection."""

    from botocore.exceptions import ReadTimeoutError

    from channel import storage

    att = _attachment()
    s3_client.bodies[(att.s3_bucket, att.s3_key)] = b"would-be-payload"
    s3_client.body_read_errors[(att.s3_bucket, att.s3_key)] = ReadTimeoutError(
        endpoint_url="https://example.com"
    )
    storage.get_attachment_bytes(att)
    assert all(body.closed for body in s3_client.handed_out_bodies)


# ---- chat-delete cascade -----------------------------------------


def _put_msg_with_attachments(
    table: FakeTable, chat_id: str, msg_id: str, att_ids: list[str]
) -> None:
    """Write a message row with denormalised attachment snapshots."""

    table.items[(f"CHAT#{chat_id}", f"MSG#2026-06-03T00:00:00Z#{msg_id}")] = {
        "PK": f"CHAT#{chat_id}",
        "SK": f"MSG#2026-06-03T00:00:00Z#{msg_id}",
        "chat_id": chat_id,
        "msg_id": msg_id,
        "role": "user",
        "text": "with attachments",
        "attachments": [
            {"id": a, "name": f"{a}.pdf", "mime": "application/pdf", "size_bytes": 1}
            for a in att_ids
        ],
        "created_at": "2026-06-03T00:00:00Z",
    }


def test_delete_chat_attachments_returns_zero_when_no_attachments(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    from channel import storage

    # Empty chat — no messages, nothing to do.
    deleted, failed = storage.delete_chat_attachments(chat_id="c-1", user_id="u-1")
    assert deleted == 0
    assert failed == 0


def test_delete_chat_attachments_deletes_referenced_rows_and_s3_objects(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    from channel import storage

    storage.put_attachment(_attachment(id="att-1"))
    storage.put_attachment(_attachment(id="att-2"))
    s3_client.objects[("channel-attachments-dev", "attachments/user/u-1/att-1")] = {}
    s3_client.objects[("channel-attachments-dev", "attachments/user/u-1/att-2")] = {}
    _put_msg_with_attachments(table, "c-1", "m-1", ["att-1", "att-2"])

    deleted, failed = storage.delete_chat_attachments(chat_id="c-1", user_id="u-1")

    assert (deleted, failed) == (2, 0)
    assert ("USER#u-1", "ATTACHMENT#att-1") not in table.items
    assert ("USER#u-1", "ATTACHMENT#att-2") not in table.items
    assert ("channel-attachments-dev", "attachments/user/u-1/att-1") in s3_client.deleted
    assert ("channel-attachments-dev", "attachments/user/u-1/att-2") in s3_client.deleted


def test_delete_chat_attachments_deduplicates_across_messages(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    """Two messages referencing the same attachment id ⇒ one delete, not two."""

    from channel import storage

    storage.put_attachment(_attachment(id="att-1"))
    s3_client.objects[("channel-attachments-dev", "attachments/user/u-1/att-1")] = {}
    _put_msg_with_attachments(table, "c-1", "m-1", ["att-1"])
    _put_msg_with_attachments(table, "c-1", "m-2", ["att-1"])

    deleted, failed = storage.delete_chat_attachments(chat_id="c-1", user_id="u-1")
    assert (deleted, failed) == (1, 0)
    # Idempotent S3 delete — only one entry recorded.
    assert s3_client.deleted.count(("channel-attachments-dev", "attachments/user/u-1/att-1")) == 1


def test_delete_chat_attachments_counts_s3_delete_failures(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    from channel import storage

    storage.put_attachment(_attachment(id="att-1"))
    storage.put_attachment(_attachment(id="att-2"))
    s3_client.objects[("channel-attachments-dev", "attachments/user/u-1/att-1")] = {}
    s3_client.delete_errors[("channel-attachments-dev", "attachments/user/u-1/att-2")] = (
        "InternalError"
    )
    _put_msg_with_attachments(table, "c-1", "m-1", ["att-1", "att-2"])

    deleted, failed = storage.delete_chat_attachments(chat_id="c-1", user_id="u-1")
    # att-1 deleted cleanly; att-2 S3 delete failed so the cascade keeps the
    # DDB row (orphan recovery later). One success, one failure.
    assert (deleted, failed) == (1, 1)
    assert ("USER#u-1", "ATTACHMENT#att-1") not in table.items
    assert ("USER#u-1", "ATTACHMENT#att-2") in table.items


def test_delete_chat_attachments_counts_missing_canonical_row_as_failure(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    """If the canonical ATTACHMENT row is gone, the helper can't know the
    S3 key — count it as a failure rather than silently no-op'ing."""

    from channel import storage

    # No put_attachment call — canonical row missing.
    _put_msg_with_attachments(table, "c-1", "m-1", ["ghost"])

    deleted, failed = storage.delete_chat_attachments(chat_id="c-1", user_id="u-1")
    assert (deleted, failed) == (0, 1)


def test_delete_chat_attachments_paginates_message_query(
    monkeypatch: pytest.MonkeyPatch, s3_client: _FakeS3
) -> None:
    """The cascade must follow LastEvaluatedKey across pages so a chat
    with more attachments than fit in one DDB page is fully drained."""

    from channel import storage

    storage_table = FakeTable()
    monkeypatch.setattr("channel.storage._get_table", lambda: storage_table)
    storage.put_attachment(_attachment(id="att-1"))
    storage.put_attachment(_attachment(id="att-2"))
    s3_client.objects[("channel-attachments-dev", "attachments/user/u-1/att-1")] = {}
    s3_client.objects[("channel-attachments-dev", "attachments/user/u-1/att-2")] = {}

    # Hand-craft a two-page query response — the FakeTable's auto-pagination
    # only triggers on Limit, but the cascade query doesn't set one.
    pages = [
        {
            "Items": [{"attachments": [{"id": "att-1"}]}],
            "LastEvaluatedKey": {"PK": "CHAT#c-1", "SK": "MSG#page-1"},
        },
        {
            "Items": [{"attachments": [{"id": "att-2"}]}],
        },
    ]
    seen_starts: list[Any] = []

    def fake_query(**kwargs: Any) -> dict[str, Any]:
        seen_starts.append(kwargs.get("ExclusiveStartKey"))
        return pages.pop(0)

    monkeypatch.setattr(storage_table, "query", fake_query)

    deleted, failed = storage.delete_chat_attachments(chat_id="c-1", user_id="u-1")
    assert (deleted, failed) == (2, 0)
    # First call has no ExclusiveStartKey; second carries the page-1 cursor.
    assert seen_starts == [None, {"PK": "CHAT#c-1", "SK": "MSG#page-1"}]


def test_delete_chat_attachments_counts_ddb_delete_failures(
    table: FakeTable, s3_client: _FakeS3, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3 deletion succeeds but the DDB row delete fails — the helper
    must catch the ClientError, count the cascade as failed, and keep
    going."""

    from channel import storage

    storage.put_attachment(_attachment(id="att-1"))
    storage.put_attachment(_attachment(id="att-2"))
    s3_client.objects[("channel-attachments-dev", "attachments/user/u-1/att-1")] = {}
    s3_client.objects[("channel-attachments-dev", "attachments/user/u-1/att-2")] = {}
    _put_msg_with_attachments(table, "c-1", "m-1", ["att-1", "att-2"])

    original_delete = table.delete_item

    def flaky_delete(Key: dict[str, str]) -> dict[str, Any]:
        if Key.get("SK") == "ATTACHMENT#att-2":
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException"}},
                "DeleteItem",
            )
        return original_delete(Key)

    monkeypatch.setattr(table, "delete_item", flaky_delete)

    deleted, failed = storage.delete_chat_attachments(chat_id="c-1", user_id="u-1")
    assert (deleted, failed) == (1, 1)
    # S3 side ran for both — the failure is on the DDB side.
    assert ("channel-attachments-dev", "attachments/user/u-1/att-2") in s3_client.deleted


# ----------------------------------------------------------------
# S3 client signature config (#196)
# ----------------------------------------------------------------


def test_get_s3_client_uses_sigv4(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boto3's default signature for ``s3.amazonaws.com`` is SigV2, and
    S3 rejects SigV2-signed presigned PUTs against KMS-encrypted buckets
    with ``InvalidArgument``. ``_get_s3_client`` must pin SigV4 so every
    presigned URL the Lambda mints is acceptable to S3.

    Regression for #196. See the issue body for the live-dev repro."""

    from channel import storage

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    client = storage._get_s3_client()
    assert client.meta.config.signature_version == "s3v4"


# ---------------------------------------------------------------------------
# JTI revocation denylist (#240)
# ---------------------------------------------------------------------------


def test_is_jti_denied_returns_false_when_row_absent(table: FakeTable) -> None:
    assert is_jti_denied("never-revoked") is False


def test_deny_jti_writes_denylist_row_then_is_denied(table: FakeTable) -> None:
    exp = 1_700_000_000
    deny_jti("tok-jti-1", exp=exp)

    stored = table.items[("DENY#tok-jti-1", "META")]
    assert stored["type"] == "DENY"
    # ttl == the denied token's own exp so DDB TTL prunes the row exactly
    # when the token would have expired anyway.
    assert stored["ttl"] == exp
    # revoked_at is a recorded ISO-8601 timestamp, not blank.
    assert stored["revoked_at"]
    assert is_jti_denied("tok-jti-1") is True


def test_deny_jti_is_idempotent(table: FakeTable) -> None:
    # Re-revoking the same JTI must not raise (a logout retry is harmless).
    deny_jti("tok-jti-2", exp=1_700_000_000)
    deny_jti("tok-jti-2", exp=1_700_000_000)
    assert is_jti_denied("tok-jti-2") is True


def test_is_jti_denied_isolates_by_jti(table: FakeTable) -> None:
    deny_jti("tok-A", exp=1_700_000_000)
    assert is_jti_denied("tok-A") is True
    assert is_jti_denied("tok-B") is False


def test_is_jti_denied_uses_strongly_consistent_read(table: FakeTable) -> None:
    # Revocation is a read-after-write check (logout writes the DENY row,
    # the next request reads it), so it must use a strongly-consistent
    # read rather than race DynamoDB's eventual-consistency window.
    is_jti_denied("tok-consistency")
    assert table.last_get_item_kwargs.get("ConsistentRead") is True


# ---------------------------------------------------------------------------
# Admin read helpers (#234) — scan_users / derive_users_from_chat_index /
# list_audit_events_for_actor / count_active_users
# ---------------------------------------------------------------------------


def _put_user_meta(table: FakeTable, user_id: str, **attrs: Any) -> None:
    """Seed a USER#META row directly — the #110 writer doesn't exist yet."""

    table.put_item(Item={"PK": f"USER#{user_id}", "SK": "META", "user_id": user_id, **attrs})


def _put_audit_row(
    table: FakeTable,
    *,
    actor_id: str,
    event_type: str,
    at: datetime,
    event_id: str,
) -> dict[str, Any]:
    """Craft an audit row in the shard for ``at`` (backdating helper)."""

    item = {
        "PK": f"AUDIT#{at:%Y-%m-%d}#{at:%H}",
        "SK": f"{int(at.timestamp())}#{event_id}",
        "event_id": event_id,
        "event_type": event_type,
        "actor_id": actor_id,
        "created_at": at.isoformat(timespec="microseconds"),
    }
    table.put_item(Item=item)
    return item


def test_scan_users_empty_table(table: FakeTable) -> None:
    rows, cursor = scan_users()
    assert rows == []
    assert cursor is None


def test_scan_users_returns_only_user_meta_rows(table: FakeTable) -> None:
    """Chat-index / message / PREFS / other SK=META families are all excluded."""

    _put_user_meta(table, "u-1", email="one@example.com")
    _put_user_meta(table, "u-2", email="two@example.com")
    # Noise: chat-index row (PK=USER#*, SK=CHAT#*), message row,
    # PREFS row, and non-USER families whose SK is also META.
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="hi", model=None)
    table.put_item(Item={"PK": "USER#u-1", "SK": "PREFS", "prefs": {}})
    table.put_item(Item={"PK": "MGMT_STATE#abc", "SK": "META", "ttl": 1})
    table.put_item(Item={"PK": "DENY#jti-1", "SK": "META", "ttl": 1})

    rows, cursor = scan_users()
    assert sorted(r["user_id"] for r in rows) == ["u-1", "u-2"]
    assert cursor is None


def test_scan_users_paginates_with_cursor(table: FakeTable) -> None:
    """Walk 5 users two-at-a-time; every row appears exactly once."""

    for i in range(5):
        _put_user_meta(table, f"u-{i}")

    seen: list[str] = []
    cursor: dict[str, Any] | None = None
    pages = 0
    while True:
        rows, cursor = scan_users(cursor=cursor, limit=2)
        seen.extend(r["user_id"] for r in rows)
        pages += 1
        if cursor is None:
            break
    assert sorted(seen) == [f"u-{i}" for i in range(5)]
    assert len(seen) == len(set(seen))  # no duplicates across cursor hops
    assert pages == 3  # 2 + 2 + 1


def test_scan_users_clamps_limit_to_hard_cap(table: FakeTable) -> None:
    """Caller-supplied limits above 100 are capped (issue #234 soft-cap)."""

    for i in range(101):
        _put_user_meta(table, f"u-{i:03d}")

    rows, cursor = scan_users(limit=999)
    assert len(rows) == 100
    assert cursor is not None
    rest, cursor = scan_users(cursor=cursor, limit=999)
    assert len(rest) == 1
    assert cursor is None


def test_scan_users_clamps_limit_floor_to_one(table: FakeTable) -> None:
    _put_user_meta(table, "u-1")
    _put_user_meta(table, "u-2")
    rows, cursor = scan_users(limit=0)
    assert len(rows) == 1
    assert cursor is not None


def test_scan_users_page_cap_returns_cursor_not_silence(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the per-call Scan budget is exhausted before ``limit`` matching
    rows are found, the helper must surface the resume cursor — callers
    treat the cursor (not the row count) as the exhaustion signal."""

    from channel import storage

    # One page of two evaluated items per call. The two chat-index rows
    # sort ahead of the META row (CHAT# < META within the same PK), so
    # the first call's whole budget is spent on filtered-out noise.
    monkeypatch.setattr(storage, "_ADMIN_SCAN_PAGE_LIMIT", 2)
    monkeypatch.setattr(storage, "_ADMIN_SCAN_MAX_PAGES", 1)
    create_chat(user_id="a-noise", title=None, model_default="m")
    create_chat(user_id="a-noise", title=None, model_default="m")
    _put_user_meta(table, "zz-real")

    rows, cursor = scan_users(limit=50)
    assert rows == []
    assert cursor is not None

    # Resuming with the returned cursor eventually finds the real row.
    seen: list[str] = []
    while cursor is not None:
        rows, cursor = scan_users(cursor=cursor, limit=50)
        seen.extend(r["user_id"] for r in rows)
    assert seen == ["zz-real"]


def test_derive_users_empty_table(table: FakeTable) -> None:
    rows, cursor = derive_users_from_chat_index()
    assert rows == []
    assert cursor is None


def test_derive_users_aggregates_per_user(table: FakeTable) -> None:
    """created_at = min, chat_count = rows, last_chat_at = max activity."""

    first = create_chat(user_id="u-a", title=None, model_default="m")
    second = create_chat(user_id="u-a", title=None, model_default="m")
    other = create_chat(user_id="u-b", title=None, model_default="m")
    bumped = "2999-01-01T00:00:00.000000+00:00"
    update_chat_index(
        user_id="u-a",
        chat=first,
        last_user_preview="hi",
        delta_count=1,
        last_message_at=bumped,
    )

    rows, cursor = derive_users_from_chat_index()
    assert cursor is None
    assert [r["user_id"] for r in rows] == ["u-a", "u-b"]  # sorted by user_id
    ua, ub = rows
    assert ua["chat_count"] == 2
    assert ua["created_at"] == min(first.created_at, second.created_at)
    assert ua["last_chat_at"] == bumped
    assert ub["chat_count"] == 1
    assert ub["created_at"] == other.created_at
    assert ub["last_chat_at"] == other.last_message_at


def test_derive_users_paginates_aggregated_list(table: FakeTable) -> None:
    for uid in ("u-a", "u-b", "u-c"):
        create_chat(user_id=uid, title=None, model_default="m")

    page1, cursor1 = derive_users_from_chat_index(limit=2)
    assert [r["user_id"] for r in page1] == ["u-a", "u-b"]
    assert cursor1 == {"user_id": "u-b"}

    page2, cursor2 = derive_users_from_chat_index(cursor=cursor1, limit=2)
    assert [r["user_id"] for r in page2] == ["u-c"]
    assert cursor2 is None


def test_derive_users_limit_none_returns_full_list_in_one_call(table: FakeTable) -> None:
    """``limit=None`` disables pagination — the whole aggregate, no cursor.

    Used by the #235 list endpoint, which needs every row for global
    sorting; cursor-looping would re-run the full chat-index scan per
    page for data this function already aggregated on the first call.
    """

    for uid in ("u-a", "u-b", "u-c"):
        create_chat(user_id=uid, title=None, model_default="m")

    rows, cursor = derive_users_from_chat_index(limit=None)
    assert [r["user_id"] for r in rows] == ["u-a", "u-b", "u-c"]
    assert cursor is None


def test_derive_users_skips_malformed_rows(table: FakeTable) -> None:
    """Rows missing user_id or created_at must not corrupt the fold."""

    create_chat(user_id="u-good", title=None, model_default="m")
    table.put_item(  # no user_id
        Item={"PK": "USER#u-bad", "SK": "CHAT#2026-01-01#x", "created_at": "2026-01-01"}
    )
    table.put_item(  # no created_at
        Item={"PK": "USER#u-bad2", "SK": "CHAT#2026-01-01#y", "user_id": "u-bad2"}
    )

    rows, _ = derive_users_from_chat_index()
    assert [r["user_id"] for r in rows] == ["u-good"]


def test_derive_users_falls_back_to_created_at_for_legacy_rows(table: FakeTable) -> None:
    """Rows that pre-date last_message_at use created_at as the activity mark."""

    table.put_item(
        Item={
            "PK": "USER#u-legacy",
            "SK": "CHAT#2026-01-01T00:00:00#legacy-chat",
            "user_id": "u-legacy",
            "created_at": "2026-01-01T00:00:00.000000+00:00",
        }
    )
    rows, _ = derive_users_from_chat_index()
    assert rows[0]["last_chat_at"] == "2026-01-01T00:00:00.000000+00:00"


def test_derive_users_follows_scan_pagination(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The internal Scan loop must walk LastEvaluatedKey to exhaustion —
    a user's chats can span pages, and a partial walk would under-count."""

    from channel import storage

    monkeypatch.setattr(storage, "_ADMIN_SCAN_PAGE_LIMIT", 1)
    create_chat(user_id="u-a", title=None, model_default="m")
    create_chat(user_id="u-a", title=None, model_default="m")
    create_chat(user_id="u-b", title=None, model_default="m")

    rows, _ = derive_users_from_chat_index()
    assert {r["user_id"]: r["chat_count"] for r in rows} == {"u-a": 2, "u-b": 1}


def test_list_audit_events_empty(table: FakeTable) -> None:
    assert list_audit_events_for_actor("nobody@example.com") == []


def test_list_audit_events_filters_by_actor(table: FakeTable) -> None:
    from channel import storage

    mine = storage.put_audit_event(event_type="auth.login", actor_id="me@example.com")
    storage.put_audit_event(event_type="auth.login", actor_id="other@example.com")

    events = list_audit_events_for_actor("me@example.com")
    assert [e["event_id"] for e in events] == [mine["event_id"]]


def test_list_audit_events_walks_older_shards_newest_first(table: FakeTable) -> None:
    """An event two hours back (different shard) is found and ordered after
    the current-hour event."""

    now = datetime.now(timezone.utc)
    old = _put_audit_row(
        table,
        actor_id="me@example.com",
        event_type="auth.login",
        at=now - timedelta(hours=2),
        event_id="evt-old",
    )
    from channel import storage

    fresh = storage.put_audit_event(event_type="auth.login", actor_id="me@example.com")

    events = list_audit_events_for_actor("me@example.com")
    assert [e["event_id"] for e in events] == [fresh["event_id"], old["event_id"]]


def test_list_audit_events_event_type_filter(table: FakeTable) -> None:
    from channel import storage

    login = storage.put_audit_event(event_type="auth.login", actor_id="me@example.com")
    storage.put_audit_event(event_type="auth.logout", actor_id="me@example.com")

    events = list_audit_events_for_actor("me@example.com", event_type="auth.login")
    assert [e["event_id"] for e in events] == [login["event_id"]]


def test_list_audit_events_since_filter_excludes_older(table: FakeTable) -> None:
    now = datetime.now(timezone.utc)
    _put_audit_row(
        table,
        actor_id="me@example.com",
        event_type="auth.login",
        at=now - timedelta(hours=2),
        event_id="evt-old",
    )
    from channel import storage

    fresh = storage.put_audit_event(event_type="auth.login", actor_id="me@example.com")

    since = (now - timedelta(hours=1)).isoformat(timespec="microseconds")
    events = list_audit_events_for_actor("me@example.com", since_iso=since)
    assert [e["event_id"] for e in events] == [fresh["event_id"]]


def test_list_audit_events_since_bounds_shard_walk(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """since_iso must shrink the shard fan-out, not just filter rows —
    an unbounded miss costs 168 empty Queries."""

    calls: list[str] = []
    original_query = table.query

    def counting_query(**kwargs: Any) -> dict[str, Any]:
        calls.append("q")
        return original_query(**kwargs)

    monkeypatch.setattr(table, "query", counting_query)
    since = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    list_audit_events_for_actor("me@example.com", since_iso=since)
    # Current-hour shard only — allow 2 in case the wall clock crosses an
    # hour boundary between this test's ``since`` and the helper's ``now``.
    # The point is "about one", not "all 168".
    assert len(calls) <= 2


def test_list_audit_events_shard_walk_capped_at_seven_days(table: FakeTable) -> None:
    now = datetime.now(timezone.utc)
    _put_audit_row(
        table,
        actor_id="me@example.com",
        event_type="auth.login",
        at=now - timedelta(hours=200),
        event_id="evt-ancient",
    )
    assert list_audit_events_for_actor("me@example.com") == []


def test_list_audit_events_respects_limit(table: FakeTable) -> None:
    now = datetime.now(timezone.utc)
    for i in range(3):
        _put_audit_row(
            table,
            actor_id="me@example.com",
            event_type="auth.login",
            at=now - timedelta(hours=i + 1),
            event_id=f"evt-{i}",
        )
    events = list_audit_events_for_actor("me@example.com", limit=2)
    assert [e["event_id"] for e in events] == ["evt-0", "evt-1"]


def test_list_audit_events_follows_query_pagination_within_shard(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filter-thinned Query page must not truncate the shard read."""

    from channel import storage

    monkeypatch.setattr(storage, "_ADMIN_SCAN_PAGE_LIMIT", 1)
    now = datetime.now(timezone.utc)
    shard_base = now.replace(minute=30, second=0, microsecond=0)
    for i in range(3):
        _put_audit_row(
            table,
            actor_id="me@example.com",
            event_type="auth.login",
            at=shard_base.replace(second=i),
            event_id=f"evt-{i}",
        )
    events = list_audit_events_for_actor("me@example.com")
    assert sorted(e["event_id"] for e in events) == ["evt-0", "evt-1", "evt-2"]


def test_list_audit_events_accepts_z_suffix_and_naive_since(table: FakeTable) -> None:
    """The since bound tolerates trailing-Z and naive ISO forms — both are
    normalized to the stored +00:00 microseconds shape before comparing."""

    from channel import storage

    fresh = storage.put_audit_event(event_type="auth.login", actor_id="me@example.com")
    now = datetime.now(timezone.utc)
    z_form = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    naive_form = (now - timedelta(minutes=5)).replace(tzinfo=None).isoformat()

    for since in (z_form, naive_form):
        events = list_audit_events_for_actor("me@example.com", since_iso=since)
        assert [e["event_id"] for e in events] == [fresh["event_id"]]


def test_parse_iso_utc_converts_offsets_to_utc() -> None:
    from channel.storage import _parse_iso_utc

    dt = _parse_iso_utc("2026-07-12T12:00:00+02:00")
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 10


def test_count_active_users_empty_table(table: FakeTable) -> None:
    assert count_active_users("2026-01-01T00:00:00+00:00") == 0


def test_count_active_users_distinct_within_window(table: FakeTable) -> None:
    """Two active chats for one user count once; stale users don't count."""

    window = "2500-01-01T00:00:00.000000+00:00"
    active = "2500-06-01T00:00:00.000000+00:00"
    for uid, n in (("u-a", 2), ("u-b", 1)):
        for _ in range(n):
            chat = create_chat(user_id=uid, title=None, model_default="m")
            update_chat_index(
                user_id=uid,
                chat=chat,
                last_user_preview="x",
                delta_count=1,
                last_message_at=active,
            )
    create_chat(user_id="u-stale", title=None, model_default="m")  # last_message_at = now

    assert count_active_users(window) == 2


def test_count_active_users_window_boundary_is_inclusive(table: FakeTable) -> None:
    boundary = "2500-01-01T00:00:00.000000+00:00"
    chat = create_chat(user_id="u-edge", title=None, model_default="m")
    update_chat_index(
        user_id="u-edge",
        chat=chat,
        last_user_preview="x",
        delta_count=1,
        last_message_at=boundary,
    )
    assert count_active_users(boundary) == 1


def test_count_active_users_skips_rows_without_user_id(table: FakeTable) -> None:
    table.put_item(
        Item={
            "PK": "USER#u-ghost",
            "SK": "CHAT#2500-01-01#g",
            "created_at": "2500-01-01T00:00:00.000000+00:00",
            "last_message_at": "2500-06-01T00:00:00.000000+00:00",
        }
    )
    assert count_active_users("2500-01-01T00:00:00+00:00") == 0


def test_count_active_users_follows_scan_pagination(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel import storage

    monkeypatch.setattr(storage, "_ADMIN_SCAN_PAGE_LIMIT", 1)
    active = "2500-06-01T00:00:00.000000+00:00"
    for uid in ("u-a", "u-b", "u-c"):
        chat = create_chat(user_id=uid, title=None, model_default="m")
        update_chat_index(
            user_id=uid,
            chat=chat,
            last_user_preview="x",
            delta_count=1,
            last_message_at=active,
        )
    assert count_active_users("2500-01-01T00:00:00+00:00") == 3


def test_get_user_meta_returns_row_when_present(table: FakeTable) -> None:
    _put_user_meta(table, "u-1", email="one@example.com", created_at="2026-01-01T00:00:00+00:00")
    row = get_user_meta("u-1")
    assert row is not None
    assert row["email"] == "one@example.com"
    assert row["created_at"] == "2026-01-01T00:00:00+00:00"
    # Read-after-write: a detail request right after the META write must
    # not observe a stale miss and 404 a real user (same discipline as
    # get_prefs / is_jti_denied).
    assert table.last_get_item_kwargs.get("ConsistentRead") is True


def test_get_user_meta_returns_none_when_absent(table: FakeTable) -> None:
    """Every user is META-less until the #110 writer ships — None, not KeyError."""

    # A sibling row under the same PK must not satisfy the point read.
    create_chat(user_id="u-1", title=None, model_default="m")
    assert get_user_meta("u-1") is None


# ----------------------------------------------------------------
# Assets (#324, epic #321)
# ----------------------------------------------------------------


def _asset(
    asset_id: str = "a-1",
    *,
    chat_id: str = "c-1",
    owner: str = "u-1",
    created_at: str = "2026-07-13T00:00:00.000000+00:00",
    content: str | None = "print('hi')",
    s3_bucket: str | None = None,
    s3_key: str | None = None,
    kind: str = "code",
    origin: str = "generated",
) -> Any:
    from channel.models import Asset

    return Asset(
        asset_id=asset_id,
        chat_id=chat_id,
        owner=owner,
        kind=kind,  # type: ignore[arg-type]
        title=f"{asset_id}.txt",
        mime="text/plain",
        size_bytes=11,
        origin=origin,  # type: ignore[arg-type]
        source={"msg_id": "m-1"},
        content=content,
        s3_bucket=s3_bucket,
        s3_key=s3_key,
        created_at=created_at,
        updated_at=created_at,
    )


def _s3_asset(asset_id: str = "a-s3", **overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "content": None,
        "s3_bucket": "channel-attachments-test",
        "s3_key": f"assets/chat/{overrides.get('chat_id', 'c-1')}/{asset_id}",
        "kind": "image",
        "origin": "tool_output",
    }
    defaults.update(overrides)
    return _asset(asset_id, **defaults)


def test_put_asset_writes_chat_partition_row_with_owner_gsi_keys(table: FakeTable) -> None:
    from channel import storage

    asset = _asset("a-keys", chat_id="c-keys", owner="u-keys")
    storage.put_asset(asset)

    key = (f"CHAT#{asset.chat_id}", f"ASSET#{asset.created_at}#{asset.asset_id}")
    assert key in table.items
    row = table.items[key]
    assert row["owner_pk"] == "ASSETOWNER#u-keys"
    assert row["owner_sk"] == f"{asset.created_at}#{asset.asset_id}"
    assert row["content"] == "print('hi')"
    # None-valued payload attrs are stripped, mirroring _attachment_item.
    assert "s3_bucket" not in row
    assert "s3_key" not in row


def test_put_asset_s3_backed_row_omits_content(table: FakeTable) -> None:
    from channel import storage

    asset = _s3_asset("a-bin", chat_id="c-bin")
    storage.put_asset(asset)

    row = table.items[(f"CHAT#{asset.chat_id}", f"ASSET#{asset.created_at}#{asset.asset_id}")]
    assert row["s3_bucket"] == "channel-attachments-test"
    assert row["s3_key"] == "assets/chat/c-bin/a-bin"
    assert "content" not in row


def test_get_asset_matches_on_id_within_chat_partition(table: FakeTable) -> None:
    from channel import storage

    storage.put_asset(_asset("a-1", chat_id="c-1"))
    storage.put_asset(_asset("a-2", chat_id="c-1", created_at="2026-07-13T00:00:01.000000+00:00"))
    storage.put_asset(_asset("a-other", chat_id="c-2"))

    found = storage.get_asset(chat_id="c-1", asset_id="a-2")
    assert found is not None
    assert found.asset_id == "a-2"
    assert storage.get_asset(chat_id="c-1", asset_id="a-other") is None
    assert storage.get_asset(chat_id="c-1", asset_id="nope") is None


def test_list_chat_assets_orders_oldest_first_and_scopes_to_chat(table: FakeTable) -> None:
    from channel import storage

    storage.put_asset(_asset("a-new", chat_id="c-1", created_at="2026-07-13T00:00:02.000000+00:00"))
    storage.put_asset(_asset("a-old", chat_id="c-1", created_at="2026-07-13T00:00:01.000000+00:00"))
    storage.put_asset(_asset("a-elsewhere", chat_id="c-2"))

    assets = storage.list_chat_assets("c-1")
    assert [a.asset_id for a in assets] == ["a-old", "a-new"]


def test_list_chat_assets_follows_pagination(table: FakeTable) -> None:
    """The partition walk must follow LastEvaluatedKey across pages."""

    from channel import storage

    for i in range(3):
        storage.put_asset(
            _asset(f"a-{i}", chat_id="c-pg", created_at=f"2026-07-13T00:00:0{i}.000000+00:00")
        )

    original_query = table.query
    calls: list[dict[str, Any]] = []

    def paginating_query(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return original_query(**{**kwargs, "Limit": 2})

    table.query = paginating_query  # type: ignore[method-assign]
    assets = storage.list_chat_assets("c-pg")
    assert [a.asset_id for a in assets] == ["a-0", "a-1", "a-2"]
    assert len(calls) == 2
    assert "ExclusiveStartKey" in calls[1]


def test_list_assets_by_owner_returns_newest_first_dicts(table: FakeTable) -> None:
    from channel import storage

    storage.put_asset(
        _asset("a-1", chat_id="c-1", owner="u-o", created_at="2026-07-13T00:00:01.000000+00:00")
    )
    storage.put_asset(
        _asset("a-2", chat_id="c-2", owner="u-o", created_at="2026-07-13T00:00:02.000000+00:00")
    )
    storage.put_asset(_asset("a-x", chat_id="c-3", owner="u-someone-else"))

    rows, cursor = storage.list_assets_by_owner("u-o")
    assert [r["asset_id"] for r in rows] == ["a-2", "a-1"]
    assert cursor is None
    assert all(isinstance(r, dict) for r in rows)


def test_list_assets_by_owner_paginates_with_cursor(table: FakeTable) -> None:
    from channel import storage

    for i in range(3):
        storage.put_asset(
            _asset(
                f"a-{i}",
                chat_id=f"c-{i}",
                owner="u-pg",
                created_at=f"2026-07-13T00:00:0{i}.000000+00:00",
            )
        )

    first, cursor = storage.list_assets_by_owner("u-pg", limit=2)
    assert [r["asset_id"] for r in first] == ["a-2", "a-1"]
    assert cursor is not None
    rest, done = storage.list_assets_by_owner("u-pg", limit=2, cursor=cursor)
    assert [r["asset_id"] for r in rest] == ["a-0"]
    assert done is None


def test_list_assets_by_owner_query_shape(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The browse query must hit the owner GSI, newest first, with a
    ProjectionExpression that keeps ``content`` off the hot path."""

    from channel import storage

    calls: list[dict[str, Any]] = []
    original_query = table.query

    def recording_query(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return original_query(**kwargs)

    monkeypatch.setattr(table, "query", recording_query)
    storage.list_assets_by_owner("u-shape", limit=7)

    (kwargs,) = calls
    assert kwargs["IndexName"] == "AssetOwnerIndex"
    assert kwargs["ScanIndexForward"] is False
    assert kwargs["Limit"] == 7
    aliases = kwargs["ExpressionAttributeNames"]
    projected = {aliases[a.strip()] for a in kwargs["ProjectionExpression"].split(",")}
    assert "content" not in projected
    assert {
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
    } == projected


@pytest.mark.parametrize(("requested", "effective"), [(0, 1), (-5, 1), (500, 100)])
def test_list_assets_by_owner_clamps_limit(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch, requested: int, effective: int
) -> None:
    from channel import storage

    calls: list[dict[str, Any]] = []
    original_query = table.query

    def recording_query(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return original_query(**kwargs)

    monkeypatch.setattr(table, "query", recording_query)
    storage.list_assets_by_owner("u-clamp", limit=requested)
    assert calls[0]["Limit"] == effective


def test_delete_asset_deletes_s3_object_before_row(table: FakeTable, s3_client: _FakeS3) -> None:
    from channel import storage

    asset = _s3_asset("a-del", chat_id="c-del")
    storage.put_asset(asset)
    storage.delete_asset(asset)

    assert s3_client.deleted == [("channel-attachments-test", "assets/chat/c-del/a-del")]
    assert ("CHAT#c-del", f"ASSET#{asset.created_at}#a-del") not in table.items


def test_delete_asset_inline_skips_s3(table: FakeTable, s3_client: _FakeS3) -> None:
    from channel import storage

    asset = _asset("a-inline", chat_id="c-del2")
    storage.put_asset(asset)
    storage.delete_asset(asset)

    assert s3_client.deleted == []
    assert ("CHAT#c-del2", f"ASSET#{asset.created_at}#a-inline") not in table.items


def test_delete_asset_leaves_row_when_s3_delete_raises(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    """S3-before-row ordering: an S3 failure must leave the row for the
    lazy-expiry reap to retry."""

    from channel import storage

    asset = _s3_asset("a-stuck", chat_id="c-stuck")
    storage.put_asset(asset)
    s3_client.delete_errors[(asset.s3_bucket, asset.s3_key)] = "AccessDenied"

    with pytest.raises(ClientError):
        storage.delete_asset(asset)
    assert ("CHAT#c-stuck", f"ASSET#{asset.created_at}#a-stuck") in table.items


def test_get_asset_bytes_inline_returns_utf8_without_s3(s3_client: _FakeS3) -> None:
    from channel import storage

    data, reason = storage.get_asset_bytes(_asset(content="héllo"))
    assert data == "héllo".encode()
    assert reason is None
    assert s3_client.handed_out_bodies == []


def test_get_asset_bytes_s3_success(s3_client: _FakeS3) -> None:
    from channel import storage

    asset = _s3_asset("a-png")
    s3_client.bodies[(asset.s3_bucket, asset.s3_key)] = b"\x89PNG fake"
    data, reason = storage.get_asset_bytes(asset)
    assert data == b"\x89PNG fake"
    assert reason is None


def test_get_asset_bytes_s3_not_found(s3_client: _FakeS3) -> None:
    from channel import storage

    data, reason = storage.get_asset_bytes(_s3_asset("a-miss"))
    assert data is None
    assert reason == "S3 object not found"


def test_get_asset_bytes_s3_read_error_converts_to_reason(s3_client: _FakeS3) -> None:
    from channel import storage

    asset = _s3_asset("a-cut")
    s3_client.bodies[(asset.s3_bucket, asset.s3_key)] = b"partial"
    s3_client.body_read_errors[(asset.s3_bucket, asset.s3_key)] = OSError("connection reset")
    data, reason = storage.get_asset_bytes(asset)
    assert data is None
    assert reason == "S3 read error: OSError"
    assert s3_client.handed_out_bodies[0].closed


def test_get_asset_bytes_guards_payloadless_instances() -> None:
    """``model_construct`` bypasses the exactly-one-payload validator;
    the helper must still fail in the (None, reason) shape."""

    from channel import storage
    from channel.models import Asset

    hollow = Asset.model_construct(content=None, s3_bucket=None, s3_key=None)
    data, reason = storage.get_asset_bytes(hollow)
    assert data is None
    assert reason == "asset has no payload coordinates"


def test_delete_chat_assets_wipes_rows_and_s3_objects(table: FakeTable, s3_client: _FakeS3) -> None:
    from channel import storage

    inline = _asset("a-inline", chat_id="c-wipe")
    binary = _s3_asset("a-bin", chat_id="c-wipe", created_at="2026-07-13T00:00:01.000000+00:00")
    elsewhere = _asset("a-keep", chat_id="c-other")
    for asset in (inline, binary, elsewhere):
        storage.put_asset(asset)

    deleted, failed = storage.delete_chat_assets(chat_id="c-wipe")

    assert (deleted, failed) == (2, 0)
    assert s3_client.deleted == [("channel-attachments-test", "assets/chat/c-wipe/a-bin")]
    remaining = [sk for (pk, sk) in table.items if pk.startswith("CHAT#")]
    assert remaining == [f"ASSET#{elsewhere.created_at}#a-keep"]


def test_delete_chat_assets_isolates_per_asset_failures(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    from channel import storage

    good = _s3_asset("a-good", chat_id="c-iso")
    bad = _s3_asset("a-bad", chat_id="c-iso", created_at="2026-07-13T00:00:01.000000+00:00")
    storage.put_asset(good)
    storage.put_asset(bad)
    s3_client.delete_errors[(bad.s3_bucket, bad.s3_key)] = "AccessDenied"

    deleted, failed = storage.delete_chat_assets(chat_id="c-iso")

    assert (deleted, failed) == (1, 1)
    # The failed asset's row survives (S3-before-row ordering).
    assert ("CHAT#c-iso", f"ASSET#{bad.created_at}#a-bad") in table.items
    assert ("CHAT#c-iso", f"ASSET#{good.created_at}#a-good") not in table.items


def test_delete_chat_assets_counts_malformed_rows_as_failures(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    from channel import storage

    storage.put_asset(_asset("a-ok", chat_id="c-mal"))
    table.put_item(
        Item={
            "PK": "CHAT#c-mal",
            "SK": "ASSET#2026-07-13T00:00:09.000000+00:00#a-garbage",
            # Missing every Asset attribute — hydration must fail, be
            # counted, and not abort the rest of the cascade.
        }
    )

    deleted, failed = storage.delete_chat_assets(chat_id="c-mal")
    assert (deleted, failed) == (1, 1)


def test_reap_orphaned_assets_deletes_orphans_and_skips_live_chats(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    from channel import storage

    live_chat = create_chat(user_id="u-reap", title=None, model_default="m")
    live = _asset("a-live", chat_id=live_chat.chat_id, owner="u-reap")
    orphan = _s3_asset("a-orphan", chat_id="c-gone", owner="u-reap")
    storage.put_asset(live)
    storage.put_asset(orphan)

    rows, _ = storage.list_assets_by_owner("u-reap")
    reaped, failed = storage.reap_orphaned_assets(rows)

    assert (reaped, failed) == (1, 0)
    assert storage.get_asset(chat_id="c-gone", asset_id="a-orphan") is None
    assert storage.get_asset(chat_id=live_chat.chat_id, asset_id="a-live") is not None
    assert s3_client.deleted == [("channel-attachments-test", "assets/chat/c-gone/a-orphan")]


def test_reap_orphaned_assets_caches_liveness_per_chat(
    table: FakeTable, s3_client: _FakeS3, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel import storage

    lookups: list[str] = []
    original = storage.get_chat_by_id

    def counting_get_chat_by_id(chat_id: str) -> Any:
        lookups.append(chat_id)
        return original(chat_id)

    monkeypatch.setattr(storage, "get_chat_by_id", counting_get_chat_by_id)
    a1 = _asset("a-1", chat_id="c-gone", owner="u-cache")
    a2 = _asset(
        "a-2", chat_id="c-gone", owner="u-cache", created_at="2026-07-13T00:00:01.000000+00:00"
    )
    storage.put_asset(a1)
    storage.put_asset(a2)

    rows, _ = storage.list_assets_by_owner("u-cache")
    reaped, failed = storage.reap_orphaned_assets(rows)

    assert (reaped, failed) == (2, 0)
    assert lookups == ["c-gone"]


def test_reap_orphaned_assets_isolates_delete_failures(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    from channel import storage

    stuck = _s3_asset("a-stuck", chat_id="c-gone2", owner="u-fail")
    fine = _asset(
        "a-fine", chat_id="c-gone2", owner="u-fail", created_at="2026-07-13T00:00:01.000000+00:00"
    )
    storage.put_asset(stuck)
    storage.put_asset(fine)
    s3_client.delete_errors[(stuck.s3_bucket, stuck.s3_key)] = "AccessDenied"

    rows, _ = storage.list_assets_by_owner("u-fail")
    reaped, failed = storage.reap_orphaned_assets(rows)

    assert (reaped, failed) == (1, 1)
    assert storage.get_asset(chat_id="c-gone2", asset_id="a-stuck") is not None


def test_reap_orphaned_assets_hydrates_content_projected_rows(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    """Browse rows arrive without ``content`` (the projection strips it);
    inline-asset rows must still hydrate for the reap."""

    from channel import storage

    inline = _asset("a-proj", chat_id="c-gone3", owner="u-proj")
    storage.put_asset(inline)
    row = dict(table.items[("CHAT#c-gone3", f"ASSET#{inline.created_at}#a-proj")])
    row.pop("content")

    reaped, failed = storage.reap_orphaned_assets([row])
    assert (reaped, failed) == (1, 0)


def test_reap_orphaned_assets_counts_malformed_rows_as_failures(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    from channel import storage

    reaped, failed = storage.reap_orphaned_assets([{"SK": "ASSET#junk"}])
    assert (reaped, failed) == (0, 1)


def test_delete_asset_skips_s3_when_bucket_missing_on_unvalidated_instance(
    table: FakeTable, s3_client: _FakeS3
) -> None:
    """Copilot review on #339: an unvalidated instance (model_construct)
    with ``s3_key`` set but ``s3_bucket=None`` must not reach boto3 —
    ``Bucket=None`` raises ``ParamValidationError``, which the cascade's
    per-asset ``except ClientError`` isolation doesn't cover."""

    from channel import storage
    from channel.models import Asset

    hollow = Asset.model_construct(
        asset_id="a-hollow",
        chat_id="c-hollow",
        owner="u-1",
        content=None,
        s3_bucket=None,
        s3_key="assets/chat/c-hollow/a-hollow",
        created_at="2026-07-13T00:00:00.000000+00:00",
        updated_at="2026-07-13T00:00:00.000000+00:00",
    )
    table.put_item(Item={"PK": "CHAT#c-hollow", "SK": f"ASSET#{hollow.created_at}#a-hollow"})

    storage.delete_asset(hollow)

    assert s3_client.deleted == []
    assert ("CHAT#c-hollow", f"ASSET#{hollow.created_at}#a-hollow") not in table.items


def test_put_asset_bytes_writes_kms_encrypted_object_and_returns_coords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#326 — server-side asset payload writes land under the
    ``assets/chat/{chat_id}/{asset_id}`` prefix with the same
    ``aws:kms`` SSE the presign path uses, and hand back the (bucket,
    key) pair the producer stamps onto the ASSET row."""
    from channel import storage

    calls: list[dict[str, Any]] = []

    class _PutOnlyS3:
        def put_object(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {}

    monkeypatch.setattr("channel.storage._get_s3_client", lambda: _PutOnlyS3())
    monkeypatch.setenv("STARTER_ATTACHMENTS_BUCKET", "channel-attachments-test")

    bucket, key = storage.put_asset_bytes(
        chat_id="c-1", asset_id="a-1", data=b"\x89PNG", mime="image/png"
    )

    assert (bucket, key) == ("channel-attachments-test", "assets/chat/c-1/a-1")
    assert calls == [
        {
            "Bucket": "channel-attachments-test",
            "Key": "assets/chat/c-1/a-1",
            "Body": b"\x89PNG",
            "ContentType": "image/png",
            "ServerSideEncryption": "aws:kms",
        }
    ]


def test_delete_asset_object_deletes_by_raw_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#326 — compensating cleanup for produced objects whose ASSET row
    write failed; a row-less object is invisible to the cascade/reap."""
    from channel import storage

    calls: list[tuple[str, str]] = []

    class _DeleteOnlyS3:
        def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
            calls.append((Bucket, Key))
            return {}

    monkeypatch.setattr("channel.storage._get_s3_client", lambda: _DeleteOnlyS3())
    storage.delete_asset_object(bucket="channel-attachments-test", key="assets/chat/c-1/a-1")
    assert calls == [("channel-attachments-test", "assets/chat/c-1/a-1")]


# ----------------------------------------------------------------
# Refresh-token rows (#290, epic #241)
# ----------------------------------------------------------------


def _iso(delta_seconds: float) -> str:
    """ISO-8601 timestamp ``delta_seconds`` from now, in the stored shape."""
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat(
        timespec="microseconds"
    )


def _seed_refresh_row(table: FakeTable, **overrides: Any) -> tuple[str, RefreshToken]:
    """Write a refresh row for an arbitrary raw token, bypassing mint.

    Lets a test place a row in a state ``mint_refresh_token`` never
    produces (already expired, revoked by logout) without reaching into
    the item dict by hand — the row still goes through the real
    ``_refresh_item`` renderer, so key shape stays honest.
    """
    from channel import storage

    raw = overrides.pop("raw_token", "raw-" + uuid.uuid4().hex)
    fields: dict[str, Any] = {
        "token_hash": storage._refresh_token_hash(raw),
        "user_id": "u-refresh",
        "device_id": "d-1",
        "issued_at": _iso(-60),
        "last_used_at": _iso(-60),
        "absolute_expires_at": _iso(REFRESH_ABSOLUTE_LIFETIME_SECONDS),
        "idle_expires_at": _iso(REFRESH_IDLE_TIMEOUT_SECONDS),
    }
    fields.update(overrides)
    token = RefreshToken(**fields)
    table.put_item(Item=storage._refresh_item(token))
    return raw, token


def _refresh_rows(table: FakeTable) -> list[dict[str, Any]]:
    return [i for i in table.items.values() if i["PK"].startswith("REFRESH#")]


def test_mint_refresh_token_never_persists_the_raw_token(table: FakeTable) -> None:
    """The headline security property of #290: the plaintext token is
    returned to the caller and exists nowhere in the row."""
    from channel import storage

    raw, token = mint_refresh_token(user_id="u-1", device_id="d-1")

    rows = _refresh_rows(table)
    assert len(rows) == 1
    row = rows[0]
    assert raw not in json.dumps(row, default=str)
    assert row["PK"] == f"REFRESH#{hashlib.sha256(raw.encode()).hexdigest()}"
    assert row["token_hash"] == token.token_hash
    assert token.token_hash != raw
    assert storage._refresh_token_hash(raw) == token.token_hash


def test_mint_refresh_token_generates_256_bits_of_entropy(table: FakeTable) -> None:
    first, _ = mint_refresh_token(user_id="u-1", device_id="d-1")
    second, _ = mint_refresh_token(user_id="u-1", device_id="d-1")
    assert first != second
    # ``token_urlsafe(32)`` renders 32 random bytes as 43 base64url chars.
    assert len(first) == 43


def test_mint_refresh_token_row_shape_carries_gsi_and_ttl(table: FakeTable) -> None:
    from channel import storage

    _, token = mint_refresh_token(user_id="u-1", device_id="d-1")
    row = _refresh_rows(table)[0]

    assert row["SK"] == "META"
    assert row["type"] == "REFRESH"
    assert row["GSI5PK"] == "REFRESH_USER#u-1"
    assert row["GSI5SK"] == f"{token.issued_at}#{token.token_hash[:16]}"
    assert row["revoked"] is False
    assert "revoked_reason" not in row
    assert "revoked_at" not in row
    # ttl must be an integer Unix second matching the absolute expiry —
    # DynamoDB's TTL service silently ignores any other representation.
    assert isinstance(row["ttl"], int)
    assert row["ttl"] == int(storage._parse_iso_utc(token.absolute_expires_at).timestamp())


def test_mint_refresh_token_defaults_to_thirty_day_absolute_and_seven_day_idle(
    table: FakeTable,
) -> None:
    from channel import storage

    _, token = mint_refresh_token(user_id="u-1", device_id="d-1")

    issued = storage._parse_iso_utc(token.issued_at)
    absolute = storage._parse_iso_utc(token.absolute_expires_at)
    idle = storage._parse_iso_utc(token.idle_expires_at)
    assert abs((absolute - issued).total_seconds() - REFRESH_ABSOLUTE_LIFETIME_SECONDS) < 5
    assert abs((idle - issued).total_seconds() - REFRESH_IDLE_TIMEOUT_SECONDS) < 5
    assert token.last_used_at == token.issued_at
    assert REFRESH_ABSOLUTE_LIFETIME_SECONDS == 30 * 24 * 3600
    assert REFRESH_IDLE_TIMEOUT_SECONDS == 7 * 24 * 3600


def test_mint_refresh_token_honours_a_carried_over_absolute_expiry(table: FakeTable) -> None:
    carried = _iso(120)
    _, token = mint_refresh_token(user_id="u-1", device_id="d-1", absolute_expires_at=carried)
    assert token.absolute_expires_at == carried


def test_refresh_item_renders_revocation_columns_when_present() -> None:
    from channel import storage

    revoked_at = _iso(0)
    row = storage._refresh_item(
        RefreshToken(
            token_hash="h" * 64,
            user_id="u-1",
            device_id="d-1",
            issued_at=_iso(-10),
            last_used_at=_iso(-10),
            absolute_expires_at=_iso(100),
            idle_expires_at=_iso(50),
            revoked=True,
            revoked_reason=RefreshRevokeReason.LOGOUT,
            revoked_at=revoked_at,
        )
    )
    assert row["revoked"] is True
    assert row["revoked_reason"] == "logout"
    assert row["revoked_at"] == revoked_at


def test_refresh_from_item_round_trips_a_live_row(table: FakeTable) -> None:
    from channel import storage

    mint_refresh_token(user_id="u-1", device_id="d-1")
    token = storage._refresh_from_item(_refresh_rows(table)[0])
    assert token.revoked is False
    assert token.revoked_reason is None
    assert token.revoked_at is None


def test_get_refresh_row_returns_none_for_unknown_hash(table: FakeTable) -> None:
    from channel import storage

    assert storage._get_refresh_row("0" * 64) is None


def test_get_refresh_row_reads_consistently(table: FakeTable) -> None:
    """Rotation writes ``revoked=True``; the very next presentation of
    that token must observe it, so the point-read cannot be eventually
    consistent."""
    from channel import storage

    _, token = mint_refresh_token(user_id="u-1", device_id="d-1")
    storage._get_refresh_row(token.token_hash)
    assert table.last_get_item_kwargs == {"ConsistentRead": True}


def test_consume_refresh_token_rotates_and_returns_a_new_token(table: FakeTable) -> None:
    from channel import storage

    raw, original = mint_refresh_token(user_id="u-1", device_id="d-1")

    result = consume_refresh_token(raw)

    assert result.outcome == RefreshConsumeOutcome.OK
    assert result.raw_token is not None and result.raw_token != raw
    assert result.token is not None
    assert result.token.token_hash != original.token_hash
    assert result.token.user_id == "u-1"
    assert result.token.device_id == "d-1"
    assert result.revoked_count == 0

    old_row = table.items[(f"REFRESH#{original.token_hash}", "META")]
    assert old_row["revoked"] is True
    assert old_row["revoked_reason"] == "rotated"
    assert old_row["revoked_at"]
    assert storage._get_refresh_row(result.token.token_hash) is not None


def test_consume_refresh_token_carries_the_absolute_expiry_forward(table: FakeTable) -> None:
    """Rotation renews the idle window but must never extend the
    absolute one — otherwise a stolen token could be refreshed
    indefinitely."""
    raw, original = mint_refresh_token(user_id="u-1", device_id="d-1")

    result = consume_refresh_token(raw)

    assert result.token is not None
    assert result.token.absolute_expires_at == original.absolute_expires_at
    assert result.token.idle_expires_at > original.idle_expires_at


def test_consume_refresh_token_unknown_token_is_not_found(table: FakeTable) -> None:
    result = consume_refresh_token("never-minted")
    assert result.outcome == RefreshConsumeOutcome.NOT_FOUND
    assert result.raw_token is None
    assert result.token is None


def test_consume_refresh_token_is_single_use(table: FakeTable) -> None:
    raw, _ = mint_refresh_token(user_id="u-1", device_id="d-1")
    assert consume_refresh_token(raw).outcome == RefreshConsumeOutcome.OK
    assert consume_refresh_token(raw).outcome == RefreshConsumeOutcome.REUSED


def test_consume_refresh_token_reuse_revokes_the_whole_device_family(table: FakeTable) -> None:
    """RFC 9700 §4.14.2 — replaying an already-rotated token means the
    family is compromised; every live descendant dies with it."""
    raw1, _ = mint_refresh_token(user_id="u-1", device_id="d-1")
    first = consume_refresh_token(raw1)
    assert first.raw_token is not None
    second = consume_refresh_token(first.raw_token)
    assert second.outcome == RefreshConsumeOutcome.OK

    # A different device must survive the cascade untouched.
    _, other_device = mint_refresh_token(user_id="u-1", device_id="d-2")

    replay = consume_refresh_token(raw1)

    assert replay.outcome == RefreshConsumeOutcome.REUSED
    assert replay.revoked_count == 1  # only the still-live d-1 descendant
    family = [r for r in _refresh_rows(table) if r["device_id"] == "d-1"]
    assert all(r["revoked"] for r in family)
    assert {r["revoked_reason"] for r in family} == {"rotated", "reuse_detected"}
    survivor = table.items[(f"REFRESH#{other_device.token_hash}", "META")]
    assert survivor["revoked"] is False


def test_consume_refresh_token_logged_out_row_is_revoked_not_reuse(table: FakeTable) -> None:
    """A row killed by logout is a dead family, not a fresh breach — it
    must not re-arm the reuse cascade (or the #294 counter)."""
    raw, _ = _seed_refresh_row(
        table,
        revoked=True,
        revoked_reason=RefreshRevokeReason.LOGOUT,
        revoked_at=_iso(-1),
    )
    result = consume_refresh_token(raw)
    assert result.outcome == RefreshConsumeOutcome.REVOKED
    assert result.revoked_count == 0


def test_consume_refresh_token_enforces_the_absolute_expiry(table: FakeTable) -> None:
    raw, _ = _seed_refresh_row(
        table,
        absolute_expires_at=_iso(-1),
        idle_expires_at=_iso(REFRESH_IDLE_TIMEOUT_SECONDS),
    )
    assert consume_refresh_token(raw).outcome == RefreshConsumeOutcome.EXPIRED_ABSOLUTE


def test_consume_refresh_token_enforces_the_idle_expiry(table: FakeTable) -> None:
    raw, _ = _seed_refresh_row(table, idle_expires_at=_iso(-1))
    assert consume_refresh_token(raw).outcome == RefreshConsumeOutcome.EXPIRED_IDLE


def test_consume_refresh_token_checks_absolute_before_idle(table: FakeTable) -> None:
    """Both windows blown: report the ceiling, which is the one the user
    cannot fix by being more active."""
    raw, _ = _seed_refresh_row(table, absolute_expires_at=_iso(-1), idle_expires_at=_iso(-2))
    assert consume_refresh_token(raw).outcome == RefreshConsumeOutcome.EXPIRED_ABSOLUTE


def test_consume_refresh_token_loser_of_the_conditional_race_sees_reuse(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two simultaneous consumes serialize on the conditional update.
    The loser is indistinguishable from a replay, so it takes the reuse
    path rather than silently tolerating a fast attacker (epic #241 Q1)."""
    raw, _ = mint_refresh_token(user_id="u-1", device_id="d-1")

    def fake_update_item(**_kwargs: Any) -> dict[str, Any]:
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "lost"}},
            "UpdateItem",
        )

    monkeypatch.setattr(table, "update_item", fake_update_item)

    result = consume_refresh_token(raw)

    assert result.outcome == RefreshConsumeOutcome.REUSED
    assert result.raw_token is None
    # The cascade's own updates fail the same way, so nothing flips.
    assert result.revoked_count == 0
    assert len(_refresh_rows(table)) == 1  # no successor minted


def test_mark_refresh_revoked_re_raises_unexpected_client_errors(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel import storage

    _, token = mint_refresh_token(user_id="u-1", device_id="d-1")

    def fake_update_item(**_kwargs: Any) -> dict[str, Any]:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "UpdateItem")

    monkeypatch.setattr(table, "update_item", fake_update_item)

    with pytest.raises(ClientError):
        storage._mark_refresh_revoked(token.token_hash, RefreshRevokeReason.LOGOUT)


def test_mark_refresh_revoked_guards_against_ghost_rows(table: FakeTable) -> None:
    """``attribute_exists(PK) AND #revoked = :live`` is what makes the
    consume race-safe and stops UpdateItem resurrecting a TTL-swept row
    as a partial ghost."""
    from channel import storage

    captured: dict[str, Any] = {}
    original = table.update_item

    def spy(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return original(**kwargs)

    table.update_item = spy  # type: ignore[method-assign]
    _, token = mint_refresh_token(user_id="u-1", device_id="d-1")
    assert storage._mark_refresh_revoked(token.token_hash, RefreshRevokeReason.LOGOUT) is True

    assert captured["ConditionExpression"] == "attribute_exists(PK) AND #revoked = :live"
    assert captured["ExpressionAttributeNames"] == {"#revoked": "revoked"}
    assert captured["ExpressionAttributeValues"][":live"] is False
    assert captured["ExpressionAttributeValues"][":revoked"] is True


def test_revoke_refresh_token_kills_the_device_family(table: FakeTable) -> None:
    raw, _ = mint_refresh_token(user_id="u-1", device_id="d-1")
    rotated = consume_refresh_token(raw)
    assert rotated.raw_token is not None
    _, other = mint_refresh_token(user_id="u-1", device_id="d-2")

    # Logging out with the *rotated* handle still kills the live successor.
    revoked = revoke_refresh_token(rotated.raw_token)

    assert revoked == 1
    family = [r for r in _refresh_rows(table) if r["device_id"] == "d-1"]
    assert all(r["revoked"] for r in family)
    assert table.items[(f"REFRESH#{other.token_hash}", "META")]["revoked"] is False


def test_revoke_refresh_token_is_idempotent_and_quiet_on_unknown_tokens(
    table: FakeTable,
) -> None:
    raw, _ = mint_refresh_token(user_id="u-1", device_id="d-1")
    assert revoke_refresh_token(raw) == 1
    assert revoke_refresh_token(raw) == 0  # already dead, nothing flips
    assert revoke_refresh_token("never-minted") == 0


def test_revoke_refresh_token_records_the_logout_reason(table: FakeTable) -> None:
    raw, token = mint_refresh_token(user_id="u-1", device_id="d-1")
    revoke_refresh_token(raw)
    assert table.items[(f"REFRESH#{token.token_hash}", "META")]["revoked_reason"] == "logout"


def test_revoke_all_user_refresh_tokens_spans_every_device(table: FakeTable) -> None:
    mint_refresh_token(user_id="u-1", device_id="d-1")
    mint_refresh_token(user_id="u-1", device_id="d-2")
    mint_refresh_token(user_id="u-1", device_id="d-3")
    _, untouched = mint_refresh_token(user_id="u-2", device_id="d-9")

    assert revoke_all_user_refresh_tokens("u-1") == 3

    mine = [r for r in _refresh_rows(table) if r["user_id"] == "u-1"]
    assert all(r["revoked"] for r in mine)
    assert {r["revoked_reason"] for r in mine} == {"user_revoked"}
    assert table.items[(f"REFRESH#{untouched.token_hash}", "META")]["revoked"] is False


def test_revoke_all_user_refresh_tokens_preserves_prior_revocation_reasons(
    table: FakeTable,
) -> None:
    """Already-revoked rows keep their original reason — reuse detection
    depends on being able to tell ``rotated`` from everything else."""
    raw, original = mint_refresh_token(user_id="u-1", device_id="d-1")
    consume_refresh_token(raw)

    assert revoke_all_user_refresh_tokens("u-1") == 1  # only the successor was live

    assert table.items[(f"REFRESH#{original.token_hash}", "META")]["revoked_reason"] == "rotated"


def test_revoke_all_user_refresh_tokens_returns_zero_for_a_user_with_no_sessions(
    table: FakeTable,
) -> None:
    assert revoke_all_user_refresh_tokens("u-nobody") == 0


def test_query_user_refresh_rows_follows_pagination_cursors(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel import storage

    pages = [
        {"Items": [{"token_hash": "a", "revoked": False}], "LastEvaluatedKey": {"PK": "x"}},
        {"Items": [{"token_hash": "b", "revoked": False}]},
    ]
    seen: list[Any] = []

    def fake_query(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs.get("ExclusiveStartKey"))
        return pages[len(seen) - 1]

    monkeypatch.setattr(table, "query", fake_query)

    rows = storage._query_user_refresh_rows("u-1")

    assert [r["token_hash"] for r in rows] == ["a", "b"]
    assert seen == [None, {"PK": "x"}]


def test_query_user_refresh_rows_stops_and_warns_at_the_page_cap(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A never-terminating cursor means something is wrong upstream —
    bail loudly rather than spin.

    The module-level ``logger`` is mocked directly rather than using
    ``caplog`` because the ``channel`` logger sets ``propagate = False``
    once ``configure_logging`` has run in the test session (same
    workaround as ``test_admin_api`` / ``test_chats_api``).
    """
    from unittest.mock import MagicMock

    from channel import storage

    calls = {"n": 0}

    def fake_query(**_kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        return {"Items": [{"token_hash": f"h{calls['n']}"}], "LastEvaluatedKey": {"PK": "more"}}

    monkeypatch.setattr(table, "query", fake_query)
    mock_logger = MagicMock()
    monkeypatch.setattr("channel.storage.logger", mock_logger)

    rows = storage._query_user_refresh_rows("u-1")

    assert calls["n"] == storage._REFRESH_QUERY_MAX_PAGES
    assert len(rows) == storage._REFRESH_QUERY_MAX_PAGES
    (warning,) = mock_logger.warning.call_args_list
    assert "refresh.user_query_truncated" in warning.args[0]
    # The user id is fingerprinted, never logged raw (Sonar S5145 / PII).
    assert "u-1" not in warning.args
    assert warning.args[1] == fingerprint_id("u-1")


def test_reuse_detection_logs_fingerprinted_identifiers_only(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reuse warning is a security signal that will be read in
    CloudWatch — it must correlate without writing raw identifiers."""
    from unittest.mock import MagicMock

    raw, _ = mint_refresh_token(user_id="u-secret", device_id="d-secret")
    consume_refresh_token(raw)

    mock_logger = MagicMock()
    monkeypatch.setattr("channel.storage.logger", mock_logger)
    consume_refresh_token(raw)

    (warning,) = mock_logger.warning.call_args_list
    assert "refresh.reuse_detected" in warning.args[0]
    assert warning.args[1] == fingerprint_id("u-secret")
    assert warning.args[2] == fingerprint_id("d-secret")
    assert "u-secret" not in warning.args
    assert "d-secret" not in warning.args


def test_query_user_refresh_rows_filters_by_device_only_when_asked(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel import storage

    captured: list[dict[str, Any]] = []

    def fake_query(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"Items": []}

    monkeypatch.setattr(table, "query", fake_query)

    storage._query_user_refresh_rows("u-1")
    storage._query_user_refresh_rows("u-1", "d-1")

    assert captured[0]["IndexName"] == "RefreshByUserIndex"
    assert "FilterExpression" not in captured[0]
    assert captured[1]["FilterExpression"] is not None


def test_revoke_refresh_rows_skips_rows_that_are_already_revoked(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel import storage

    called: list[str] = []
    monkeypatch.setattr(
        storage,
        "_mark_refresh_revoked",
        lambda token_hash, reason: called.append(token_hash) or True,
    )

    count = storage._revoke_refresh_rows(
        [
            {"token_hash": "live", "revoked": False},
            {"token_hash": "dead", "revoked": True},
        ],
        RefreshRevokeReason.LOGOUT,
    )

    assert count == 1
    assert called == ["live"]


def test_revoke_refresh_rows_does_not_count_conditional_losers(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel import storage

    monkeypatch.setattr(storage, "_mark_refresh_revoked", lambda token_hash, reason: False)
    count = storage._revoke_refresh_rows(
        [{"token_hash": "h", "revoked": False}], RefreshRevokeReason.LOGOUT
    )
    assert count == 0


def test_refresh_token_model_rejects_revocation_metadata_without_the_flag() -> None:
    with pytest.raises(ValidationError):
        RefreshToken(
            token_hash="h" * 64,
            user_id="u-1",
            device_id="d-1",
            issued_at=_iso(0),
            last_used_at=_iso(0),
            absolute_expires_at=_iso(100),
            idle_expires_at=_iso(50),
            revoked=False,
            revoked_reason=RefreshRevokeReason.LOGOUT,
        )


def test_refresh_consume_result_requires_a_token_pair_on_success() -> None:
    with pytest.raises(ValidationError):
        RefreshConsumeResult(outcome=RefreshConsumeOutcome.OK)


def test_refresh_consume_result_rejects_a_half_populated_token_pair() -> None:
    with pytest.raises(ValidationError):
        RefreshConsumeResult(outcome=RefreshConsumeOutcome.OK, raw_token="x")


def test_refresh_consume_result_rejects_a_token_on_a_failure_outcome() -> None:
    token = RefreshToken(
        token_hash="h" * 64,
        user_id="u-1",
        device_id="d-1",
        issued_at=_iso(0),
        last_used_at=_iso(0),
        absolute_expires_at=_iso(100),
        idle_expires_at=_iso(50),
    )
    with pytest.raises(ValidationError):
        RefreshConsumeResult(
            outcome=RefreshConsumeOutcome.REUSED,
            raw_token="x",
            token=token,
        )


def test_revoke_refresh_family_sweeps_the_index_the_requested_number_of_times(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``RefreshByUserIndex`` is eventually consistent — DynamoDB refuses
    ``ConsistentRead`` on a GSI — so the breach path re-queries. A row
    that hadn't propagated during sweep 1 is caught by sweep 2."""
    from channel import storage

    sweeps: list[tuple[str, str | None]] = []
    late_arrival = [
        [{"token_hash": "early", "revoked": False}],
        [{"token_hash": "early", "revoked": True}, {"token_hash": "late", "revoked": False}],
    ]

    def fake_query_rows(user_id: str, device_id: str | None = None) -> list[dict[str, Any]]:
        sweeps.append((user_id, device_id))
        return late_arrival[len(sweeps) - 1]

    monkeypatch.setattr(storage, "_query_user_refresh_rows", fake_query_rows)

    revoked = storage._revoke_refresh_family("u-1", "d-1", RefreshRevokeReason.LOGOUT, sweeps=2)

    assert sweeps == [("u-1", "d-1"), ("u-1", "d-1")]
    # "early" flipped on sweep 1, "late" on sweep 2 — the row that a
    # single-pass cascade would have left live.
    assert revoked == 2


def test_reuse_cascade_double_sweeps_while_logout_and_sign_out_all_do_not(
    table: FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the security-critical path pays for the extra Query."""
    from channel import storage

    calls: list[int] = []

    def fake_family(
        user_id: str,
        device_id: str | None,
        reason: RefreshRevokeReason,
        *,
        sweeps: int = 1,
    ) -> int:
        calls.append(sweeps)
        return 0

    raw_reuse, _ = mint_refresh_token(user_id="u-1", device_id="d-1")
    consume_refresh_token(raw_reuse)
    raw_logout, _ = mint_refresh_token(user_id="u-1", device_id="d-2")

    monkeypatch.setattr(storage, "_revoke_refresh_family", fake_family)
    consume_refresh_token(raw_reuse)  # replay → reuse cascade
    revoke_refresh_token(raw_logout)
    revoke_all_user_refresh_tokens("u-1")

    assert calls == [storage._REFRESH_REUSE_SWEEPS, 1, 1]
    assert storage._REFRESH_REUSE_SWEEPS == 2
