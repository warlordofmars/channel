# Copyright (c) 2026 John Carter. All rights reserved.
"""Chat REST + SSE API.

All endpoints require a valid management JWT.  Ownership is enforced by
looking the chat up via the ``ChatByIdIndex`` GSI and comparing
``user_id`` to the JWT ``sub`` claim — mismatches return 404 (not 403)
so chat existence isn't leaked.
"""

from __future__ import annotations

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
from channel.models import (
    Chat,
    ChatCreate,
    ChatPatch,
    Message,
    MessageRole,
    RegenerateRequest,
    SendMessageRequest,
)

_DEFAULT_MODEL = "claude-sonnet-4-6"

# Conversation-continuity bound: how many prior turns to feed back into
# the Strands Agent on each request. 100 covers all but the longest
# chats; Bedrock model-context-window caps the real upper bound.
# A future ConversationManager can trim/summarise beyond this.
_HISTORY_TURNS_LIMIT = 100


def _to_strands_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert stored ``Message`` rows into Strands' ``Messages`` shape.

    Strands expects ``[{"role": "user"|"assistant", "content": [{"text": str}]}]``
    in chronological order. ``storage.list_messages`` returns the same
    chronological order (``ScanIndexForward=True``), so no reordering.
    """
    return [
        {"role": m.role.value, "content": [{"text": m.text}]} for m in messages
    ]


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
    state: dict[str, Any] | None = None,
    persist_user: bool = True,
    index_delta_count: int = 2,
) -> Any:
    """Persist the user turn, stream Strands events, persist the assistant turn.

    ``state`` is an opt-in dict the caller passes in to capture the
    accumulated assistant text, the user/assistant msg ids, and the final
    done-event fields.  ``post_message`` uses it to build the idempotency
    replay payload after the stream completes.  Callers that don't need
    capture (e.g. the ``regenerate`` handler) can omit it.

    ``persist_user`` controls whether the user message is written to
    DynamoDB and announced via ``user_persisted``.  Regenerate sets this
    to ``False`` because the user turn already exists.  ``index_delta_count``
    is the net change to ``message_count`` on the chat-index row — 2 for a
    fresh send (user + assistant), 0 for regenerate (deleted assistant +
    new assistant cancel out).
    """

    state = state if state is not None else {}
    resolved_model = resolve_model_id(model)
    state["resolved_model"] = resolved_model

    # Load the chat's stored history BEFORE persisting the new user
    # message so the loaded list is the true prior context. For
    # regenerate (``persist_user=False``) the trailing message is
    # already the user turn we're about to re-stream; drop it so
    # Strands doesn't see it twice (once in ``messages=`` history,
    # once via ``stream_async(user_message)``).
    prior_msgs, _ = storage.list_messages(
        chat.chat_id, limit=_HISTORY_TURNS_LIMIT, cursor=None
    )
    if not persist_user and prior_msgs and prior_msgs[-1].role == MessageRole.USER:
        prior_msgs = prior_msgs[:-1]
    prior_messages = _to_strands_messages(prior_msgs)

    if persist_user:
        user_msg = storage.put_message(
            chat_id=chat.chat_id,
            role=MessageRole.USER,
            text=user_message,
            model=None,
        )
        state["user_msg_id"] = user_msg.msg_id
        yield sse_user_persisted(msg_id=user_msg.msg_id, seq=0)

    agent = build_agent(
        model_id=model,
        user_id=claims["sub"],
        chat_id=chat.chat_id,
        prior_messages=prior_messages,
    )
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
    state["assistant_text"] = assistant_text
    state["input_tokens"] = input_tokens
    state["output_tokens"] = output_tokens
    state["stop_reason"] = stop_reason

    assistant_msg = storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.ASSISTANT,
        text=assistant_text,
        model=resolved_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    state["assistant_msg_id"] = assistant_msg.msg_id

    storage.update_chat_index(
        user_id=claims["sub"],
        chat=chat,
        last_user_preview=user_message,
        delta_count=index_delta_count,
        last_message_at=assistant_msg.created_at,
    )

    yield sse_done(
        msg_id=assistant_msg.msg_id,
        seq=1,
        model=resolved_model,
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
        state: dict[str, Any] = {}
        async for chunk in _stream_bedrock_reply(
            chat=chat,
            user_message=payload.message,
            model=model,
            claims=claims,
            state=state,
        ):
            yield chunk
        if idempotency_key and state.get("assistant_msg_id"):
            storage.store_idempotency_result(
                user_id=claims["sub"],
                key=idempotency_key,
                payload={
                    "user_msg_id": state["user_msg_id"],
                    "text": state["assistant_text"],
                    "done": {
                        "msg_id": state["assistant_msg_id"],
                        "seq": 1,
                        "model": state["resolved_model"],
                        "input_tokens": state["input_tokens"],
                        "output_tokens": state["output_tokens"],
                        "stop_reason": state["stop_reason"],
                    },
                },
            )

    return StreamingResponse(_produce(), media_type="text/event-stream")


@router.post("/{chat_id}/regenerate")
async def regenerate(
    payload: RegenerateRequest,
    chat_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> StreamingResponse:
    """Drop the last assistant message and re-stream from the last user message."""

    chat = await _load_owned_chat(chat_id, claims["sub"])
    model = payload.model or chat.model_default or _DEFAULT_MODEL

    storage.delete_last_assistant_message(chat_id)

    msgs, _ = storage.list_messages(chat_id, limit=50, cursor=None)
    last_user = next((m for m in reversed(msgs) if m.role == MessageRole.USER), None)
    if last_user is None:
        raise HTTPException(
            status_code=400, detail="Cannot regenerate — chat has no user messages."
        )

    return StreamingResponse(
        _stream_bedrock_reply(
            chat=chat,
            user_message=last_user.text,
            model=model,
            claims=claims,
            persist_user=False,
            index_delta_count=0,
        ),
        media_type="text/event-stream",
    )
