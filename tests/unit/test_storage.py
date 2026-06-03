# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for channel.storage chat persistence helpers.

These run against an in-memory fake table to verify item shapes,
ordering, and pagination without DDB Local.  See
``tests/integration/test_chat_persistence.py`` for the DDB-Local
counterpart.
"""

from __future__ import annotations

from typing import Any

import pytest
from boto3.dynamodb.conditions import (
    And,
    AttributeNotExists,
    BeginsWith,
    ConditionBase,
    Equals,
    Or,
)

from channel.models import MessageRole
from channel.storage import (
    create_chat,
    delete_last_assistant_message,
    get_chat_by_id,
    list_chats_for_user,
    list_messages,
    patch_chat,
    put_message,
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
