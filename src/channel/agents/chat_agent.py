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

DEFAULT_SYSTEM_PROMPT = (
    "You are Channel, a helpful AI assistant.  Be concise, accurate, "
    "and tailored to the user's apparent expertise.  Use markdown for "
    "structure (lists, headings, code blocks) when it aids clarity."
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
    """

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
