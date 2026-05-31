# Copyright (c) 2026 John Carter. All rights reserved.
"""Bedrock AgentCore Memory integration via Strands hooks.

Phase 7c — write-only. Every chat round-trip produces one atomic
``CreateEvent`` containing the user+assistant pair. Recall
(``RetrieveMemoryRecords`` + prompt injection) lands in 7d.

Design rationale: ``docs/superpowers/specs/2026-05-31-phase-7c-agentcore-memory-writes-design.md``.

Strands 1.41 has no native AgentCore Memory adapter (verified in
``docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md``)
so this module talks to AgentCore directly via boto3.
"""

from __future__ import annotations

import os
from typing import Any

import boto3

_ROLE_MAP: dict[str, str] = {"user": "USER", "assistant": "ASSISTANT"}

# Module-level cache keyed by env. Resets on Lambda cold-start; value is
# the AgentCore-assigned memoryId (which includes an opaque suffix).
_memory_id_cache: dict[str, str] = {}


def get_or_create_memory(env: str) -> str:
    """Return the AgentCore Memory id for ``env``, creating it if absent.

    Lazy + idempotent. First call within a Lambda instance pays a
    ``ListMemories`` RPC (~200ms) to find an existing Memory by name;
    subsequent calls hit the module-level cache. If no match exists,
    falls through to ``CreateMemory``.

    AgentCore appends an opaque suffix to ``memoryId`` (e.g.
    ``channel-dev-A1B2C3D4``). Look up by **name**, not by id, or
    re-deploys against an existing Memory will create duplicates.

    Override the default ``channel-{env}`` naming via
    ``STARTER_AGENTCORE_MEMORY_NAME`` — useful for pointing a personal
    dev environment at a pre-existing Memory resource.
    """
    if env in _memory_id_cache:
        return _memory_id_cache[env]

    name = os.environ.get("STARTER_AGENTCORE_MEMORY_NAME") or f"channel-{env}"
    control = boto3.client("bedrock-agentcore-control")

    existing = control.list_memories()
    for mem in existing.get("memorySummaries", []):
        if mem["name"] == name:
            _memory_id_cache[env] = mem["id"]
            return mem["id"]

    created = control.create_memory(
        name=name,
        memoryStrategies=[],
        eventExpiryDuration=90,
    )
    memory_id = created["memory"]["id"]
    _memory_id_cache[env] = memory_id
    return memory_id


def _payload_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Strands message dicts to AgentCore ``CreateEvent`` payload.

    Strands messages have shape
    ``{"role": str, "content": [{"text": str}, ...]}``. AgentCore's
    payload is a list of typed conversational entries with role
    upper-cased and ``content.text`` as a single string.

    Multi-block content (text + toolUse interleaved) gets its text
    blocks concatenated. Non-text blocks (toolUse, toolResult) are
    dropped — AgentCore's v1 payload spec only accepts text.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = _ROLE_MAP.get(msg["role"])
        if role is None:
            raise ValueError(f"unsupported role: {msg['role']!r}")
        text = "".join(
            block["text"] for block in msg.get("content", []) if "text" in block
        )
        out.append({"conversational": {"role": role, "content": {"text": text}}})
    return out
