# Copyright (c) 2026 John Carter. All rights reserved.
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from channel.agents.inline_agent import (
    InlineAgentRequest,
    InlineAgentResponse,
    _agent_client,
    _bedrock_session_id,
    invoke,
    invoke_stream,
)


def setup_function() -> None:
    _agent_client.cache_clear()


def teardown_function() -> None:
    _agent_client.cache_clear()


# ---------------------------------------------------------------------------
# _bedrock_session_id
# ---------------------------------------------------------------------------


def test_bedrock_session_id_namespaces_to_user() -> None:
    assert _bedrock_session_id("user1", "sess-abc") == "user1:sess-abc"


def test_bedrock_session_id_different_users_dont_collide() -> None:
    assert _bedrock_session_id("user1", "x") != _bedrock_session_id("user2", "x")


# ---------------------------------------------------------------------------
# _agent_client caching
# ---------------------------------------------------------------------------


def test_agent_client_passes_region_when_set() -> None:
    import os
    from unittest.mock import patch as p

    env = {k: v for k, v in os.environ.items() if k not in ("AWS_REGION", "AWS_DEFAULT_REGION")}
    with (
        p("os.environ", {**env, "AWS_REGION": "eu-west-1"}),
        p("boto3.client", return_value=MagicMock()) as mock_boto,
    ):
        _agent_client()
        mock_boto.assert_called_once_with("bedrock-agent-runtime", region_name="eu-west-1")


def test_agent_client_omits_region_when_unset() -> None:
    import os
    from unittest.mock import patch as p

    env = {k: v for k, v in os.environ.items() if k not in ("AWS_REGION", "AWS_DEFAULT_REGION")}
    with (
        p("os.environ", env),
        p("boto3.client", return_value=MagicMock()) as mock_boto,
    ):
        _agent_client()
        mock_boto.assert_called_once_with("bedrock-agent-runtime")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_completion(*chunks: str) -> dict:
    """Build a fake completion event stream from text chunks."""
    events = [{"chunk": {"bytes": c.encode()}} for c in chunks]
    return {"completion": iter(events)}


def _mock_completion_with_empty() -> dict:
    """Completion stream with an empty bytes chunk (should be skipped)."""
    return {
        "completion": iter(
            [
                {"chunk": {"bytes": b""}},
                {"chunk": {"bytes": b"hi"}},
                {"chunk": {}},  # missing "bytes" key
            ]
        )
    }


# ---------------------------------------------------------------------------
# invoke (non-streaming)
# ---------------------------------------------------------------------------


def test_invoke_returns_concatenated_chunks() -> None:
    mock_client = MagicMock()
    mock_client.invoke_inline_agent.return_value = _mock_completion("Hello", " world")

    with patch("channel.agents.inline_agent._agent_client", return_value=mock_client):
        result = invoke(
            InlineAgentRequest(message="Hi", session_id="s1"),
            user_id="user1",
        )

    assert isinstance(result, InlineAgentResponse)
    assert result.reply == "Hello world"
    assert result.session_id == "s1"


def test_invoke_generates_session_id_when_omitted() -> None:
    mock_client = MagicMock()
    mock_client.invoke_inline_agent.return_value = _mock_completion("ok")

    with patch("channel.agents.inline_agent._agent_client", return_value=mock_client):
        result = invoke(InlineAgentRequest(message="Hi"), user_id="user1")

    assert result.session_id  # a UUID was generated
    assert len(result.session_id) == 36  # UUID format


def test_invoke_passes_instruction() -> None:
    mock_client = MagicMock()
    mock_client.invoke_inline_agent.return_value = _mock_completion("ok")

    with patch("channel.agents.inline_agent._agent_client", return_value=mock_client):
        invoke(
            InlineAgentRequest(message="Hi", session_id="s1", instruction="Be brief."),
            user_id="user1",
        )

    call_kwargs = mock_client.invoke_inline_agent.call_args[1]
    assert call_kwargs["agentInstruction"] == "Be brief."


def test_invoke_omits_instruction_key_when_not_set() -> None:
    mock_client = MagicMock()
    mock_client.invoke_inline_agent.return_value = _mock_completion("ok")

    with patch("channel.agents.inline_agent._agent_client", return_value=mock_client):
        invoke(InlineAgentRequest(message="Hi", session_id="s1"), user_id="user1")

    call_kwargs = mock_client.invoke_inline_agent.call_args[1]
    assert "agentInstruction" not in call_kwargs


def test_invoke_namespaces_session_id() -> None:
    mock_client = MagicMock()
    mock_client.invoke_inline_agent.return_value = _mock_completion("ok")

    with patch("channel.agents.inline_agent._agent_client", return_value=mock_client):
        invoke(
            InlineAgentRequest(message="Hi", session_id="my-session"),
            user_id="alice",
        )

    call_kwargs = mock_client.invoke_inline_agent.call_args[1]
    assert call_kwargs["sessionId"] == "alice:my-session"


def test_invoke_skips_empty_bytes() -> None:
    mock_client = MagicMock()
    mock_client.invoke_inline_agent.return_value = _mock_completion_with_empty()

    with patch("channel.agents.inline_agent._agent_client", return_value=mock_client):
        result = invoke(InlineAgentRequest(message="Hi", session_id="s1"), user_id="u1")

    assert result.reply == "hi"


# ---------------------------------------------------------------------------
# invoke_stream
# ---------------------------------------------------------------------------


def test_invoke_stream_yields_delta_and_done() -> None:
    mock_client = MagicMock()
    mock_client.invoke_inline_agent.return_value = _mock_completion("Hello", " world")

    with patch("channel.agents.inline_agent._agent_client", return_value=mock_client):
        chunks = list(
            invoke_stream(
                InlineAgentRequest(message="Hi", session_id="s1"),
                user_id="user1",
            )
        )

    assert len(chunks) == 3
    assert json.loads(chunks[0][len("data: ") :]) == {"type": "delta", "text": "Hello"}
    assert json.loads(chunks[1][len("data: ") :]) == {"type": "delta", "text": " world"}
    done = json.loads(chunks[2][len("data: ") :])
    assert done["type"] == "done"
    assert done["session_id"] == "s1"


def test_invoke_stream_sse_format() -> None:
    mock_client = MagicMock()
    mock_client.invoke_inline_agent.return_value = _mock_completion("hi")

    with patch("channel.agents.inline_agent._agent_client", return_value=mock_client):
        chunks = list(invoke_stream(InlineAgentRequest(message="X", session_id="s1"), user_id="u1"))

    for chunk in chunks:
        assert chunk.startswith("data: ")
        assert chunk.endswith("\n\n")


def test_invoke_stream_generates_session_id_when_omitted() -> None:
    mock_client = MagicMock()
    mock_client.invoke_inline_agent.return_value = _mock_completion("hi")

    with patch("channel.agents.inline_agent._agent_client", return_value=mock_client):
        chunks = list(invoke_stream(InlineAgentRequest(message="X"), user_id="u1"))

    done = json.loads(chunks[-1][len("data: ") :])
    assert done["type"] == "done"
    assert len(done["session_id"]) == 36  # UUID
