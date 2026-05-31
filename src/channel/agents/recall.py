# Copyright (c) 2026 John Carter. All rights reserved.
"""Bedrock AgentCore Memory recall via Strands hooks.

Phase 7d implementation. Subscribes to ``BeforeInvocationEvent`` and
injects relevant prior-conversation context (scoped to the caller's
``actorId``) into the system prompt as a Markdown addendum.

Design rationale: ``docs/superpowers/specs/2026-05-31-phase-7d-memory-recall-auto-titling-design.md``.

Strands 1.41 has no native AgentCore Memory adapter (verified in the
spike at ``docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md``)
so this module talks to AgentCore directly via boto3 — same pattern as
the 7c write hook in ``agents/memory.py``.
"""

from __future__ import annotations

from typing import Any

_RECALL_HEADING = "## What I remember about previous conversations"


def _format_recall_addendum(records: list[dict[str, Any]]) -> str:
    """Render ``MemoryRecordSummary`` records as a Markdown addendum.

    Returns the empty string when no records have usable text content.
    Defensive: records missing ``content.text`` or with empty text are
    silently skipped so a malformed AgentCore response can't corrupt
    the system prompt.
    """
    bullets: list[str] = []
    for rec in records:
        text = rec.get("content", {}).get("text") if isinstance(rec, dict) else None
        if text:
            bullets.append(f"- {text}")
    if not bullets:
        return ""
    return _RECALL_HEADING + "\n\n" + "\n".join(bullets)
