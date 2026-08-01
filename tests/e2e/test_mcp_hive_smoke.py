# Copyright (c) 2026 John Carter. All rights reserved.
"""E2e smoke test against the real Hive MCP server (#207).

Skipped unless ``CHANNEL_E2E_HIVE_ENABLED=1`` AND Playwright is installed
— OAuth flow against the real Hive requires a real browser. CI does not
run this; humans run it on demand before merging the MCP work.

Flow:
  1. Mint a JWT via the dev bypass.
  2. POST /api/mcp/servers with the Hive URL.
  3. Open the returned ``auth_start_url`` in Playwright and complete
     the OAuth grant.
  4. GET /api/mcp/servers — confirm auth_status flipped to ACTIVE.
  5. Create a chat, send a prompt that invokes a Hive tool, watch the
     SSE stream emit ``tool_started`` + ``tool_finished`` with the
     ``hive_`` tool prefix.
  6. DELETE the server; confirm both rows disappear.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CHANNEL_E2E_HIVE_ENABLED") != "1",
    reason="Hive e2e smoke requires CHANNEL_E2E_HIVE_ENABLED=1 + a Playwright install",
)


def test_hive_register_auth_use_delete() -> None:
    pytest.skip("Implementation deferred to a follow-up — see #207 comment for status")
