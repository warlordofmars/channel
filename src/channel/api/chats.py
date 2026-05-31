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
    limit: int = 50,
    cursor: str | None = None,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """List the authenticated user's chats, newest first."""

    chats, next_cursor = storage.list_chats_for_user(claims["sub"], limit=limit, cursor=cursor)
    return {
        "items": [c.model_dump() for c in chats],
        "next_cursor": next_cursor,
    }
