# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api/admin router (#235).

Auth-gating tests drive the REAL ``require_admin`` dependency with real
mgmt JWTs (repo rule: don't mock auth; mock the AWS boundary), so the
403 / 401 boundary proven here is the one production enforces. Storage
is patched at the ``channel.api.admin.storage`` module seam, following
the test_chats_api.py pattern.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("STARTER_JWT_SECRET", "test-secret-for-unit-tests")

from channel.api.main import app  # noqa: E402
from channel.auth.tokens import issue_mgmt_jwt  # noqa: E402
from channel.models import Chat  # noqa: E402

client = TestClient(app)


def _admin_headers() -> dict[str, str]:
    token = issue_mgmt_jwt(
        {"user_id": "admin@test.com", "email": "admin@test.com", "role": "admin"}
    )
    return {"Authorization": f"Bearer {token}"}


def _user_headers() -> dict[str, str]:
    token = issue_mgmt_jwt({"user_id": "user@test.com", "email": "user@test.com", "role": "user"})
    return {"Authorization": f"Bearer {token}"}


def _encode_cursor(offset: int, sort: str) -> str:
    payload = json.dumps({"offset": offset, "sort": sort}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _stub_empty_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("channel.api.admin.storage.scan_users", lambda **_kw: ([], None))
    monkeypatch.setattr(
        "channel.api.admin.storage.derive_users_from_chat_index", lambda **_kw: ([], None)
    )


def _meta_row(user_id: str, **attrs: Any) -> dict[str, Any]:
    return {"PK": f"USER#{user_id}", "SK": "META", "user_id": user_id, **attrs}


def _chat(user_id: str, chat_id: str, created_at: str, last_message_at: str, **kw: Any) -> Chat:
    return Chat(
        chat_id=chat_id,
        user_id=user_id,
        title=kw.get("title", "New chat"),
        created_at=created_at,
        last_message_at=last_message_at,
        last_user_preview=kw.get("last_user_preview", "secret preview"),
        model_default="m",
        message_count=kw.get("message_count", 0),
        archived=kw.get("archived", False),
    )


# ----------------------------------------------------------------
# Auth boundary — the endpoints, not the SPA shell, enforce admin
# ----------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/admin/users", "/api/admin/users/u-1"])
def test_admin_routes_reject_missing_auth(path: str) -> None:
    # HTTPBearer returns 403 (not 401) when no Authorization header is
    # supplied; either is "rejected for missing auth" so accept both.
    resp = client.get(path)
    assert resp.status_code in (401, 403)


@pytest.mark.parametrize("path", ["/api/admin/users", "/api/admin/users/u-1"])
def test_admin_routes_reject_invalid_token_with_401(path: str) -> None:
    resp = client.get(path, headers={"Authorization": "Bearer totally.invalid.jwt"})
    assert resp.status_code == 401


@pytest.mark.parametrize("path", ["/api/admin/users", "/api/admin/users/u-1"])
def test_admin_routes_reject_non_admin_with_403(path: str) -> None:
    """A valid mgmt JWT with role=user must be refused server-side."""
    resp = client.get(path, headers=_user_headers())
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin role required"


def test_admin_list_allows_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_empty_storage(monkeypatch)
    resp = client.get("/api/admin/users", headers=_admin_headers())
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "next_cursor": None}


# ----------------------------------------------------------------
# List — fallback, looping, shape
# ----------------------------------------------------------------


def test_list_falls_back_to_chat_index_when_meta_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No USER#META rows (pre-#110 world) → derived rows, email=user_id."""
    monkeypatch.setattr("channel.api.admin.storage.scan_users", lambda **_kw: ([], None))
    derived = [
        {
            "user_id": "a@x.com",
            "created_at": "2026-01-01T00:00:00+00:00",
            "chat_count": 2,
            "last_chat_at": "2026-06-01T00:00:00+00:00",
        }
    ]
    monkeypatch.setattr(
        "channel.api.admin.storage.derive_users_from_chat_index",
        lambda **_kw: (derived, None),
    )
    resp = client.get("/api/admin/users", headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == [
        {
            "user_id": "a@x.com",
            "email": "a@x.com",
            "created_at": "2026-01-01T00:00:00+00:00",
            "last_login_at": None,
            "chat_count": 2,
            "last_chat_at": "2026-06-01T00:00:00+00:00",
        }
    ]
    assert body["next_cursor"] is None


def test_list_loops_scan_users_past_empty_page_with_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """([], cursor) means "keep walking", NOT "fall back" (#234 handoff)."""
    pages = [
        ([], {"PK": "USER#zz", "SK": "META"}),
        ([_meta_row("b@x.com", email="b@x.com", created_at="2026-02-01T00:00:00+00:00")], None),
    ]
    calls: list[dict[str, Any] | None] = []

    def fake_scan_users(*, cursor: Any = None, limit: int = 50) -> Any:
        calls.append(cursor)
        return pages[len(calls) - 1]

    monkeypatch.setattr("channel.api.admin.storage.scan_users", fake_scan_users)
    monkeypatch.setattr(
        "channel.api.admin.storage.derive_users_from_chat_index", lambda **_kw: ([], None)
    )
    resp = client.get("/api/admin/users", headers=_admin_headers())
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [u["user_id"] for u in items] == ["b@x.com"]
    # The second call resumed from the first call's cursor.
    assert calls == [None, {"PK": "USER#zz", "SK": "META"}]


def test_list_joins_meta_rows_with_chat_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    """META rows are authoritative; chat aggregates fill activity fields."""
    meta = [
        _meta_row(
            "a@x.com",
            email="a@x.com",
            created_at="2026-01-01T00:00:00+00:00",
            last_login_at="2026-07-01T00:00:00+00:00",
        ),
        # META row with no chats — a registered user who never chatted.
        _meta_row("c@x.com", email="c@x.com", created_at="2026-03-01T00:00:00+00:00"),
    ]
    derived = [
        {
            "user_id": "a@x.com",
            "created_at": "2026-01-02T00:00:00+00:00",
            "chat_count": 3,
            "last_chat_at": "2026-06-01T00:00:00+00:00",
        },
        # Chat-index user with no META row — must not vanish mid-migration.
        {
            "user_id": "b@x.com",
            "created_at": "2026-02-01T00:00:00+00:00",
            "chat_count": 1,
            "last_chat_at": "2026-05-01T00:00:00+00:00",
        },
    ]
    monkeypatch.setattr("channel.api.admin.storage.scan_users", lambda **_kw: (meta, None))
    monkeypatch.setattr(
        "channel.api.admin.storage.derive_users_from_chat_index",
        lambda **_kw: (derived, None),
    )
    resp = client.get("/api/admin/users", headers=_admin_headers(), params={"sort": "email"})
    assert resp.status_code == 200
    by_id = {u["user_id"]: u for u in resp.json()["items"]}
    assert set(by_id) == {"a@x.com", "b@x.com", "c@x.com"}
    assert by_id["a@x.com"]["chat_count"] == 3
    assert by_id["a@x.com"]["last_chat_at"] == "2026-06-01T00:00:00+00:00"
    assert by_id["a@x.com"]["last_login_at"] == "2026-07-01T00:00:00+00:00"
    # META created_at wins over the chat-derived floor.
    assert by_id["a@x.com"]["created_at"] == "2026-01-01T00:00:00+00:00"
    assert by_id["b@x.com"]["email"] == "b@x.com"
    assert by_id["b@x.com"]["last_login_at"] is None
    assert by_id["c@x.com"]["chat_count"] == 0
    assert by_id["c@x.com"]["last_chat_at"] is None


def test_list_meta_row_missing_user_id_falls_back_to_pk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = [{"PK": "USER#pk-only@x.com", "SK": "META", "email": "pk-only@x.com"}]
    monkeypatch.setattr("channel.api.admin.storage.scan_users", lambda **_kw: (meta, None))
    monkeypatch.setattr(
        "channel.api.admin.storage.derive_users_from_chat_index", lambda **_kw: ([], None)
    )
    resp = client.get("/api/admin/users", headers=_admin_headers())
    assert resp.json()["items"][0]["user_id"] == "pk-only@x.com"


def test_list_truncates_and_warns_when_walk_cap_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A never-exhausting cursor stops at the safety valve, not forever.

    The module-level ``logger`` is mocked directly rather than using
    ``caplog`` because the channel logger sets ``propagate = False``
    once ``configure_logging`` has run in the test session (same
    workaround as ``test_chats_api`` / ``test_tool_hooks``).
    """
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "channel.api.admin.storage.scan_users",
        lambda **_kw: ([], {"PK": "USER#x", "SK": "META"}),
    )
    monkeypatch.setattr(
        "channel.api.admin.storage.derive_users_from_chat_index",
        lambda **_kw: ([], {"user_id": "x"}),
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("channel.api.admin.logger", mock_logger)
    resp = client.get("/api/admin/users", headers=_admin_headers())
    assert resp.status_code == 200
    warnings = [str(call.args[0]) for call in mock_logger.warning.call_args_list]
    assert any("scan_users cursor" in m for m in warnings)
    assert any("derive_users_from_chat_index cursor" in m for m in warnings)


# ----------------------------------------------------------------
# List — sorting
# ----------------------------------------------------------------


def _three_derived_users(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("channel.api.admin.storage.scan_users", lambda **_kw: ([], None))
    derived = [
        {
            "user_id": "b@x.com",
            "created_at": "2026-02-01",
            "chat_count": 1,
            "last_chat_at": "2026-04-01",
        },
        {
            "user_id": "a@x.com",
            "created_at": "2026-03-01",
            "chat_count": 1,
            "last_chat_at": "2026-06-01",
        },
        {
            "user_id": "c@x.com",
            "created_at": "2026-01-01",
            "chat_count": 1,
            "last_chat_at": "2026-05-01",
        },
    ]
    monkeypatch.setattr(
        "channel.api.admin.storage.derive_users_from_chat_index",
        lambda **_kw: (derived, None),
    )


def test_list_sorts_by_last_chat_at_desc_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _three_derived_users(monkeypatch)
    resp = client.get("/api/admin/users", headers=_admin_headers())
    assert [u["user_id"] for u in resp.json()["items"]] == ["a@x.com", "c@x.com", "b@x.com"]


def test_list_sorts_by_created_at_desc(monkeypatch: pytest.MonkeyPatch) -> None:
    _three_derived_users(monkeypatch)
    resp = client.get("/api/admin/users", headers=_admin_headers(), params={"sort": "created_at"})
    assert [u["user_id"] for u in resp.json()["items"]] == ["a@x.com", "b@x.com", "c@x.com"]


def test_list_sorts_by_email_asc(monkeypatch: pytest.MonkeyPatch) -> None:
    _three_derived_users(monkeypatch)
    resp = client.get("/api/admin/users", headers=_admin_headers(), params={"sort": "email"})
    assert [u["user_id"] for u in resp.json()["items"]] == ["a@x.com", "b@x.com", "c@x.com"]


def test_list_sorts_null_timestamps_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """META-only users (no chats) must sink to the bottom of activity sorts."""
    meta = [
        _meta_row("never@x.com", email="never@x.com", created_at="2026-01-01"),
        _meta_row("active@x.com", email="active@x.com", created_at="2026-01-02"),
    ]
    derived = [
        {
            "user_id": "active@x.com",
            "created_at": "2026-01-02",
            "chat_count": 1,
            "last_chat_at": "2026-06-01",
        }
    ]
    monkeypatch.setattr("channel.api.admin.storage.scan_users", lambda **_kw: (meta, None))
    monkeypatch.setattr(
        "channel.api.admin.storage.derive_users_from_chat_index",
        lambda **_kw: (derived, None),
    )
    resp = client.get("/api/admin/users", headers=_admin_headers())
    assert [u["user_id"] for u in resp.json()["items"]] == ["active@x.com", "never@x.com"]


def test_list_rejects_unknown_sort_with_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_empty_storage(monkeypatch)
    resp = client.get("/api/admin/users", headers=_admin_headers(), params={"sort": "nope"})
    assert resp.status_code == 422


@pytest.mark.parametrize("limit", [0, 101, -5])
def test_list_rejects_out_of_range_limit_with_422(
    monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    _stub_empty_storage(monkeypatch)
    resp = client.get("/api/admin/users", headers=_admin_headers(), params={"limit": limit})
    assert resp.status_code == 422


# ----------------------------------------------------------------
# List — pagination
# ----------------------------------------------------------------


def _many_derived_users(monkeypatch: pytest.MonkeyPatch, n: int) -> None:
    monkeypatch.setattr("channel.api.admin.storage.scan_users", lambda **_kw: ([], None))
    derived = [
        {
            "user_id": f"u-{i:02d}@x.com",
            "created_at": "2026-01-01",
            "chat_count": 1,
            "last_chat_at": f"2026-06-{i + 1:02d}",
        }
        for i in range(n)
    ]
    monkeypatch.setattr(
        "channel.api.admin.storage.derive_users_from_chat_index",
        lambda **_kw: (derived, None),
    )


def test_list_paginates_with_opaque_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cursor walk covers every user exactly once; token is opaque base64."""
    _many_derived_users(monkeypatch, 5)
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/admin/users", headers=_admin_headers(), params=params)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) <= 2
        seen.extend(u["user_id"] for u in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
        # Opaque token: base64url JSON with no DDB key shapes inside.
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        assert set(decoded) == {"offset", "sort"}
    assert cursor is None
    assert len(seen) == 5
    assert len(set(seen)) == 5


def test_list_final_exact_page_has_no_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page that exactly drains the list must not dangle a cursor."""
    _many_derived_users(monkeypatch, 4)
    first = client.get("/api/admin/users", headers=_admin_headers(), params={"limit": 2}).json()
    assert first["next_cursor"] is not None
    second = client.get(
        "/api/admin/users",
        headers=_admin_headers(),
        params={"limit": 2, "cursor": first["next_cursor"]},
    ).json()
    assert len(second["items"]) == 2
    assert second["next_cursor"] is None


@pytest.mark.parametrize(
    "bad_cursor",
    [
        "not-base64!!!",
        base64.urlsafe_b64encode(b"[1,2,3]").decode(),  # not a dict
        base64.urlsafe_b64encode(b'{"offset":"x","sort":"last_chat_at"}').decode(),
        base64.urlsafe_b64encode(b'{"offset":-1,"sort":"last_chat_at"}').decode(),
        base64.urlsafe_b64encode(b'{"offset":true,"sort":"last_chat_at"}').decode(),
        base64.urlsafe_b64encode(b'{"offset":0}').decode(),  # sort missing
    ],
)
def test_list_rejects_malformed_cursor_with_400(
    monkeypatch: pytest.MonkeyPatch, bad_cursor: str
) -> None:
    _stub_empty_storage(monkeypatch)
    resp = client.get("/api/admin/users", headers=_admin_headers(), params={"cursor": bad_cursor})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid cursor"


def test_list_rejects_cursor_issued_under_different_sort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An offset into a differently-ordered list would skip/repeat rows."""
    _stub_empty_storage(monkeypatch)
    cursor = _encode_cursor(2, "email")
    resp = client.get(
        "/api/admin/users",
        headers=_admin_headers(),
        params={"cursor": cursor, "sort": "created_at"},
    )
    assert resp.status_code == 400


# ----------------------------------------------------------------
# Detail
# ----------------------------------------------------------------


def _stub_detail_storage(
    monkeypatch: pytest.MonkeyPatch,
    *,
    meta: dict[str, Any] | None,
    chat_pages: list[tuple[list[Chat], Any]],
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    captured: dict[str, Any] = {"chat_calls": [], "audit_kwargs": None}
    monkeypatch.setattr("channel.api.admin.storage.get_user_meta", lambda _uid: meta)

    def fake_list_chats(user_id: str, *, limit: int, cursor: Any, include_archived: bool) -> Any:
        captured["chat_calls"].append(
            {"cursor": cursor, "limit": limit, "include_archived": include_archived}
        )
        return chat_pages[len(captured["chat_calls"]) - 1]

    monkeypatch.setattr("channel.api.admin.storage.list_chats_for_user", fake_list_chats)

    def fake_audit(actor_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured["audit_kwargs"] = {"actor_id": actor_id, **kwargs}
        return events or []

    monkeypatch.setattr("channel.api.admin.storage.list_audit_events_for_actor", fake_audit)
    return captured


def test_detail_404_when_no_meta_and_no_chats(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_detail_storage(monkeypatch, meta=None, chat_pages=[([], None)])
    resp = client.get("/api/admin/users/ghost@x.com", headers=_admin_headers())
    assert resp.status_code == 404
    assert resp.json()["detail"] == "User not found"


def test_detail_derives_user_from_chats_when_meta_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chats = [
        _chat("a@x.com", "c-2", "2026-02-01", "2026-06-01", message_count=4),
        _chat("a@x.com", "c-1", "2026-01-01", "2026-03-01", archived=True),
    ]
    captured = _stub_detail_storage(monkeypatch, meta=None, chat_pages=[(chats, None)])
    resp = client.get("/api/admin/users/a@x.com", headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == {
        "user_id": "a@x.com",
        "email": "a@x.com",
        "created_at": "2026-01-01",  # min chat created_at
        "last_login_at": None,
        "chat_count": 2,
        "last_chat_at": "2026-06-01",  # max last_message_at (activity)
    }
    # Archived chats included; newest-created first; preview excluded.
    assert [c["chat_id"] for c in body["recent_chats"]] == ["c-2", "c-1"]
    assert body["recent_chats"][1]["archived"] is True
    assert all("last_user_preview" not in c for c in body["recent_chats"])
    assert captured["chat_calls"][0]["include_archived"] is True


def test_detail_meta_only_user_has_zero_chats(monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta_row(
        "m@x.com",
        email="m@x.com",
        created_at="2026-01-01",
        last_login_at="2026-07-01",
    )
    _stub_detail_storage(monkeypatch, meta=meta, chat_pages=[([], None)])
    resp = client.get("/api/admin/users/m@x.com", headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["chat_count"] == 0
    assert body["user"]["last_chat_at"] is None
    assert body["user"]["created_at"] == "2026-01-01"
    assert body["user"]["last_login_at"] == "2026-07-01"
    assert body["recent_chats"] == []


def test_detail_walks_chat_pages_and_caps_recent_at_ten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_page = [
        _chat("a@x.com", f"c-{i:02d}", f"2026-01-{20 - i:02d}", f"2026-02-{20 - i:02d}")
        for i in range(8)
    ]
    second_page = [
        _chat("a@x.com", f"c-{i:02d}", f"2026-01-{20 - i:02d}", f"2026-02-{20 - i:02d}")
        for i in range(8, 14)
    ]
    captured = _stub_detail_storage(
        monkeypatch,
        meta=None,
        chat_pages=[(first_page, {"PK": "USER#a@x.com", "SK": "CHAT#..."}), (second_page, None)],
    )
    resp = client.get("/api/admin/users/a@x.com", headers=_admin_headers())
    body = resp.json()
    assert body["user"]["chat_count"] == 14
    assert len(body["recent_chats"]) == 10
    assert len(captured["chat_calls"]) == 2
    assert captured["chat_calls"][1]["cursor"] == {"PK": "USER#a@x.com", "SK": "CHAT#..."}


def test_detail_chat_walk_stops_at_safety_valve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mocks the module logger directly — see the propagate=False note above."""
    from unittest.mock import MagicMock

    from channel.api import admin

    chat = _chat("a@x.com", "c-1", "2026-01-01", "2026-02-01")
    pages = [([chat], {"PK": "USER#a@x.com", "SK": "CHAT#loop"})] * (
        admin._MAX_STORAGE_WALK_CALLS + 1
    )
    _stub_detail_storage(monkeypatch, meta=None, chat_pages=pages)
    mock_logger = MagicMock()
    monkeypatch.setattr("channel.api.admin.logger", mock_logger)
    resp = client.get("/api/admin/users/a@x.com", headers=_admin_headers())
    assert resp.status_code == 200
    assert resp.json()["user"]["chat_count"] == admin._MAX_STORAGE_WALK_CALLS
    warnings = [str(call.args[0]) for call in mock_logger.warning.call_args_list]
    assert any("chat walk" in m for m in warnings)


def test_detail_audit_events_shaped_and_windowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """PK/SK/ttl/actor_id stripped; since_iso ~7 days back; limit 25."""
    events = [
        {
            "PK": "AUDIT#2026-07-10#03",
            "SK": "1751000000#e-1",
            "ttl": 1782000000,
            "event_id": "e-1",
            "event_type": "auth.logout",
            "actor_id": "a@x.com",
            "created_at": "2026-07-10T03:00:00+00:00",
            "details": {"reason": "user"},
        },
        {
            "PK": "AUDIT#2026-07-09#11",
            "SK": "1750900000#e-2",
            "event_id": "e-2",
            "event_type": "auth.login",
            "actor_id": "a@x.com",
            "created_at": "2026-07-09T11:00:00+00:00",
        },
    ]
    captured = _stub_detail_storage(
        monkeypatch,
        meta=_meta_row("a@x.com", email="a@x.com", created_at="2026-01-01"),
        chat_pages=[([], None)],
        events=events,
    )
    resp = client.get("/api/admin/users/a@x.com", headers=_admin_headers())
    body = resp.json()
    assert body["recent_audit_events"] == [
        {
            "event_id": "e-1",
            "event_type": "auth.logout",
            "created_at": "2026-07-10T03:00:00+00:00",
            "details": {"reason": "user"},
        },
        {
            "event_id": "e-2",
            "event_type": "auth.login",
            "created_at": "2026-07-09T11:00:00+00:00",
            "details": None,
        },
    ]
    kwargs = captured["audit_kwargs"]
    assert kwargs["actor_id"] == "a@x.com"
    assert kwargs["limit"] == 25
    # since_iso must be passed (the 168-empty-Query guard from #234) and
    # sit ~7 days in the past.
    from datetime import datetime, timedelta, timezone

    since = datetime.fromisoformat(kwargs["since_iso"])
    age = datetime.now(timezone.utc) - since
    assert timedelta(days=6, hours=23) < age < timedelta(days=7, hours=1)
