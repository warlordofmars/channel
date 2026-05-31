# Copyright (c) 2026 John Carter. All rights reserved.
"""Dev-only debug endpoints.

Mounted by ``main.py`` only when ``STARTER_ENABLE_DEBUG_ENDPOINTS=1``.
The check happens at app construction so prod (which never sets the
env var) doesn't register these routes at all — see Phase 7c spec
Risk #5.

Current endpoints:

- ``GET /api/_debug/memory/events`` — list AgentCore events for the
  caller (scoped by ``jwt.sub``) and a given ``chat_id``. Used by the
  Phase 7c Playwright e2e to verify writes landed.
- ``DELETE /api/_debug/memory/events/{event_id}`` — best-effort cleanup
  hook for the Playwright e2e so reruns within the same hour start clean.

Both endpoints require a valid mgmt JWT and scope the AgentCore call
by the caller's ``jwt.sub`` — leaked enablement on a real environment
still cannot expose another user's events.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from fastapi import APIRouter, Depends, Query

from channel.agents.memory import _sanitize_actor_id, get_or_create_memory
from channel.api._auth import require_mgmt_user

router = APIRouter(prefix="/api/_debug", tags=["debug"])


@router.get("/memory/events")
async def list_memory_events(
    chat_id: str = Query(...),
    limit: int = Query(10, ge=1, le=100),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Return the most recent AgentCore events for caller + chat_id."""
    memory_id = get_or_create_memory(os.environ["STARTER_ENV"])
    client = boto3.client("bedrock-agentcore")
    resp = client.list_events(
        memoryId=memory_id,
        actorId=_sanitize_actor_id(claims["sub"]),
        sessionId=chat_id,
        maxResults=limit,
    )
    return {"events": resp.get("events", [])}


@router.delete("/memory/events")
async def delete_memory_event(
    chat_id: str = Query(...),
    event_id: str = Query(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, str]:
    """Delete one AgentCore event. Best-effort cleanup for e2e tests.

    ``event_id`` is a query parameter (not a path parameter) because the
    AgentCore event id format is ``<digits>#<hex>`` — the ``#`` is a URL
    fragment delimiter in path position, so the suffix would silently
    drop on the wire and AgentCore would reject the resulting
    digits-only id with a regex validation error.
    """
    memory_id = get_or_create_memory(os.environ["STARTER_ENV"])
    client = boto3.client("bedrock-agentcore")
    client.delete_event(
        memoryId=memory_id,
        actorId=_sanitize_actor_id(claims["sub"]),
        sessionId=chat_id,
        eventId=event_id,
    )
    return {"status": "deleted"}
