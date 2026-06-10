# Copyright (c) 2026 John Carter. All rights reserved.
"""Authenticated MCP transport thunk — the spike's architectural seam.

Wraps :func:`mcp.client.streamable_http.streamable_http_client` with a
pre-configured :class:`httpx.AsyncClient` carrying the bearer token in
its default headers. Strands' :class:`strands.tools.mcp.MCPClient`
consumes the thunk via its ``transport_callable`` constructor parameter
— see ``.venv/.../strands/tools/mcp/mcp_client.py:118``.

The token never crosses the seam: Channel mints the authenticated client,
hands Strands a thunk, Strands opens the streams and reads tool calls
through them. Strands never sees ``access_token``.

Per the spike (§Question 3, "Mid-chain refresh edge case"), v1
re-instantiates ``MCPClient`` per turn — the thunk captures the token
that was valid at turn start. Mid-chain expiry is acceptable: the next
tool call surfaces an auth error which the SPA's "Reconnect" affordance
resolves. v2 may swap in a mutable-token closure if telemetry warrants.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from mcp.client.streamable_http import streamable_http_client


def make_authenticated_transport(
    *,
    server_url: str,
    access_token: str,
) -> Callable[[], Any]:
    """Return a thunk Strands' MCPClient can consume.

    The thunk, when called, returns an async-context-manager that opens
    the MCP transport with the bearer token attached on every outbound
    HTTP request.
    """
    auth_header = f"Bearer {access_token}"

    def thunk() -> Any:
        # Pre-configured httpx.AsyncClient — the mcp lib's
        # `streamable_http_client` accepts an `http_client=` kwarg and
        # reuses our defaults. Setting Authorization on the client's
        # default headers means every request the transport issues
        # carries it. The new client is created fresh per thunk call so
        # Strands' MCPClient owns its lifecycle.
        http_client = httpx.AsyncClient(headers={"Authorization": auth_header})
        return streamable_http_client(server_url, http_client=http_client)

    return thunk
