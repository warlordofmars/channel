# Copyright (c) 2026 John Carter. All rights reserved.
"""Dev-only debug endpoints.

Mounted by ``main.py`` only when ``CHANNEL_ENABLE_DEBUG_ENDPOINTS=1``.
The check happens at app construction so prod (which never sets the
env var) doesn't register these routes at all — see Phase 7c spec
Risk #5.

Current endpoints:

- ``GET /api/_debug/memory/events`` — list AgentCore events for the
  caller (scoped by ``jwt.sub``) and a given ``chat_id``. Used by the
  Phase 7c Playwright e2e to verify writes landed.
- ``DELETE /api/_debug/memory/events/{event_id}`` — best-effort cleanup
  hook for the Playwright e2e so reruns within the same hour start clean.
- ``GET /api/_debug/recall/inspect`` — return the recall block
  ``AgentCoreRecallHook`` WOULD inject for the caller + ``chat_id``,
  with per-fragment provenance + a token estimate. The investigation
  instrument for the long-chat-degradation hypothesis (#227).

All endpoints require a valid mgmt JWT and scope the AgentCore call
by the caller's ``jwt.sub`` — leaked enablement on a real environment
still cannot expose another user's events.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from fastapi import APIRouter, Depends, Query

from channel.agents.memory import _sanitize_actor_id, get_or_create_memory
from channel.agents.recall import AgentCoreRecallHook
from channel.api._auth import require_mgmt_user

router = APIRouter(prefix="/api/_debug", tags=["debug"])

# Rough chars-per-token divisor for the recall-block size estimate. See
# ``_estimate_tokens``.
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Rough token-count estimate for the recall block.

    Uses the standard ~4-chars-per-token heuristic instead of a live
    Bedrock ``CountTokens`` call: this endpoint is a diagnostic for
    order-of-magnitude sizing ("is the recall block dominating the prompt
    budget?"), so an exact count isn't worth an extra AWS round-trip and a
    model-id choice. Ceil-division, so any non-empty block reports at least
    one token and the empty block reports zero.
    """
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


@router.get("/memory/events")
async def list_memory_events(
    chat_id: str = Query(...),
    limit: int = Query(10, ge=1, le=100),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Return the most recent AgentCore events for caller + chat_id."""
    memory_id = get_or_create_memory(os.environ["CHANNEL_ENV"])
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
    memory_id = get_or_create_memory(os.environ["CHANNEL_ENV"])
    client = boto3.client("bedrock-agentcore")
    client.delete_event(
        memoryId=memory_id,
        actorId=_sanitize_actor_id(claims["sub"]),
        sessionId=chat_id,
        eventId=event_id,
    )
    return {"status": "deleted"}


@router.get("/recall/inspect")
async def inspect_recall(
    chat_id: str = Query(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Return the recall block ``AgentCoreRecallHook`` WOULD inject for the
    caller + ``chat_id`` right now — without firing a real turn (#227).

    Investigation instrument for the long-chat-degradation hypothesis: it
    surfaces the exact ``## What we've talked about before`` addendum the
    hook builds, its per-fragment provenance (each fragment's source
    ``session_id`` — which equals the source chat id), and a heuristic
    token estimate, so an investigator can see whether recall is dominated
    by fragments from other, topically-unrelated chats and how big the
    block is relative to the prompt budget.

    Scoped to the caller's ``jwt.sub`` (same as the other _debug
    endpoints): even if the flag leaks on a real environment, one user
    cannot inspect another's recall. The hook constructor sanitizes the
    raw ``sub`` into the AgentCore ``actorId``.

    Reuses the hook's own fetch + formatting path via
    ``preview_addendum`` so the output matches what really gets injected.
    That path bypasses the hook's 5-turn cache (a fresh view of Memory)
    and ignores the ``CHANNEL_RECALL_ENABLED`` kill-switch so the block is
    inspectable during an A/B run; ``recall_enabled`` reports whether a
    live turn would actually inject it.
    """
    memory_id = get_or_create_memory(os.environ["CHANNEL_ENV"])
    client = boto3.client("bedrock-agentcore")
    hook = AgentCoreRecallHook(
        memory_id=memory_id,
        actor_id=claims["sub"],
        client=client,
    )
    block, records = await hook.preview_addendum(chat_id=chat_id)
    fragments = [
        {
            "session_id": rec.get("sessionId", ""),
            "created_at": rec.get("createdAt", ""),
            "turn_count": len(rec.get("payload", [])),
        }
        for rec in records
    ]
    return {
        "chat_id": chat_id,
        "actor_id": _sanitize_actor_id(claims["sub"]),
        "recall_enabled": os.environ.get("CHANNEL_RECALL_ENABLED", "1") == "1",
        "block": block,
        "fragments": fragments,
        "session_count": len(fragments),
        "char_count": len(block),
        "token_estimate": _estimate_tokens(block),
    }
