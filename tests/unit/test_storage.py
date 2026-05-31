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
from boto3.dynamodb.conditions import And, BeginsWith, ConditionBase, Equals

from channel.models import MessageRole
from channel.storage import (
    create_chat,
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


class FakeTable:
    """Minimal in-memory stand-in for ``boto3.resource('dynamodb').Table``."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, Item: dict[str, Any]) -> dict[str, Any]:
        self.items[(Item["PK"], Item["SK"])] = dict(Item)
        return {}

    def get_item(self, Key: dict[str, str]) -> dict[str, Any]:
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        conds = _extract_conditions(kwargs["KeyConditionExpression"])
        limit = kwargs.get("Limit")
        scan_forward = kwargs.get("ScanIndexForward", True)

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
        if limit is not None:
            items = items[:limit]
        return {"Items": items}

    def update_item(self, Key: dict[str, str], **kwargs: Any) -> dict[str, Any]:
        item = self.items.setdefault((Key["PK"], Key["SK"]), {**Key})
        values = kwargs.get("ExpressionAttributeValues") or {}
        expression = kwargs.get("UpdateExpression", "")
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
            item[attr.strip()] = values[placeholder.strip()]
        for addition in [s.strip() for s in add_clause.split(",") if s.strip()]:
            attr, _, placeholder = addition.partition(" ")
            item[attr.strip()] = item.get(attr.strip(), 0) + values[placeholder.strip()]
        return {"Attributes": item}


@pytest.fixture
def table(monkeypatch: pytest.MonkeyPatch) -> FakeTable:
    fake = FakeTable()
    monkeypatch.setattr("channel.storage._table", lambda: fake)
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


def test_list_chats_passes_cursor(table: FakeTable) -> None:
    create_chat(user_id="u-1", title=None, model_default="m")
    chats, _ = list_chats_for_user("u-1", limit=10, cursor={"PK": "USER#u-1", "SK": "CHAT#x"})
    # Fake table doesn't actually honor ExclusiveStartKey; we just need
    # the code path that forwards a non-None cursor to be exercised.
    assert isinstance(chats, list)


def test_put_message_writes_sequenced_row(table: FakeTable) -> None:
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


def test_list_messages_passes_cursor(table: FakeTable) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="one", model=None)
    msgs, _ = list_messages(
        chat.chat_id,
        limit=10,
        cursor={"PK": f"CHAT#{chat.chat_id}", "SK": "MSG#x"},
    )
    assert isinstance(msgs, list)


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


def test_patch_chat_with_no_fields_is_noop(table: FakeTable) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    pk = "USER#u-1"
    sk = f"CHAT#{chat.created_at}#{chat.chat_id}"
    before = dict(table.items[(pk, sk)])
    patch_chat(user_id="u-1", chat=chat, title=None, archived=None)
    assert table.items[(pk, sk)] == before
