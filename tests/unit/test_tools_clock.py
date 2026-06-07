# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the current_time smoke-test tool (#181)."""

from __future__ import annotations

import re

from channel.agents.tools.clock import current_time


def test_current_time_returns_iso8601_utc_string():
    result = current_time()
    # Expect format like "2026-06-06T12:34:56Z"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", result)


def test_current_time_is_a_strands_tool():
    """Strands' @tool decorator wraps the function in a DecoratedFunctionTool
    that exposes ``tool_spec`` (and ``_tool_spec``). The chassis registers
    this via build_agent(tools=[current_time]); Strands introspects the
    function's docstring + signature to build the tool schema."""
    assert hasattr(current_time, "tool_spec")
