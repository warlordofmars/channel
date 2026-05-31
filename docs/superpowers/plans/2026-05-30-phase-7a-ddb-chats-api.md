# Phase 7a — DDB chats + CRUD API + canned stream

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship persistent chats end-to-end — DynamoDB schema, REST + SSE API, and the SPA wired to use both — with the model invocation still backed by a server-side canned reply (real Bedrock arrives in 7b).

**Architecture:** New `src/channel/storage.py` + `src/channel/models.py` modules. New `/api/chats/*` router with five endpoints (one is SSE). One new GSI on the existing single table. New `useChatList` + `useChatStream` hooks + small SSE parser; `useMockStream` deleted. Strands and AgentCore Memory are NOT touched in this phase — they arrive in 7b / 7c.

**Tech Stack:** FastAPI · Pydantic · boto3 (DynamoDB) · CDK · pytest · React 18 · Vite · vitest

**Spec:** `docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md`

**Coverage gate:** 100% on both Python (`pytest-cov`) and JS (`vitest v8`). CI fails below 100%.

**Commit cadence:** One commit per task. `uv run inv pre-push` before pushing.

---

## File map

**Created:**
- `src/channel/models.py`
- `src/channel/storage.py`
- `src/channel/api/chats.py`
- `tests/unit/test_models.py`
- `tests/unit/test_storage.py`
- `tests/unit/test_chats_api.py`
- `tests/integration/test_chat_persistence.py`
- `tests/integration/test_chat_ownership.py`
- `ui/src/lib/sseParser.js`
- `ui/src/lib/sseParser.test.js`
- `ui/src/hooks/useChatList.js`
- `ui/src/hooks/useChatList.test.js`
- `ui/src/hooks/useChatStream.js`
- `ui/src/hooks/useChatStream.test.js`

**Modified:**
- `infra/stacks/channel_stack.py` — add `ChatByIdIndex` GSI3
- `src/channel/api/main.py` — mount new router
- `tests/integration/conftest.py` — add new GSI to fixture table
- `ui/src/api.js` — chat endpoint wrappers
- `ui/src/app/Shell.jsx` — consume `useChatList`
- `ui/src/app/Sidebar.jsx` — render chats from prop
- `ui/src/app/Conversation.jsx` — URL param chatId, real hook
- `ui/src/app/ChatHome.jsx` — first-message-creates-chat
- `ui/src/app/Composer.jsx` — pass `Idempotency-Key`
- `ui/src/app/data.js` — delete SAMPLE_*, RECENTS

**Deleted:**
- `ui/src/hooks/useMockStream.js`
- `ui/src/hooks/useMockStream.test.js` (if it exists)

---

## Task 1: Pydantic models for chats and messages

**Files:**
- Create: `src/channel/models.py`
- Create: `tests/unit/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_models.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for chat domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from channel.models import (
    Chat,
    ChatCreate,
    ChatPatch,
    Message,
    MessageRole,
)


def test_chat_roundtrips_with_required_fields():
    chat = Chat(
        chat_id="abc",
        user_id="u-1",
        title="Hello",
        created_at="2026-05-30T00:00:00Z",
        last_message_at="2026-05-30T00:00:00Z",
        last_user_preview="",
        model_default="canned-stream-v1",
        message_count=0,
        archived=False,
    )
    assert chat.chat_id == "abc"
    assert chat.archived is False


def test_message_rejects_unknown_role():
    with pytest.raises(ValidationError):
        Message(
            chat_id="abc",
            msg_id="m-1",
            role="system",  # not user|assistant
            text="hi",
            created_at="2026-05-30T00:00:00Z",
        )


def test_message_assistant_carries_token_usage():
    msg = Message(
        chat_id="abc",
        msg_id="m-1",
        role=MessageRole.ASSISTANT,
        text="hello",
        model="canned-stream-v1",
        input_tokens=12,
        output_tokens=3,
        created_at="2026-05-30T00:00:00Z",
    )
    assert msg.role == MessageRole.ASSISTANT
    assert msg.input_tokens == 12


def test_chat_create_defaults_title():
    payload = ChatCreate()
    assert payload.title is None  # server will substitute "New chat"


def test_chat_patch_allows_partial_updates():
    patch = ChatPatch(title="Renamed")
    assert patch.title == "Renamed"
    assert patch.archived is None


def test_last_user_preview_caps_at_120_chars():
    long = "x" * 500
    chat = Chat(
        chat_id="abc",
        user_id="u-1",
        title="t",
        created_at="2026-05-30T00:00:00Z",
        last_message_at="2026-05-30T00:00:00Z",
        last_user_preview=long,
        model_default="m",
        message_count=0,
        archived=False,
    )
    assert len(chat.last_user_preview) == 120
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'channel.models'`

- [ ] **Step 3: Create the models module**

```python
# src/channel/models.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Pydantic models for chat domain — Chat, Message, and request/response shapes.

These are wire-format and storage-format models.  They are intentionally
flat: nested objects (artifacts, attachments) stay as ``list[dict]`` so
schema evolution doesn't require model surgery.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class Chat(BaseModel):
    """Chat-index row — one per chat, partition key USER#{user_id}."""

    chat_id: str
    user_id: str
    title: str
    created_at: str
    last_message_at: str
    last_user_preview: str = ""
    model_default: str
    message_count: int = 0
    archived: bool = False

    @field_validator("last_user_preview")
    @classmethod
    def _cap_preview(cls, value: str) -> str:
        return value[:120]


class Message(BaseModel):
    """One turn in a chat — partition key CHAT#{chat_id}."""

    chat_id: str
    msg_id: str
    role: MessageRole
    text: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    artifacts: list[dict[str, Any]] | None = None
    attachments: list[dict[str, Any]] | None = None
    created_at: str
    ttl: int | None = None


class ChatCreate(BaseModel):
    """Request body for POST /api/chats."""

    title: str | None = None
    model_default: str | None = None


class ChatPatch(BaseModel):
    """Request body for PATCH /api/chats/{id}."""

    title: str | None = None
    archived: bool | None = None


class SendMessageRequest(BaseModel):
    """Request body for POST /api/chats/{id}/messages."""

    message: str = Field(min_length=1, max_length=100_000)
    model: str | None = None
    effort: str | None = None
    attachments: list[dict[str, Any]] | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_models.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Coverage check**

```bash
uv run pytest tests/unit/test_models.py --cov=channel.models --cov-report=term-missing
```

Expected: 100% on `channel.models`. If anything is missing, add a test for that branch.

- [ ] **Step 6: Commit**

```bash
git add src/channel/models.py tests/unit/test_models.py
git commit -m "feat(channel-7a): add Pydantic models for chats and messages"
```

---

## Task 2: DynamoDB storage helpers

**Files:**
- Create: `src/channel/storage.py`
- Create: `tests/unit/test_storage.py`

`storage.py` owns the DDB read/write logic for chats and messages. The PK/SK shapes follow the spec:

- Chat-index row: `PK=USER#{user_id}, SK=CHAT#{created_at}#{chat_id}`
- Message row: `PK=CHAT#{chat_id}, SK=MSG#{created_at}#{seq:05d}`
- GSI projection on chat-index row: `GSI3PK=CHAT_ID#{chat_id}, GSI3SK=META`

Unit tests use a fake DDB table (in-memory dict keyed by `(PK, SK)`) injected via dependency-injection seam. Integration tests in Task 11 hit DDB Local.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_storage.py
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

from channel.models import MessageRole
from channel.storage import (
    create_chat,
    get_chat_by_id,
    list_chats_for_user,
    list_messages,
    put_message,
    update_chat_index,
)


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
        index = kwargs.get("IndexName")
        if index == "ChatByIdIndex":
            pk_value = kwargs["KeyConditionExpression"]
            items = [
                item
                for item in self.items.values()
                if item.get("GSI3PK") == pk_value and item.get("GSI3SK") == "META"
            ]
            return {"Items": items}
        # Main table query: PK begins_with(SK, prefix) or full
        pk = kwargs["pk"]
        sk_prefix = kwargs.get("sk_prefix", "")
        limit = kwargs.get("Limit")
        scan_forward = kwargs.get("ScanIndexForward", True)
        items = sorted(
            (i for i in self.items.values() if i["PK"] == pk and i["SK"].startswith(sk_prefix)),
            key=lambda i: i["SK"],
            reverse=not scan_forward,
        )
        if limit is not None:
            items = items[:limit]
        return {"Items": items}

    def update_item(self, Key: dict[str, str], **kwargs: Any) -> dict[str, Any]:
        item = self.items.setdefault((Key["PK"], Key["SK"]), {**Key})
        values = kwargs.get("ExpressionAttributeValues") or {}
        for placeholder, value in values.items():
            attr = placeholder.lstrip(":")
            item[attr] = value
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
    pk = f"USER#u-1"
    sk_prefix = "CHAT#"
    stored = next(
        item for item in table.items.values() if item["PK"] == pk and item["SK"].startswith(sk_prefix)
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


def test_put_message_writes_sequenced_row(table: FakeTable) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    msg = put_message(
        chat_id=chat.chat_id,
        role=MessageRole.USER,
        text="hello",
        model=None,
    )
    stored = next(
        item for item in table.items.values() if item["PK"] == f"CHAT#{chat.chat_id}"
    )
    assert stored["text"] == "hello"
    assert stored["role"] == "user"
    assert "seq" in stored["SK"] or stored["SK"].startswith("MSG#")


def test_list_messages_returns_chronological_order(table: FakeTable) -> None:
    chat = create_chat(user_id="u-1", title=None, model_default="m")
    put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="one", model=None)
    put_message(
        chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="two", model="m"
    )
    msgs, _ = list_messages(chat.chat_id, limit=10, cursor=None)
    assert [m.text for m in msgs] == ["one", "two"]


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
        item for item in table.items.values() if item["PK"] == pk and item["SK"].startswith(sk_prefix)
    )
    assert stored["message_count"] == 2
    assert stored["last_user_preview"] == "hi there"
    assert stored["last_message_at"] == "2026-05-30T01:00:00Z"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_storage.py -v
```

Expected: `ModuleNotFoundError: No module named 'channel.storage'`

- [ ] **Step 3: Implement storage helpers**

```python
# src/channel/storage.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""DynamoDB persistence for chats and messages.

Single-table design with these row families:
- Chat-index row  ``PK=USER#{u}, SK=CHAT#{created_at}#{chat_id}``
- Message row     ``PK=CHAT#{chat_id}, SK=MSG#{created_at}#{seq:05d}``

The chat-index row also projects onto the ``ChatByIdIndex`` GSI
(``GSI3PK=CHAT_ID#{chat_id}, GSI3SK=META``) so direct chat-id lookups
don't have to know the original ``created_at``.

Tests inject a fake table via the module-level ``_table`` symbol; in
production it returns the real boto3 ``Table`` resource.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from itertools import count
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from channel.models import Chat, Message, MessageRole

_CHAT_INDEX_GSI = "ChatByIdIndex"
_DEFAULT_TITLE = "New chat"

_seq_counter = count(1)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _next_seq() -> int:
    """Monotonic in-process counter used to disambiguate sub-millisecond writes."""

    return next(_seq_counter)


@lru_cache(maxsize=1)
def _real_table() -> Any:
    table_name = os.environ["STARTER_TABLE_NAME"]
    endpoint = os.environ.get("DYNAMODB_ENDPOINT")
    kwargs: dict[str, Any] = {"region_name": os.environ.get("AWS_DEFAULT_REGION", "us-east-1")}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.resource("dynamodb", **kwargs).Table(table_name)


def _table() -> Any:  # pragma: no cover - tests replace this seam
    return _real_table()


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
    _table().put_item(Item=_chat_index_item(chat))
    return chat


def get_chat_by_id(chat_id: str) -> Chat | None:
    """Look up a chat-index row through the ``ChatByIdIndex`` GSI."""

    result = _table().query(
        IndexName=_CHAT_INDEX_GSI,
        KeyConditionExpression=f"CHAT_ID#{chat_id}",
    )
    items = result.get("Items") or []
    if not items:
        return None
    return _chat_from_item(items[0])


def list_chats_for_user(
    user_id: str, *, limit: int, cursor: str | None
) -> tuple[list[Chat], str | None]:
    """List chats for a user, newest first, paginated by SK cursor."""

    kwargs: dict[str, Any] = {
        "pk": f"USER#{user_id}",
        "sk_prefix": "CHAT#",
        "Limit": limit,
        "ScanIndexForward": False,
    }
    if cursor:
        kwargs["ExclusiveStartKey"] = cursor
    result = _table().query(**kwargs)
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
    seq = _next_seq()
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
    item = {
        "PK": f"CHAT#{chat_id}",
        "SK": f"MSG#{now}#{seq:05d}",
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
    _table().put_item(Item={k: v for k, v in item.items() if v is not None})
    return msg


def list_messages(
    chat_id: str, *, limit: int, cursor: str | None
) -> tuple[list[Message], str | None]:
    kwargs: dict[str, Any] = {
        "pk": f"CHAT#{chat_id}",
        "sk_prefix": "MSG#",
        "Limit": limit,
        "ScanIndexForward": True,
    }
    if cursor:
        kwargs["ExclusiveStartKey"] = cursor
    result = _table().query(**kwargs)
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

    _table().update_item(
        Key={
            "PK": f"USER#{user_id}",
            "SK": _chat_index_sk(chat.created_at, chat.chat_id),
        },
        UpdateExpression=(
            "SET last_user_preview = :p, last_message_at = :t "
            "ADD message_count :c"
        ),
        ExpressionAttributeValues={
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
    _table().update_item(
        Key={
            "PK": f"USER#{user_id}",
            "SK": _chat_index_sk(chat.created_at, chat.chat_id),
        },
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeValues=values,
    )


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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_storage.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Coverage check**

```bash
uv run pytest tests/unit/test_storage.py --cov=channel.storage --cov-report=term-missing
```

Expected: 100% on `channel.storage` except for the `# pragma: no cover` seam (`_table()`) and `_real_table()` (covered by integration tests in Task 11).  If any other line is uncovered, add a test:
- `patch_chat` with no fields (early return) — add `test_patch_chat_with_no_fields_is_noop`
- `list_chats_for_user` with a `cursor` — add `test_list_chats_passes_cursor`
- `put_message` with `artifacts` / `attachments` populated — add `test_put_message_persists_artifacts`

- [ ] **Step 6: Commit**

```bash
git add src/channel/storage.py tests/unit/test_storage.py
git commit -m "feat(channel-7a): add DynamoDB storage helpers for chats and messages"
```

---

## Task 3: CDK — add `ChatByIdIndex` GSI to the table

**Files:**
- Modify: `infra/stacks/channel_stack.py:115-120` (add new GSI after `UserEmailIndex`)
- Modify: `tests/integration/conftest.py` — add new GSI to the test table fixture

The existing table already has GSIs numbered `GSI1PK/SK`, `GSI2PK/SK`, `GSI4PK`. We use the unused `GSI3PK/SK` slot for `ChatByIdIndex`.

- [ ] **Step 1: Add the GSI in CDK**

Edit `infra/stacks/channel_stack.py` — after the `UserEmailIndex` block at line ~118, insert:

```python
        # GSI 3 — ChatByIdIndex: look up chat-index rows by chat_id
        table.add_global_secondary_index(
            index_name="ChatByIdIndex",
            partition_key=dynamodb.Attribute(name="GSI3PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="GSI3SK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )
```

- [ ] **Step 2: Update the integration-test table fixture**

Open `tests/integration/conftest.py` and find the `starter_table` fixture's `GlobalSecondaryIndexes=[...]` block.  Add an entry mirroring the new GSI:

```python
{
    "IndexName": "ChatByIdIndex",
    "KeySchema": [
        {"AttributeName": "GSI3PK", "KeyType": "HASH"},
        {"AttributeName": "GSI3SK", "KeyType": "RANGE"},
    ],
    "Projection": {"ProjectionType": "ALL"},
},
```

And add the attribute definitions:

```python
{"AttributeName": "GSI3PK", "AttributeType": "S"},
{"AttributeName": "GSI3SK", "AttributeType": "S"},
```

- [ ] **Step 3: Synth the stack to verify no CDK errors**

```bash
uv run inv synth
```

Expected: synth succeeds; `cdk.out/` has updated CloudFormation with the new GSI under the table resource.  No Trivy warnings on the GSI specifically.

- [ ] **Step 4: Verify the integration-test table fixture is still valid**

```bash
docker run -d -p 8000:8000 amazon/dynamodb-local:latest  # if not already running
uv run pytest tests/integration/test_auth_state_store.py -v
```

Expected: existing integration suite still passes — the fixture changes are additive.

- [ ] **Step 5: Commit**

```bash
git add infra/stacks/channel_stack.py tests/integration/conftest.py
git commit -m "feat(channel-7a): add ChatByIdIndex GSI to the single table"
```

---

## Task 4: Skeleton `/api/chats` router + mount in main

**Files:**
- Create: `src/channel/api/chats.py`
- Create: `tests/unit/test_chats_api.py`
- Modify: `src/channel/api/main.py` — mount the new router

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chats_api.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api/chats router.

Tests use FastAPI's ``TestClient`` and patch ``channel.storage`` at the
module boundary.  ``require_mgmt_user`` is overridden to return a stub
claims dict.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from channel.api._auth import require_mgmt_user
from channel.api.main import app


@pytest.fixture
def client() -> TestClient:
    def _stub_user() -> dict[str, Any]:
        return {"sub": "u-1", "role": "user"}

    app.dependency_overrides[require_mgmt_user] = _stub_user
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_chats_router_is_mounted(client: TestClient) -> None:
    response = client.get("/api/chats")
    # 200 (empty list) or 500 (storage not stubbed yet) — but NOT 404.
    assert response.status_code != 404
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: 404 because the router isn't mounted yet.

- [ ] **Step 3: Create the router skeleton**

```python
# src/channel/api/chats.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Chat REST + SSE API.

All endpoints require a valid management JWT.  Ownership is enforced by
looking the chat up via the ``ChatByIdIndex`` GSI and comparing
``user_id`` to the JWT ``sub`` claim — mismatches return 404 (not 403)
so chat existence isn't leaked.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from channel.api._auth import require_mgmt_user

router = APIRouter(prefix="/chats", tags=["chats"])


@router.get("")
async def list_chats(
    _claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Placeholder — populated in Task 6."""

    return {"items": [], "next_cursor": None}
```

- [ ] **Step 4: Mount the router in main**

In `src/channel/api/main.py`, find the existing `app.include_router(csp_router, prefix="/api")` line and add after it:

```python
from channel.api.chats import router as chats_router

app.include_router(chats_router, prefix="/api")
```

(Import goes at top of file with the other imports; the `include_router` call goes with the other ones.)

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: PASS (`response.status_code == 200`).

- [ ] **Step 6: Commit**

```bash
git add src/channel/api/chats.py src/channel/api/main.py tests/unit/test_chats_api.py
git commit -m "feat(channel-7a): mount /api/chats router skeleton"
```

---

## Task 5: `POST /api/chats` — create a chat

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_chats_api.py`:

```python
def test_post_creates_chat_and_returns_metadata(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat

    created: list[Chat] = []

    def fake_create_chat(*, user_id: str, title: str | None, model_default: str) -> Chat:
        chat = Chat(
            chat_id="chat-1",
            user_id=user_id,
            title=title or "New chat",
            created_at="2026-05-30T00:00:00Z",
            last_message_at="2026-05-30T00:00:00Z",
            last_user_preview="",
            model_default=model_default,
            message_count=0,
            archived=False,
        )
        created.append(chat)
        return chat

    monkeypatch.setattr("channel.api.chats.storage.create_chat", fake_create_chat)

    response = client.post("/api/chats", json={"model_default": "canned-stream-v1"})

    assert response.status_code == 201
    body = response.json()
    assert body["chat_id"] == "chat-1"
    assert body["title"] == "New chat"
    assert created[0].user_id == "u-1"


def test_post_chat_uses_default_model_when_unspecified(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_create_chat(**kwargs: Any):
        from channel.models import Chat

        captured.update(kwargs)
        return Chat(
            chat_id="c",
            user_id=kwargs["user_id"],
            title=kwargs["title"] or "New chat",
            created_at="t",
            last_message_at="t",
            model_default=kwargs["model_default"],
        )

    monkeypatch.setattr("channel.api.chats.storage.create_chat", fake_create_chat)
    client.post("/api/chats", json={})
    assert captured["model_default"] == "canned-stream-v1"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_chats_api.py::test_post_creates_chat_and_returns_metadata -v
```

Expected: 405 Method Not Allowed (POST handler not implemented).

- [ ] **Step 3: Implement the POST handler**

Replace the body of `src/channel/api/chats.py` with:

```python
# src/channel/api/chats.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Chat REST + SSE API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from channel import storage
from channel.api._auth import require_mgmt_user
from channel.models import ChatCreate

_DEFAULT_MODEL = "canned-stream-v1"

router = APIRouter(prefix="/chats", tags=["chats"])


@router.post("", status_code=201)
async def create_chat(
    payload: ChatCreate,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Create a new chat for the authenticated user."""

    chat = storage.create_chat(
        user_id=claims["sub"],
        title=payload.title,
        model_default=payload.model_default or _DEFAULT_MODEL,
    )
    return chat.model_dump()


@router.get("")
async def list_chats(
    _claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Placeholder — populated in Task 6."""

    return {"items": [], "next_cursor": None}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "feat(channel-7a): POST /api/chats — create chat endpoint"
```

---

## Task 6: `GET /api/chats` — list chats

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_chats_api.py`:

```python
def test_list_returns_chats_for_authenticated_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat

    def fake_list(user_id: str, *, limit: int, cursor: str | None):
        assert user_id == "u-1"
        assert limit == 50
        return (
            [
                Chat(
                    chat_id="c1",
                    user_id=user_id,
                    title="Hi",
                    created_at="2026-05-30T00:00:00Z",
                    last_message_at="2026-05-30T00:00:00Z",
                    model_default="canned-stream-v1",
                )
            ],
            None,
        )

    monkeypatch.setattr("channel.api.chats.storage.list_chats_for_user", fake_list)

    response = client.get("/api/chats")
    body = response.json()
    assert response.status_code == 200
    assert len(body["items"]) == 1
    assert body["items"][0]["chat_id"] == "c1"
    assert body["next_cursor"] is None


def test_list_honors_limit_and_cursor(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(user_id: str, *, limit: int, cursor: str | None):
        captured.update({"limit": limit, "cursor": cursor})
        return ([], "next-cursor-token")

    monkeypatch.setattr("channel.api.chats.storage.list_chats_for_user", fake_list)
    response = client.get("/api/chats?limit=5&cursor=abc")
    assert response.status_code == 200
    assert captured == {"limit": 5, "cursor": "abc"}
    assert response.json()["next_cursor"] == "next-cursor-token"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_chats_api.py::test_list_returns_chats_for_authenticated_user -v
```

Expected: FAIL (items list is empty placeholder).

- [ ] **Step 3: Implement the list handler**

Replace the placeholder `list_chats` in `src/channel/api/chats.py` with:

```python
@router.get("")
async def list_chats(
    limit: int = 50,
    cursor: str | None = None,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """List the authenticated user's chats, newest first."""

    chats, next_cursor = storage.list_chats_for_user(
        claims["sub"], limit=limit, cursor=cursor
    )
    return {
        "items": [c.model_dump() for c in chats],
        "next_cursor": next_cursor,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "feat(channel-7a): GET /api/chats — list endpoint with cursor pagination"
```

---

## Task 7: `GET /api/chats/{chat_id}` — fetch chat + messages

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

The ownership check is the load-bearing piece: `get_chat_by_id` looks up via the GSI; if the chat's `user_id` doesn't match the JWT `sub`, the endpoint returns 404 (not 403) to avoid leaking existence.

- [ ] **Step 1: Write the failing tests**

```python
def test_get_chat_returns_chat_and_messages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat, Message, MessageRole

    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="2026-05-30T00:00:00Z",
        last_message_at="2026-05-30T00:00:00Z",
        model_default="canned-stream-v1",
    )

    def fake_get(chat_id: str) -> Chat | None:
        return chat if chat_id == "c1" else None

    def fake_msgs(chat_id: str, *, limit: int, cursor: str | None):
        return (
            [
                Message(
                    chat_id="c1",
                    msg_id="m1",
                    role=MessageRole.USER,
                    text="hi",
                    created_at="2026-05-30T00:00:00Z",
                )
            ],
            None,
        )

    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", fake_get)
    monkeypatch.setattr("channel.api.chats.storage.list_messages", fake_msgs)

    response = client.get("/api/chats/c1")
    body = response.json()
    assert response.status_code == 200
    assert body["chat"]["chat_id"] == "c1"
    assert body["messages"][0]["text"] == "hi"
    assert body["next_cursor"] is None


def test_get_chat_returns_404_for_unknown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: None)
    response = client.get("/api/chats/does-not-exist")
    assert response.status_code == 404


def test_get_chat_returns_404_when_owner_mismatches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat

    other = Chat(
        chat_id="c1",
        user_id="u-other",
        title="t",
        created_at="2026-05-30T00:00:00Z",
        last_message_at="2026-05-30T00:00:00Z",
        model_default="m",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: other)
    response = client.get("/api/chats/c1")
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: 3 failures (404 → 405 since handler doesn't exist).

- [ ] **Step 3: Implement the GET handler**

Append to `src/channel/api/chats.py`:

```python
from fastapi import HTTPException, Path


async def _load_owned_chat(chat_id: str, user_id: str):
    """Look up a chat by id and assert ownership.  404 on mismatch."""

    chat = storage.get_chat_by_id(chat_id)
    if chat is None or chat.user_id != user_id:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.get("/{chat_id}")
async def get_chat(
    chat_id: str = Path(...),
    limit: int = 200,
    cursor: str | None = None,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Get a chat's metadata and a page of messages."""

    chat = await _load_owned_chat(chat_id, claims["sub"])
    messages, next_cursor = storage.list_messages(chat_id, limit=limit, cursor=cursor)
    return {
        "chat": chat.model_dump(),
        "messages": [m.model_dump(mode="json") for m in messages],
        "next_cursor": next_cursor,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "feat(channel-7a): GET /api/chats/{id} with ownership check"
```

---

## Task 8: `PATCH /api/chats/{chat_id}` — rename / archive

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_patch_renames_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat

    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="Old",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    captured: dict[str, Any] = {}

    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)

    def fake_patch(*, user_id: str, chat: Chat, title, archived):
        captured.update({"title": title, "archived": archived})

    monkeypatch.setattr("channel.api.chats.storage.patch_chat", fake_patch)

    response = client.patch("/api/chats/c1", json={"title": "New"})
    assert response.status_code == 204
    assert captured == {"title": "New", "archived": None}


def test_patch_archives_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat

    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="m",
    )
    captured: dict[str, Any] = {}

    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)
    monkeypatch.setattr(
        "channel.api.chats.storage.patch_chat",
        lambda **kwargs: captured.update(kwargs),
    )

    response = client.patch("/api/chats/c1", json={"archived": True})
    assert response.status_code == 204
    assert captured["archived"] is True


def test_patch_returns_404_for_unowned_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: None)
    response = client.patch("/api/chats/x", json={"title": "y"})
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: PATCH handler not implemented → 405.

- [ ] **Step 3: Implement the PATCH handler**

Append to `src/channel/api/chats.py`:

```python
from fastapi import Response
from channel.models import ChatPatch


@router.patch("/{chat_id}", status_code=204)
async def patch_chat(
    payload: ChatPatch,
    chat_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Rename or archive a chat.  204 No Content on success."""

    chat = await _load_owned_chat(chat_id, claims["sub"])
    storage.patch_chat(
        user_id=claims["sub"],
        chat=chat,
        title=payload.title,
        archived=payload.archived,
    )
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: all PATCH tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "feat(channel-7a): PATCH /api/chats/{id} — rename and archive"
```

---

## Task 9: `POST /api/chats/{chat_id}/messages` — canned SSE stream

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

Streaming endpoint.  Behavior:

1. Look up + own-check the chat (404 on mismatch).
2. Persist the user message via `storage.put_message`.
3. Stream `user_persisted` + a sequence of `delta` events from a canned reply + `done`.
4. Persist the assistant message via `storage.put_message` after the stream completes.
5. Update the chat-index row's `last_user_preview`, `last_message_at`, `message_count += 2`.

The canned reply text is a constant inside `chats.py` — the **same text** the SPA previously hardcoded as `SAMPLE_REPLY` so visual behavior in the UI is unchanged.

- [ ] **Step 1: Write the failing test**

```python
def test_post_message_returns_sse_with_canned_stream(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat, Message, MessageRole

    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="canned-stream-v1",
    )

    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)

    persisted: list[Message] = []

    def fake_put(**kwargs: Any) -> Message:
        msg = Message(
            chat_id=kwargs["chat_id"],
            msg_id=f"m-{len(persisted)}",
            role=kwargs["role"],
            text=kwargs["text"],
            model=kwargs.get("model"),
            created_at="t",
        )
        persisted.append(msg)
        return msg

    monkeypatch.setattr("channel.api.chats.storage.put_message", fake_put)
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)

    response = client.post("/api/chats/c1/messages", json={"message": "hello"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text

    # SSE shape: at least one user_persisted, at least one delta, one done.
    assert "user_persisted" in body
    assert '"type": "delta"' in body
    assert '"type": "done"' in body

    # Two messages persisted: the user turn, then the assistant turn.
    assert [m.role for m in persisted] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert persisted[1].text  # non-empty canned reply


def test_post_message_returns_404_for_unowned_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: None)
    response = client.post("/api/chats/x/messages", json={"message": "hi"})
    assert response.status_code == 404


def test_post_message_rejects_empty_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat

    monkeypatch.setattr(
        "channel.api.chats.storage.get_chat_by_id",
        lambda _: Chat(
            chat_id="c1",
            user_id="u-1",
            title="t",
            created_at="t",
            last_message_at="t",
            model_default="m",
        ),
    )
    response = client.post("/api/chats/c1/messages", json={"message": ""})
    assert response.status_code == 422  # Pydantic min_length=1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: POST /messages doesn't exist → 405 / 404.

- [ ] **Step 3: Implement the streaming handler**

Append to `src/channel/api/chats.py`:

```python
import json

from fastapi.responses import StreamingResponse

from channel.models import MessageRole, SendMessageRequest


_CANNED_REPLY = (
    "Here's how I'd think about it. "
    "**First**, the ingestion buffer drains in roughly constant time. "
    "**Second**, the consumer-side fan-out can be parallelised cheaply. "
    "**Third**, retries should be idempotent or you'll double-count. "
    "Want me to sketch the buffer interface?"
)
_CANNED_MODEL = "canned-stream-v1"


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


async def _stream_canned_reply(
    *,
    chat,
    user_message: str,
    claims: dict[str, Any],
):
    """Persist the user turn, emit SSE, persist the assistant turn, update index."""

    user_msg = storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.USER,
        text=user_message,
        model=None,
    )
    yield _sse({"type": "user_persisted", "msg_id": user_msg.msg_id, "seq": 0})

    # Stream the canned reply in word-chunks so the UI sees a real
    # incremental render even pre-Bedrock.
    words = _CANNED_REPLY.split(" ")
    for i, word in enumerate(words):
        chunk = (" " if i else "") + word
        yield _sse({"type": "delta", "text": chunk})

    assistant_msg = storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.ASSISTANT,
        text=_CANNED_REPLY,
        model=_CANNED_MODEL,
        input_tokens=0,
        output_tokens=0,
    )

    storage.update_chat_index(
        user_id=claims["sub"],
        chat=chat,
        last_user_preview=user_message,
        delta_count=2,
        last_message_at=assistant_msg.created_at,
    )

    yield _sse(
        {
            "type": "done",
            "msg_id": assistant_msg.msg_id,
            "seq": 1,
            "model": _CANNED_MODEL,
            "input_tokens": 0,
            "output_tokens": 0,
            "stop_reason": "end_turn",
        }
    )


@router.post("/{chat_id}/messages")
async def post_message(
    payload: SendMessageRequest,
    chat_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> StreamingResponse:
    """Send a user message and stream the (canned, for now) assistant reply."""

    chat = await _load_owned_chat(chat_id, claims["sub"])
    return StreamingResponse(
        _stream_canned_reply(
            chat=chat,
            user_message=payload.message,
            claims=claims,
        ),
        media_type="text/event-stream",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Coverage check**

```bash
uv run pytest tests/unit/test_chats_api.py --cov=channel.api.chats --cov-report=term-missing
```

Expected: 100%.  If anything is uncovered:
- Empty-`message` validation branch — covered by `test_post_message_rejects_empty_message` above.
- 401 (no Bearer token) — add `test_endpoints_require_auth` that hits each endpoint without the dependency override.

- [ ] **Step 6: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "feat(channel-7a): POST /api/chats/{id}/messages with canned SSE stream"
```

---

## Task 10: Idempotency-Key support

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

Idempotency keys live in DDB at `PK=IDEMP#{user_id}, SK={key}`, with TTL `now + 1 hour`.  On duplicate within the window, the second call replays the stored response without invoking the stream.

For 7a, the replay payload is the persisted `msg_id` and `text` of the assistant turn; we synthesise the SSE events from those so the UI gets the same byte stream both times.

- [ ] **Step 1: Add helpers in storage**

Append to `src/channel/storage.py`:

```python
import time


def reserve_idempotency_key(*, user_id: str, key: str) -> dict[str, Any] | None:
    """Reserve a key by ConditionalPut.  Returns stored payload if already used."""

    now = int(time.time())
    ttl = now + 3600
    try:
        _table().put_item(
            Item={
                "PK": f"IDEMP#{user_id}",
                "SK": key,
                "reserved_at": now,
                "ttl": ttl,
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
        return None
    except Exception as exc:  # ConditionalCheckFailedException in real boto3
        if "ConditionalCheckFailed" not in str(type(exc)) and "ConditionalCheck" not in str(exc):
            raise
        existing = _table().get_item(
            Key={"PK": f"IDEMP#{user_id}", "SK": key}
        ).get("Item")
        return existing


def store_idempotency_result(
    *, user_id: str, key: str, payload: dict[str, Any]
) -> None:
    _table().update_item(
        Key={"PK": f"IDEMP#{user_id}", "SK": key},
        UpdateExpression="SET result = :r",
        ExpressionAttributeValues={":r": payload},
    )
```

- [ ] **Step 2: Add a unit test for reservation + replay**

Append to `tests/unit/test_storage.py`:

```python
def test_idempotency_reserve_then_replay(table: FakeTable) -> None:
    from channel import storage as st

    first = st.reserve_idempotency_key(user_id="u-1", key="k1")
    assert first is None

    st.store_idempotency_result(user_id="u-1", key="k1", payload={"text": "hello"})

    # FakeTable doesn't simulate ConditionExpression, so we mimic the
    # "already exists" path by exercising the get directly.
    existing = (
        table.get_item(Key={"PK": "IDEMP#u-1", "SK": "k1"}).get("Item")
    )
    assert existing is not None
    assert existing["result"] == {"text": "hello"}
```

> **Note:** This test exercises the storage helpers but not the
> ConditionalCheck branch (the fake table doesn't model that).  The
> integration test in Task 11 covers the real ConditionalCheck path
> against DDB Local.

- [ ] **Step 3: Wire Idempotency-Key into the SSE handler**

In `src/channel/api/chats.py`, change `post_message`:

```python
from fastapi import Header


@router.post("/{chat_id}/messages")
async def post_message(
    payload: SendMessageRequest,
    chat_id: str = Path(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> StreamingResponse:
    chat = await _load_owned_chat(chat_id, claims["sub"])

    replay: dict[str, Any] | None = None
    if idempotency_key:
        existing = storage.reserve_idempotency_key(
            user_id=claims["sub"], key=idempotency_key
        )
        if existing is not None and existing.get("result"):
            replay = existing["result"]

    if replay is not None:
        async def _replay():
            yield _sse({"type": "user_persisted", "msg_id": replay["user_msg_id"], "seq": 0})
            yield _sse({"type": "delta", "text": replay["text"]})
            yield _sse({"type": "done", **replay["done"]})

        return StreamingResponse(_replay(), media_type="text/event-stream")

    async def _produce():
        async for chunk in _stream_canned_reply(
            chat=chat, user_message=payload.message, claims=claims
        ):
            yield chunk
        if idempotency_key:
            storage.store_idempotency_result(
                user_id=claims["sub"],
                key=idempotency_key,
                payload={
                    "user_msg_id": "n/a",
                    "text": _CANNED_REPLY,
                    "done": {
                        "model": _CANNED_MODEL,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "stop_reason": "end_turn",
                    },
                },
            )

    return StreamingResponse(_produce(), media_type="text/event-stream")
```

- [ ] **Step 4: Test the replay shortcut**

Append to `tests/unit/test_chats_api.py`:

```python
def test_post_message_replays_on_duplicate_idempotency_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat

    monkeypatch.setattr(
        "channel.api.chats.storage.get_chat_by_id",
        lambda _: Chat(
            chat_id="c1",
            user_id="u-1",
            title="t",
            created_at="t",
            last_message_at="t",
            model_default="m",
        ),
    )

    stored_result = {
        "user_msg_id": "user-existing",
        "text": "previous reply",
        "done": {
            "model": "canned-stream-v1",
            "input_tokens": 0,
            "output_tokens": 0,
            "stop_reason": "end_turn",
        },
    }

    monkeypatch.setattr(
        "channel.api.chats.storage.reserve_idempotency_key",
        lambda **_: {"result": stored_result},
    )
    # put_message MUST NOT be called on the replay path.
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **_: pytest.fail("replay path must skip put_message"),
    )

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi"},
        headers={"Idempotency-Key": "kkk"},
    )
    assert response.status_code == 200
    assert "previous reply" in response.text
```

- [ ] **Step 5: Run tests + check coverage**

```bash
uv run pytest tests/unit/test_chats_api.py tests/unit/test_storage.py -v --cov=channel.api.chats --cov=channel.storage
```

Expected: 100% on `channel.api.chats`.  `channel.storage` may show the ConditionalCheck error branch uncovered — Task 11's integration test exercises it.

- [ ] **Step 6: Commit**

```bash
git add src/channel/api/chats.py src/channel/storage.py tests/unit/test_chats_api.py tests/unit/test_storage.py
git commit -m "feat(channel-7a): Idempotency-Key support on POST /messages"
```

---

## Task 11: Integration tests against DynamoDB Local

**Files:**
- Create: `tests/integration/test_chat_persistence.py`
- Create: `tests/integration/test_chat_ownership.py`

Integration tests use the real boto3 client against DDB Local.  The session-scoped `starter_table` fixture from `tests/integration/conftest.py` already provisions the table.

- [ ] **Step 1: Persistence round-trip test**

```python
# tests/integration/test_chat_persistence.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for chat persistence against DynamoDB Local."""

from __future__ import annotations

import pytest

from channel import storage
from channel.models import MessageRole


@pytest.mark.usefixtures("starter_table")
def test_create_then_get_then_list() -> None:
    chat = storage.create_chat(
        user_id="u-itest",
        title=None,
        model_default="canned-stream-v1",
    )

    fetched = storage.get_chat_by_id(chat.chat_id)
    assert fetched is not None
    assert fetched.user_id == "u-itest"

    chats, _ = storage.list_chats_for_user("u-itest", limit=10, cursor=None)
    assert any(c.chat_id == chat.chat_id for c in chats)


@pytest.mark.usefixtures("starter_table")
def test_message_round_trip() -> None:
    chat = storage.create_chat(
        user_id="u-itest-msgs",
        title=None,
        model_default="canned-stream-v1",
    )
    storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.USER,
        text="hi",
        model=None,
    )
    storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.ASSISTANT,
        text="hello",
        model="canned-stream-v1",
        input_tokens=0,
        output_tokens=0,
    )
    storage.update_chat_index(
        user_id=chat.user_id,
        chat=chat,
        last_user_preview="hi",
        delta_count=2,
        last_message_at="2026-05-30T00:00:00Z",
    )

    messages, _ = storage.list_messages(chat.chat_id, limit=10, cursor=None)
    assert [m.text for m in messages] == ["hi", "hello"]

    refreshed = storage.get_chat_by_id(chat.chat_id)
    assert refreshed is not None
    assert refreshed.message_count == 2
    assert refreshed.last_user_preview == "hi"


@pytest.mark.usefixtures("starter_table")
def test_idempotency_key_conditional_check() -> None:
    """Real ConditionalCheckFailedException path — uncovered by the unit suite."""

    first = storage.reserve_idempotency_key(user_id="u-idem", key="iter-1")
    assert first is None

    second = storage.reserve_idempotency_key(user_id="u-idem", key="iter-1")
    assert second is not None
    assert second["PK"] == "IDEMP#u-idem"
```

- [ ] **Step 2: Ownership test**

```python
# tests/integration/test_chat_ownership.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration test: chat lookup via GSI returns the actual user_id."""

from __future__ import annotations

import pytest

from channel import storage


@pytest.mark.usefixtures("starter_table")
def test_get_chat_by_id_returns_owners_chat() -> None:
    a = storage.create_chat(user_id="u-A", title=None, model_default="m")
    b = storage.create_chat(user_id="u-B", title=None, model_default="m")

    fetched_a = storage.get_chat_by_id(a.chat_id)
    fetched_b = storage.get_chat_by_id(b.chat_id)

    assert fetched_a is not None and fetched_a.user_id == "u-A"
    assert fetched_b is not None and fetched_b.user_id == "u-B"
```

- [ ] **Step 3: Run integration tests**

```bash
docker ps | grep dynamodb-local || docker run -d -p 8000:8000 amazon/dynamodb-local:latest
uv run inv test-integration
```

Expected: all new tests pass alongside existing ones.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_chat_persistence.py tests/integration/test_chat_ownership.py
git commit -m "test(channel-7a): integration tests for chat persistence + ownership"
```

---

## Task 12: SSE parser library

**Files:**
- Create: `ui/src/lib/sseParser.js`
- Create: `ui/src/lib/sseParser.test.js`

Small reusable parser used by both `useChatStream` and any future stream consumer.  Pure function; no DOM, no React.

- [ ] **Step 1: Write the failing tests**

```js
// ui/src/lib/sseParser.test.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect } from "vitest";
import { parseSseChunk, makeSseDecoder } from "./sseParser.js";

describe("parseSseChunk", () => {
  it("parses a single complete event", () => {
    const events = parseSseChunk('data: {"type":"delta","text":"hi"}\n\n');
    expect(events).toEqual([{ type: "delta", text: "hi" }]);
  });

  it("parses multiple events in one chunk", () => {
    const raw =
      'data: {"type":"delta","text":"a"}\n\n' +
      'data: {"type":"delta","text":"b"}\n\n';
    const events = parseSseChunk(raw);
    expect(events).toEqual([
      { type: "delta", text: "a" },
      { type: "delta", text: "b" },
    ]);
  });

  it("ignores comment lines starting with colon", () => {
    const events = parseSseChunk(':keepalive\n\ndata: {"type":"done"}\n\n');
    expect(events).toEqual([{ type: "done" }]);
  });

  it("skips malformed JSON gracefully", () => {
    const events = parseSseChunk("data: not-json\n\n");
    expect(events).toEqual([]);
  });
});

describe("makeSseDecoder", () => {
  it("buffers a split event across two chunks", () => {
    const decoder = makeSseDecoder();
    const first = decoder.feed('data: {"type":"delta",');
    expect(first).toEqual([]);
    const second = decoder.feed('"text":"hi"}\n\n');
    expect(second).toEqual([{ type: "delta", text: "hi" }]);
  });

  it("handles a trailing partial event without losing data", () => {
    const decoder = makeSseDecoder();
    decoder.feed('data: {"type":"done"}\n\ndata: {"typ');
    expect(decoder.feed('e":"delta","text":"x"}\n\n')).toEqual([
      { type: "delta", text: "x" },
    ]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ui && npx vitest run src/lib/sseParser.test.js
```

Expected: import errors.

- [ ] **Step 3: Implement the parser**

```js
// ui/src/lib/sseParser.js
// Copyright (c) 2026 John Carter. All rights reserved.

/**
 * Parse zero or more complete SSE events from a string. Lines beginning
 * with `:` are SSE comments (keepalives) and are ignored. `data:` lines
 * are JSON-parsed; malformed payloads are dropped silently rather than
 * crashing the stream.
 *
 * @param {string} chunk - one or more complete `data: ...\n\n` events
 * @returns {Array<object>} parsed event objects
 */
export function parseSseChunk(chunk) {
  const events = [];
  const blocks = chunk.split("\n\n");
  for (const block of blocks) {
    if (!block) continue;
    let payload = null;
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("data:")) {
        payload = line.slice(5).trimStart();
      }
    }
    if (payload == null) continue;
    try {
      events.push(JSON.parse(payload));
    } catch {
      // malformed payload — drop and continue
    }
  }
  return events;
}

/**
 * Stateful SSE decoder for use with Web Streams. Buffers partial events
 * across chunks; `feed(chunk)` returns the complete events that became
 * available with this chunk.
 *
 * @returns {{feed: (chunk: string) => Array<object>}}
 */
export function makeSseDecoder() {
  let buffer = "";
  return {
    feed(chunk) {
      buffer += chunk;
      const separatorIndex = buffer.lastIndexOf("\n\n");
      if (separatorIndex === -1) return [];
      const complete = buffer.slice(0, separatorIndex + 2);
      buffer = buffer.slice(separatorIndex + 2);
      return parseSseChunk(complete);
    },
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ui && npx vitest run src/lib/sseParser.test.js
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/sseParser.js ui/src/lib/sseParser.test.js
git commit -m "feat(channel-7a): SSE parser library for chat streaming"
```

---

## Task 13: API client wrappers

**Files:**
- Modify: `ui/src/api.js`

Add typed wrappers around the new endpoints.  Stream wrapper returns the `Response` object so the caller can consume `response.body` as a ReadableStream.

- [ ] **Step 1: Read the existing file to find the auth-header pattern**

```bash
head -40 ui/src/api.js
```

Note the existing `getAuthHeader()` (or equivalent — patterns vary) and the API base URL.  Use the same pattern in the new wrappers.

- [ ] **Step 2: Add chat endpoint wrappers**

Append to `ui/src/api.js`:

```js
// ---- Chats ----------------------------------------------------------------

export async function createChat({ title = null, modelDefault = null } = {}) {
  const response = await fetch("/api/chats", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ title, model_default: modelDefault }),
  });
  if (!response.ok) throw new Error(`createChat ${response.status}`);
  return response.json();
}

export async function listChats({ limit = 50, cursor = null } = {}) {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (cursor) qs.set("cursor", cursor);
  const response = await fetch(`/api/chats?${qs}`, {
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`listChats ${response.status}`);
  return response.json();
}

export async function getChat(chatId, { limit = 200, cursor = null } = {}) {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (cursor) qs.set("cursor", cursor);
  const response = await fetch(`/api/chats/${chatId}?${qs}`, {
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`getChat ${response.status}`);
  return response.json();
}

export async function patchChat(chatId, { title, archived } = {}) {
  const response = await fetch(`/api/chats/${chatId}`, {
    method: "PATCH",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ title, archived }),
  });
  if (!response.ok) throw new Error(`patchChat ${response.status}`);
}

export async function streamMessage(
  chatId,
  { message, model, effort, attachments, idempotencyKey, signal } = {},
) {
  const headers = { ...authHeader(), "Content-Type": "application/json" };
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  const response = await fetch(`/api/chats/${chatId}/messages`, {
    method: "POST",
    headers,
    body: JSON.stringify({ message, model, effort, attachments }),
    signal,
  });
  if (!response.ok) throw new Error(`streamMessage ${response.status}`);
  return response;
}
```

If `authHeader()` doesn't already exist in `ui/src/api.js`, infer the existing pattern (likely a small helper that reads `localStorage.getItem("starter_mgmt_token")` and returns `{ Authorization: "Bearer …" }`) and add it.  Don't invent a new name if one already exists.

- [ ] **Step 3: Commit**

```bash
git add ui/src/api.js
git commit -m "feat(channel-7a): API client wrappers for chats endpoints"
```

---

## Task 14: `useChatList` hook

**Files:**
- Create: `ui/src/hooks/useChatList.js`
- Create: `ui/src/hooks/useChatList.test.js`

- [ ] **Step 1: Write the failing tests**

```js
// ui/src/hooks/useChatList.test.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

vi.mock("../api.js", () => ({
  listChats: vi.fn(),
  createChat: vi.fn(),
  patchChat: vi.fn(),
}));

import * as api from "../api.js";
import { useChatList } from "./useChatList.js";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useChatList", () => {
  it("loads chats on mount", async () => {
    api.listChats.mockResolvedValue({
      items: [{ chat_id: "a", title: "A" }],
      next_cursor: null,
    });
    const { result } = renderHook(() => useChatList());

    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.chats).toEqual([{ chat_id: "a", title: "A" }]);
  });

  it("optimistically prepends on createChat", async () => {
    api.listChats.mockResolvedValue({ items: [], next_cursor: null });
    api.createChat.mockResolvedValue({
      chat_id: "new",
      title: "New chat",
      created_at: "t",
      last_message_at: "t",
      model_default: "m",
      message_count: 0,
      archived: false,
    });
    const { result } = renderHook(() => useChatList());
    await waitFor(() => expect(result.current.status).toBe("idle"));

    let created;
    await act(async () => {
      created = await result.current.createChat();
    });

    expect(created.chat_id).toBe("new");
    expect(result.current.chats[0].chat_id).toBe("new");
  });

  it("optimistically updates title via renameChat", async () => {
    api.listChats.mockResolvedValue({
      items: [{ chat_id: "a", title: "Old" }],
      next_cursor: null,
    });
    api.patchChat.mockResolvedValue(undefined);
    const { result } = renderHook(() => useChatList());
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => result.current.renameChat("a", "Renamed"));
    expect(result.current.chats[0].title).toBe("Renamed");
  });

  it("optimistically archives a chat", async () => {
    api.listChats.mockResolvedValue({
      items: [{ chat_id: "a", archived: false }],
      next_cursor: null,
    });
    api.patchChat.mockResolvedValue(undefined);
    const { result } = renderHook(() => useChatList());
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => result.current.archiveChat("a"));
    expect(result.current.chats.find((c) => c.chat_id === "a")).toBeUndefined();
  });

  it("sets error status on initial fetch failure", async () => {
    api.listChats.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useChatList());
    await waitFor(() => expect(result.current.status).toBe("error"));
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ui && npx vitest run src/hooks/useChatList.test.js
```

Expected: import errors.

- [ ] **Step 3: Implement the hook**

```js
// ui/src/hooks/useChatList.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";

/**
 * Drives the sidebar Recents list and exposes optimistic chat-lifecycle
 * actions (create / rename / archive). Failures revert state and set
 * `status === "error"` for the caller to display.
 */
export function useChatList() {
  const [chats, setChats] = useState([]);
  const [status, setStatus] = useState("loading");

  const refresh = useCallback(async () => {
    setStatus("loading");
    try {
      const { items } = await api.listChats();
      setChats(items);
      setStatus("idle");
    } catch {
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const createChat = useCallback(async (opts = {}) => {
    const chat = await api.createChat(opts);
    setChats((prev) => [chat, ...prev]);
    return chat;
  }, []);

  const renameChat = useCallback(async (chatId, title) => {
    setChats((prev) =>
      prev.map((c) => (c.chat_id === chatId ? { ...c, title } : c)),
    );
    await api.patchChat(chatId, { title });
  }, []);

  const archiveChat = useCallback(async (chatId) => {
    setChats((prev) => prev.filter((c) => c.chat_id !== chatId));
    await api.patchChat(chatId, { archived: true });
  }, []);

  return { chats, status, refresh, createChat, renameChat, archiveChat };
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ui && npx vitest run src/hooks/useChatList.test.js
```

Expected: all tests pass.

- [ ] **Step 5: Coverage check**

```bash
cd ui && npx vitest run src/hooks/useChatList.test.js --coverage
```

Expected: 100% on `useChatList.js`. If `refresh` (re-fetch) is uncovered, add `test_refresh_repopulates_chats` that calls `refresh` after the initial load with a different mock return.

- [ ] **Step 6: Commit**

```bash
git add ui/src/hooks/useChatList.js ui/src/hooks/useChatList.test.js
git commit -m "feat(channel-7a): useChatList hook for sidebar Recents"
```

---

## Task 15: `useChatStream` hook (canned-stream client)

**Files:**
- Create: `ui/src/hooks/useChatStream.js`
- Create: `ui/src/hooks/useChatStream.test.js`

Real SSE-driven replacement for `useMockStream`. Same surface: `{ turns, send, abort, status, error }`.

- [ ] **Step 1: Write the failing tests**

```js
// ui/src/hooks/useChatStream.test.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

vi.mock("../api.js", () => ({
  getChat: vi.fn(),
  streamMessage: vi.fn(),
}));

import * as api from "../api.js";
import { useChatStream } from "./useChatStream.js";

function makeMockResponseBody(events) {
  // events: array of {type, ...payload}
  const encoder = new TextEncoder();
  const chunks = events.map(
    (e) => `data: ${JSON.stringify(e)}\n\n`,
  );
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[i]));
      i += 1;
    },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useChatStream", () => {
  it("loads chat history on mount when chatId is set", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [{ msg_id: "m1", role: "user", text: "hello" }],
      next_cursor: null,
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.turns).toEqual([
      { msg_id: "m1", role: "user", text: "hello" },
    ]);
  });

  it("skips history load when chatId is null", () => {
    const { result } = renderHook(() => useChatStream(null));
    expect(result.current.turns).toEqual([]);
    expect(api.getChat).not.toHaveBeenCalled();
  });

  it("streams deltas into the assistant turn", async () => {
    api.getChat.mockResolvedValue({ chat: { chat_id: "c1" }, messages: [], next_cursor: null });
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        { type: "user_persisted", msg_id: "user-1", seq: 0 },
        { type: "delta", text: "Hello" },
        { type: "delta", text: " world" },
        {
          type: "done",
          msg_id: "asst-1",
          seq: 1,
          model: "canned-stream-v1",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "end_turn",
        },
      ]),
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    expect(result.current.turns).toHaveLength(2);
    expect(result.current.turns[0]).toMatchObject({ role: "user", msg_id: "user-1" });
    expect(result.current.turns[1]).toMatchObject({
      role: "assistant",
      text: "Hello world",
      streaming: false,
      msg_id: "asst-1",
    });
  });

  it("sets error status when the server returns non-ok", async () => {
    api.getChat.mockResolvedValue({ chat: { chat_id: "c1" }, messages: [], next_cursor: null });
    api.streamMessage.mockRejectedValue(new Error("500"));

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    expect(result.current.status).toBe("error");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ui && npx vitest run src/hooks/useChatStream.test.js
```

Expected: import errors.

- [ ] **Step 3: Implement the hook**

```js
// ui/src/hooks/useChatStream.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import { makeSseDecoder } from "../lib/sseParser.js";

/**
 * Real SSE-driven replacement for useMockStream. Loads chat history on
 * mount, optimistically renders new turns on send, parses streamed
 * deltas, and finalises turns on the done event.
 *
 * @param {string|null} chatId
 */
export function useChatStream(chatId) {
  const [turns, setTurns] = useState([]);
  const [status, setStatus] = useState(chatId ? "loading-history" : "idle");
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  useEffect(() => {
    if (!chatId) {
      setTurns([]);
      setStatus("idle");
      return;
    }
    let cancelled = false;
    setStatus("loading-history");
    api
      .getChat(chatId)
      .then(({ messages }) => {
        if (cancelled) return;
        setTurns(messages);
        setStatus("idle");
      })
      .catch(() => {
        if (cancelled) return;
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [chatId]);

  const send = useCallback(
    async ({ message, model, effort, attachments }) => {
      if (!chatId) return;
      setError(null);
      const tempUserId = `tmp-u-${Date.now()}`;
      const tempAsstId = `tmp-a-${Date.now()}`;
      setTurns((prev) => [
        ...prev,
        { msg_id: tempUserId, role: "user", text: message, pending: true },
        { msg_id: tempAsstId, role: "assistant", text: "", streaming: true },
      ]);
      setStatus("streaming");

      const controller = new AbortController();
      abortRef.current = controller;

      let response;
      try {
        response = await api.streamMessage(chatId, {
          message,
          model,
          effort,
          attachments,
          idempotencyKey: crypto.randomUUID(),
          signal: controller.signal,
        });
      } catch (err) {
        setError(err);
        setStatus("error");
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const sse = makeSseDecoder();

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const events = sse.feed(decoder.decode(value, { stream: true }));
        for (const event of events) {
          if (event.type === "user_persisted") {
            setTurns((prev) =>
              prev.map((t) =>
                t.msg_id === tempUserId
                  ? { ...t, msg_id: event.msg_id, pending: false }
                  : t,
              ),
            );
          } else if (event.type === "delta") {
            setTurns((prev) =>
              prev.map((t) =>
                t.msg_id === tempAsstId
                  ? { ...t, text: t.text + event.text }
                  : t,
              ),
            );
          } else if (event.type === "done") {
            setTurns((prev) =>
              prev.map((t) =>
                t.msg_id === tempAsstId
                  ? {
                      ...t,
                      msg_id: event.msg_id,
                      streaming: false,
                      model: event.model,
                      input_tokens: event.input_tokens,
                      output_tokens: event.output_tokens,
                    }
                  : t,
              ),
            );
            setStatus("idle");
          }
        }
      }
    },
    [chatId],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { turns, send, abort, status, error };
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ui && npx vitest run src/hooks/useChatStream.test.js
```

Expected: all tests pass.

- [ ] **Step 5: Coverage check + fill gaps**

```bash
cd ui && npx vitest run src/hooks/useChatStream.test.js --coverage
```

Expected gaps to fill if any:
- `abort()` branch — add `test_abort_aborts_in_flight_request`.
- History-fetch error path — covered by `it("sets error status…")` already, but if the *getChat* failure path is uncovered, add `test_history_load_failure_sets_error_status`.
- The `null chatId` cleanup — covered by `test_skips_history_load_when_chatId_is_null`.

- [ ] **Step 6: Commit**

```bash
git add ui/src/hooks/useChatStream.js ui/src/hooks/useChatStream.test.js
git commit -m "feat(channel-7a): useChatStream hook for real SSE chat streaming"
```

---

## Task 16: Wire `useChatList` into `Sidebar` and `Shell`

**Files:**
- Modify: `ui/src/app/Sidebar.jsx`
- Modify: `ui/src/app/Sidebar.test.jsx` (if it exists; otherwise create)
- Modify: `ui/src/app/Shell.jsx`
- Modify: `ui/src/app/Shell.test.jsx`

The sidebar today reads `RECENTS` from `data.js`. Replace that with props passed down from `Shell`, which sources them from `useChatList`.

- [ ] **Step 1: Read the existing Sidebar to find the RECENTS reference**

```bash
grep -n "RECENTS\|recents" ui/src/app/Sidebar.jsx ui/src/app/Shell.jsx
```

- [ ] **Step 2: Update Sidebar to consume a prop**

Edit `ui/src/app/Sidebar.jsx`:
- Remove `import { RECENTS } from "./data.js"` (or whatever the import is).
- Add a `chats` prop to the component signature.
- Replace internal references to `RECENTS` with `chats`.
- Replace `recent.title` with `chat.title`, `recent.id` with `chat.chat_id`, etc. — match field names to the server response.

- [ ] **Step 3: Update Sidebar.test.jsx**

Replace any test that imports `RECENTS` with a fixture that passes a `chats` array prop:

```jsx
const sampleChats = [
  { chat_id: "a", title: "Alpha", last_message_at: "2026-05-30T00:00:00Z" },
  { chat_id: "b", title: "Beta",  last_message_at: "2026-05-29T00:00:00Z" },
];

it("renders chat titles from the chats prop", () => {
  render(<Sidebar chats={sampleChats} onNewChat={() => {}} />);
  expect(screen.getByText("Alpha")).toBeInTheDocument();
  expect(screen.getByText("Beta")).toBeInTheDocument();
});
```

If `Sidebar.test.jsx` doesn't exist, create it with this test plus tests for the "New chat" button click → `onNewChat()` callback.

- [ ] **Step 4: Update Shell to use useChatList**

Edit `ui/src/app/Shell.jsx`:

```jsx
import { useChatList } from "../hooks/useChatList.js";
import { useNavigate } from "react-router-dom";

// inside component:
const { chats, createChat } = useChatList();
const navigate = useNavigate();

const handleNewChat = async () => {
  const chat = await createChat();
  navigate(`/app/c/${chat.chat_id}`);
};

// pass to <Sidebar chats={chats} onNewChat={handleNewChat} ... />
```

- [ ] **Step 5: Update Shell.test.jsx**

Mock `useChatList` to return a stub `{chats, createChat, ...}`. Verify Sidebar receives chats and `onNewChat` triggers navigation.

```jsx
vi.mock("../hooks/useChatList.js", () => ({
  useChatList: () => ({
    chats: [{ chat_id: "a", title: "Alpha" }],
    createChat: vi.fn().mockResolvedValue({ chat_id: "new" }),
    renameChat: vi.fn(),
    archiveChat: vi.fn(),
    status: "idle",
  }),
}));
```

- [ ] **Step 6: Run tests**

```bash
cd ui && npx vitest run src/app/Sidebar.test.jsx src/app/Shell.test.jsx
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add ui/src/app/Sidebar.jsx ui/src/app/Sidebar.test.jsx ui/src/app/Shell.jsx ui/src/app/Shell.test.jsx
git commit -m "feat(channel-7a): wire useChatList into Sidebar via Shell"
```

---

## Task 17: Rewire `ChatHome` → first-message-creates-chat

**Files:**
- Modify: `ui/src/app/ChatHome.jsx`
- Modify: `ui/src/app/ChatHome.test.jsx`

When the empty-state composer is submitted on `/app`, create a chat and navigate to `/app/c/{chat_id}` with the first message in route state so `Conversation` can send it on mount.

- [ ] **Step 1: Read the existing ChatHome composer submit handler**

```bash
grep -n "send\|onSubmit\|composer" ui/src/app/ChatHome.jsx
```

- [ ] **Step 2: Rewire the submit handler**

Edit `ui/src/app/ChatHome.jsx`:

```jsx
import { useChatList } from "../hooks/useChatList.js";
import { useNavigate } from "react-router-dom";

// inside component:
const { createChat } = useChatList();
const navigate = useNavigate();

const handleSend = async (text, atts, model, effort) => {
  const chat = await createChat({ modelDefault: model?.id });
  navigate(`/app/c/${chat.chat_id}`, {
    state: { firstMessage: { message: text, model, effort, attachments: atts } },
  });
};

// pass handleSend into Composer's onSend prop
```

- [ ] **Step 3: Update the test**

`ChatHome.test.jsx`:

```jsx
vi.mock("../hooks/useChatList.js", () => ({
  useChatList: () => ({
    createChat: vi.fn().mockResolvedValue({ chat_id: "new-1" }),
  }),
}));

const navigateMock = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigateMock };
});

it("creates a chat and navigates with firstMessage state", async () => {
  render(<ChatHome />);
  // simulate composer submit — adjust selectors to actual UI
  fireEvent.click(screen.getByText("Send"));
  await waitFor(() =>
    expect(navigateMock).toHaveBeenCalledWith(
      "/app/c/new-1",
      expect.objectContaining({ state: expect.any(Object) }),
    ),
  );
});
```

- [ ] **Step 4: Run tests**

```bash
cd ui && npx vitest run src/app/ChatHome.test.jsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/ChatHome.jsx ui/src/app/ChatHome.test.jsx
git commit -m "feat(channel-7a): ChatHome first-message-creates-chat flow"
```

---

## Task 18: Rewire `Conversation.jsx` to use `useChatStream`

**Files:**
- Modify: `ui/src/app/Conversation.jsx`
- Modify: `ui/src/app/Conversation.test.jsx`

Today `Conversation` invokes `useMockStream` and has a `kickedIdRef` StrictMode guard. We swap to `useChatStream`, read `id` from URL params (the existing route param name in `App.jsx:47` — `/app/c/:id`), and read the `firstMessage` from route state to send on mount once. The `kickedIdRef` guard is no longer needed.

`Conversation` is the **default export** in this file — don't change that.

- [ ] **Step 1: Read the existing `Conversation` mount logic**

```bash
grep -n "useMockStream\|kickedIdRef\|useParams\|useLocation" ui/src/app/Conversation.jsx
```

- [ ] **Step 2: Swap hooks and route plumbing**

Replace the top of `Conversation.jsx`:

```jsx
import { useEffect, useRef } from "react";
import { useLocation, useParams } from "react-router-dom";
import { useChatStream } from "../hooks/useChatStream.js";

export default function Conversation() {
  const { id: chatId } = useParams();        // route is `/app/c/:id`
  const location = useLocation();
  const { turns, send, abort, status } = useChatStream(chatId);
  const sentFirstRef = useRef(false);

  useEffect(() => {
    if (sentFirstRef.current) return;
    const first = location.state?.firstMessage;
    if (chatId && first) {
      sentFirstRef.current = true;
      send(first);
    }
  }, [chatId, location.state, send]);

  // ... existing render using turns, send, abort, status
}
```

Delete the `kickedIdRef` guard and any rAF/mock-stream wiring from this file.

- [ ] **Step 3: Update the test**

`Conversation.test.jsx`:

```jsx
vi.mock("../hooks/useChatStream.js", () => ({
  useChatStream: vi.fn(),
}));

import * as useChatStreamModule from "../hooks/useChatStream.js";

it("renders turns from useChatStream", () => {
  useChatStreamModule.useChatStream.mockReturnValue({
    turns: [{ role: "user", text: "hi", msg_id: "m1" }],
    send: vi.fn(),
    abort: vi.fn(),
    status: "idle",
  });
  render(
    <MemoryRouter initialEntries={["/app/c/c1"]}>
      <Routes>
        <Route path="/app/c/:id" element={<Conversation />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByText("hi")).toBeInTheDocument();
});

it("sends firstMessage from route state on mount", () => {
  const send = vi.fn();
  useChatStreamModule.useChatStream.mockReturnValue({
    turns: [],
    send,
    abort: vi.fn(),
    status: "idle",
  });
  render(
    <MemoryRouter initialEntries={[{ pathname: "/app/c/c1", state: { firstMessage: { message: "hi" } } }]}>
      <Routes>
        <Route path="/app/c/:id" element={<Conversation />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(send).toHaveBeenCalledWith({ message: "hi" });
});
```

- [ ] **Step 4: Run tests**

```bash
cd ui && npx vitest run src/app/Conversation.test.jsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/Conversation.jsx ui/src/app/Conversation.test.jsx
git commit -m "feat(channel-7a): Conversation uses useChatStream + URL chatId"
```

---

## Task 19: Composer pass-through stays unchanged; verify

**Files:**
- (no edits expected)

The composer doesn't need a code change for 7a — the per-send Idempotency-Key is generated inside `useChatStream` via `crypto.randomUUID()` (see Task 15 implementation). Composer continues to pass `text, atts, model, effort` through its existing `onSend` callback.

- [ ] **Step 1: Run the composer test as a sanity check**

```bash
cd ui && npx vitest run src/app/Composer.test.jsx
```

Expected: existing tests pass.  If they reference `useMockStream` directly (unlikely), update them in this task; otherwise skip the commit.

- [ ] **Step 2: Skip-commit only if no changes**

```bash
git status --short ui/src/app/Composer.jsx ui/src/app/Composer.test.jsx
```

If clean, no commit. If there were tweaks, commit them as `chore(channel-7a): composer test wiring`.

---

## Task 20: Drop `useMockStream` and `data.js` mock chat data

**Files:**
- Delete: `ui/src/hooks/useMockStream.js`
- Delete: `ui/src/hooks/useMockStream.test.js` (if it exists)
- Modify: `ui/src/app/data.js`

By this task, nothing should still import `useMockStream`, `SAMPLE_REPLY`, `SAMPLE_USER`, or `RECENTS`.

- [ ] **Step 1: Verify nothing still references the deleted symbols**

```bash
grep -rn "useMockStream\|SAMPLE_REPLY\|SAMPLE_USER\|RECENTS" ui/src/
```

Expected: only references in the files we're about to delete. If anything else references them, fix the caller first (likely an outdated test in Tasks 17 / 18 — go back and update).

- [ ] **Step 2: Delete the mock hook**

```bash
git rm ui/src/hooks/useMockStream.js
git rm -f ui/src/hooks/useMockStream.test.js 2>/dev/null || true
```

- [ ] **Step 3: Remove the mock chat data from `data.js`**

Open `ui/src/app/data.js`. Delete the `SAMPLE_REPLY`, `SAMPLE_USER`, and `RECENTS` exports (and their consumers above, e.g. helper functions used only by `useMockStream`). Keep `MODELS`, `EFFORTS`, `QUICK_ACTIONS`, `PROJECTS`, `ARTIFACTS`, `PROJECT_DOCS` — these are still used by other views.

- [ ] **Step 4: Run the full vitest suite**

```bash
cd ui && npx vitest run
```

Expected: 100% green. If a test fails because it imported one of the deleted symbols, update the test to use a local fixture.

- [ ] **Step 5: Run frontend coverage gate**

```bash
cd ui && npx vitest run --coverage
```

Expected: 100% on all files in `ui/src/`. If `data.js`'s remaining exports show as uncovered (because their callers were also mocks that got deleted), either ensure another view exercises them or add a tiny smoke test.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore(channel-7a): drop useMockStream and mock chat data"
```

---

## Task 21: Full pre-push gate + smoke run

**Files:** none modified.

- [ ] **Step 1: Run the pre-push gate**

```bash
uv run inv pre-push
```

Expected: lint + typecheck + unit + frontend all green.

- [ ] **Step 2: Run integration tests**

```bash
docker ps | grep dynamodb-local || docker run -d -p 8000:8000 amazon/dynamodb-local:latest
uv run inv test-integration
```

Expected: green.

- [ ] **Step 3: Synth infra**

```bash
uv run inv synth
```

Expected: green; the new `ChatByIdIndex` GSI appears in the CloudFormation template under the table resource.

- [ ] **Step 4: Live smoke**

```bash
uv run inv dev --seed
```

In a second terminal, navigate to `http://localhost:5173/app/login?test_email=smoke@example.com`. Verify:
- Sidebar shows no chats initially.
- Submitting from the empty-state composer creates a chat (visible URL change to `/app/c/{id}`) and the canned reply streams in word-by-word.
- Sidebar gains the new chat.
- Reloading the page restores the conversation from the API.
- Opening a second tab to the same URL also restores the conversation.

If any of these fail, fix the bug (TDD: write a failing test first), then re-run the gate.

- [ ] **Step 5: Commit (if any fixes)**

```bash
git status --short
# If any files changed during smoke fixes:
git add -A && git commit -m "fix(channel-7a): smoke-driven fixes from manual run"
```

---

## Task 22: Open the PR

**Files:** none modified directly.

- [ ] **Step 1: Rebase on origin/development**

Follow the procedure in `.claude/agents/issue-worker.md §"Push discipline"`:

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD
```

Expected: clean linear history of the 7a commits.

- [ ] **Step 2: Push using explicit refspec**

Use the canonical first-push form from §6 of `issue-worker.md`. (Quoted here only as a placeholder — the agent doc is the source of truth.)

- [ ] **Step 3: Open the PR**

```bash
gh pr create --base development \
  --title "feat(channel-7a): DDB chats + CRUD API + canned SSE stream" \
  --body "$(cat <<'EOF'
## Summary

Phase 7a of the Bedrock + AgentCore Memory chat backend
(see `docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md`).

- New \`/api/chats/*\` REST + SSE endpoints
- DDB schema for chats + messages on the existing single table
- New \`ChatByIdIndex\` GSI for direct chat-id lookups
- \`useChatList\` + \`useChatStream\` hooks; \`useMockStream\` deleted
- \`POST /messages\` returns a **server-side canned reply** — real Bedrock
  arrives in 7b
- Idempotency-Key support on the streaming endpoint
- 100% test coverage on both backend and frontend
- Lambda timeout / IAM / adapter changes deferred to 7b

Closes #NNN

## Test plan
- [ ] uv run inv pre-push  (all green)
- [ ] uv run inv test-integration  (DDB Local)
- [ ] uv run inv synth  (CloudFormation shows new GSI)
- [ ] Manual: send a message, reload, verify history
- [ ] Manual: open a second tab, verify history
EOF
)"
gh pr merge --auto --squash --delete-branch
```

(Replace `#NNN` with the actual GitHub issue number tracking phase 7a.)

- [ ] **Step 4: Watch CI**

```bash
gh run watch
```

Expected: all checks green. If anything fails, fix it (TDD), push (explicit refspec), and re-watch.

---

## End-state checklist

After this plan is done, this is true:

- [ ] Five endpoints live under `/api/chats/*`: `POST`, `GET (list)`, `GET (by id)`, `PATCH`, `POST /messages` (SSE).
- [ ] DDB single table has the `ChatByIdIndex` GSI.
- [ ] `src/channel/models.py` and `src/channel/storage.py` exist with 100% unit coverage.
- [ ] `tests/integration/test_chat_persistence.py` + `test_chat_ownership.py` cover the GSI + ConditionalCheck branches.
- [ ] `useMockStream` is gone; `useChatStream` + `useChatList` + `sseParser` cover its surface.
- [ ] Sidebar shows real chats from the API; reload restores conversation.
- [ ] Bedrock, Strands, AgentCore Memory — untouched (those land in 7b/7c/7d per the spec).
- [ ] `inv pre-push` green; `inv synth` green; `inv test-integration` green.

## Open items intentionally deferred

- Real Bedrock streaming — **7b**
- Strands integration + Mangum → aws-lambda-web-adapter switch + Lambda timeout 30s → 5min — **7b**
- AgentCore Memory writes via Strands adapter — **7c**
- Memory recall + auto-titling — **7d**
- `/api/models` endpoint — **7b** (lives with the model picker becoming real)
- `/regenerate` endpoint — **7b**
- CloudWatch EMF metrics (`ChatTurnDurationMs`, `BedrockInvokeFailures`,
  `AgentCoreWriteFailures`) — deferred until the things they measure
  exist; `ChatTurnDurationMs` could land in 7a but the canned stream
  makes it deterministic and uninformative
- `tests/integration/test_streaming_disconnect.py` — disconnect
  semantics are mostly moot until 7b's real-Bedrock stream; the canned
  generator already commits the assistant turn before yielding `done`
- `tests/e2e/test_chat_smoke.py` — `tests/e2e/` is in pending-reauthor
  state per CLAUDE.md; this plan substitutes manual smoke (Task 21)
  and adds e2e when the harness comes back online
- Cross-device live sidebar sync — explicitly out of scope
- Hard delete + GDPR purge — out of scope
