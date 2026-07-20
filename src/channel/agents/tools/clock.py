# Copyright (c) 2026 John Carter. All rights reserved.
"""``current_time`` — chassis smoke-test tool (epic #128 / #181).

Returns the current UTC time as an ISO-8601 string. The chassis
registers this tool when ``CHANNEL_CLOCK_TOOL_ENABLED=1``; default off
in prod per strategy spec policy P2 ("smoke-test, not a product
feature"). It exists only so the chassis has a real Strands tool to
exercise every hook + every SSE event + every SPA renderer end-to-end.

If a future feature wants real time-aware tools, that's a separate
design pass — do not flip the prod feature flag here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from strands import tool


@tool
def current_time() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Use when the user asks "what time is it" or needs the current
    timestamp for relative-time reasoning.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
