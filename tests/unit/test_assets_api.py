# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api asset routes (#325, epic #321).

Auth-gating tests drive the REAL ``require_mgmt_user`` dependency with
real mgmt JWTs (repo rule: don't mock auth; mock the AWS boundary).
Ownership tests drive the REAL ``_load_owned_chat`` logic by patching
``storage.get_chat_by_id`` — so the 404-not-403 boundary proven here is
the one production enforces. Asset storage helpers are patched at the
``channel.storage`` module seam, following the test_admin_api.py
pattern.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("STARTER_JWT_SECRET", "test-secret-for-unit-tests")

from channel import storage  # noqa: E402
from channel.api import assets as assets_api  # noqa: E402
from channel.api.main import app  # noqa: E402
from channel.auth.tokens import issue_mgmt_jwt  # noqa: E402
from channel.models import Asset, Chat  # noqa: E402

client = TestClient(app)

OWNER = "owner@test.com"
INTRUDER = "intruder@test.com"
CHAT_ID = "chat-1111"


def _headers(user_id: str = OWNER) -> dict[str, str]:
    token = issue_mgmt_jwt({"user_id": user_id, "email": user_id, "role": "user"})
    return {"Authorization": f"Bearer {token}"}


def _chat(user_id: str = OWNER, chat_id: str = CHAT_ID) -> Chat:
    return Chat(
        chat_id=chat_id,
        user_id=user_id,
        title="t",
        created_at="2026-07-13T00:00:00.000000+00:00",
        last_message_at="2026-07-13T00:00:01.000000+00:00",
        model_default="m",
        message_count=1,
        archived=False,
    )


def _asset(
    asset_id: str = "a-1",
    *,
    chat_id: str = CHAT_ID,
    inline: bool = True,
    mime: str | None = None,
    source: dict[str, Any] | None = None,
) -> Asset:
    payload: dict[str, Any] = (
        {"content": f"print('{asset_id}')"}
        if inline
        else {"s3_bucket": "bkt", "s3_key": f"assets/chat/{chat_id}/{asset_id}"}
    )
    return Asset(
        asset_id=asset_id,
        chat_id=chat_id,
        owner=OWNER,
        kind="code" if inline else "image",
        title=f"{asset_id}.py" if inline else f"{asset_id}.png",
        mime=mime or ("text/x-python" if inline else "image/png"),
        size_bytes=64,
        origin="generated" if inline else "tool_output",
        source=source or {"msg_id": f"m-{asset_id}"},
        created_at="2026-07-13T00:00:01.000000+00:00",
        updated_at="2026-07-13T00:00:01.000000+00:00",
        **payload,
    )


def _row(
    asset_id: str,
    *,
    chat_id: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A content-projected AssetOwnerIndex browse row, DDB-typed
    (numbers arrive as Decimal)."""
    return {
        "PK": f"CHAT#{chat_id}",
        "SK": f"ASSET#2026-07-13T00:00:01.000000+00:00#{asset_id}",
        "owner_pk": f"ASSETOWNER#{OWNER}",
        "owner_sk": f"2026-07-13T00:00:01.000000+00:00#{asset_id}",
        "asset_id": asset_id,
        "chat_id": chat_id,
        "owner": OWNER,
        "kind": "code",
        "title": f"{asset_id}.py",
        "mime": "text/x-python",
        "size_bytes": Decimal("64"),
        "origin": "generated",
        "source": source or {"msg_id": f"m-{asset_id}"},
        "created_at": "2026-07-13T00:00:01.000000+00:00",
        "updated_at": "2026-07-13T00:00:01.000000+00:00",
    }


def _lek(asset_id: str) -> dict[str, str]:
    return {
        "PK": f"CHAT#{CHAT_ID}",
        "SK": f"ASSET#2026-07-13T00:00:01.000000+00:00#{asset_id}",
        "owner_pk": f"ASSETOWNER#{OWNER}",
        "owner_sk": f"2026-07-13T00:00:01.000000+00:00#{asset_id}",
    }


def _own_chat(monkeypatch: pytest.MonkeyPatch, chat: Chat | None = None) -> None:
    monkeypatch.setattr(storage, "get_chat_by_id", lambda _cid: chat)


@pytest.fixture
def metrics_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    calls: list[tuple[int, int]] = []

    async def _record(reaped: int, failed: int) -> None:
        # Yield once so the stub is genuinely async (matches the real
        # awaitable's shape and keeps S7503 quiet).
        await asyncio.sleep(0)
        calls.append((reaped, failed))

    monkeypatch.setattr(assets_api, "record_asset_lazy_expiry_reaps", _record)
    return calls


# ----------------------------------------------------------------
# Auth boundary
# ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/api/chats/{CHAT_ID}/assets"),
        ("GET", f"/api/chats/{CHAT_ID}/assets/a-1"),
        ("GET", f"/api/chats/{CHAT_ID}/assets/a-1/content"),
        ("DELETE", f"/api/chats/{CHAT_ID}/assets/a-1"),
        ("GET", "/api/assets"),
    ],
)
def test_asset_routes_reject_missing_auth(method: str, path: str) -> None:
    # HTTPBearer returns 403 (not 401) when no Authorization header is
    # supplied; either is "rejected for missing auth" so accept both.
    resp = client.request(method, path)
    assert resp.status_code in (401, 403)


# ----------------------------------------------------------------
# Ownership — 404 (never 403) on mismatch, real _load_owned_chat
# ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/api/chats/{CHAT_ID}/assets"),
        ("GET", f"/api/chats/{CHAT_ID}/assets/a-1"),
        ("GET", f"/api/chats/{CHAT_ID}/assets/a-1/content"),
        ("DELETE", f"/api/chats/{CHAT_ID}/assets/a-1"),
    ],
)
def test_chat_scoped_routes_404_for_non_owner(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    """The chat exists and belongs to OWNER; INTRUDER must see a 404
    indistinguishable from a missing chat — never a 403."""
    _own_chat(monkeypatch, _chat(user_id=OWNER))
    resp = client.request(method, path, headers=_headers(INTRUDER))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Chat not found"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/api/chats/{CHAT_ID}/assets"),
        ("GET", f"/api/chats/{CHAT_ID}/assets/a-1"),
        ("GET", f"/api/chats/{CHAT_ID}/assets/a-1/content"),
        ("DELETE", f"/api/chats/{CHAT_ID}/assets/a-1"),
    ],
)
def test_chat_scoped_routes_404_for_missing_chat(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    _own_chat(monkeypatch, None)
    resp = client.request(method, path, headers=_headers(OWNER))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Chat not found"


# ----------------------------------------------------------------
# GET /api/chats/{chat_id}/assets — list, oldest first
# ----------------------------------------------------------------


def test_list_chat_assets_returns_card_descriptors(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    a1 = _asset("a-1")
    a2 = _asset("a-2", inline=False)
    monkeypatch.setattr(storage, "list_chat_assets", lambda _cid: [a1, a2])

    resp = client.get(f"/api/chats/{CHAT_ID}/assets", headers=_headers())

    assert resp.status_code == 200
    body = resp.json()
    # Exact card contract (#321 decision Q4) — the #327/#328 consumers
    # depend on this precise field set. No payload, no S3 coordinates.
    assert body == {
        "items": [
            {
                "asset_id": "a-1",
                "chat_id": CHAT_ID,
                "msg_id": "m-a-1",
                "kind": "code",
                "title": "a-1.py",
                "mime": "text/x-python",
                "size_bytes": 64,
                "origin": "generated",
                "created_at": "2026-07-13T00:00:01.000000+00:00",
                "source": {"msg_id": "m-a-1"},
            },
            {
                "asset_id": "a-2",
                "chat_id": CHAT_ID,
                "msg_id": "m-a-2",
                "kind": "image",
                "title": "a-2.png",
                "mime": "image/png",
                "size_bytes": 64,
                "origin": "tool_output",
                "created_at": "2026-07-13T00:00:01.000000+00:00",
                "source": {"msg_id": "m-a-2"},
            },
        ]
    }


def test_list_chat_assets_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    monkeypatch.setattr(storage, "list_chat_assets", lambda _cid: [])
    resp = client.get(f"/api/chats/{CHAT_ID}/assets", headers=_headers())
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_list_normalises_decimal_source_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """DDB Decimals in ``source`` surface as JSON numbers — integral
    values as ints (``3``, not ``3.0``), non-integral as floats."""
    _own_chat(monkeypatch, _chat())
    asset = _asset(
        "a-1", source={"msg_id": "m-1", "fence_index": Decimal("3"), "ratio": Decimal("0.5")}
    )
    monkeypatch.setattr(storage, "list_chat_assets", lambda _cid: [asset])

    resp = client.get(f"/api/chats/{CHAT_ID}/assets", headers=_headers())

    item = resp.json()["items"][0]
    assert item["source"] == {"msg_id": "m-1", "fence_index": 3, "ratio": 0.5}


# ----------------------------------------------------------------
# GET /api/chats/{chat_id}/assets/{asset_id} — single metadata
# ----------------------------------------------------------------


def test_get_asset_returns_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    captured: dict[str, Any] = {}

    def _get(*, chat_id: str, asset_id: str) -> Asset:
        captured.update(chat_id=chat_id, asset_id=asset_id)
        return _asset("a-9")

    monkeypatch.setattr(storage, "get_asset", _get)

    resp = client.get(f"/api/chats/{CHAT_ID}/assets/a-9", headers=_headers())

    assert resp.status_code == 200
    assert captured == {"chat_id": CHAT_ID, "asset_id": "a-9"}
    body = resp.json()
    assert body["asset_id"] == "a-9"
    assert "content" not in body and "s3_key" not in body and "s3_bucket" not in body


def test_get_asset_404_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    monkeypatch.setattr(storage, "get_asset", lambda **_kw: None)
    resp = client.get(f"/api/chats/{CHAT_ID}/assets/a-nope", headers=_headers())
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Asset not found"


# ----------------------------------------------------------------
# GET .../content — inline + S3 paths, failure vocabulary mapping
# ----------------------------------------------------------------


def test_content_inline_served_with_charset(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    asset = _asset("a-1", mime="text/x-python")
    monkeypatch.setattr(storage, "get_asset", lambda **_kw: asset)

    resp = client.get(f"/api/chats/{CHAT_ID}/assets/a-1/content", headers=_headers())

    assert resp.status_code == 200
    assert resp.content == b"print('a-1')"
    assert resp.headers["content-type"] == "text/x-python; charset=utf-8"
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_content_text_mime_with_existing_charset_not_doubled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _own_chat(monkeypatch, _chat())
    asset = _asset("a-1", mime="text/plain; charset=utf-8")
    monkeypatch.setattr(storage, "get_asset", lambda **_kw: asset)

    resp = client.get(f"/api/chats/{CHAT_ID}/assets/a-1/content", headers=_headers())

    assert resp.headers["content-type"] == "text/plain; charset=utf-8"


def test_content_s3_backed_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    asset = _asset("a-2", inline=False)
    monkeypatch.setattr(storage, "get_asset", lambda **_kw: asset)
    captured: list[Asset] = []

    def _get_bytes(a: Asset) -> tuple[bytes, None]:
        captured.append(a)
        return b"\x89PNG-bytes", None

    monkeypatch.setattr(storage, "get_asset_bytes", _get_bytes)

    resp = client.get(f"/api/chats/{CHAT_ID}/assets/a-2/content", headers=_headers())

    assert resp.status_code == 200
    assert resp.content == b"\x89PNG-bytes"
    assert resp.headers["content-type"] == "image/png"
    assert captured == [asset]


def test_content_404_when_asset_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    monkeypatch.setattr(storage, "get_asset", lambda **_kw: None)
    resp = client.get(f"/api/chats/{CHAT_ID}/assets/a-nope/content", headers=_headers())
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Asset not found"


def test_content_404_when_s3_object_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dedicated "S3 object not found" reason maps to 404 — the row
    outlived its payload, so the asset is effectively gone."""
    _own_chat(monkeypatch, _chat())
    monkeypatch.setattr(storage, "get_asset", lambda **_kw: _asset("a-2", inline=False))
    monkeypatch.setattr(storage, "get_asset_bytes", lambda _a: (None, "S3 object not found"))

    resp = client.get(f"/api/chats/{CHAT_ID}/assets/a-2/content", headers=_headers())

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Asset content not found"


def test_content_502_on_other_s3_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    monkeypatch.setattr(storage, "get_asset", lambda **_kw: _asset("a-2", inline=False))
    monkeypatch.setattr(storage, "get_asset_bytes", lambda _a: (None, "S3 error: AccessDenied"))

    resp = client.get(f"/api/chats/{CHAT_ID}/assets/a-2/content", headers=_headers())

    assert resp.status_code == 502
    assert resp.json()["detail"] == "Asset content unavailable"


# ----------------------------------------------------------------
# DELETE — idempotent
# ----------------------------------------------------------------


def test_delete_asset_deletes_and_204(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    asset = _asset("a-1")
    monkeypatch.setattr(storage, "get_asset", lambda **_kw: asset)
    deleted: list[Asset] = []
    monkeypatch.setattr(storage, "delete_asset", deleted.append)

    resp = client.delete(f"/api/chats/{CHAT_ID}/assets/a-1", headers=_headers())

    assert resp.status_code == 204
    assert deleted == [asset]


def test_delete_missing_asset_is_idempotent_204(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    monkeypatch.setattr(storage, "get_asset", lambda **_kw: None)
    deleted: list[Asset] = []
    monkeypatch.setattr(storage, "delete_asset", deleted.append)

    resp = client.delete(f"/api/chats/{CHAT_ID}/assets/a-gone", headers=_headers())

    assert resp.status_code == 204
    assert deleted == []


# ----------------------------------------------------------------
# GET /api/assets — browse: owner scoping, cursors, orphan reap
# ----------------------------------------------------------------


def test_browse_lists_owner_assets_with_opaque_cursor(
    monkeypatch: pytest.MonkeyPatch, metrics_calls: list[tuple[int, int]]
) -> None:
    _own_chat(monkeypatch, _chat())  # every chat alive
    captured: dict[str, Any] = {}
    lek = _lek("a-2")

    def _list(owner: str, *, limit: int, cursor: dict[str, Any] | None) -> Any:
        captured.update(owner=owner, limit=limit, cursor=cursor)
        return [_row("a-1", chat_id=CHAT_ID), _row("a-2", chat_id="chat-2222")], lek

    monkeypatch.setattr(storage, "list_assets_by_owner", _list)

    resp = client.get("/api/assets", headers=_headers(), params={"limit": 2})

    assert resp.status_code == 200
    # Owner comes from the JWT sub claim only — never a query param.
    assert captured == {"owner": OWNER, "limit": 2, "cursor": None}
    body = resp.json()
    assert [i["asset_id"] for i in body["items"]] == ["a-1", "a-2"]
    assert body["items"][0]["size_bytes"] == 64  # Decimal → int
    assert body["items"][0]["msg_id"] == "m-a-1"
    # The cursor is an opaque base64url token, not a raw DDB key dict —
    # and it round-trips to exactly the LastEvaluatedKey.
    token = body["next_cursor"]
    assert isinstance(token, str)
    assert json.loads(base64.urlsafe_b64decode(token.encode())) == lek
    assert metrics_calls == []  # no orphans → no reap, no metric


def test_browse_last_page_has_null_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    monkeypatch.setattr(
        storage, "list_assets_by_owner", lambda *_a, **_kw: ([_row("a-1", chat_id=CHAT_ID)], None)
    )
    resp = client.get("/api/assets", headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["next_cursor"] is None


def test_browse_forwards_decoded_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    _own_chat(monkeypatch, _chat())
    lek = _lek("a-5")
    captured: dict[str, Any] = {}

    def _list(owner: str, *, limit: int, cursor: dict[str, Any] | None) -> Any:
        captured["cursor"] = cursor
        return [], None

    monkeypatch.setattr(storage, "list_assets_by_owner", _list)
    token = base64.urlsafe_b64encode(json.dumps(lek).encode()).decode()

    resp = client.get("/api/assets", headers=_headers(), params={"cursor": token})

    assert resp.status_code == 200
    assert captured["cursor"] == lek


@pytest.mark.parametrize(
    "token",
    [
        "not-base64!!",  # binascii garbage
        base64.urlsafe_b64encode(b"\xff\xfe").decode(),  # not UTF-8 JSON
        base64.urlsafe_b64encode(b'"a string"').decode(),  # JSON but not a dict
        base64.urlsafe_b64encode(b'{"PK": "x"}').decode(),  # wrong key set
        base64.urlsafe_b64encode(
            json.dumps(
                {"PK": "x", "SK": "y", "owner_pk": f"ASSETOWNER#{OWNER}", "owner_sk": 7}
            ).encode()
        ).decode(),  # non-string value
        base64.urlsafe_b64encode(
            json.dumps(
                {"PK": "x", "SK": "y", "owner_pk": "ASSETOWNER#someone-else", "owner_sk": "z"}
            ).encode()
        ).decode(),  # foreign owner partition
    ],
)
def test_browse_rejects_malformed_or_foreign_cursor(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    monkeypatch.setattr(
        storage,
        "list_assets_by_owner",
        lambda *_a, **_kw: pytest.fail("storage must not be reached with a bad cursor"),
    )
    resp = client.get("/api/assets", headers=_headers(), params={"cursor": token})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid cursor"


@pytest.mark.parametrize("limit", [0, 101])
def test_browse_limit_bounds_are_422(limit: int) -> None:
    resp = client.get("/api/assets", headers=_headers(), params={"limit": limit})
    assert resp.status_code == 422


def test_browse_drops_orphans_and_reaps(
    monkeypatch: pytest.MonkeyPatch, metrics_calls: list[tuple[int, int]]
) -> None:
    """Rows whose chat is gone are silently dropped from the page and
    handed to the lazy-expiry reap; the metric records the outcome."""
    live_chat = _chat(chat_id="chat-live")
    monkeypatch.setattr(
        storage, "get_chat_by_id", lambda cid: live_chat if cid == "chat-live" else None
    )
    rows = [
        _row("a-1", chat_id="chat-live"),
        _row("a-2", chat_id="chat-dead"),
        _row("a-3", chat_id="chat-dead"),  # same dead chat — liveness cached
    ]
    monkeypatch.setattr(storage, "list_assets_by_owner", lambda *_a, **_kw: (rows, None))
    reap_calls: list[list[dict[str, Any]]] = []

    def _reap(orphans: list[dict[str, Any]]) -> tuple[int, int]:
        reap_calls.append(orphans)
        return len(orphans), 0

    monkeypatch.setattr(storage, "reap_orphaned_assets", _reap)

    resp = client.get("/api/assets", headers=_headers())

    assert resp.status_code == 200
    assert [i["asset_id"] for i in resp.json()["items"]] == ["a-1"]
    assert reap_calls == [[rows[1], rows[2]]]
    assert metrics_calls == [(2, 0)]


def test_browse_reap_failure_never_fails_the_browse(
    monkeypatch: pytest.MonkeyPatch, metrics_calls: list[tuple[int, int]]
) -> None:
    monkeypatch.setattr(storage, "get_chat_by_id", lambda _cid: None)
    rows = [_row("a-1", chat_id="chat-dead")]
    monkeypatch.setattr(storage, "list_assets_by_owner", lambda *_a, **_kw: (rows, None))

    def _boom(_orphans: list[dict[str, Any]]) -> tuple[int, int]:
        raise RuntimeError("ddb down")

    monkeypatch.setattr(storage, "reap_orphaned_assets", _boom)

    resp = client.get("/api/assets", headers=_headers())

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "next_cursor": None}
    assert metrics_calls == [(0, 1)]  # every orphan counted as a failed reap
