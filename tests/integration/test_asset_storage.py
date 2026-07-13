# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for the #324 asset helpers against DynamoDB Local.

The in-memory ``FakeTable`` unit suite covers shape and branch logic;
these prove the wire-protocol contracts — the ``AssetOwnerIndex`` GSI
actually exists in the shared schema and serves newest-first queries,
``LastEvaluatedKey`` round-trips through the browse pagination
(GSI cursors carry both the index keys and the primary keys), the
ProjectionExpression really strips ``content``, and the chat-delete
cascade + lazy-expiry reap work against real rows. S3 stays mocked —
that side is covered by the unit tests, mirroring
:mod:`tests.integration.test_attachments_cascade`.

The session-scoped table is shared with the rest of the integration
suite (no per-test row cleanup), so every test scopes its assertions
with a per-test unique owner / chat id.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from channel import storage
from channel.models import Asset


class _FakeS3:
    """Per-test S3 stub: delete_object is a no-op recorder."""

    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.deleted.append((Bucket, Key))
        return {}


@pytest.fixture
def fake_s3(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    fake = _FakeS3()
    monkeypatch.setattr("channel.storage._get_s3_client", lambda: fake)
    return fake


def _asset(
    asset_id: str,
    *,
    chat_id: str,
    owner: str,
    created_at: str,
    inline: bool = True,
) -> Asset:
    payload: dict[str, Any] = (
        {"content": f"content of {asset_id}"}
        if inline
        else {
            "s3_bucket": "channel-attachments-test",
            "s3_key": f"assets/chat/{chat_id}/{asset_id}",
        }
    )
    return Asset(
        asset_id=asset_id,
        chat_id=chat_id,
        owner=owner,
        kind="code" if inline else "image",
        title=f"{asset_id}.txt",
        mime="text/plain" if inline else "image/png",
        size_bytes=64,
        origin="generated" if inline else "tool_output",
        source={"msg_id": f"m-{asset_id}"},
        created_at=created_at,
        updated_at=created_at,
        **payload,
    )


def _ts(i: int) -> str:
    return f"2026-07-13T00:00:{i:02d}.000000+00:00"


@pytest.mark.usefixtures("starter_table")
def test_put_get_list_round_trip() -> None:
    tag = f"assetrt-{uuid.uuid4().hex[:8]}"
    chat_id = f"c-{tag}"
    a1 = _asset("a-1", chat_id=chat_id, owner=f"u-{tag}", created_at=_ts(1))
    a2 = _asset("a-2", chat_id=chat_id, owner=f"u-{tag}", created_at=_ts(2), inline=False)
    storage.put_asset(a1)
    storage.put_asset(a2)

    found = storage.get_asset(chat_id=chat_id, asset_id="a-2")
    assert found == a2
    assert storage.get_asset(chat_id=chat_id, asset_id="a-nope") is None

    listed = storage.list_chat_assets(chat_id)
    assert listed == [a1, a2]  # oldest first
    # Full-fidelity hydration includes the inline payload.
    assert listed[0].content == "content of a-1"


@pytest.mark.usefixtures("starter_table")
def test_owner_gsi_serves_newest_first_with_cursor_round_trip() -> None:
    """Real GSI pagination: 5 assets across 3 chats, page size 2 —
    every asset surfaces exactly once, newest first, across cursor
    hops whose keys DynamoDB must accept as ``ExclusiveStartKey``."""

    tag = f"assetgsi-{uuid.uuid4().hex[:8]}"
    owner = f"u-{tag}"
    expected: list[str] = []
    for i in range(5):
        asset_id = f"a-{i}"
        storage.put_asset(
            _asset(
                asset_id,
                chat_id=f"c-{tag}-{i % 3}",
                owner=owner,
                created_at=_ts(i),
                inline=(i % 2 == 0),
            )
        )
        expected.append(asset_id)
    # Someone else's asset must never surface on this owner's pages.
    storage.put_asset(
        _asset("a-other", chat_id=f"c-{tag}-x", owner=f"u-other-{tag}", created_at=_ts(9))
    )

    seen: list[str] = []
    cursor: dict[str, Any] | None = None
    for _ in range(10):
        rows, cursor = storage.list_assets_by_owner(owner, limit=2, cursor=cursor)
        seen.extend(str(r["asset_id"]) for r in rows)
        for row in rows:
            # The browse projection keeps inline payloads off the wire.
            assert "content" not in row
            assert "asset_id" in row and "source" in row
        if cursor is None:
            break
    else:
        pytest.fail("list_assets_by_owner never exhausted the owner's assets")

    assert seen == list(reversed(expected))  # newest first, no dupes, no strays


@pytest.mark.usefixtures("starter_table")
def test_delete_chat_assets_cascades_rows_and_s3(fake_s3: _FakeS3) -> None:
    tag = f"assetcas-{uuid.uuid4().hex[:8]}"
    owner = f"u-{tag}"
    chat_id = f"c-{tag}"
    inline = _asset("a-inline", chat_id=chat_id, owner=owner, created_at=_ts(1))
    binary = _asset("a-bin", chat_id=chat_id, owner=owner, created_at=_ts(2), inline=False)
    surviving = _asset("a-keep", chat_id=f"c-{tag}-other", owner=owner, created_at=_ts(3))
    for asset in (inline, binary, surviving):
        storage.put_asset(asset)

    deleted, failed = storage.delete_chat_assets(chat_id=chat_id)

    assert (deleted, failed) == (2, 0)
    assert storage.list_chat_assets(chat_id) == []
    # Other chats' assets untouched; only the S3-backed asset hit S3.
    assert storage.list_chat_assets(f"c-{tag}-other") == [surviving]
    assert fake_s3.deleted == [("channel-attachments-test", f"assets/chat/{chat_id}/a-bin")]
    # The owner GSI no longer serves the wiped rows.
    rows, _ = storage.list_assets_by_owner(owner)
    assert [r["asset_id"] for r in rows] == ["a-keep"]


@pytest.mark.usefixtures("starter_table")
def test_reap_orphaned_assets_respects_chat_liveness(fake_s3: _FakeS3) -> None:
    """End-to-end lazy-expiry shape: a live chat's assets survive the
    reap; a deleted chat's assets (the cascade-failure orphan case) are
    reaped rows + S3, driven by real ``ChatByIdIndex`` liveness."""

    tag = f"assetreap-{uuid.uuid4().hex[:8]}"
    owner = f"u-{tag}"
    live_chat = storage.create_chat(user_id=owner, title=None, model_default="m")
    doomed_chat = storage.create_chat(user_id=owner, title=None, model_default="m")
    live = _asset("a-live", chat_id=live_chat.chat_id, owner=owner, created_at=_ts(1))
    orphan = _asset(
        "a-orphan", chat_id=doomed_chat.chat_id, owner=owner, created_at=_ts(2), inline=False
    )
    storage.put_asset(live)
    storage.put_asset(orphan)

    # Simulate the failed-cascade orphan: the chat row goes away, the
    # asset rows stay behind.
    storage.delete_chat(user_id=owner, chat=doomed_chat)
    assert storage.get_chat_by_id(doomed_chat.chat_id) is None

    rows, _ = storage.list_assets_by_owner(owner)
    reaped, failed = storage.reap_orphaned_assets(rows)

    assert (reaped, failed) == (1, 0)
    assert storage.list_chat_assets(doomed_chat.chat_id) == []
    assert storage.list_chat_assets(live_chat.chat_id) == [live]
    assert fake_s3.deleted == [
        ("channel-attachments-test", f"assets/chat/{doomed_chat.chat_id}/a-orphan")
    ]
