# Copyright (c) 2026 John Carter. All rights reserved.
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from functools import lru_cache
from typing import TYPE_CHECKING

import boto3
from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient


class BedrockMessage(BaseModel):
    role: str
    content: str


class ConverseRequest(BaseModel):
    messages: list[BedrockMessage]
    system: str | None = None
    max_tokens: int = 1024


class ConverseResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    stop_reason: str


def get_model_id() -> str:
    return os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-6")


@lru_cache(maxsize=1)
def _bedrock_client() -> BedrockRuntimeClient:
    """Return a cached Bedrock runtime client.

    Region resolution follows boto3 precedence: AWS_REGION → AWS_DEFAULT_REGION
    → ~/.aws/config.  We only pass region_name when one of the env vars is
    explicitly set so local dev and CI inherit the configured default naturally.
    """
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    kwargs: dict = {}
    if region:
        kwargs["region_name"] = region
    return boto3.client("bedrock-runtime", **kwargs)  # type: ignore[return-value]


def converse_stream(request: ConverseRequest) -> Iterator[str]:
    """Yield SSE-formatted chunks from a Bedrock streaming conversation.

    Each yielded string is a complete ``data: ...\\n\\n`` SSE event.  Two
    event types are emitted:

    * ``{"type": "delta", "text": "..."}`` — one incremental text token.
    * ``{"type": "done", "stop_reason": "...", "input_tokens": N,
      "output_tokens": M}`` — final event after the model finishes.
    """
    client = _bedrock_client()
    messages = [{"role": m.role, "content": [{"text": m.content}]} for m in request.messages]
    kwargs: dict = {
        "modelId": get_model_id(),
        "messages": messages,
        "inferenceConfig": {"maxTokens": request.max_tokens},
    }
    if request.system:
        kwargs["system"] = [{"text": request.system}]

    response = client.converse_stream(**kwargs)
    input_tokens = 0
    output_tokens = 0
    stop_reason = ""

    for event in response["stream"]:
        if "contentBlockDelta" in event:
            text = event["contentBlockDelta"]["delta"].get("text", "")
            if text:
                yield f"data: {json.dumps({'type': 'delta', 'text': text})}\n\n"
        elif "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason", "")
        elif "metadata" in event:
            usage = event["metadata"].get("usage", {})
            input_tokens = usage.get("inputTokens", 0)
            output_tokens = usage.get("outputTokens", 0)

    yield (
        f"data: {json.dumps({'type': 'done', 'stop_reason': stop_reason, 'input_tokens': input_tokens, 'output_tokens': output_tokens})}\n\n"
    )


def converse(request: ConverseRequest) -> ConverseResponse:
    client = _bedrock_client()
    messages = [{"role": m.role, "content": [{"text": m.content}]} for m in request.messages]
    kwargs: dict = {
        "modelId": get_model_id(),
        "messages": messages,
        "inferenceConfig": {"maxTokens": request.max_tokens},
    }
    if request.system:
        kwargs["system"] = [{"text": request.system}]
    response = client.converse(**kwargs)
    return ConverseResponse(
        content=response["output"]["message"]["content"][0]["text"],
        input_tokens=response["usage"]["inputTokens"],
        output_tokens=response["usage"]["outputTokens"],
        stop_reason=response["stopReason"],
    )
