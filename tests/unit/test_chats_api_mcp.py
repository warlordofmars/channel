# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the MCP-clients side of _build_tool_registry."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from strands.tools.mcp import MCPClient

from channel.api import chats as chats_module
from channel.models import (
    ChatMCPMode,
    ChatMCPSettings,
    MCPServer,
    MCPServerAuthStatus,
)


def _make_server(server_id: str, name: str, globally_enabled: bool = True) -> MCPServer:
    return MCPServer(
        server_id=server_id, user_id="u1", name=name,
        url=f"https://{name}.example.com/mcp",
        client_id=f"dcr-{server_id}", tool_prefix=name,
        auth_status=MCPServerAuthStatus.ACTIVE,
        globally_enabled=globally_enabled,
        created_at="x", updated_at="x",
    )


@pytest.mark.asyncio
async def test_build_mcp_clients_inherits_globally_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chats_module.storage, "list_mcp_servers_for_user",
        lambda _: [
            _make_server("a", "alpha", globally_enabled=True),
            _make_server("b", "beta", globally_enabled=False),
        ],
    )
    monkeypatch.setattr(
        chats_module.storage, "get_chat_mcp_settings",
        lambda _: ChatMCPSettings(chat_id="chat-1", mode=ChatMCPMode.INHERIT),
    )
    monkeypatch.setattr(
        chats_module.mcp_auth, "get_valid_access_token",
        AsyncMock(return_value="bearer-tok"),
    )

    clients = await chats_module._build_mcp_clients_for_chat(
        user_id="u1", chat_id="chat-1",
    )
    assert len(clients) == 1
    assert isinstance(clients[0], MCPClient)


@pytest.mark.asyncio
async def test_build_mcp_clients_explicit_mode_uses_exact_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chats_module.storage, "list_mcp_servers_for_user",
        lambda _: [
            _make_server("a", "alpha", globally_enabled=True),
            _make_server("b", "beta", globally_enabled=True),
        ],
    )
    monkeypatch.setattr(
        chats_module.storage, "get_chat_mcp_settings",
        lambda _: ChatMCPSettings(
            chat_id="chat-1",
            mode=ChatMCPMode.EXPLICIT,
            explicit_server_ids=["b"],
        ),
    )
    monkeypatch.setattr(
        chats_module.mcp_auth, "get_valid_access_token",
        AsyncMock(return_value="bearer-tok"),
    )
    clients = await chats_module._build_mcp_clients_for_chat(
        user_id="u1", chat_id="chat-1",
    )
    assert len(clients) == 1  # only "beta"


@pytest.mark.asyncio
async def test_build_mcp_clients_skips_auth_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth-failed servers are dropped (not raised) so a broken Hive
    registration doesn't block other tools."""
    from channel.mcp.auth import MCPAuthFailedError

    monkeypatch.setattr(
        chats_module.storage, "list_mcp_servers_for_user",
        lambda _: [_make_server("a", "alpha")],
    )
    monkeypatch.setattr(
        chats_module.storage, "get_chat_mcp_settings",
        lambda _: ChatMCPSettings(chat_id="chat-1"),
    )

    async def fail(**_: Any) -> str:
        raise MCPAuthFailedError("expired")

    monkeypatch.setattr(chats_module.mcp_auth, "get_valid_access_token", fail)
    flipped: dict[str, Any] = {}
    monkeypatch.setattr(
        chats_module.storage, "set_mcp_server_auth_status",
        lambda **kw: flipped.update(kw),
    )

    clients = await chats_module._build_mcp_clients_for_chat(
        user_id="u1", chat_id="chat-1",
    )
    assert clients == []
    assert flipped["status"] == MCPServerAuthStatus.EXPIRED
