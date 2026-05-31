# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for AgentCore Memory hook + bootstrap helpers."""

from __future__ import annotations

import pytest

from channel.agents.memory import _payload_from_messages


def test_payload_from_messages_pairs_user_and_assistant_text():
    messages = [
        {"role": "user", "content": [{"text": "what's 2+2?"}]},
        {"role": "assistant", "content": [{"text": "4"}]},
    ]

    payload = _payload_from_messages(messages)

    assert payload == [
        {"conversational": {"role": "USER", "content": {"text": "what's 2+2?"}}},
        {"conversational": {"role": "ASSISTANT", "content": {"text": "4"}}},
    ]


def test_payload_from_messages_concatenates_multi_block_content():
    # Strands sometimes emits multiple text blocks within one message
    # (e.g. when a tool result is interleaved). Concatenate text blocks;
    # non-text blocks (toolUse, toolResult) are dropped — AgentCore's
    # v1 payload spec only accepts text.
    messages = [
        {
            "role": "assistant",
            "content": [
                {"text": "Let me check. "},
                {"toolUse": {"name": "calc", "input": {"x": 2}}},
                {"text": "The answer is 4."},
            ],
        },
    ]

    payload = _payload_from_messages(messages)

    assert payload == [
        {
            "conversational": {
                "role": "ASSISTANT",
                "content": {"text": "Let me check. The answer is 4."},
            },
        },
    ]


def test_payload_from_messages_raises_on_unknown_role():
    with pytest.raises(ValueError, match="unsupported role"):
        _payload_from_messages([{"role": "system", "content": [{"text": "..."}]}])
