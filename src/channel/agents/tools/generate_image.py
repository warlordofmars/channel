# Copyright (c) 2026 John Carter. All rights reserved.
"""``generate_image`` — Stability Stable Image Core image generation (#279).

A single narrow tool: ``generate_image(prompt, aspect_ratio)``. It calls
Bedrock ``InvokeModel`` on Stability AI Stable Image Core
(``stability.stable-image-core-v1:1``), decodes the returned base64 PNG,
and hands the bytes to the post-stream asset pipeline (#326) for durable
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

Content moderation is Bedrock's built-in Stability RAI filter — no
custom layer (decision 3). A blocked generation returns a non-null first
``finish_reasons`` entry (``"Filter reason: prompt"`` /
``"Filter reason: output image"`` / ``"Filter reason: input image"``) with
``images`` absent; the tool then returns a ``ToolResult``-shaped error
whose reason token is ``content_filtered`` (carried in the first content
block's ``text``, NOT an ``error_type`` key on the dict). A non-filter
``"Inference error"`` maps to ``generation_failed``. ``translate_event``
extracts that token into the SSE ``tool_error`` frame's ``error_type``
field, mirroring ``code_exec``'s error-shape contract — so the SPA sees
``error_type="content_filtered"``.

There is NO cost gating — billing is deferred (product decision). The
only controls are the ``STARTER_IMAGE_GEN_ENABLED`` registration
kill-switch (gated in ``chats._build_tool_registry``) and the
``ImageGenInvocations`` / ``ImageGenFailures`` EMF counters
(``record_image_gen_outcome`` emits the invocation counter on every
call and the failure counter on each non-success). All three Stability
text-to-image generators (Core / Ultra / SD3.5 Large) share ONE
InvokeModel request/response contract, so a later switch to a premium
tier is a pure ``STARTER_IMAGE_GEN_MODEL`` env flip — no code change.

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

# Default image model id. Overridable via ``STARTER_IMAGE_GEN_MODEL`` —
# the fallback mechanism to a premium generator, since all three
# Stability text-to-image models (Stable Image Core / Ultra / SD3.5
# Large) share the SAME InvokeModel request/response contract. Verified
# against the AWS Bedrock Stable Image Core request/response docs
# 2026-07-17 — invoked directly by base model id (no cross-region
# inference profile for image models).
_DEFAULT_MODEL_ID = "stability.stable-image-core-v1:1"

# Region the generate_image tool invokes Bedrock in. This is the app's
# FIRST cross-region service dependency: the Channel stack runs entirely
# in us-east-1, but the Stability text-to-image generators are ACTIVE
# only in us-west-2 (absent from us-east-1, and there is no us-east-1
# cross-region inference profile for them), so ONLY this tool's
# bedrock-runtime client targets us-west-2. The returned PNG comes back
# to the us-east-1 Lambda and persists to the us-east-1 assets bucket via
# the unchanged pipeline — data at rest stays in us-east-1. The matching
# IAM foundation-model ARNs in ``channel_stack.py`` are region-pinned to
# us-west-2 to authorize this call. ``STARTER_IMAGE_GEN_REGION`` overrides
# the region (e.g. a personal dev env); default us-west-2.
_IMAGE_GEN_REGION_ENV = "STARTER_IMAGE_GEN_REGION"
_DEFAULT_IMAGE_GEN_REGION = "us-west-2"

# Attribute name of the per-turn sink stashed on the invoking Agent. The
# tool appends ``{"tool_use_id", "b64", "mime", "title"}`` entries; the
# stream path in ``chats.py`` reads this attribute post-stream to persist
# the generated images as assets. Exported so ``chats.py`` reads the same
# name (no stringly-typed drift).
GENERATED_IMAGE_SINK_ATTR = "generated_image_sink"

# Stability's ``aspect_ratio`` enum (verified against the AWS Bedrock
# Stable Image Core text-to-image request docs 2026-07-17). Any caller
# ratio outside this set — including Nova Canvas's old ``4:3`` / ``3:4``,
# which Stability does NOT support — falls back to ``1:1`` rather than
# erroring, the same fail-open posture as the effort-tier fallback in
# ``chat_agent.max_tokens_for_effort``. The tool advertises a curated
# subset of these in its docstring; the full set is accepted so a
# model-chosen valid ratio is never needlessly coerced.
_SUPPORTED_ASPECT_RATIOS: frozenset[str] = frozenset(
    {"16:9", "1:1", "21:9", "2:3", "3:2", "4:5", "5:4", "9:16", "9:21"}
)
_DEFAULT_ASPECT_RATIO = "1:1"

# Stability caps the prompt at 10,000 characters; a longer prompt is a
# guaranteed ValidationException. Truncate rather than burn an invocation
# — 10,000 chars is already far longer than any useful description.
_PROMPT_MAX_CHARS = 10_000

# Asset card title derived from the prompt's first line, capped so the
# inline card stays legible.
_TITLE_MAX_CHARS = 80

# The generated PNG bytes always carry this MIME — we request
# ``output_format: "png"`` so Stability returns PNG.
_OUTPUT_MIME = "image/png"

# boto3's default read timeout is 60s; image generation can exceed that
# for larger / premium requests. 300s matches the streaming-chat Lambda
# timeout envelope.
_READ_TIMEOUT_SECONDS = 300


def _error_result(error_type: str) -> dict[str, Any]:
    """Build a Strands-``ToolResult``-shaped error dict.

    See ``src/channel/agents/tools/web_search.py:_error_result`` for the
    full rationale on why this shape (``{"status": "error", "content":
    [{"text": <reason>}]}``) — not ``{"error_type": ...}`` — is
    load-bearing for the SSE error contract."""
    return {"status": "error", "content": [{"text": error_type}]}


def _image_gen_region() -> str:
    """Region to invoke the image model in — default ``us-west-2``.

    ``STARTER_IMAGE_GEN_REGION`` overrides it (unset or empty → the
    default). See ``_IMAGE_GEN_REGION_ENV`` for why this crosses regions:
    the Stability text-to-image generators are offered only in us-west-2,
    so this tool's client targets us-west-2 even though the rest of the
    stack is us-east-1."""
    return os.environ.get(_IMAGE_GEN_REGION_ENV) or _DEFAULT_IMAGE_GEN_REGION


@functools.lru_cache(maxsize=1)
def _get_bedrock_runtime_client():  # type: ignore[no-untyped-def]
    """Lazy-load the boto3 ``bedrock-runtime`` client with a long read
    timeout.

    Mirrors ``code_exec._get_lambda_client`` / ``web_search._get_exa_search``
    — deferring the boto3 import and client construction until the model
    actually calls ``generate_image`` keeps them off the cold-start path
    for turns that don't generate an image. ``region_name`` comes from the
    ``_image_gen_region`` seam (default ``us-west-2`` — the image models'
    only region; see ``_IMAGE_GEN_REGION_ENV``)."""
    import boto3  # noqa: PLC0415  # pragma: no cover
    from botocore.config import Config  # noqa: PLC0415  # pragma: no cover

    return boto3.client(  # pragma: no cover
        "bedrock-runtime",
        region_name=_image_gen_region(),
        config=Config(read_timeout=_READ_TIMEOUT_SECONDS),
    )


def _resolve_aspect_ratio(aspect_ratio: str) -> str:
    """Return a Stability-supported aspect ratio, falling back to 1:1.

    Any ratio outside Stability's enum (including the model inventing one,
    or a Nova-era ``4:3`` / ``3:4``) falls back to ``1:1`` — never raise on
    a ratio string the model supplied."""
    if aspect_ratio in _SUPPORTED_ASPECT_RATIOS:
        return aspect_ratio
    return _DEFAULT_ASPECT_RATIO


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
    account hasn't enabled the Stability image model — account state, not
    a code bug), and ``generation_failed`` for everything else
    (validation, service errors).

    ``ResourceNotFoundException`` also maps to ``model_access_denied``:
    Bedrock raises it (not ``AccessDeniedException``) both for an
    unknown model id AND for a model the account can't currently invoke
    — including a Stability generator that has not been access-enabled in
    the Bedrock console. All of those are account/model-access state the
    caller resolves by enabling the model, so they share the token rather
    than degrading to the generic ``generation_failed``."""
    code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code", "")
    if code in ("ThrottlingException", "TooManyRequestsException"):
        return "rate_limit"
    if code in ("AccessDeniedException", "ResourceNotFoundException"):
        return "model_access_denied"
    return "generation_failed"


def _classify_finish_reason(reason: str) -> str:
    """Map a Stability ``finish_reasons[0]`` string to an error token.

    Stability signals a content block via ``"Filter reason: prompt"`` /
    ``"Filter reason: output image"`` / ``"Filter reason: input image"``
    → ``content_filtered``. Any other non-null reason (e.g.
    ``"Inference error"``) → ``generation_failed``. The reason string is a
    bounded enum (model metadata, not prompt content) so it is safe to log
    — but it is never echoed verbatim to the model / SPA; only the bounded
    token is surfaced."""
    if reason.startswith("Filter reason:"):
        return "content_filtered"
    return "generation_failed"


def _invoke_image_model(prompt: str, aspect_ratio: str) -> tuple[str | None, str | None]:
    """Invoke the Stability image model and return ``(b64, error_type)``.

    Exactly one of the two is non-None: ``(b64, None)`` on success,
    ``(None, error_type)`` on failure. Synchronous (boto3 ``invoke_model``
    is blocking) — the async ``generate_image`` wrapper runs this off the
    event loop via ``asyncio.to_thread``.

    The flat request body (``{prompt, aspect_ratio, output_format}``) and
    the ``{images, seeds, finish_reasons}`` response are shared by all
    three Stability text-to-image generators, so ``STARTER_IMAGE_GEN_MODEL``
    can switch to Ultra / SD3.5 Large with no code change. A content-filter
    rejection comes back as a non-null ``finish_reasons[0]`` with ``images``
    absent → mapped via ``_classify_finish_reason``."""
    model_id = os.environ.get("STARTER_IMAGE_GEN_MODEL", _DEFAULT_MODEL_ID)
    body = {
        "prompt": prompt[:_PROMPT_MAX_CHARS],
        "aspect_ratio": aspect_ratio,
        "output_format": "png",
    }
    client = _get_bedrock_runtime_client()
    try:
        # ``contentType`` / ``accept`` are explicit (both "application/json")
        # to match the AWS Bedrock InvokeModel docs; boto3 defaults them to
        # the same value, but pinning them is self-documenting and immune to
        # a future default change.
        resp = client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body).encode(),
        )
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
    # Stability puts a non-null first finish_reason on a blocked / failed
    # generation (``[null]`` on success). Its raw text is NOT surfaced
    # verbatim — only the bounded token — but is safe to log (S5145: it's
    # model metadata, never prompt content).
    finish_reasons = payload.get("finish_reasons") or []
    if finish_reasons and finish_reasons[0] is not None:
        error_type = _classify_finish_reason(str(finish_reasons[0]))
        logger.warning("generate_image.blocked type=%s prompt_len=%d", error_type, len(prompt))
        return None, error_type
    images = payload.get("images") or []
    if not images:
        # Defensive: no finish_reason but also no image — treat as a failed
        # generation rather than crashing on an empty index.
        logger.warning("generate_image.no_image prompt_len=%d", len(prompt))
        return None, "generation_failed"
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
    """Generate an image from a text description using Stable Image Core.

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
            (landscape), "9:16" (portrait), "3:2", or "2:3". Any other
            value falls back to "1:1".
    """
    ratio = _resolve_aspect_ratio(aspect_ratio)
    b64, error_type = await asyncio.to_thread(_invoke_image_model, prompt, ratio)
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
        "generate_image.generated tool_use_id_hash=%s aspect_ratio=%s",
        fingerprint_id(tool_context.tool_use["toolUseId"]),
        ratio,
    )
    return {
        "status": "success",
        "content": [
            {
                "text": (
                    f"Image generated ({ratio}) and rendered inline "
                    "for the user. Briefly describe what you created."
                )
            }
        ],
    }
