# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the Strands Agent factory."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from channel.agents.chat_agent import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_SYSTEM_PROMPT,
    build_agent,
    resolve_model_id,
)


@pytest.fixture(autouse=True)
def _set_starter_env(monkeypatch):
    """build_agent needs STARTER_ENV to derive the AgentCore Memory name."""
    monkeypatch.setenv("STARTER_ENV", "test")


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
    monkeypatch.setattr("channel.agents.chat_agent.get_or_create_memory", lambda env: "mem-test")
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreMemoryHook", lambda **kw: object())

    agent = build_agent(
        model_id="claude-sonnet-4-6",
        user_id="u-1",
        chat_id="c-1",
    )

    assert isinstance(agent, FakeAgent)
    assert captured["bedrock_kwargs"]["model_id"] == "us.anthropic.claude-sonnet-4-6"
    assert captured["bedrock_kwargs"]["max_tokens"] == DEFAULT_MAX_TOKENS
    assert captured["system_prompt"] == DEFAULT_SYSTEM_PROMPT


def test_build_agent_uses_custom_system_prompt_when_provided(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", lambda **_: object())
    monkeypatch.setattr(
        "channel.agents.chat_agent.Agent",
        lambda model, system_prompt=None, **kw: captured.update({"system_prompt": system_prompt}),
    )
    monkeypatch.setattr("channel.agents.chat_agent.get_or_create_memory", lambda env: "mem-test")
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreMemoryHook", lambda **kw: object())

    build_agent(
        model_id="claude-sonnet-4-6",
        user_id="u-1",
        chat_id="c-1",
        system_prompt="Be brief.",
    )
    assert captured["system_prompt"] == "Be brief."


def test_build_agent_attaches_agentcore_memory_hook(monkeypatch):
    """Hook must be constructed with caller's user_id+chat_id and passed
    in via ``hooks=[hook]``."""

    captured: dict[str, object] = {}
    sentinel_hook = MagicMock(name="memory_hook")

    def fake_hook_factory(**kwargs):
        captured["hook_kwargs"] = kwargs
        return sentinel_hook

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", lambda **_: object())
    monkeypatch.setattr("channel.agents.chat_agent.Agent", FakeAgent)
    monkeypatch.setattr(
        "channel.agents.chat_agent.AgentCoreMemoryHook",
        fake_hook_factory,
    )
    monkeypatch.setattr(
        "channel.agents.chat_agent.get_or_create_memory",
        lambda env: f"channel_{env}_MEMID",
    )

    build_agent(
        model_id="claude-sonnet-4-6",
        user_id="user-abc",
        chat_id="chat-xyz",
    )

    assert captured["hook_kwargs"] == {
        "memory_id": "channel_test_MEMID",
        "actor_id": "user-abc",
        "session_id": "chat-xyz",
    }
    assert captured["agent_kwargs"]["hooks"] == [sentinel_hook]
