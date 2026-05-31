# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for AgentCore Memory recall hook + helpers."""

from __future__ import annotations

from channel.agents.recall import _format_recall_addendum


def test_format_recall_addendum_returns_empty_string_for_no_records():
    """No records → no addendum. The caller appends only if non-empty."""
    assert _format_recall_addendum([]) == ""


def test_format_recall_addendum_renders_records_as_markdown_bullets():
    records = [
        {"content": {"text": "User builds chess engines"}, "score": 0.92},
        {"content": {"text": "Favourite colour: sage green"}, "score": 0.85},
    ]

    result = _format_recall_addendum(records)

    # Heading + each record on its own bullet.
    assert "## What I remember about previous conversations" in result
    assert "- User builds chess engines" in result
    assert "- Favourite colour: sage green" in result


def test_format_recall_addendum_skips_records_missing_text():
    """Defensive: AgentCore responses without ``content.text`` are dropped
    so a malformed payload can't corrupt the prompt."""
    records = [
        {"content": {"text": "valid record"}, "score": 0.9},
        {"content": {}, "score": 0.85},  # no text key
        {"score": 0.8},  # no content key
        {"content": {"text": ""}, "score": 0.75},  # empty text
    ]

    result = _format_recall_addendum(records)

    assert "- valid record" in result
    # Heading appears once, no empty bullets.
    assert result.count("- ") == 1
