# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the Strands Agent factory."""

from __future__ import annotations

import pytest

from channel.agents.chat_agent import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_SYSTEM_PROMPT,
    build_agent,
    resolve_model_id,
)


def test_resolve_model_id_returns_full_bedrock_id_for_known_id():
    assert resolve_model_id("claude-sonnet-4-6") == "us.anthropic.claude-sonnet-4-6"


def test_resolve_model_id_returns_full_bedrock_id_for_haiku():
    assert resolve_model_id("claude-haiku-4-5") == "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_resolve_model_id_raises_on_unknown_model():
    with pytest.raises(ValueError, match="unknown model"):
        resolve_model_id("claude-opus-4-99-fictional")


def test_default_system_prompt_is_non_empty_string():
    assert isinstance(DEFAULT_SYSTEM_PROMPT, str)
    assert len(DEFAULT_SYSTEM_PROMPT) > 0


def test_default_max_tokens_is_positive_int():
    assert isinstance(DEFAULT_MAX_TOKENS, int) and DEFAULT_MAX_TOKENS > 0


def test_build_agent_constructs_strands_agent_with_bedrock_model(monkeypatch):
    captured: dict[str, object] = {}

    class FakeBedrockModel:
        def __init__(self, **kwargs):
            captured["bedrock_kwargs"] = kwargs

    class FakeAgent:
        def __init__(self, model, system_prompt=None, **kwargs):
            captured["agent_model"] = model
            captured["system_prompt"] = system_prompt
            captured["agent_kwargs"] = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", FakeBedrockModel)
    monkeypatch.setattr("channel.agents.chat_agent.Agent", FakeAgent)

    agent = build_agent(model_id="claude-sonnet-4-6")

    assert isinstance(agent, FakeAgent)
    assert captured["bedrock_kwargs"]["model_id"] == "us.anthropic.claude-sonnet-4-6"
    assert captured["bedrock_kwargs"]["max_tokens"] == DEFAULT_MAX_TOKENS
    assert captured["system_prompt"] == DEFAULT_SYSTEM_PROMPT
    # memory adapter is None in 7b — wired in 7c.  Strands' Agent does not
    # accept a ``memory`` kwarg in 1.41.0, so we simply omit it; no memory
    # is attached by default.
    assert "memory" not in captured["agent_kwargs"]


def test_build_agent_uses_custom_system_prompt_when_provided(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", lambda **_: object())
    monkeypatch.setattr(
        "channel.agents.chat_agent.Agent",
        lambda model, system_prompt=None, **kw: captured.update({"system_prompt": system_prompt}),
    )

    build_agent(model_id="claude-sonnet-4-6", system_prompt="Be brief.")
    assert captured["system_prompt"] == "Be brief."
