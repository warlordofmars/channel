# Copyright (c) 2026 John Carter. All rights reserved.
"""Chat REST + SSE API.

All endpoints require a valid management JWT.  Ownership is enforced by
looking the chat up via the ``ChatByIdIndex`` GSI and comparing
``user_id`` to the JWT ``sub`` claim — mismatches return 404 (not 403)
so chat existence isn't leaked.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response
from fastapi.responses import StreamingResponse

from channel import storage
from channel.api._auth import require_mgmt_user
from channel.models import Chat, ChatCreate, ChatPatch, MessageRole, SendMessageRequest

_DEFAULT_MODEL = "canned-stream-v1"

_CANNED_REPLY = """Good question — here's how I'd think about it.

The core trade-off is between **read latency** and **write amplification**. A columnar store wins big on analytical scans because it only touches the columns you query, but you pay for that on ingest.

A few concrete recommendations:

1. **Batch your writes.** Buffer events for 5–10 seconds and flush in bulk. Columnar formats hate row-at-a-time inserts.
2. **Partition by time, then by tenant.** Most of your queries are time-bounded, so this prunes the search space dramatically before any column is read.
3. **Keep a hot row-store tail.** Serve the last few minutes from the existing row store and merge at query time — users never notice the seam.

Want me to sketch the ingestion buffer as a small artifact you can drop into the pipeline?"""
_CANNED_MODEL = "canned-stream-v1"

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


async def _load_owned_chat(chat_id: str, user_id: str) -> Chat:
    """Look up a chat by id and assert ownership.  404 on mismatch."""

    chat = storage.get_chat_by_id(chat_id)
    if chat is None or chat.user_id != user_id:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.get("/{chat_id}")
async def get_chat(
    chat_id: str = Path(...),
    limit: int = 200,
    cursor: str | None = None,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Get a chat's metadata and a page of messages."""

    chat = await _load_owned_chat(chat_id, claims["sub"])
    messages, next_cursor = storage.list_messages(chat_id, limit=limit, cursor=cursor)
    return {
        "chat": chat.model_dump(),
        "messages": [m.model_dump(mode="json") for m in messages],
        "next_cursor": next_cursor,
    }


@router.patch("/{chat_id}", status_code=204)
async def patch_chat(
    payload: ChatPatch,
    chat_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Rename or archive a chat.  204 No Content on success."""

    chat = await _load_owned_chat(chat_id, claims["sub"])
    storage.patch_chat(
        user_id=claims["sub"],
        chat=chat,
        title=payload.title,
        archived=payload.archived,
    )
    return Response(status_code=204)


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _stream_canned_reply(
    *,
    chat: Chat,
    user_message: str,
    claims: dict[str, Any],
) -> Any:
    """Persist the user turn, emit SSE, persist the assistant turn, update index."""

    user_msg = storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.USER,
        text=user_message,
        model=None,
    )
    yield _sse({"type": "user_persisted", "msg_id": user_msg.msg_id, "seq": 0})

    # Stream the canned reply in word-chunks so the UI sees a real
    # incremental render even pre-Bedrock.
    words = _CANNED_REPLY.split(" ")
    for i, word in enumerate(words):
        chunk = (" " if i else "") + word
        yield _sse({"type": "delta", "text": chunk})

    assistant_msg = storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.ASSISTANT,
        text=_CANNED_REPLY,
        model=_CANNED_MODEL,
        input_tokens=0,
        output_tokens=0,
    )

    storage.update_chat_index(
        user_id=claims["sub"],
        chat=chat,
        last_user_preview=user_message,
        delta_count=2,
        last_message_at=assistant_msg.created_at,
    )

    yield _sse(
        {
            "type": "done",
            "msg_id": assistant_msg.msg_id,
            "seq": 1,
            "model": _CANNED_MODEL,
            "input_tokens": 0,
            "output_tokens": 0,
            "stop_reason": "end_turn",
        }
    )


@router.post("/{chat_id}/messages")
async def post_message(
    payload: SendMessageRequest,
    chat_id: str = Path(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> StreamingResponse:
    """Send a user message and stream the (canned, for now) assistant reply."""

    chat = await _load_owned_chat(chat_id, claims["sub"])

    replay: dict[str, Any] | None = None
    if idempotency_key:
        existing = storage.reserve_idempotency_key(user_id=claims["sub"], key=idempotency_key)
        if existing is not None and existing.get("result"):
            replay = existing["result"]

    if replay is not None:
        replay_payload = replay

        async def _replay() -> Any:
            yield _sse(
                {
                    "type": "user_persisted",
                    "msg_id": replay_payload["user_msg_id"],
                    "seq": 0,
                }
            )
            yield _sse({"type": "delta", "text": replay_payload["text"]})
            yield _sse({"type": "done", **replay_payload["done"]})

        return StreamingResponse(_replay(), media_type="text/event-stream")

    async def _produce() -> Any:
        async for chunk in _stream_canned_reply(
            chat=chat, user_message=payload.message, claims=claims
        ):
            yield chunk
        if idempotency_key:
            storage.store_idempotency_result(
                user_id=claims["sub"],
                key=idempotency_key,
                payload={
                    "user_msg_id": "n/a",
                    "text": _CANNED_REPLY,
                    "done": {
                        "model": _CANNED_MODEL,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "stop_reason": "end_turn",
                    },
                },
            )

    return StreamingResponse(_produce(), media_type="text/event-stream")
