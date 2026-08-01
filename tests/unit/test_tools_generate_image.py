# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the ``generate_image`` Stable Image Core tool (#279).

The tool does no rendering itself — it invokes Bedrock ``InvokeModel``
on Stability Stable Image Core, decodes the base64 PNG, stashes it on
the invoking Agent's per-turn sink, and returns a text-only
``ToolResult``. All AWS calls are mocked. The error_type taxonomy
(including the ``finish_reasons``-based content-filter detection), the
sink-harvest channel, and the EMF invocation counter are covered here."""

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


def _success_payload(data: bytes = b"PNGDATA") -> dict:
    """A successful Stability response: base64 image, null finish_reason."""
    return {"images": [_b64(data)], "seeds": [2130420379], "finish_reasons": [None]}


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
    "ratio",
    ["16:9", "1:1", "21:9", "2:3", "3:2", "4:5", "5:4", "9:16", "9:21"],
)
def test_resolve_aspect_ratio_supported_passthrough(ratio):
    """Every value in Stability's enum passes straight through."""
    from channel.agents.tools.generate_image import _resolve_aspect_ratio

    assert _resolve_aspect_ratio(ratio) == ratio


@pytest.mark.parametrize("ratio", ["4:3", "3:4", "banana", "", "1:2"])
def test_resolve_aspect_ratio_unsupported_falls_back_to_square(ratio):
    """A ratio outside Stability's enum — including Nova's old 4:3 / 3:4 —
    falls back to 1:1, never raises."""
    from channel.agents.tools.generate_image import _resolve_aspect_ratio

    assert _resolve_aspect_ratio(ratio) == "1:1"


def test_advertised_ratios_are_all_supported():
    """Every aspect ratio the tool docstring advertises must be a real
    Stability enum value (else the model would pick one that silently
    coerces to 1:1)."""
    from channel.agents.tools.generate_image import _SUPPORTED_ASPECT_RATIOS

    advertised = {"1:1", "16:9", "9:16", "3:2", "2:3"}
    assert advertised <= _SUPPORTED_ASPECT_RATIOS


def test_image_gen_region_defaults_to_us_west_2(monkeypatch):
    """Unset defaults to us-west-2 — the only region offering the Stability
    generators (the app's sole cross-region service dependency)."""
    from channel.agents.tools.generate_image import _image_gen_region

    monkeypatch.delenv("CHANNEL_IMAGE_GEN_REGION", raising=False)
    assert _image_gen_region() == "us-west-2"


def test_image_gen_region_env_overrides(monkeypatch):
    """CHANNEL_IMAGE_GEN_REGION overrides the default region."""
    from channel.agents.tools.generate_image import _image_gen_region

    monkeypatch.setenv("CHANNEL_IMAGE_GEN_REGION", "eu-central-1")
    assert _image_gen_region() == "eu-central-1"


def test_image_gen_region_empty_falls_back_to_default(monkeypatch):
    """An empty override falls back to the us-west-2 default."""
    from channel.agents.tools.generate_image import _image_gen_region

    monkeypatch.setenv("CHANNEL_IMAGE_GEN_REGION", "")
    assert _image_gen_region() == "us-west-2"


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
        # Bedrock surfaces a not-access-enabled Stability model (and an
        # unknown model id) as ResourceNotFoundException, not
        # AccessDeniedException — both are account/model-access state.
        ("ResourceNotFoundException", "model_access_denied"),
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


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("Filter reason: prompt", "content_filtered"),
        ("Filter reason: output image", "content_filtered"),
        ("Filter reason: input image", "content_filtered"),
        ("Inference error", "generation_failed"),
        ("something unexpected", "generation_failed"),
    ],
)
def test_classify_finish_reason(reason, expected):
    from channel.agents.tools.generate_image import _classify_finish_reason

    assert _classify_finish_reason(reason) == expected


# --------------------------------------------------------------------------
# _invoke_image_model — the sync Bedrock call
# --------------------------------------------------------------------------


def test_invoke_success_returns_b64(monkeypatch):
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response(_success_payload())
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_image_model

    b64, error = _invoke_image_model("a cat", "1:1")
    assert error is None
    assert b64 == _b64()


def test_invoke_builds_stability_request_body(monkeypatch):
    """Request body is Stability's flat shape (prompt / aspect_ratio /
    output_format=png) and carries the default model id."""
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response(_success_payload())
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    monkeypatch.delenv("CHANNEL_IMAGE_GEN_MODEL", raising=False)
    from channel.agents.tools.generate_image import _invoke_image_model

    _invoke_image_model("a photo of a fox", "16:9")

    kwargs = fake_client.invoke_model.call_args.kwargs
    assert kwargs["modelId"] == "stability.stable-image-core-v1:1"
    assert kwargs["contentType"] == "application/json"
    assert kwargs["accept"] == "application/json"
    body = json.loads(kwargs["body"])
    assert body == {
        "prompt": "a photo of a fox",
        "aspect_ratio": "16:9",
        "output_format": "png",
    }


def test_invoke_honours_model_override_env(monkeypatch):
    """CHANNEL_IMAGE_GEN_MODEL flips to a premium Stability generator with
    no code change (identical request contract)."""
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response(_success_payload())
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    monkeypatch.setenv("CHANNEL_IMAGE_GEN_MODEL", "stability.stable-image-ultra-v1:1")
    from channel.agents.tools.generate_image import _invoke_image_model

    _invoke_image_model("x", "1:1")
    assert (
        fake_client.invoke_model.call_args.kwargs["modelId"] == "stability.stable-image-ultra-v1:1"
    )


def test_invoke_truncates_overlong_prompt(monkeypatch):
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response(_success_payload())
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _PROMPT_MAX_CHARS, _invoke_image_model

    _invoke_image_model("z" * (_PROMPT_MAX_CHARS + 5000), "1:1")
    body = json.loads(fake_client.invoke_model.call_args.kwargs["body"])
    assert len(body["prompt"]) == _PROMPT_MAX_CHARS


def test_invoke_filter_finish_reason_is_content_filtered(monkeypatch):
    """A blocked generation returns a non-null ``Filter reason: *`` first
    finish_reason with ``images`` absent → ``content_filtered``."""
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response(
        {"finish_reasons": ["Filter reason: prompt"], "seeds": [0]}
    )
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_image_model

    b64, error = _invoke_image_model("something disallowed", "1:1")
    assert b64 is None
    assert error == "content_filtered"


def test_invoke_inference_error_finish_reason_is_generation_failed(monkeypatch):
    """A non-filter ``Inference error`` finish_reason → generation_failed."""
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response(
        {"finish_reasons": ["Inference error"], "seeds": [0]}
    )
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_image_model

    b64, error = _invoke_image_model("x", "1:1")
    assert b64 is None
    assert error == "generation_failed"


def test_invoke_no_image_defensive_is_generation_failed(monkeypatch):
    """Null finish_reason but no image (shouldn't happen per the contract)
    → generation_failed, not a crash on an empty index."""
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = _fake_invoke_response(
        {"finish_reasons": [None], "images": [], "seeds": [0]}
    )
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_image_model

    b64, error = _invoke_image_model("x", "1:1")
    assert b64 is None
    assert error == "generation_failed"


def test_invoke_client_error_maps_error_type(monkeypatch):
    fake_client = MagicMock()
    fake_client.invoke_model.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no access"}}, "InvokeModel"
    )
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_image_model

    b64, error = _invoke_image_model("x", "1:1")
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
    from channel.agents.tools.generate_image import _invoke_image_model

    b64, error = _invoke_image_model("x", "1:1")
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
    from channel.agents.tools.generate_image import _invoke_image_model

    b64, error = _invoke_image_model("x", "1:1")
    assert b64 is None
    assert error == "generation_failed"


def test_invoke_missing_body_key_is_generation_failed(monkeypatch):
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = {}  # no "body"
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._get_bedrock_runtime_client",
        lambda: fake_client,
    )
    from channel.agents.tools.generate_image import _invoke_image_model

    b64, error = _invoke_image_model("x", "1:1")
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
        "channel.agents.tools.generate_image._invoke_image_model",
        lambda prompt, ratio: (_b64(b"IMG"), None),
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
    assert "16:9" in result["content"][0]["text"]
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


async def test_generate_image_resolves_unsupported_ratio(monkeypatch):
    """An unsupported aspect ratio is coerced to 1:1 before the invoke, and
    the success text reflects the resolved ratio."""
    _patch_metric(monkeypatch)
    seen: list[str] = []

    def _fake_invoke(prompt, ratio):
        seen.append(ratio)
        return _b64(), None

    monkeypatch.setattr("channel.agents.tools.generate_image._invoke_image_model", _fake_invoke)
    from channel.agents.tools.generate_image import generate_image

    result = await generate_image(prompt="x", aspect_ratio="4:3", tool_context=_fake_context())
    assert seen == ["1:1"]
    assert "1:1" in result["content"][0]["text"]


async def test_generate_image_error_returns_tool_result_shape(monkeypatch):
    recorded = _patch_metric(monkeypatch)
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._invoke_image_model",
        lambda prompt, ratio: (None, "content_filtered"),
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
        "channel.agents.tools.generate_image._invoke_image_model",
        lambda prompt, ratio: (None, None),
    )
    from channel.agents.tools.generate_image import generate_image

    result = await generate_image(prompt="x", tool_context=_fake_context())
    assert result == {"status": "error", "content": [{"text": "generation_failed"}]}


async def test_generate_image_reuses_existing_sink(monkeypatch):
    """A second generation in the same turn appends to the same list."""
    _patch_metric(monkeypatch)
    monkeypatch.setattr(
        "channel.agents.tools.generate_image._invoke_image_model",
        lambda prompt, ratio: (_b64(), None),
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
