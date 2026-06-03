# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the Strands Agent factory."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from channel.agents.chat_agent import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_SYSTEM_PROMPT,
    build_agent,
    max_tokens_for_effort,
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


@pytest.mark.parametrize(
    "effort, expected",
    [
        ("low", 1024),
        ("medium", 4096),
        ("high", 16384),
        ("max", 32768),
        # Case-insensitive — UI ships capitalized values.
        ("Low", 1024),
        ("HIGH", 16384),
    ],
)
def test_max_tokens_for_effort_known_tiers(effort, expected):
    assert max_tokens_for_effort(effort) == expected


def test_max_tokens_for_effort_none_falls_back_to_default():
    assert max_tokens_for_effort(None) == DEFAULT_MAX_TOKENS


def test_max_tokens_for_effort_unknown_value_falls_back_to_default():
    # Stale clients or future tier names must not error — they get the
    # default budget so the response still streams.
    assert max_tokens_for_effort("turbo") == DEFAULT_MAX_TOKENS


def _patch_strands(monkeypatch, captured):
    """Common stub for build_agent's Strands + memory dependencies."""

    class FakeBedrockModel:
        def __init__(self, **kwargs):
            captured["bedrock_kwargs"] = kwargs

    class FakeAgent:
        def __init__(self, model, system_prompt=None, **kwargs):
            captured["agent_model"] = model

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", FakeBedrockModel)
    monkeypatch.setattr("channel.agents.chat_agent.Agent", FakeAgent)
    monkeypatch.setattr("channel.agents.chat_agent.get_or_create_memory", lambda env: "mem-test")
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreMemoryHook", lambda **kw: object())
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreRecallHook", lambda **kw: object())


def test_build_agent_with_effort_low_sets_max_tokens_1024(monkeypatch):
    captured: dict[str, object] = {}
    _patch_strands(monkeypatch, captured)
    build_agent(
        model_id="claude-sonnet-4-6",
        user_id="u-1",
        chat_id="c-1",
        effort="low",
    )
    assert captured["bedrock_kwargs"]["max_tokens"] == 1024


def test_build_agent_with_effort_high_sets_max_tokens_16384(monkeypatch):
    captured: dict[str, object] = {}
    _patch_strands(monkeypatch, captured)
    build_agent(
        model_id="claude-sonnet-4-6",
        user_id="u-1",
        chat_id="c-1",
        effort="high",
    )
    assert captured["bedrock_kwargs"]["max_tokens"] == 16384


def test_build_agent_with_effort_max_sets_max_tokens_32768(monkeypatch):
    captured: dict[str, object] = {}
    _patch_strands(monkeypatch, captured)
    build_agent(
        model_id="claude-sonnet-4-6",
        user_id="u-1",
        chat_id="c-1",
        effort="Max",
    )
    assert captured["bedrock_kwargs"]["max_tokens"] == 32768


def test_build_agent_effort_overrides_explicit_max_tokens(monkeypatch):
    """When both are set, ``effort`` wins — it's the higher-level intent."""

    captured: dict[str, object] = {}
    _patch_strands(monkeypatch, captured)
    build_agent(
        model_id="claude-sonnet-4-6",
        user_id="u-1",
        chat_id="c-1",
        max_tokens=999,
        effort="low",
    )
    assert captured["bedrock_kwargs"]["max_tokens"] == 1024


def test_build_agent_without_effort_keeps_explicit_max_tokens(monkeypatch):
    captured: dict[str, object] = {}
    _patch_strands(monkeypatch, captured)
    build_agent(
        model_id="claude-sonnet-4-6",
        user_id="u-1",
        chat_id="c-1",
        max_tokens=2048,
    )
    assert captured["bedrock_kwargs"]["max_tokens"] == 2048


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
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreRecallHook", lambda **kw: object())

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
        lambda model, system_prompt=None, **kw: (
            captured.update({"system_prompt": system_prompt}) or MagicMock()
        ),
    )
    monkeypatch.setattr("channel.agents.chat_agent.get_or_create_memory", lambda env: "mem-test")
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreMemoryHook", lambda **kw: object())
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreRecallHook", lambda **kw: object())

    build_agent(
        model_id="claude-sonnet-4-6",
        user_id="u-1",
        chat_id="c-1",
        system_prompt="Be brief.",
    )
    assert captured["system_prompt"] == "Be brief."


def test_build_agent_attaches_recall_hook_before_write_hook(monkeypatch):
    """Both hooks must be attached. Order: recall (BeforeInvocationEvent)
    FIRST, write (AfterInvocationEvent) SECOND."""

    captured: dict[str, object] = {}
    fake_recall = MagicMock(name="recall_hook")
    fake_write = MagicMock(name="write_hook")

    def recall_factory(**kwargs):
        captured["recall_kwargs"] = kwargs
        return fake_recall

    def write_factory(**kwargs):
        captured["write_kwargs"] = kwargs
        return fake_write

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", lambda **_: object())
    monkeypatch.setattr("channel.agents.chat_agent.Agent", FakeAgent)
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreRecallHook", recall_factory)
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreMemoryHook", write_factory)
    monkeypatch.setattr(
        "channel.agents.chat_agent.get_or_create_memory",
        lambda env: f"channel_{env}_MEMID",
    )

    build_agent(
        model_id="claude-sonnet-4-6",
        user_id="user-abc",
        chat_id="chat-xyz",
    )

    # Recall hook built with memory_id + actor_id (NO session_id —
    # recall is cross-session within an actor).
    assert captured["recall_kwargs"] == {
        "memory_id": "channel_test_MEMID",
        "actor_id": "user-abc",
    }
    # Write hook built with memory_id + actor_id + session_id.
    assert captured["write_kwargs"] == {
        "memory_id": "channel_test_MEMID",
        "actor_id": "user-abc",
        "session_id": "chat-xyz",
    }
    # Order: recall before write.
    assert captured["agent_kwargs"]["hooks"] == [fake_recall, fake_write]


def test_build_agent_attaches_chat_id_to_agent_instance(monkeypatch):
    """Recall hook reads chat_id off ``event.agent.chat_id``; set it at
    construction time."""
    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", lambda **_: object())
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreMemoryHook", lambda **_: object())
    monkeypatch.setattr("channel.agents.chat_agent.AgentCoreRecallHook", lambda **_: object())
    monkeypatch.setattr(
        "channel.agents.chat_agent.get_or_create_memory",
        lambda env: "m",
    )

    class FakeAgent:
        def __init__(self, **kwargs):
            self._kwargs = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.Agent", FakeAgent)

    agent = build_agent(
        model_id="claude-sonnet-4-6",
        user_id="u",
        chat_id="chat-zzz",
    )
    assert agent.chat_id == "chat-zzz"


def test_build_titler_agent_uses_haiku_with_no_hooks(monkeypatch):
    from channel.agents.chat_agent import build_titler_agent

    captured: dict[str, object] = {}

    class FakeBedrockModel:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", FakeBedrockModel)
    monkeypatch.setattr("channel.agents.chat_agent.Agent", FakeAgent)

    build_titler_agent()

    assert "haiku" in captured["model_kwargs"]["model_id"].lower()
    assert captured["model_kwargs"]["max_tokens"] == 60
    assert captured["agent_kwargs"].get("hooks", []) == []


def test_build_titler_agent_respects_starter_titler_model_override(monkeypatch):
    from channel.agents.chat_agent import build_titler_agent

    monkeypatch.setenv("STARTER_TITLER_MODEL", "claude-sonnet-4-6")
    captured: dict[str, object] = {}

    class FakeBedrockModel:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", FakeBedrockModel)
    monkeypatch.setattr("channel.agents.chat_agent.Agent", lambda **_: object())

    build_titler_agent()
    assert captured["model_kwargs"]["model_id"] == "us.anthropic.claude-sonnet-4-6"


def test_build_followups_agent_uses_haiku_with_no_hooks(monkeypatch):
    from channel.agents.chat_agent import build_followups_agent

    captured: dict[str, object] = {}

    class FakeBedrockModel:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", FakeBedrockModel)
    monkeypatch.setattr("channel.agents.chat_agent.Agent", FakeAgent)

    build_followups_agent()

    assert "haiku" in captured["model_kwargs"]["model_id"].lower()
    assert captured["model_kwargs"]["max_tokens"] == 120
    assert captured["agent_kwargs"].get("hooks", []) == []
    # The system prompt should mention follow-ups so a wiring mistake
    # (e.g. accidentally using the titler prompt) fails loudly.
    assert "follow-up" in captured["agent_kwargs"]["system_prompt"].lower()


def test_build_followups_agent_respects_starter_followups_model_override(monkeypatch):
    from channel.agents.chat_agent import build_followups_agent

    monkeypatch.setenv("STARTER_FOLLOWUPS_MODEL", "claude-sonnet-4-6")
    captured: dict[str, object] = {}

    class FakeBedrockModel:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", FakeBedrockModel)
    monkeypatch.setattr("channel.agents.chat_agent.Agent", lambda **_: object())

    build_followups_agent()
    assert captured["model_kwargs"]["model_id"] == "us.anthropic.claude-sonnet-4-6"
