# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration tests for the /api/admin endpoints against DynamoDB Local (#235).

The unit suite proves branch logic against mocked storage; these prove
the full request → auth → storage → DynamoDB Local round-trip,
including the sparse-USER#META fallback to chat-index derivation and
the mixed META + derived union.

The session-scoped table is shared with the rest of the integration
suite (no per-test cleanup), so assertions filter to rows tagged with
a per-test unique id and never assert global totals or global order.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from channel import storage
from channel.api.main import app
from channel.auth.tokens import issue_mgmt_jwt

client = TestClient(app)


def _admin_headers() -> dict[str, str]:
    token = issue_mgmt_jwt(
        {"user_id": "admin@test.com", "email": "admin@test.com", "role": "admin"}
    )
    return {"Authorization": f"Bearer {token}"}


def _walk_all_users(params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Drain the paginated list — the shared table holds other tests' rows."""
    users: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(200):
        query: dict[str, Any] = {"limit": 100, **(params or {})}
        if cursor:
            query["cursor"] = cursor
        resp = client.get("/api/admin/users", headers=_admin_headers(), params=query)
        assert resp.status_code == 200
        body = resp.json()
        users.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return users
    pytest.fail("admin user list never exhausted")


@pytest.mark.usefixtures("starter_table")
def test_admin_endpoints_enforce_admin_role() -> None:
    """The server-side boundary holds against a real (non-admin) mgmt JWT."""
    user_token = issue_mgmt_jwt(
        {"user_id": "user@test.com", "email": "user@test.com", "role": "user"}
    )
    for path in ("/api/admin/users", "/api/admin/users/anyone@x.com"):
        resp = client.get(path, headers={"Authorization": f"Bearer {user_token}"})
        assert resp.status_code == 403
        resp = client.get(path)
        assert resp.status_code in (401, 403)


@pytest.mark.usefixtures("starter_table")
def test_list_derives_users_from_chat_index_when_meta_sparse() -> None:
    """Users with chats but no USER#META row surface via the fallback."""
    tag = f"adminapi-{uuid.uuid4().hex[:8]}"
    ua, ub = f"{tag}-a@x.com", f"{tag}-b@x.com"
    first = storage.create_chat(user_id=ua, title=None, model_default="m")
    storage.create_chat(user_id=ua, title=None, model_default="m")
    storage.create_chat(user_id=ub, title=None, model_default="m")
    bumped = "2999-01-01T00:00:00.000000+00:00"
    storage.update_chat_index(
        user_id=ua, chat=first, last_user_preview="x", delta_count=1, last_message_at=bumped
    )

    mine = {u["user_id"]: u for u in _walk_all_users() if u["user_id"].startswith(tag)}
    assert set(mine) == {ua, ub}
    assert mine[ua]["email"] == ua  # JWT sub == email; derived rows surface it
    assert mine[ua]["chat_count"] == 2
    assert mine[ua]["last_chat_at"] == bumped
    assert mine[ua]["last_login_at"] is None
    assert mine[ub]["chat_count"] == 1


@pytest.mark.usefixtures("starter_table")
def test_list_merges_meta_rows_with_chat_aggregates(starter_table: Any) -> None:
    """A USER#META row is authoritative for identity; chats fill activity."""
    tag = f"adminapi-{uuid.uuid4().hex[:8]}"
    uid = f"{tag}-meta@x.com"
    starter_table.put_item(
        Item={
            "PK": f"USER#{uid}",
            "SK": "META",
            "user_id": uid,
            "email": uid,
            "created_at": "2026-01-01T00:00:00.000000+00:00",
            "last_login_at": "2026-07-01T00:00:00.000000+00:00",
        }
    )
    storage.create_chat(user_id=uid, title=None, model_default="m")

    mine = [u for u in _walk_all_users() if u["user_id"] == uid]
    assert len(mine) == 1
    row = mine[0]
    assert row["email"] == uid
    assert row["created_at"] == "2026-01-01T00:00:00.000000+00:00"
    assert row["last_login_at"] == "2026-07-01T00:00:00.000000+00:00"
    assert row["chat_count"] == 1
    assert row["last_chat_at"] is not None


@pytest.mark.usefixtures("starter_table")
def test_detail_round_trip_with_chats_and_audit_events() -> None:
    tag = f"adminapi-{uuid.uuid4().hex[:8]}"
    uid = f"{tag}-detail@x.com"
    first = storage.create_chat(user_id=uid, title=None, model_default="m")
    second = storage.create_chat(user_id=uid, title=None, model_default="m")
    storage.patch_chat(user_id=uid, chat=first, title=None, archived=True)
    event = storage.put_audit_event(event_type="auth.login", actor_id=uid)

    resp = client.get(f"/api/admin/users/{uid}", headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()

    assert body["user"]["user_id"] == uid
    assert body["user"]["email"] == uid
    assert body["user"]["chat_count"] == 2
    assert body["user"]["created_at"] == min(first.created_at, second.created_at)
    assert body["user"]["last_login_at"] is None

    chat_ids = {c["chat_id"] for c in body["recent_chats"]}
    assert chat_ids == {first.chat_id, second.chat_id}
    archived_flags = {c["chat_id"]: c["archived"] for c in body["recent_chats"]}
    assert archived_flags[first.chat_id] is True  # archived chats still count
    assert all("last_user_preview" not in c for c in body["recent_chats"])
    assert all("PK" not in c and "SK" not in c for c in body["recent_chats"])

    event_ids = [e["event_id"] for e in body["recent_audit_events"]]
    assert event.get("event_id") in event_ids
    for e in body["recent_audit_events"]:
        assert set(e) == {"event_id", "event_type", "created_at", "details"}


@pytest.mark.usefixtures("starter_table")
def test_detail_meta_only_user_and_unknown_user(starter_table: Any) -> None:
    tag = f"adminapi-{uuid.uuid4().hex[:8]}"
    uid = f"{tag}-metaonly@x.com"
    starter_table.put_item(
        Item={
            "PK": f"USER#{uid}",
            "SK": "META",
            "user_id": uid,
            "email": uid,
            "created_at": "2026-02-02T00:00:00.000000+00:00",
        }
    )

    resp = client.get(f"/api/admin/users/{uid}", headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["chat_count"] == 0
    assert body["user"]["last_chat_at"] is None
    assert body["recent_chats"] == []

    missing = client.get(f"/api/admin/users/{tag}-ghost@x.com", headers=_admin_headers())
    assert missing.status_code == 404
