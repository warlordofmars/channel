# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the ``generate_image`` Nova Canvas tool (#279).

The tool does no rendering itself — it invokes Bedrock ``InvokeModel``
on Nova Canvas, decodes the base64 PNG, stashes it on the invoking
Agent's per-turn sink, and returns a text-only ``ToolResult``. All AWS
calls are mocked. The error_type taxonomy, the sink-harvest channel,
and the EMF invocation counter are covered here."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import botocore.exceptions
import pytest


@pytest.fixture(autouse=True)
def _clear_lazy_caches():
    """Reset the ``_get_bedrock_runtime_client`` lru_cache between tests so
    a real boto3 client from a sibling test can't leak across — same
    pattern as ``test_tools_code_exec.py``."""
    from channel.agents.tools.generate_image import _get_bedrock_runtime_client

    _get_bedrock_runtime_client.cache_clear()
    yield
    _get_bedrock_runtime_client.cache_clear()


def _fake_invoke_response(payload: dict) -> dict:
    """Build a boto3 ``invoke_model`` response shape: ``{"body": <stream>}``."""
    stream = MagicMock()
    stream.read.return_value = json.dumps(payload).encode()
    return {"body": stream}


def _b64(data: bytes = b"PNGDATA") -> str:
    return base64.b64encode(data).decode()


def _fake_context(tool_use_id: str = "tu-1", agent: Any | None = None) -> SimpleNamespace:
    """A minimal stand-in for Strands' ``ToolContext``.

    ``DecoratedFunctionTool.__call__`` passes injected kwargs straight
    through to the wrapped function, so the tool only touches
    ``tool_context.tool_use["toolUseId"]`` and ``tool_context.agent`` — a
    SimpleNamespace is enough."""
    return SimpleNamespace(
        tool_use={"toolUseId": tool_use_id},
        agent=agent if agent is not None else SimpleNamespace(),
    )


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        ("1:1", (1024, 1024)),
        ("16:9", (1280, 720)),
        ("9:16", (720, 1280)),
        ("4:3", (1024, 768)),
        ("3:4", (768, 1024)),
    ],
)
def test_resolve_dimensions_known_ratios(ratio, expected):
    from channel.agents.tools.generate_image import _resolve_dimensions

    assert _resolve_dimensions(ratio) == expected


def test_resolve_dimensions_unknown_falls_back_to_square():
    """A ratio the model invented falls back to 1:1 — never raises."""
    from channel.agents.tools.generate_image import _resolve_dimensions

    assert _resolve_dimensions("banana") == (1024, 1024)


def test_all_dimensions_satisfy_nova_constraints():
    """Every mapped resolution: sides 320-4096 and divisible by 16,
    aspect 1:4..4:1, total pixels < 4,194,304 (Nova user-guide caps)."""
    from channel.agents.tools.generate_image import _ASPECT_RATIO_DIMENSIONS

    for width, height in _ASPECT_RATIO_DIMENSIONS.values():
        assert 320 <= width <= 4096 and width % 16 == 0
        assert 320 <= height <= 4096 and height % 16 == 0
        assert 0.25 <= width / height <= 4.0
        assert width * height < 4_194_304


def test_title_from_prompt_uses_first_non_empty_line():
    from channel.agents.tools.generate_image import _title_from_prompt

    assert _title_from_prompt("\n  \nA red bicycle\nsecond line") == "A red bicycle"


def test_title_from_prompt_truncates_long_line():
    from channel.agents.tools.generate_image import _TITLE_MAX_CHARS, _title_from_prompt

    title = _title_from_prompt("x" * 200)
    assert len(title) == _TITLE_MAX_CHARS


def test_title_from_prompt_blank_falls_back():
    from channel.agents.tools.generate_image import _title_from_prompt

    assert _title_from_prompt("   \n\n  ") == "Generated image"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("ThrottlingException", "rate_limit"),
        ("TooManyRequestsException", "rate_limit"),
        ("AccessDeniedException", "model_access_denied"),
        ("ValidationException", "generation_failed"),
        ("SomethingElse", "generation_failed"),
    ],
)
def test_classify_client_error(code, expected):
    from channel.agents.tools.generate_image import _classify_client_error

    exc = botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": "boom"}}, "InvokeModel"
    )
    assert _classify_client_error(exc) == expected


# --------------------------------------------------------------------------
# _invoke_nova_canvas — the sync Bedrock call
# --------------------------------------------------------------------------


def test_invoke_success_returns_b64(monkeypatch):
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response({"images": [_b64()]})
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_nova_canvas

    b64, error = _invoke_nova_canvas("a cat", 1024, 1024)
    assert error is None
    assert b64 == _b64()


def test_invoke_builds_text_image_request_body(monkeypatch):
    """Request body carries the TEXT_IMAGE task + resolved dimensions +
    numberOfImages=1, and the default model id."""
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response({"images": [_b64()]})
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    monkeypatch.delenv("STARTER_IMAGE_GEN_MODEL", raising=False)
    from channel.agents.tools.generate_image import _invoke_nova_canvas

    _invoke_nova_canvas("a photo of a fox", 1280, 720)

    kwargs = fake_client.invoke_model.call_args.kwargs
    assert kwargs["modelId"] == "amazon.nova-canvas-v1:0"
    body = json.loads(kwargs["body"])
    assert body["taskType"] == "TEXT_IMAGE"
    assert body["textToImageParams"]["text"] == "a photo of a fox"
    assert body["imageGenerationConfig"]["width"] == 1280
    assert body["imageGenerationConfig"]["height"] == 720
    assert body["imageGenerationConfig"]["numberOfImages"] == 1


def test_invoke_honours_model_override_env(monkeypatch):
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response({"images": [_b64()]})
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    monkeypatch.setenv("STARTER_IMAGE_GEN_MODEL", "amazon.nova-canvas-vNEXT:0")
    from channel.agents.tools.generate_image import _invoke_nova_canvas

    _invoke_nova_canvas("x", 1024, 1024)
    assert fake_client.invoke_model.call_args.kwargs["modelId"] == "amazon.nova-canvas-vNEXT:0"


def test_invoke_truncates_overlong_prompt(monkeypatch):
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response({"images": [_b64()]})
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _PROMPT_MAX_CHARS, _invoke_nova_canvas

    _invoke_nova_canvas("z" * 5000, 1024, 1024)
    body = json.loads(fake_client.invoke_model.call_args.kwargs["body"])
    assert len(body["textToImageParams"]["text"]) == _PROMPT_MAX_CHARS


def test_invoke_empty_images_is_content_filtered(monkeypatch):
    """A fully-blocked generation returns ``images: []`` + ``error`` →
    ``content_filtered``."""
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response(
        {"images": [], "error": "blocked by responsible AI policy"}
    )
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_nova_canvas

    b64, error = _invoke_nova_canvas("something disallowed", 1024, 1024)
    assert b64 is None
    assert error == "content_filtered"


def test_invoke_client_error_maps_error_type(monkeypatch):
    fake_client = MagicMock()
    fake_client.invoke_model.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no access"}}, "InvokeModel"
    )
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_nova_canvas

    b64, error = _invoke_nova_canvas("x", 1024, 1024)
    assert b64 is None
    assert error == "model_access_denied"


def test_invoke_boto_core_error_is_generation_failed(monkeypatch):
    fake_client = MagicMock()
    fake_client.invoke_model.side_effect = botocore.exceptions.ReadTimeoutError(
        endpoint_url="https://bedrock-runtime"
    )
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_nova_canvas

    b64, error = _invoke_nova_canvas("x", 1024, 1024)
    assert b64 is None
    assert error == "generation_failed"


def test_invoke_invalid_response_payload_is_generation_failed(monkeypatch):
    """A non-JSON / malformed InvokeModel body → structured error, not a
    chassis crash."""
    fake_client = MagicMock()
    stream = MagicMock()
    stream.read.return_value = b"not json"
    fake_client.invoke_model.return_value = {"body": stream}
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_nova_canvas

    b64, error = _invoke_nova_canvas("x", 1024, 1024)
    assert b64 is None
    assert error == "generation_failed"


def test_invoke_missing_body_key_is_generation_failed(monkeypatch):
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = {}  # no "body"
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_nova_canvas

    b64, error = _invoke_nova_canvas("x", 1024, 1024)
    assert b64 is None
    assert error == "generation_failed"


# --------------------------------------------------------------------------
# generate_image — the async @tool wrapper (sink + metric + ToolResult)
# --------------------------------------------------------------------------


def _patch_metric(monkeypatch) -> list[bool]:
    """Replace ``record_image_gen_outcome`` with a capturing async double.
    Returns the list that receives each call's ``success`` value."""
    recorded: list[bool] = []

    async def _fake(success: bool) -> None:
        recorded.append(success)

    monkeypatch.setattr("channel.agents.tools.generate_image.record_image_gen_outcome", _fake)
    return recorded


async def test_generate_image_success_appends_to_sink(monkeypatch):
    recorded = _patch_metric(monkeypatch)
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._invoke_nova_canvas",
        lambda prompt, w, h: (_b64(b"IMG"), None),
    )
    from channel.agents.tools.generate_image import (
        GENERATED_IMAGE_SINK_ATTR,
        generate_image,
    )

    agent = SimpleNamespace()
    ctx = _fake_context(tool_use_id="tu-42", agent=agent)
    result = await generate_image(prompt="a red bicycle", aspect_ratio="16:9", tool_context=ctx)

    assert result["status"] == "success"
    assert "rendered inline" in result["content"][0]["text"]
    sink = getattr(agent, GENERATED_IMAGE_SINK_ATTR)
    assert sink == [
        {
            "tool_use_id": "tu-42",
            "b64": _b64(b"IMG"),
            "mime": "image/png",
            "title": "a red bicycle",
        }
    ]
    assert recorded == [True]


async def test_generate_image_error_returns_tool_result_shape(monkeypatch):
    recorded = _patch_metric(monkeypatch)
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._invoke_nova_canvas",
        lambda prompt, w, h: (None, "content_filtered"),
    )
    from channel.agents.tools.generate_image import (
        GENERATED_IMAGE_SINK_ATTR,
        generate_image,
    )

    agent = SimpleNamespace()
    ctx = _fake_context(agent=agent)
    result = await generate_image(prompt="x", tool_context=ctx)

    # ToolResult-shaped error so translate_event extracts the reason.
    assert result == {"status": "error", "content": [{"text": "content_filtered"}]}
    # No sink entry (and the attribute is never created) for a failed gen.
    assert not hasattr(agent, GENERATED_IMAGE_SINK_ATTR)
    assert recorded == [False]


async def test_generate_image_error_defaults_error_type(monkeypatch):
    """Defensive: a None error_type still yields a bounded token."""
    _patch_metric(monkeypatch)
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._invoke_nova_canvas",
        lambda prompt, w, h: (None, None),
    )
    from channel.agents.tools.generate_image import generate_image

    result = await generate_image(prompt="x", tool_context=_fake_context())
    assert result == {"status": "error", "content": [{"text": "generation_failed"}]}


async def test_generate_image_reuses_existing_sink(monkeypatch):
    """A second generation in the same turn appends to the same list."""
    _patch_metric(monkeypatch)
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._invoke_nova_canvas",
        lambda prompt, w, h: (_b64(), None),
    )
    from channel.agents.tools.generate_image import (
        GENERATED_IMAGE_SINK_ATTR,
        generate_image,
    )

    agent = SimpleNamespace()
    await generate_image(prompt="one", tool_context=_fake_context("tu-1", agent))
    await generate_image(prompt="two", tool_context=_fake_context("tu-2", agent))

    sink = getattr(agent, GENERATED_IMAGE_SINK_ATTR)
    assert [e["tool_use_id"] for e in sink] == ["tu-1", "tu-2"]
    assert [e["title"] for e in sink] == ["one", "two"]


def test_generate_image_is_a_strands_tool():
    from channel.agents.tools.generate_image import generate_image

    assert hasattr(generate_image, "tool_spec")
    assert generate_image.tool_name == "generate_image"
