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
