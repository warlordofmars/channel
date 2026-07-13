# Copyright (c) 2026 John Carter. All rights reserved.
"""Admin REST API — user list + user detail (#235, epic #233).

Read-only operational visibility: who is registered and what their
signs of life look like. Every route is gated server-side by
``require_admin`` (JWT ``role == "admin"``) — the SPA's ``/app/admin``
route shell (#330) client-gates for UX only; **these endpoints are the
real authorization boundary**.

Pagination contract
-------------------

The #234 storage helpers return DDB-shaped resume cursors and bound
per-call read cost, but a *sorted* user list cannot be produced from an
unsorted Scan one page at a time. So the list endpoint materializes the
full user set per request (walking the storage cursors to exhaustion —
explicitly sanctioned by the #234 handoff), sorts in memory, and
paginates by offset. The client-facing ``cursor`` is an opaque
base64url-JSON token ``{"offset": N, "sort": "<sort>"}`` — DDB key
shapes never leak to clients. O(table)-per-request is the accepted
v0.1 trade-off (tiny user count, per epic #233); the walk is capped at
:data:`_MAX_STORAGE_WALK_CALLS` calls as a safety valve.

``last_login_at`` is read from the ``USER#META`` row when a future
login-path denormalization (epic #233 open question 5) writes it;
until then it is ``null`` for every user (nullable per design
decision 5). It is deliberately NOT derived from the audit log here —
per-user audit walks in a list view cost up to 168 Queries each.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from channel import storage
from channel.api._auth import require_admin
from channel.logging_config import fingerprint_id
from channel.models import Chat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Safety valve on the per-request storage walks. Each ``scan_users``
# call evaluates at most 2 500 items (25 pages x 100), so 40 calls
# bounds one request at ~100k evaluated items. At v0.1 user counts a
# single call exhausts the table; if this cap is ever hit the list is
# served truncated and a warning is logged — revisit the list-users
# access path (epic #233 open question 1) before raising it.
_MAX_STORAGE_WALK_CALLS = 40

# Detail-view caps fixed by the issue: 10 recent chats, 25 audit
# events over the last 7 days.
_RECENT_CHATS_LIMIT = 10
_RECENT_AUDIT_LIMIT = 25
_AUDIT_WINDOW_DAYS = 7

_SORTS = ("last_chat_at", "created_at", "email")
SortField = Literal["last_chat_at", "created_at", "email"]


# ----------------------------------------------------------------
# Cursor codec — opaque offset tokens, never DDB key shapes
# ----------------------------------------------------------------


def _encode_cursor(offset: int, sort: str) -> str:
    payload = json.dumps({"offset": offset, "sort": sort}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(token: str, sort: str) -> int:
    """Decode an opaque list cursor; 400 on anything malformed.

    A corrupt or foreign cursor must be a client error, never a 500.
    The embedded ``sort`` must match the request's ``sort`` — an
    offset into a differently-ordered list would silently skip or
    repeat rows.
    """
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()))
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("offset"), int)
        or isinstance(payload.get("offset"), bool)
        or payload["offset"] < 0
        or payload.get("sort") != sort
    ):
        raise HTTPException(status_code=400, detail="invalid cursor")
    return int(payload["offset"])


# ----------------------------------------------------------------
# User-set materialization
# ----------------------------------------------------------------


def _walk_chat_aggregates() -> dict[str, dict[str, Any]]:
    """Full chat-index aggregation keyed by user_id.

    ``derive_users_from_chat_index`` walks the chat-index scan to
    exhaustion internally; the loop here follows its *row* cursor so
    every aggregated user is collected regardless of page size.
    """
    aggregates: dict[str, dict[str, Any]] = {}
    cursor: dict[str, Any] | None = None
    for _ in range(_MAX_STORAGE_WALK_CALLS):
        rows, cursor = storage.derive_users_from_chat_index(cursor=cursor, limit=100)
        for row in rows:
            aggregates[str(row["user_id"])] = row
        if cursor is None:
            return aggregates
    logger.warning(
        "admin user list truncated: derive_users_from_chat_index cursor still live after %d calls",
        _MAX_STORAGE_WALK_CALLS,
    )
    return aggregates


def _walk_user_meta_rows() -> list[dict[str, Any]]:
    """Collect every ``USER#META`` row.

    ``scan_users`` may legally return ``([], cursor)`` — the cursor,
    not the row count, signals exhaustion (#234 handoff) — so the loop
    keys off the cursor alone.
    """
    collected: list[dict[str, Any]] = []
    cursor: dict[str, Any] | None = None
    for _ in range(_MAX_STORAGE_WALK_CALLS):
        rows, cursor = storage.scan_users(cursor=cursor, limit=100)
        collected.extend(rows)
        if cursor is None:
            return collected
    logger.warning(
        "admin user list truncated: scan_users cursor still live after %d calls",
        _MAX_STORAGE_WALK_CALLS,
    )
    return collected


def _user_row(
    *,
    user_id: str,
    email: str | None,
    created_at: str | None,
    last_login_at: str | None,
    chat_count: int,
    last_chat_at: str | None,
) -> dict[str, Any]:
    """The one list/detail row shape (#238 consumes this verbatim)."""
    return {
        "user_id": user_id,
        # JWT sub == email today (see auth.mgmt_auth._make_user), so the
        # derived fallback surfaces user_id as the email.
        "email": email or user_id,
        "created_at": created_at,
        "last_login_at": last_login_at,
        "chat_count": chat_count,
        "last_chat_at": last_chat_at,
    }


def _materialize_users() -> list[dict[str, Any]]:
    """Full user set as response-shaped rows.

    ``USER#META`` rows are authoritative when present (post-#110);
    chat-index aggregates fill ``chat_count`` / ``last_chat_at`` either
    way. While META is sparse (today), the aggregate set IS the user
    list — the fallback the issue prescribes.
    """
    aggregates = _walk_chat_aggregates()
    meta_rows = _walk_user_meta_rows()
    if not meta_rows:
        return [
            _user_row(
                user_id=str(agg["user_id"]),
                email=None,
                created_at=agg.get("created_at"),
                last_login_at=None,
                chat_count=int(agg.get("chat_count", 0)),
                last_chat_at=agg.get("last_chat_at"),
            )
            for agg in aggregates.values()
        ]
    users: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in meta_rows:
        # USER#META rows carry PK=USER#{user_id}; prefer the explicit
        # attribute, fall back to the key (defensive until #110 locks
        # the row shape).
        user_id = str(item.get("user_id") or str(item.get("PK", ""))[len("USER#") :])
        agg = aggregates.get(user_id, {})
        seen.add(user_id)
        users.append(
            _user_row(
                user_id=user_id,
                email=item.get("email"),
                created_at=item.get("created_at") or agg.get("created_at"),
                last_login_at=item.get("last_login_at"),
                chat_count=int(agg.get("chat_count", 0)),
                last_chat_at=agg.get("last_chat_at"),
            )
        )
    # Users visible in the chat index but lacking a META row (possible
    # mid-migration once #110 ships) must not vanish from the list.
    for user_id, agg in aggregates.items():
        if user_id not in seen:
            users.append(
                _user_row(
                    user_id=user_id,
                    email=None,
                    created_at=agg.get("created_at"),
                    last_login_at=None,
                    chat_count=int(agg.get("chat_count", 0)),
                    last_chat_at=agg.get("last_chat_at"),
                )
            )
    return users


def _sort_users(users: list[dict[str, Any]], sort: str) -> None:
    """In-place sort. Timestamps descending (nulls last), email ascending.

    The pre-pass user_id sort makes ordering deterministic across
    requests when the sort key ties — offset pagination re-derives the
    list per request, so unstable ties would skip/repeat rows across
    pages.
    """
    users.sort(key=lambda u: u["user_id"])
    if sort == "email":
        users.sort(key=lambda u: str(u["email"]))
    else:
        # reverse=True puts the "" (null) sentinel last — nulls sort
        # after every real timestamp in a descending listing.
        users.sort(key=lambda u: u[sort] or "", reverse=True)


# ----------------------------------------------------------------
# Routes
# ----------------------------------------------------------------


@router.get("/users")
async def list_users(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    sort: SortField = "last_chat_at",
    _claims: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Paginated registered-user list, sorted server-side.

    ``sort`` accepts ``last_chat_at`` (default, desc), ``created_at``
    (desc), ``email`` (asc); anything else is a 422. ``next_cursor``
    is non-null while more rows exist and is only valid for the same
    ``sort`` it was issued under.
    """
    offset = _decode_cursor(cursor, sort) if cursor else 0
    users = _materialize_users()
    _sort_users(users, sort)
    page = users[offset : offset + limit]
    has_more = offset + limit < len(users)
    return {
        "items": page,
        "next_cursor": _encode_cursor(offset + limit, sort) if has_more else None,
    }


def _walk_user_chats(user_id: str) -> list[Chat]:
    """Every chat-index row for one user, newest-created first.

    Bounded per-user Query (not a table scan); archived chats are
    included — a user with only archived chats is still a registered
    user (#234 handoff).
    """
    chats: list[Chat] = []
    cursor: Any | None = None
    for _ in range(_MAX_STORAGE_WALK_CALLS):
        rows, cursor = storage.list_chats_for_user(
            user_id, limit=100, cursor=cursor, include_archived=True
        )
        chats.extend(rows)
        if cursor is None:
            return chats
    logger.warning(
        # user_id is the user's email (JWT sub) — fingerprint it rather
        # than writing PII into the log sink (same discipline as chats.py).
        "admin user detail truncated: chat walk for user %s cursor still live after %d calls",
        fingerprint_id(user_id),
        _MAX_STORAGE_WALK_CALLS,
    )
    return chats


def _chat_summary(chat: Chat) -> dict[str, Any]:
    """Trimmed chat shape for the admin detail view.

    Deliberately excludes ``last_user_preview`` — the admin surface is
    operational visibility (epic #233), not message content.
    """
    return {
        "chat_id": chat.chat_id,
        "title": chat.title,
        "created_at": chat.created_at,
        "last_message_at": chat.last_message_at,
        "message_count": chat.message_count,
        "archived": chat.archived,
    }


def _audit_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Audit event minus DDB plumbing (PK/SK/ttl) and the redundant actor_id."""
    return {
        "event_id": item.get("event_id"),
        "event_type": item.get("event_type"),
        "created_at": item.get("created_at"),
        "details": item.get("details"),
    }


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: str = Path(...),
    _claims: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Single-user detail: list-row shape + recent chats + audit events.

    404 when the user has neither a ``USER#META`` row nor any
    chat-index row.
    """
    meta = storage.get_user_meta(user_id)
    chats = _walk_user_chats(user_id)
    if meta is None and not chats:
        raise HTTPException(status_code=404, detail="User not found")

    last_chat_at = max((c.last_message_at or c.created_at for c in chats), default=None)
    min_chat_created = min((c.created_at for c in chats), default=None)
    meta = meta or {}
    since = (datetime.now(timezone.utc) - timedelta(days=_AUDIT_WINDOW_DAYS)).isoformat(
        timespec="microseconds"
    )
    events = storage.list_audit_events_for_actor(
        user_id, since_iso=since, limit=_RECENT_AUDIT_LIMIT
    )
    return {
        "user": _user_row(
            user_id=user_id,
            email=meta.get("email"),
            created_at=meta.get("created_at") or min_chat_created,
            last_login_at=meta.get("last_login_at"),
            chat_count=len(chats),
            last_chat_at=last_chat_at,
        ),
        "recent_chats": [_chat_summary(c) for c in chats[:_RECENT_CHATS_LIMIT]],
        "recent_audit_events": [_audit_summary(e) for e in events],
    }
