# Copyright (c) 2026 John Carter. All rights reserved.
"""Chat REST + SSE API.

All endpoints require a valid management JWT.  Ownership is enforced by
looking the chat up via the ``ChatByIdIndex`` GSI and comparing
``user_id`` to the JWT ``sub`` claim — mismatches return 404 (not 403)
so chat existence isn't leaked.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import boto3
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response
from fastapi.responses import StreamingResponse
from strands.types.exceptions import MaxTokensReachedException

from channel import storage
from channel.agents.chat_agent import (
    build_agent,
    build_followups_agent,
    build_titler_agent,
    resolve_model_id,
)
from channel.agents.memory import _sanitize_actor_id, get_or_create_memory
from channel.agents.strands_sse import (
    sse_delta,
    sse_done,
    sse_follow_ups_suggested,
    sse_title_suggested,
    sse_user_persisted,
    translate_event,
)
from channel.api._auth import require_mgmt_user
from channel.metrics import (
    record_auto_title_outcome,
    record_chat_delete_memory_wipe_outcome,
    record_followup_outcome,
)
from channel.models import (
    Chat,
    ChatCreate,
    ChatPatch,
    FeedbackRequest,
    Message,
    MessageRole,
    RegenerateRequest,
    SendMessageRequest,
)

logger = logging.getLogger(__name__)

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
    return [{"role": m.role.value, "content": [{"text": m.text}]} for m in messages]


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
    include_archived: bool = False,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """List the authenticated user's chats, newest first.

    Archived chats are filtered out server-side by default. The SPA's
    Recents list relies on that. Pass ``?include_archived=1`` to fetch
    everything (used by archive-management surfaces).
    """

    chats, next_cursor = storage.list_chats_for_user(
        claims["sub"],
        limit=limit,
        cursor=cursor,
        include_archived=include_archived,
    )
    return {
        "items": [c.model_dump() for c in chats],
        "next_cursor": next_cursor,
    }


def _postprocess_title(raw: str) -> str:
    """Normalize raw titler output into a usable sidebar title.

    Haiku occasionally emits preamble (e.g. ``"Here's a 3-6 word title:
    Debug pytest fixture"``). We strip leading explanations by taking
    the part after the LAST colon if a colon is present, then split
    into words and cap at 6 (matches the titler's 3-6 word system
    prompt). Strips wrapping quote characters and trailing sentence
    punctuation.

    Returns empty string if no usable text remains — caller treats
    that as a failure outcome (better to leave "New chat" than to
    surface preamble as a title).
    """
    if not raw:
        return ""
    candidate = raw.rsplit(":", 1)[-1] if ":" in raw else raw
    candidate = candidate.strip().strip('"').strip("'").strip()
    words = candidate.split()
    if not words:
        return ""
    return " ".join(words[:6]).rstrip(".,;:!?")


def _agentcore_client() -> Any:
    """Lazy boto3 client construction — patched in unit tests."""
    return boto3.client("bedrock-agentcore")


def _memory_id_for_env() -> str:
    """Resolve the current env's AgentCore memoryId. Cached by
    ``get_or_create_memory``; cheap to call per request."""
    env = os.environ.get("STARTER_ENV", "unknown")
    return get_or_create_memory(env)


async def _wipe_agentcore_session(actor_id: str, chat_id: str) -> None:
    """Best-effort delete of all AgentCore events for one chat session.

    Pages list_events and calls delete_event per event. Wrapped in
    try/except at the endpoint level; this helper just does the work
    and lets exceptions propagate so the caller can record the
    failure metric. Same fail-soft contract as Phase 7c memory writes
    and Phase 8a recall.
    """
    client = _agentcore_client()
    memory_id = _memory_id_for_env()
    sanitized_actor = _sanitize_actor_id(actor_id)
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "memoryId": memory_id,
            "actorId": sanitized_actor,
            "sessionId": chat_id,
            "maxResults": 100,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        resp = await asyncio.to_thread(client.list_events, **kwargs)
        for event in resp.get("events", []):
            await asyncio.to_thread(
                client.delete_event,
                memoryId=memory_id,
                actorId=sanitized_actor,
                sessionId=chat_id,
                eventId=event["eventId"],
            )
        next_token = resp.get("nextToken")
        if not next_token:
            break


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


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(
    chat_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Permanently delete a chat: DDB message rows + chat-index row +
    AgentCore session events. AgentCore failure is logged + counted
    but does NOT fail the user's delete (DDB is the source of truth
    for chat existence)."""
    chat = await _load_owned_chat(chat_id, claims["sub"])
    storage.delete_chat(user_id=claims["sub"], chat=chat)
    try:
        await _wipe_agentcore_session(actor_id=claims["sub"], chat_id=chat_id)
        await record_chat_delete_memory_wipe_outcome(success=True)
    except Exception as exc:
        logger.warning(
            "agentcore.chat_delete_wipe_failed chat_id=%s",
            chat_id,
            extra={"error_type": type(exc).__name__, "error_message": str(exc)},
            exc_info=True,
        )
        await record_chat_delete_memory_wipe_outcome(success=False)
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
    effort: str | None = None,
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

    ``effort`` overrides the per-turn ``max_tokens`` budget on the
    Strands BedrockModel (issue #154). When omitted, the user's saved
    pref tier applies.
    """

    state = state if state is not None else {}
    resolved_model = resolve_model_id(model)
    state["resolved_model"] = resolved_model
    # Capture "this was the first round-trip" BEFORE we persist anything.
    # Auto-title block below uses it to fire exactly once per chat
    # (Phase 7d idempotency).
    was_first_round_trip = chat.message_count == 0
    # Load prefs once at stream start. Used by the follow-ups block
    # after the assistant turn lands; defaults are applied at the
    # storage layer when no row exists. Per-request ``effort`` overrides
    # the saved pref so the segmented control in the Composer takes
    # priority over the Customize default.
    prefs = storage.get_prefs(claims["sub"])
    effective_effort = effort if effort is not None else prefs.effort

    # Load the chat's stored history BEFORE persisting the new user
    # message so the loaded list is the true prior context. For
    # regenerate (``persist_user=False``) the trailing message is
    # already the user turn we're about to re-stream; drop it so
    # Strands doesn't see it twice (once in ``messages=`` history,
    # once via ``stream_async(user_message)``).
    prior_msgs, _ = storage.list_messages(chat.chat_id, limit=_HISTORY_TURNS_LIMIT, cursor=None)
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
        effort=effective_effort,
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

    # Phase 7d auto-title: after the FIRST round-trip lands, fire a
    # one-shot Haiku Agent to summarise the exchange in 3-6 words.
    # Inline-emitted in the same SSE stream after ``done`` and BEFORE
    # close — the SPA's readSse loop iterates until reader is done.
    # Fail-soft: titler errors log + EMF + swallow; the user's stream
    # has already completed by this point.
    if was_first_round_trip and os.environ.get("STARTER_AUTO_TITLE_ENABLED", "1") == "1":
        truncated = False
        try:
            titler = build_titler_agent()
            titler_prompt = f"User: {user_message}\nAssistant: {assistant_text[:500]}"
            title_chunks: list[str] = []
            try:
                async for event in titler.stream_async(titler_prompt):
                    kind, payload = translate_event(event)
                    if kind == "delta":
                        title_chunks.append(payload)
            except MaxTokensReachedException:
                # Strands emits deltas BEFORE raising on token-cap, so
                # title_chunks already holds usable partial text.
                # _postprocess_title strips preamble and caps at 6
                # words; empty result → caller records failure.
                truncated = True
            title = _postprocess_title("".join(title_chunks))
            if title:
                storage.patch_chat(
                    user_id=claims["sub"],
                    chat=chat,
                    title=title,
                    archived=None,
                )
                yield sse_title_suggested(chat_id=chat.chat_id, title=title)
                await record_auto_title_outcome(success=True)
            else:
                await record_auto_title_outcome(success=False)
        except Exception as exc:
            logger.warning(
                "auto_title_failed chat_id=%s truncated=%s",
                chat.chat_id,
                truncated,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )
            await record_auto_title_outcome(success=False)

    # Follow-up suggestions. Runs after auto-title so the SPA gets the
    # title first (sidebar refresh) and the chips appear under the
    # assistant turn. Gated by both an env kill-switch and the user's
    # ``suggest_followups`` pref (default True). Fail-soft: a generation
    # error logs + EMF + swallow; the stream has already completed
    # successfully by this point.
    followups_enabled = os.environ.get("STARTER_FOLLOWUPS_ENABLED", "1") == "1"
    if followups_enabled and prefs.suggest_followups:
        try:
            followups_agent = build_followups_agent()
            followups_prompt = (
                f"User: {user_message}\n\nAssistant: {assistant_text[:1000]}\n\nFollow-up prompts:"
            )
            chunks: list[str] = []
            try:
                async for event in followups_agent.stream_async(followups_prompt):
                    kind, payload = translate_event(event)
                    if kind == "delta":
                        chunks.append(payload)
            except MaxTokensReachedException:
                # Strands emits deltas BEFORE raising on token-cap; any
                # already-accumulated lines below are still usable.
                pass
            suggestions = _parse_followups("".join(chunks))
            if suggestions:
                yield sse_follow_ups_suggested(
                    chat_id=chat.chat_id,
                    message_id=assistant_msg.msg_id,
                    suggestions=suggestions,
                )
                await record_followup_outcome(success=True)
            else:
                await record_followup_outcome(success=False)
        except Exception as exc:
            logger.warning(
                "followups_generation_failed chat_id=%s",
                chat.chat_id,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )
            await record_followup_outcome(success=False)


def _parse_followups(raw: str) -> list[str]:
    """Parse a free-form Haiku reply into a list of 2-3 follow-up prompts.

    Trims numbering, list bullets, trailing punctuation, and empty
    lines. Caps at 3 suggestions to keep the chip row visually bounded.
    """
    suggestions: list[str] = []
    for line in raw.splitlines():
        cleaned = line.strip().lstrip("-*•0123456789. ").rstrip(".,;:")
        if cleaned:
            suggestions.append(cleaned)
    return suggestions[:3]


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
            effort=payload.effort,
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
            effort=payload.effort,
        ),
        media_type="text/event-stream",
    )


@router.post("/{chat_id}/messages/{msg_id}/feedback", status_code=204)
async def submit_feedback(
    payload: FeedbackRequest,
    chat_id: str = Path(...),
    msg_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Persist a thumbs-up / thumbs-down feedback record for a message.

    Idempotent overwrite — submitting again replaces the prior record
    on the same message. ``msg_id`` must belong to ``chat_id`` and the
    chat must be owned by the caller. Mismatches return 404 so chat /
    message existence isn't leaked.
    """

    chat = await _load_owned_chat(chat_id, claims["sub"])
    feedback = storage.put_message_feedback(
        chat_id=chat.chat_id,
        msg_id=msg_id,
        kind=payload.kind,
        note=payload.note,
    )
    if feedback is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return Response(status_code=204)
