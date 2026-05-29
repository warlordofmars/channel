# Copyright (c) 2026 John Carter. All rights reserved.
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from functools import lru_cache
from typing import TYPE_CHECKING

import boto3
from pydantic import BaseModel

from channel.agents.bedrock import get_model_id

if TYPE_CHECKING:  # pragma: no cover
    from mypy_boto3_bedrock_agent_runtime import AgentsforBedrockRuntimeClient


class InlineAgentRequest(BaseModel):
    """Parameters for an inline Bedrock Agent invocation.

    ``session_id`` is a caller-supplied opaque string that identifies a
    conversation.  The wrapper namespaces it to the authenticated user so
    that sessions are never shared across users:
    ``bedrock_session_id = f"{user_id}:{session_id}"``.

    Omit ``session_id`` to start a new conversation; a random UUID is used.
    """

    message: str
    session_id: str | None = None
    instruction: str | None = None
    max_tokens: int = 1024


class InlineAgentResponse(BaseModel):
    reply: str
    session_id: str  # the caller-supplied session_id (not the namespaced Bedrock ID)


@lru_cache(maxsize=1)
def _agent_client() -> AgentsforBedrockRuntimeClient:
    """Return a cached Bedrock agent runtime client.

    Region resolution follows boto3 precedence: AWS_REGION → AWS_DEFAULT_REGION
    → ~/.aws/config.  We only pass region_name when one of the env vars is
    explicitly set so local dev and CI inherit the configured default naturally.
    """
    import os

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    kwargs: dict = {}
    if region:
        kwargs["region_name"] = region
    return boto3.client("bedrock-agent-runtime", **kwargs)  # type: ignore[return-value]


def _bedrock_session_id(user_id: str, session_id: str) -> str:
    """Namespace a caller session ID to a user so sessions never bleed across users."""
    return f"{user_id}:{session_id}"


def invoke(request: InlineAgentRequest, *, user_id: str) -> InlineAgentResponse:
    """Non-streaming inline agent invocation.

    Collects all ``chunk`` events from the response stream and concatenates
    them into a single reply string.
    """
    session_id = request.session_id or str(uuid.uuid4())
    chunks = list(_stream_chunks(request, user_id=user_id, session_id=session_id))
    return InlineAgentResponse(reply="".join(chunks), session_id=session_id)


def invoke_stream(request: InlineAgentRequest, *, user_id: str) -> Iterator[str]:
    """Streaming inline agent invocation — yields SSE-formatted events.

    Two event types are emitted:

    * ``{"type": "delta", "text": "..."}`` — one incremental text chunk.
    * ``{"type": "done", "session_id": "..."}`` — final event after the agent
      finishes.  ``session_id`` is the caller-supplied identifier so the
      client can resume the conversation in a later request.
    """
    session_id = request.session_id or str(uuid.uuid4())
    for text in _stream_chunks(request, user_id=user_id, session_id=session_id):
        yield f"data: {json.dumps({'type': 'delta', 'text': text})}\n\n"
    yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"


def _stream_chunks(
    request: InlineAgentRequest,
    *,
    user_id: str,
    session_id: str,
) -> Iterator[str]:
    """Yield decoded text chunks from the InvokeInlineAgent event stream."""
    client = _agent_client()
    kwargs: dict = {
        "foundationModel": get_model_id(),
        "inputText": request.message,
        "sessionId": _bedrock_session_id(user_id, session_id),
        "endSession": False,
        "enableTrace": False,
        "inlineSessionState": {
            "promptSessionAttributes": {},
        },
        "inferenceConfig": {
            "maximumLength": request.max_tokens,
        },
    }
    if request.instruction:
        kwargs["agentInstruction"] = request.instruction

    response = client.invoke_inline_agent(**kwargs)
    for event in response["completion"]:
        if "chunk" in event:
            raw = event["chunk"].get("bytes", b"")
            if raw:
                yield raw.decode("utf-8")
