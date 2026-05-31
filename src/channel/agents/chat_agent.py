# Copyright (c) 2026 John Carter. All rights reserved.
"""Strands ``Agent`` factory for the chat router.

The factory constructs a fresh ``strands.Agent`` per turn with a
``BedrockModel`` and an optional system prompt.  Memory adapter is
``None`` in 7b — 7c wires the AgentCore Memory adapter once the spike
finalises the recall semantics.  Strands' ``Agent.__init__`` in 1.41.0
does not accept a ``memory`` kwarg, so we simply omit it; the default
behaviour is no memory attached.
"""

from __future__ import annotations

from strands import Agent
from strands.models import BedrockModel

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
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Agent:
    """Construct a fresh Strands ``Agent`` for one chat turn."""

    bedrock = BedrockModel(
        model_id=resolve_model_id(model_id),
        max_tokens=max_tokens,
    )
    return Agent(
        model=bedrock,
        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
    )
