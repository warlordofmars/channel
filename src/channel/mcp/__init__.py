# Copyright (c) 2026 John Carter. All rights reserved.
"""MCP-server registry + OAuth lifecycle (#207).

This package owns Channel's side of the OAuth 2.1 + DCR lifecycle for
MCP servers. Strands' ``MCPClient`` sees only the pre-authenticated
transport thunk constructed in :mod:`channel.mcp.transports`. See
``docs/superpowers/specs/2026-06-06-mcp-server-registry-spike.md`` for
the architectural seam.
"""
