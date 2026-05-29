# Copyright (c) 2026 John Carter. All rights reserved.
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

from channel.agents.bedrock import (
    BedrockMessage,
    ConverseRequest,
    ConverseResponse,
    _bedrock_client,
    converse,
    converse_stream,
    get_model_id,
)


def setup_function() -> None:
    _bedrock_client.cache_clear()


def teardown_function() -> None:
    _bedrock_client.cache_clear()


def test_get_model_id_default() -> None:
    env = {k: v for k, v in os.environ.items() if k != "BEDROCK_MODEL_ID"}
    with patch.dict(os.environ, env, clear=True):
        assert get_model_id() == "anthropic.claude-sonnet-4-6"


def test_get_model_id_env_override() -> None:
    with patch.dict(os.environ, {"BEDROCK_MODEL_ID": "anthropic.claude-haiku-4-5-20251001-v1:0"}):
        assert get_model_id() == "anthropic.claude-haiku-4-5-20251001-v1:0"


def test_bedrock_client_passes_region_when_set() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("AWS_REGION", "AWS_DEFAULT_REGION")}
    with (
        patch.dict(os.environ, {**env, "AWS_REGION": "eu-west-1"}, clear=True),
        patch("boto3.client", return_value=MagicMock()) as mock_boto,
    ):
        _bedrock_client()
        mock_boto.assert_called_once_with("bedrock-runtime", region_name="eu-west-1")


def test_bedrock_client_omits_region_when_unset() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("AWS_REGION", "AWS_DEFAULT_REGION")}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("boto3.client", return_value=MagicMock()) as mock_boto,
    ):
        _bedrock_client()
        mock_boto.assert_called_once_with("bedrock-runtime")


def test_bedrock_client_falls_back_to_default_region() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("AWS_REGION", "AWS_DEFAULT_REGION")}
    with (
        patch.dict(os.environ, {**env, "AWS_DEFAULT_REGION": "ap-southeast-1"}, clear=True),
        patch("boto3.client", return_value=MagicMock()) as mock_boto,
    ):
        _bedrock_client()
        mock_boto.assert_called_once_with("bedrock-runtime", region_name="ap-southeast-1")


def _mock_converse_response(text: str = "Hello!") -> dict:
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": 10, "outputTokens": 5},
        "stopReason": "end_turn",
    }


def test_converse_success() -> None:
    mock_client = MagicMock()
    mock_client.converse.return_value = _mock_converse_response("Hi there!")

    with patch("channel.agents.bedrock._bedrock_client", return_value=mock_client):
        result = converse(ConverseRequest(messages=[BedrockMessage(role="user", content="Hello")]))

    assert isinstance(result, ConverseResponse)
    assert result.content == "Hi there!"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.stop_reason == "end_turn"


def test_converse_with_system_prompt() -> None:
    mock_client = MagicMock()
    mock_client.converse.return_value = _mock_converse_response("Got it.")

    with patch("channel.agents.bedrock._bedrock_client", return_value=mock_client):
        converse(
            ConverseRequest(
                messages=[BedrockMessage(role="user", content="Hi")],
                system="You are a helpful assistant.",
            )
        )

    call_kwargs = mock_client.converse.call_args[1]
    assert "system" in call_kwargs
    assert call_kwargs["system"] == [{"text": "You are a helpful assistant."}]


def test_converse_without_system_prompt_omits_key() -> None:
    mock_client = MagicMock()
    mock_client.converse.return_value = _mock_converse_response()

    with patch("channel.agents.bedrock._bedrock_client", return_value=mock_client):
        converse(ConverseRequest(messages=[BedrockMessage(role="user", content="Hi")]))

    call_kwargs = mock_client.converse.call_args[1]
    assert "system" not in call_kwargs


def test_converse_message_shape() -> None:
    mock_client = MagicMock()
    mock_client.converse.return_value = _mock_converse_response()

    with patch("channel.agents.bedrock._bedrock_client", return_value=mock_client):
        converse(
            ConverseRequest(
                messages=[
                    BedrockMessage(role="user", content="Hello"),
                    BedrockMessage(role="assistant", content="Hi"),
                    BedrockMessage(role="user", content="Bye"),
                ]
            )
        )

    call_kwargs = mock_client.converse.call_args[1]
    assert call_kwargs["messages"] == [
        {"role": "user", "content": [{"text": "Hello"}]},
        {"role": "assistant", "content": [{"text": "Hi"}]},
        {"role": "user", "content": [{"text": "Bye"}]},
    ]


# ---------------------------------------------------------------------------
# converse_stream
# ---------------------------------------------------------------------------


def _stream_events(*events) -> dict:
    return {"stream": list(events)}


def test_converse_stream_yields_delta_and_done() -> None:
    mock_client = MagicMock()
    mock_client.converse_stream.return_value = _stream_events(
        {"contentBlockDelta": {"delta": {"text": "Hello"}}},
        {"contentBlockDelta": {"delta": {"text": " world"}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5}}},
    )

    with patch("channel.agents.bedrock._bedrock_client", return_value=mock_client):
        chunks = list(
            converse_stream(ConverseRequest(messages=[BedrockMessage(role="user", content="Hi")]))
        )

    assert len(chunks) == 3
    assert json.loads(chunks[0][len("data: ") :]) == {"type": "delta", "text": "Hello"}
    assert json.loads(chunks[1][len("data: ") :]) == {"type": "delta", "text": " world"}
    done = json.loads(chunks[2][len("data: ") :])
    assert done["type"] == "done"
    assert done["stop_reason"] == "end_turn"
    assert done["input_tokens"] == 10
    assert done["output_tokens"] == 5


def test_converse_stream_sse_format() -> None:
    mock_client = MagicMock()
    mock_client.converse_stream.return_value = _stream_events(
        {"contentBlockDelta": {"delta": {"text": "Hi"}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1}}},
    )

    with patch("channel.agents.bedrock._bedrock_client", return_value=mock_client):
        chunks = list(
            converse_stream(ConverseRequest(messages=[BedrockMessage(role="user", content="X")]))
        )

    for chunk in chunks:
        assert chunk.startswith("data: ")
        assert chunk.endswith("\n\n")


def test_converse_stream_with_system_prompt() -> None:
    mock_client = MagicMock()
    mock_client.converse_stream.return_value = _stream_events(
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 2}}},
    )

    with patch("channel.agents.bedrock._bedrock_client", return_value=mock_client):
        list(
            converse_stream(
                ConverseRequest(
                    messages=[BedrockMessage(role="user", content="Hi")],
                    system="Be brief.",
                )
            )
        )

    call_kwargs = mock_client.converse_stream.call_args[1]
    assert call_kwargs["system"] == [{"text": "Be brief."}]


def test_converse_stream_skips_empty_text_delta() -> None:
    mock_client = MagicMock()
    mock_client.converse_stream.return_value = _stream_events(
        {"contentBlockDelta": {"delta": {}}},  # no "text" key
        {"contentBlockDelta": {"delta": {"text": ""}}},  # empty string
        {"contentBlockDelta": {"delta": {"text": "ok"}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 2, "outputTokens": 1}}},
    )

    with patch("channel.agents.bedrock._bedrock_client", return_value=mock_client):
        chunks = list(
            converse_stream(ConverseRequest(messages=[BedrockMessage(role="user", content="X")]))
        )

    # Only "ok" delta + done
    assert len(chunks) == 2
    assert json.loads(chunks[0][len("data: ") :])["text"] == "ok"
