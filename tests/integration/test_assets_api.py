# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for the /api asset routes against DynamoDB Local (#325).

The unit suite proves branch logic against mocked storage; these prove
the full request → auth → ownership → storage → DynamoDB Local
round-trip: the 404-not-403 ownership boundary against real chat rows,
the inline content round-trip, real ``AssetOwnerIndex`` pagination
through the opaque API cursors, and the browse-time orphan drop + reap
against real rows. S3 stays mocked (unit-covered), mirroring
:mod:`tests.integration.test_asset_storage`.

The session-scoped table is shared with the rest of the integration
suite (no per-test cleanup), so every test scopes its data with
per-test unique owners / chats and never asserts global totals.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from channel import storage
from channel.api.main import app
from channel.auth.tokens import issue_mgmt_jwt
from channel.models import Asset

client = TestClient(app)


def _headers(user_id: str) -> dict[str, str]:
    token = issue_mgmt_jwt({"user_id": user_id, "email": user_id, "role": "user"})
    return {"Authorization": f"Bearer {token}"}


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


def _ts(i: int) -> str:
    return f"2026-07-13T00:00:{i:02d}.000000+00:00"


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


@pytest.mark.usefixtures("starter_table")
def test_ownership_boundary_404_not_403_end_to_end() -> None:
    """A foreign user probing another user's chat sees responses
    indistinguishable from a nonexistent chat: 404 "Chat not found" on
    every chat-scoped asset route — never 403, never a hint the chat
    exists."""
    tag = uuid.uuid4().hex[:8]
    owner, intruder = f"own-{tag}@t.io", f"bad-{tag}@t.io"
    chat = storage.create_chat(user_id=owner, title=None, model_default="m")
    asset = _asset("a-1", chat_id=chat.chat_id, owner=owner, created_at=_ts(1))
    storage.put_asset(asset)

    # Owner sees the asset through every route.
    listed = client.get(f"/api/chats/{chat.chat_id}/assets", headers=_headers(owner))
    assert listed.status_code == 200
    assert [i["asset_id"] for i in listed.json()["items"]] == ["a-1"]

    # Intruder gets 404 (not 403) everywhere, same body as missing-chat.
    for method, path in [
        ("GET", f"/api/chats/{chat.chat_id}/assets"),
        ("GET", f"/api/chats/{chat.chat_id}/assets/a-1"),
        ("GET", f"/api/chats/{chat.chat_id}/assets/a-1/content"),
        ("DELETE", f"/api/chats/{chat.chat_id}/assets/a-1"),
    ]:
        resp = client.request(method, path, headers=_headers(intruder))
        assert resp.status_code == 404, (method, path)
        assert resp.json()["detail"] == "Chat not found"

    # And the asset is untouched by the intruder's DELETE attempt.
    assert storage.get_asset(chat_id=chat.chat_id, asset_id="a-1") is not None


@pytest.mark.usefixtures("starter_table")
def test_inline_content_round_trip_and_delete() -> None:
    tag = uuid.uuid4().hex[:8]
    owner = f"own-{tag}@t.io"
    chat = storage.create_chat(user_id=owner, title=None, model_default="m")
    storage.put_asset(_asset("a-1", chat_id=chat.chat_id, owner=owner, created_at=_ts(1)))

    resp = client.get(f"/api/chats/{chat.chat_id}/assets/a-1/content", headers=_headers(owner))
    assert resp.status_code == 200
    assert resp.content == b"content of a-1"
    assert resp.headers["content-type"] == "text/plain; charset=utf-8"

    deleted = client.delete(f"/api/chats/{chat.chat_id}/assets/a-1", headers=_headers(owner))
    assert deleted.status_code == 204
    assert storage.get_asset(chat_id=chat.chat_id, asset_id="a-1") is None
    # Idempotent second delete.
    again = client.delete(f"/api/chats/{chat.chat_id}/assets/a-1", headers=_headers(owner))
    assert again.status_code == 204


@pytest.mark.usefixtures("starter_table")
def test_browse_paginates_newest_first_through_opaque_cursors() -> None:
    """Real GSI pagination through the API layer: 5 assets across two
    live chats, page size 2 — every asset exactly once, newest first,
    cursors round-tripping as opaque tokens."""
    tag = uuid.uuid4().hex[:8]
    owner = f"own-{tag}@t.io"
    chat_a = storage.create_chat(user_id=owner, title=None, model_default="m")
    chat_b = storage.create_chat(user_id=owner, title=None, model_default="m")
    for i, chat_id in enumerate([chat_a.chat_id, chat_b.chat_id] * 2 + [chat_a.chat_id], start=1):
        storage.put_asset(_asset(f"a-{i}", chat_id=chat_id, owner=owner, created_at=_ts(i)))

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/assets", headers=_headers(owner), params=params)
        assert resp.status_code == 200
        body = resp.json()
        seen.extend(i["asset_id"] for i in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert seen == ["a-5", "a-4", "a-3", "a-2", "a-1"]  # newest first, no dupes


@pytest.mark.usefixtures("starter_table")
def test_browse_drops_and_reaps_orphans_against_real_rows(fake_s3: _FakeS3) -> None:
    """Assets whose chat row is gone vanish from the browse page AND
    their rows are actually reaped from the table (S3 side recorded by
    the stub)."""
    tag = uuid.uuid4().hex[:8]
    owner = f"own-{tag}@t.io"
    chat = storage.create_chat(user_id=owner, title=None, model_default="m")
    dead_chat_id = f"dead-{tag}"  # no chat row was ever created for this id
    storage.put_asset(_asset("live-1", chat_id=chat.chat_id, owner=owner, created_at=_ts(1)))
    storage.put_asset(
        _asset("orph-1", chat_id=dead_chat_id, owner=owner, created_at=_ts(2), inline=False)
    )

    resp = client.get("/api/assets", headers=_headers(owner))

    assert resp.status_code == 200
    assert [i["asset_id"] for i in resp.json()["items"]] == ["live-1"]
    # The orphan's row is gone from the table and its S3 object was deleted.
    assert storage.get_asset(chat_id=dead_chat_id, asset_id="orph-1") is None
    assert fake_s3.deleted == [("channel-attachments-test", f"assets/chat/{dead_chat_id}/orph-1")]
    # The live asset survived the reap.
    assert storage.get_asset(chat_id=chat.chat_id, asset_id="live-1") is not None
