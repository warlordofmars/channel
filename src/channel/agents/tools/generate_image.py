# Copyright (c) 2026 John Carter. All rights reserved.
"""``generate_image`` — Amazon Nova Canvas image generation (#279).

A single narrow tool: ``generate_image(prompt, aspect_ratio)``. It calls
Bedrock ``InvokeModel`` on Amazon Nova Canvas (``amazon.nova-canvas-v1:0``)
with the ``TEXT_IMAGE`` task, decodes the returned base64 PNG, and hands
the bytes to the post-stream asset pipeline (#326) for durable
persistence — the generated image is surfaced to the user as an
``origin=generated`` ASSET via the ``asset_created`` SSE frame + inline
card, NEVER as base64 over SSE (epic #321 decision 6).

The harvest channel is deliberately out-of-band from SSE: the tool
appends the base64 payload to a per-turn sink stashed on the invoking
``Agent`` (via ``tool_context.agent``, mirroring how ``chat_agent`` already
attaches ``agent.chat_id`` / ``agent.chain_state``). ``chats.py`` reads that
sink in the same post-stream slot as the code-exec image producer — the
ASSET rows need the assistant ``msg_id`` which only exists once the
assistant turn has landed. The tool's own ``ToolResult`` carries only a
short text confirmation (which reaches the model so it can describe what
it made, and is dropped by the memory hook's ``toolResult`` strip); the
image bytes never ride the ``ToolResult`` content, so ``translate_event``
emits a generic ``tool_finished`` with no payload.

Content moderation is Bedrock's built-in Nova Canvas RAI filter — no
custom layer (decision 3). A fully-blocked generation returns an empty
``images`` list with an ``error`` field; that surfaces as a
``ToolResult``-shaped error with ``error_type="content_filtered"``,
mirroring ``code_exec``'s error-shape contract so ``translate_event``'s
reason extraction works.

There is NO cost gating — billing is deferred (product decision). The
only controls are the ``STARTER_IMAGE_GEN_ENABLED`` registration
kill-switch (gated in ``chats._build_tool_registry``) and the
``ImageGenInvocations`` EMF counter emitted per invocation.

NOTE: this module deliberately does NOT use ``from __future__ import
annotations``. Strands' ``@tool`` decorator validates the injected
context parameter by annotation identity (``param.annotation is
ToolContext``); under PEP 563 the annotation would be the string
``"ToolContext"`` and the guard would silently no-op.
"""

import asyncio
import functools
import json
import logging
import os
from typing import Any

import botocore.exceptions
from strands import tool
from strands.types.tools import ToolContext

from channel.logging_config import fingerprint_id
from channel.metrics import record_image_gen_outcome

logger = logging.getLogger(__name__)

# Default Nova Canvas model id. Overridable via ``STARTER_IMAGE_GEN_MODEL``
# for a personal dev env pointed at a different image model. Verified
# against the AWS Nova user guide (image-gen request/response structure)
# 2026-07-13 — invoked directly by base model id (no cross-region
# inference profile for image models).
_DEFAULT_MODEL_ID = "amazon.nova-canvas-v1:0"

# Attribute name of the per-turn sink stashed on the invoking Agent. The
# tool appends ``{"tool_use_id", "b64", "mime", "title"}`` entries; the
# stream path in ``chats.py`` reads this attribute post-stream to persist
# the generated images as assets. Exported so ``chats.py`` reads the same
# name (no stringly-typed drift).
GENERATED_IMAGE_SINK_ATTR = "generated_image_sink"

# Aspect-ratio -> (width, height). All dimensions satisfy Nova Canvas's
# constraints: each side 320-4096 and divisible by 16, aspect between
# 1:4 and 4:1, total pixels < 4,194,304 (verified against the Nova user
# guide 2026-07-13). Unknown ratios fall back to 1:1 — never error on a
# ratio the model invented (mirrors the effort-tier fallback in
# ``chat_agent.max_tokens_for_effort``).
_ASPECT_RATIO_DIMENSIONS: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
}
_DEFAULT_ASPECT_RATIO = "1:1"

# Nova Canvas caps the prompt at 1024 characters; a longer prompt is a
# guaranteed ValidationException. Truncate rather than burn an invocation
# — a 1024-char prompt is already far longer than any useful description.
_PROMPT_MAX_CHARS = 1024

# Asset card title derived from the prompt's first line, capped so the
# inline card stays legible.
_TITLE_MAX_CHARS = 80

# The generated PNG bytes always carry this MIME — Nova Canvas returns
# PNG for TEXT_IMAGE.
_OUTPUT_MIME = "image/png"

# boto3's default read timeout is 60s; Nova Canvas can exceed that for
# larger / premium requests (per the AWS user guide's read_timeout
# note). 300s matches the streaming-chat Lambda timeout envelope.
_READ_TIMEOUT_SECONDS = 300


def _error_result(error_type: str) -> dict[str, Any]:
    """Build a Strands-``ToolResult``-shaped error dict.

    See ``src/channel/agents/tools/web_search.py:_error_result`` for the
    full rationale on why this shape (``{"status": "error", "content":
    [{"text": <reason>}]}``) — not ``{"error_type": ...}`` — is
    load-bearing for the SSE error contract."""
    return {"status": "error", "content": [{"text": error_type}]}


@functools.lru_cache(maxsize=1)
def _get_bedrock_runtime_client():  # type: ignore[no-untyped-def]
    """Lazy-load the boto3 ``bedrock-runtime`` client with a long read
    timeout.

    Mirrors ``code_exec._get_lambda_client`` / ``web_search._get_exa_search``
    — deferring the boto3 import and client construction until the model
    actually calls ``generate_image`` keeps them off the cold-start path
    for turns that don't generate an image."""
    import boto3  # noqa: PLC0415  # pragma: no cover
    from botocore.config import Config  # noqa: PLC0415  # pragma: no cover

    return boto3.client(  # pragma: no cover
        "bedrock-runtime",
        config=Config(read_timeout=_READ_TIMEOUT_SECONDS),
    )


def _resolve_dimensions(aspect_ratio: str) -> tuple[int, int]:
    """Map a caller-supplied aspect ratio to (width, height).

    Unknown ratios fall back to the 1:1 dimensions — never raise on a
    ratio string the model invented."""
    return _ASPECT_RATIO_DIMENSIONS.get(
        aspect_ratio, _ASPECT_RATIO_DIMENSIONS[_DEFAULT_ASPECT_RATIO]
    )


def _title_from_prompt(prompt: str) -> str:
    """Derive an inline-card title from the prompt's first non-empty line."""
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_TITLE_MAX_CHARS]
    return "Generated image"


def _classify_client_error(exc: botocore.exceptions.ClientError) -> str:
    """Map a Bedrock ``ClientError`` to a bounded ``error_type`` token.

    The SPA renders distinct affordances per token, so the set stays
    small: ``rate_limit`` (throttling), ``model_access_denied`` (the
    account hasn't enabled Nova Canvas — account state, not a code bug),
    and ``generation_failed`` for everything else (validation, service
    errors).

    ``ResourceNotFoundException`` also maps to ``model_access_denied``:
    Bedrock raises it (not ``AccessDeniedException``) both for an
    unknown model id AND for a model the account can't currently invoke
    — including a provider-``LEGACY`` model deactivated after 30 days of
    disuse ("This Model is marked by provider as Legacy … Please upgrade
    to an active model"). All of those are account/model-access state
    the caller resolves by enabling the model, so they share the token
    rather than degrading to the generic ``generation_failed``."""
    code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code", "")
    if code in ("ThrottlingException", "TooManyRequestsException"):
        return "rate_limit"
    if code in ("AccessDeniedException", "ResourceNotFoundException"):
        return "model_access_denied"
    return "generation_failed"


def _invoke_nova_canvas(prompt: str, width: int, height: int) -> tuple[str | None, str | None]:
    """Invoke Nova Canvas ``TEXT_IMAGE`` and return ``(b64, error_type)``.

    Exactly one of the two is non-None: ``(b64, None)`` on success,
    ``(None, error_type)`` on failure. Synchronous (boto3 ``invoke_model``
    is blocking) — the async ``generate_image`` wrapper runs this off the
    event loop via ``asyncio.to_thread``.

    Content-filter rejections come back as an empty ``images`` list with
    an ``error`` field (per the Nova user guide's RAI note) → mapped to
    ``content_filtered``."""
    model_id = os.environ.get("STARTER_IMAGE_GEN_MODEL", _DEFAULT_MODEL_ID)
    body = {
        "taskType": "TEXT_IMAGE",
        "textToImageParams": {"text": prompt[:_PROMPT_MAX_CHARS]},
        "imageGenerationConfig": {
            "width": width,
            "height": height,
            "quality": "standard",
            "numberOfImages": 1,
        },
    }
    client = _get_bedrock_runtime_client()
    try:
        resp = client.invoke_model(modelId=model_id, body=json.dumps(body).encode())
    except botocore.exceptions.ClientError as exc:
        error_type = _classify_client_error(exc)
        logger.warning("generate_image.client_error type=%s prompt_len=%d", error_type, len(prompt))
        return None, error_type
    except botocore.exceptions.BotoCoreError as exc:
        # Transport-level failures (endpoint/read/connect timeouts). Without
        # this catch they'd bubble out of the @tool and crash the chassis.
        logger.warning("generate_image.transport_error %r prompt_len=%d", exc, len(prompt))
        return None, "generation_failed"
    try:
        payload = json.loads(resp["body"].read())
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("generate_image.invalid_payload %r prompt_len=%d", exc, len(prompt))
        return None, "generation_failed"
    images = payload.get("images") or []
    if not images:
        # Fully blocked by the RAI content filter (numberOfImages=1, so an
        # empty list means the single image was blocked). The ``error``
        # field carries Nova's reason but is NOT surfaced verbatim — it can
        # echo prompt content (S5145).
        logger.warning("generate_image.content_filtered prompt_len=%d", len(prompt))
        return None, "content_filtered"
    return images[0], None


def _sink_for(agent: Any) -> list[dict[str, str]]:
    """Return (creating if absent) the per-turn generated-image sink on
    the invoking Agent.

    Lazily initialised so a turn that never calls ``generate_image`` leaves
    no attribute behind (``chats.py`` reads it with a ``[]`` default)."""
    sink = getattr(agent, GENERATED_IMAGE_SINK_ATTR, None)
    if sink is None:
        sink = []
        setattr(agent, GENERATED_IMAGE_SINK_ATTR, sink)
    return sink


@tool(context=True)
async def generate_image(
    prompt: str,
    aspect_ratio: str = "1:1",
    *,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Generate an image from a text description using Amazon Nova Canvas.

    Use when the user asks you to create, draw, design, illustrate, or
    imagine a picture — concept art, a scene, a character, a logo, a
    product mockup, or any photographic / artistic visual. For
    architecture or flow diagrams prefer a ```mermaid``` fence; for data
    plots prefer code_exec with matplotlib. This tool is for generative
    imagery, not charts.

    The generated image is saved and rendered inline for the user
    automatically — you do NOT need to embed it in your reply, and a
    markdown image link (``![...](...)``) will NOT resolve. Just call the
    tool, then briefly describe what you created.

    Args:
        prompt: A vivid, detailed description of the image — subject,
            setting, style, lighting, mood. Use positive phrasing:
            describe what you WANT to see, not what to leave out.
        aspect_ratio: One of "1:1" (square, the default), "16:9"
            (landscape), "9:16" (portrait), "4:3", or "3:4". Any other
            value falls back to "1:1".
    """
    width, height = _resolve_dimensions(aspect_ratio)
    b64, error_type = await asyncio.to_thread(_invoke_nova_canvas, prompt, width, height)
    await record_image_gen_outcome(success=b64 is not None)
    if b64 is None:
        return _error_result(error_type or "generation_failed")
    _sink_for(tool_context.agent).append(
        {
            "tool_use_id": tool_context.tool_use["toolUseId"],
            "b64": b64,
            "mime": _OUTPUT_MIME,
            "title": _title_from_prompt(prompt),
        }
    )
    logger.info(
        "generate_image.generated tool_use_id_hash=%s width=%d height=%d",
        fingerprint_id(tool_context.tool_use["toolUseId"]),
        width,
        height,
    )
    return {
        "status": "success",
        "content": [
            {
                "text": (
                    f"Image generated ({width}x{height}) and rendered inline "
                    "for the user. Briefly describe what you created."
                )
            }
        ],
    }
