# Copyright (c) 2026 John Carter. All rights reserved.
"""Chat REST + SSE API.

All endpoints require a valid management JWT.  Ownership is enforced by
looking the chat up via the ``ChatByIdIndex`` GSI and comparing
``user_id`` to the JWT ``sub`` claim — mismatches return 404 (not 403)
so chat existence isn't leaked.
"""

from __future__ import annotations

import json as _json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response
from fastapi.responses import StreamingResponse

from channel import storage
from channel.agents.chat_agent import build_agent, resolve_model_id
from channel.agents.strands_sse import (
    sse_delta,
    sse_done,
    sse_user_persisted,
    translate_event,
)
from channel.api._auth import require_mgmt_user
from channel.models import Chat, ChatCreate, ChatPatch, MessageRole, SendMessageRequest

_DEFAULT_MODEL = "claude-sonnet-4-6"


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


async def _stream_bedrock_reply(
    *,
    chat: Chat,
    user_message: str,
    model: str,
    claims: dict[str, Any],
) -> Any:
    """Persist the user turn, stream Strands events, persist the assistant turn."""

    user_msg = storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.USER,
        text=user_message,
        model=None,
    )
    yield sse_user_persisted(msg_id=user_msg.msg_id, seq=0)

    agent = build_agent(model_id=model)
    accumulated: list[str] = []
    stop_reason = "end_turn"
    input_tokens = 0
    output_tokens = 0

    async for event in agent.stream_async(user_message):
        kind, payload = translate_event(event)
        if kind == "delta":
            accumulated.append(payload)
            yield sse_delta(payload)
        elif kind == "stop":
            stop_reason = payload["stop_reason"]
        elif kind == "usage":
            input_tokens = payload["input_tokens"]
            output_tokens = payload["output_tokens"]

    assistant_text = "".join(accumulated)
    assistant_msg = storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.ASSISTANT,
        text=assistant_text,
        model=resolve_model_id(model),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    storage.update_chat_index(
        user_id=claims["sub"],
        chat=chat,
        last_user_preview=user_message,
        delta_count=2,
        last_message_at=assistant_msg.created_at,
    )

    yield sse_done(
        msg_id=assistant_msg.msg_id,
        seq=1,
        model=resolve_model_id(model),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop_reason=stop_reason,
    )


@router.post("/{chat_id}/messages")
async def post_message(
    payload: SendMessageRequest,
    chat_id: str = Path(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> StreamingResponse:
    """Send a user message and stream the Bedrock assistant reply via Strands."""

    chat = await _load_owned_chat(chat_id, claims["sub"])
    model = payload.model or _DEFAULT_MODEL

    replay: dict[str, Any] | None = None
    if idempotency_key:
        existing = storage.reserve_idempotency_key(user_id=claims["sub"], key=idempotency_key)
        if existing is not None and existing.get("result"):
            replay = existing["result"]

    if replay is not None:
        replay_payload = replay

        async def _replay() -> Any:
            yield sse_user_persisted(msg_id=replay_payload["user_msg_id"], seq=0)
            yield sse_delta(replay_payload["text"])
            yield sse_done(**replay_payload["done"])

        return StreamingResponse(_replay(), media_type="text/event-stream")

    async def _produce() -> Any:
        # JSON-peek hack: capture the real msg_ids from the byte stream so
        # the idempotency replay payload can echo them.  Task 6 replaces
        # this with a clean ``state`` dict passed into ``_stream_bedrock_reply``
        # which ALSO captures the accumulated assistant text (currently
        # left empty — drives Task 6's xfail tests).
        produced_user_msg_id: str | None = None
        produced_done: dict[str, Any] | None = None
        async for chunk in _stream_bedrock_reply(
            chat=chat, user_message=payload.message, model=model, claims=claims
        ):
            try:
                event = _json.loads(chunk[6:-2])
            except Exception:  # pragma: no cover  - unreachable from sse_* helpers
                event = None
            if event:
                if event.get("type") == "user_persisted" and produced_user_msg_id is None:
                    produced_user_msg_id = event["msg_id"]
                elif event.get("type") == "done":
                    produced_done = {
                        "msg_id": event["msg_id"],
                        "seq": event["seq"],
                        "model": event["model"],
                        "input_tokens": event["input_tokens"],
                        "output_tokens": event["output_tokens"],
                        "stop_reason": event["stop_reason"],
                    }
            yield chunk
        if idempotency_key and produced_user_msg_id and produced_done:
            storage.store_idempotency_result(
                user_id=claims["sub"],
                key=idempotency_key,
                payload={
                    "user_msg_id": produced_user_msg_id,
                    "text": "",  # populated in Task 6
                    "done": produced_done,
                },
            )

    return StreamingResponse(_produce(), media_type="text/event-stream")
