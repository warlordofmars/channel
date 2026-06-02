# Copyright (c) 2026 John Carter. All rights reserved.
"""Strands ``Agent`` factory for the chat router.

The factory constructs a fresh ``strands.Agent`` per turn with a
``BedrockModel`` and an optional system prompt. Both AgentCore Memory
hooks attach to every Agent — recall (``BeforeInvocationEvent``, Phase
7d) and write (``AfterInvocationEvent``, Phase 7c). Recall runs first
so the injected system-prompt addendum is available downstream; write
runs last so the just-completed turn lands in AgentCore.

See:
- ``docs/superpowers/specs/2026-05-31-phase-7c-agentcore-memory-writes-design.md``
- ``docs/superpowers/specs/2026-05-31-phase-7d-memory-recall-auto-titling-design.md``
"""

from __future__ import annotations

import os
from typing import Any, cast

from strands import Agent
from strands.models import BedrockModel
from strands.types.content import Messages

from channel.agents.memory import AgentCoreMemoryHook, get_or_create_memory
from channel.agents.recall import AgentCoreRecallHook

# Caller-supplied short ids (``claude-sonnet-4-6``) → Bedrock cross-region
# inference-profile IDs.  Strands' BedrockModel calls ``converse_stream``,
# which requires inference profiles (not base model IDs) for on-demand
# throughput in us-east-1.  Verified via
# ``aws bedrock list-inference-profiles --region us-east-1``.
# The CDK stack at ``infra/stacks/channel_stack.py`` grants
# ``bedrock:InvokeModelWithResponseStream`` on both the inference-profile
# resources (us.* and global.*) and the underlying foundation models.
_MODEL_ID_MAP = {
    "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
    "claude-haiku-4-5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
}

DEFAULT_MAX_TOKENS = 4096

# Map the SPA's segmented "Response effort" control to ``max_tokens`` on
# the Strands ``BedrockModel`` (per issue #154). The UI ships four
# tiers (Low / Medium / High / Max — see ``ui/src/app/data.js``); the
# issue spec locked the first three to a doubling pattern, Max
# continues that pattern.  Look-up is case-insensitive so the API can
# accept either the UI's capitalized values or lower-case strings from
# scripted clients without a normalisation layer in the route.
_EFFORT_MAX_TOKENS = {
    "low": 1024,
    "medium": 4096,
    "high": 16384,
    "max": 32768,
}


def max_tokens_for_effort(effort: str | None) -> int:
    """Resolve the ``max_tokens`` budget for a caller-supplied effort tier.

    Unknown / missing values fall back to ``DEFAULT_MAX_TOKENS`` so a
    stale SPA build or an effort string we haven't seen yet still
    produces a working response.
    """
    if effort is None:
        return DEFAULT_MAX_TOKENS
    return _EFFORT_MAX_TOKENS.get(effort.lower(), DEFAULT_MAX_TOKENS)


DEFAULT_SYSTEM_PROMPT = (
    "You are Channel, a helpful AI assistant.  Be concise, accurate, "
    "and tailored to the user's apparent expertise.  Use markdown for "
    "structure (lists, headings, code blocks) when it aids clarity."
)

_DEFAULT_TITLER_MODEL = "claude-haiku-4-5"
_TITLER_MAX_TOKENS = 60

_TITLER_SYSTEM_PROMPT = (
    "Summarise the following exchange in 3-6 words, sentence case, no "
    "quotes, no trailing punctuation. The summary becomes the chat's "
    "title in the sidebar."
)

_DEFAULT_FOLLOWUPS_MODEL = "claude-haiku-4-5"
_FOLLOWUPS_MAX_TOKENS = 120

_FOLLOWUPS_SYSTEM_PROMPT = (
    "Given the user message and the assistant's reply, suggest 2 to 3 "
    "short follow-up prompts the user might want to send next. Return "
    "ONE prompt per line. No numbering, no quotes, no preamble. Each "
    "prompt under 12 words."
)


def resolve_model_id(short_id: str) -> str:
    """Map a caller-supplied short id to a full Bedrock model ARN."""

    try:
        return _MODEL_ID_MAP[short_id]
    except KeyError as exc:
        raise ValueError(f"unknown model: {short_id!r}") from exc


def build_agent(
    *,
    model_id: str,
    user_id: str,
    chat_id: str,
    prior_messages: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = None,
) -> Agent:
    """Construct a fresh Strands ``Agent`` for one chat turn.

    ``user_id`` becomes the AgentCore ``actorId`` and ``chat_id`` the
    AgentCore ``sessionId``.  The Memory resource itself is discovered
    (or created) once per Lambda cold-start, keyed off the
    ``STARTER_ENV`` env var.

    ``prior_messages`` seeds the agent with the chat's stored history
    so each turn doesn't restart from scratch.  Shape is Strands'
    ``Messages``: ``[{"role": "user"|"assistant", "content": [{"text": "..."}]}, ...]``
    in chronological order.  The new user message must NOT be included
    — the caller passes it via ``agent.stream_async(user_message)``.

    ``effort`` is the SPA's "Response effort" tier (low / medium / high
    / max). When supplied it overrides ``max_tokens`` via
    :func:`max_tokens_for_effort`. Unknown values silently fall back to
    the default budget — never error on a stale client.
    """

    if effort is not None:
        max_tokens = max_tokens_for_effort(effort)
    bedrock = BedrockModel(
        model_id=resolve_model_id(model_id),
        max_tokens=max_tokens,
    )
    memory_id = get_or_create_memory(os.environ["STARTER_ENV"])
    recall_hook = AgentCoreRecallHook(memory_id=memory_id, actor_id=user_id)
    memory_hook = AgentCoreMemoryHook(
        memory_id=memory_id,
        actor_id=user_id,
        session_id=chat_id,
    )
    agent = Agent(
        model=bedrock,
        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        # Recall (Before) first, write (After) second.
        hooks=[recall_hook, memory_hook],
        # Strands types ``messages`` as ``list[Message]`` (its TypedDict);
        # at runtime the shape is plain dicts. ``cast`` keeps mypy happy
        # without making consumers of build_agent import Strands types.
        messages=cast(Messages, prior_messages or []),
    )
    # The recall hook reads chat_id off ``event.agent.chat_id`` (Strands'
    # BeforeInvocationEvent doesn't carry chat context natively; this is
    # a Channel-specific attribute).
    agent.chat_id = chat_id  # type: ignore[attr-defined]
    return agent


def build_titler_agent() -> Agent:
    """Build a one-shot Strands Agent for auto-title generation.

    Cheap model (Haiku by default; ``STARTER_TITLER_MODEL`` overrides),
    tight max_tokens, NO memory hooks — we don't want titling events
    polluting AgentCore Memory. Caller invokes ``stream_async`` with
    the user/assistant pair and reads the accumulated text.
    """
    titler_model_id = os.environ.get("STARTER_TITLER_MODEL", _DEFAULT_TITLER_MODEL)
    bedrock = BedrockModel(
        model_id=resolve_model_id(titler_model_id),
        max_tokens=_TITLER_MAX_TOKENS,
    )
    return Agent(
        model=bedrock,
        system_prompt=_TITLER_SYSTEM_PROMPT,
        hooks=[],
    )


def build_followups_agent() -> Agent:
    """Build a one-shot Strands Agent for follow-up prompt suggestions.

    Mirrors :func:`build_titler_agent`: cheap model (Haiku by default;
    ``STARTER_FOLLOWUPS_MODEL`` overrides), tight max_tokens, NO memory
    hooks — follow-up generation runs after the assistant turn is
    persisted and must not write anything to AgentCore Memory itself.
    """
    model_id = os.environ.get("STARTER_FOLLOWUPS_MODEL", _DEFAULT_FOLLOWUPS_MODEL)
    bedrock = BedrockModel(
        model_id=resolve_model_id(model_id),
        max_tokens=_FOLLOWUPS_MAX_TOKENS,
    )
    return Agent(
        model=bedrock,
        system_prompt=_FOLLOWUPS_SYSTEM_PROMPT,
        hooks=[],
    )
