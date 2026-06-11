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


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``socket.getaddrinfo`` so the URL re-validator's DNS lookup
    doesn't depend on real resolution for synthetic test hostnames."""
    import socket as _socket

    def fake_getaddrinfo(_host: str, _port: int | None, *_a: Any, **_kw: Any) -> Any:
        # 8.8.8.8 — globally-routable IPv4, not in any blocked range.
        return [(2, 1, 6, "", ("8.8.8.8", 0))]

    monkeypatch.setattr(_socket, "getaddrinfo", fake_getaddrinfo)


def _make_server(server_id: str, name: str, globally_enabled: bool = True) -> MCPServer:
    return MCPServer(
        server_id=server_id,
        user_id="u1",
        name=name,
        url=f"https://{name}.example.com/mcp",
        client_id=f"dcr-{server_id}",
        tool_prefix=name,
        auth_status=MCPServerAuthStatus.ACTIVE,
        globally_enabled=globally_enabled,
        created_at="x",
        updated_at="x",
    )


@pytest.mark.asyncio
async def test_build_mcp_clients_inherits_globally_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chats_module.storage,
        "list_mcp_servers_for_user",
        lambda _: [
            _make_server("a", "alpha", globally_enabled=True),
            _make_server("b", "beta", globally_enabled=False),
        ],
    )
    monkeypatch.setattr(
        chats_module.storage,
        "get_chat_mcp_settings",
        lambda _: ChatMCPSettings(chat_id="chat-1", mode=ChatMCPMode.INHERIT),
    )
    monkeypatch.setattr(
        chats_module.mcp_auth,
        "get_valid_access_token",
        AsyncMock(return_value="bearer-tok"),
    )

    clients = await chats_module._build_mcp_clients_for_chat(
        user_id="u1",
        chat_id="chat-1",
    )
    assert len(clients) == 1
    assert isinstance(clients[0], MCPClient)


@pytest.mark.asyncio
async def test_build_mcp_clients_explicit_mode_uses_exact_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chats_module.storage,
        "list_mcp_servers_for_user",
        lambda _: [
            _make_server("a", "alpha", globally_enabled=True),
            _make_server("b", "beta", globally_enabled=True),
        ],
    )
    monkeypatch.setattr(
        chats_module.storage,
        "get_chat_mcp_settings",
        lambda _: ChatMCPSettings(
            chat_id="chat-1",
            mode=ChatMCPMode.EXPLICIT,
            explicit_server_ids=["b"],
        ),
    )
    monkeypatch.setattr(
        chats_module.mcp_auth,
        "get_valid_access_token",
        AsyncMock(return_value="bearer-tok"),
    )
    clients = await chats_module._build_mcp_clients_for_chat(
        user_id="u1",
        chat_id="chat-1",
    )
    assert len(clients) == 1  # only "beta"


@pytest.mark.asyncio
async def test_build_mcp_clients_skips_never_authed_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A NEVER_AUTHED server has no token row yet — silently skip it
    instead of attempting token resolution (which would flip the row
    to EXPIRED and lose the user-visible 'never connected' state)."""
    never_authed = MCPServer(
        server_id="a",
        user_id="u1",
        name="alpha",
        url="https://alpha.example.com/mcp",
        client_id="dcr-a",
        tool_prefix="alpha",
        auth_status=MCPServerAuthStatus.NEVER_AUTHED,
        globally_enabled=True,
        created_at="x",
        updated_at="x",
    )
    monkeypatch.setattr(
        chats_module.storage,
        "list_mcp_servers_for_user",
        lambda _: [never_authed],
    )
    monkeypatch.setattr(
        chats_module.storage,
        "get_chat_mcp_settings",
        lambda _: ChatMCPSettings(chat_id="chat-1"),
    )
    # If we accidentally tried to resolve a token, this would raise.
    monkeypatch.setattr(
        chats_module.mcp_auth,
        "get_valid_access_token",
        AsyncMock(side_effect=AssertionError("must not be called")),
    )
    flipped: dict[str, Any] = {}
    monkeypatch.setattr(
        chats_module.storage,
        "set_mcp_server_auth_status",
        lambda **kw: flipped.update(kw),
    )

    clients = await chats_module._build_mcp_clients_for_chat(
        user_id="u1",
        chat_id="chat-1",
    )
    assert clients == []
    # NEVER_AUTHED must NOT be flipped to EXPIRED on the chat-start path.
    assert flipped == {}


@pytest.mark.asyncio
async def test_build_mcp_clients_skips_expired_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-EXPIRED server is silently skipped — no avoidable
    refresh-roundtrip every turn."""
    expired = MCPServer(
        server_id="a",
        user_id="u1",
        name="alpha",
        url="https://alpha.example.com/mcp",
        client_id="dcr-a",
        tool_prefix="alpha",
        auth_status=MCPServerAuthStatus.EXPIRED,
        globally_enabled=True,
        created_at="x",
        updated_at="x",
    )
    monkeypatch.setattr(
        chats_module.storage,
        "list_mcp_servers_for_user",
        lambda _: [expired],
    )
    monkeypatch.setattr(
        chats_module.storage,
        "get_chat_mcp_settings",
        lambda _: ChatMCPSettings(chat_id="chat-1"),
    )
    monkeypatch.setattr(
        chats_module.mcp_auth,
        "get_valid_access_token",
        AsyncMock(side_effect=AssertionError("must not be called")),
    )
    clients = await chats_module._build_mcp_clients_for_chat(
        user_id="u1",
        chat_id="chat-1",
    )
    assert clients == []


@pytest.mark.asyncio
async def test_build_mcp_clients_kill_switch_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STARTER_MCP_REGISTRY_ENABLED != '1' short-circuits with zero DDB
    reads."""
    monkeypatch.setenv("STARTER_MCP_REGISTRY_ENABLED", "0")

    def _explode(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("must not be called when kill switch is off")

    monkeypatch.setattr(chats_module.storage, "list_mcp_servers_for_user", _explode)
    clients = await chats_module._build_mcp_clients_for_chat(
        user_id="u1",
        chat_id="chat-1",
    )
    assert clients == []


@pytest.mark.asyncio
async def test_build_mcp_clients_kill_switch_default_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset env var defaults to enabled — test/dev envs that don't
    provision the flag still see MCP behavior."""
    monkeypatch.delenv("STARTER_MCP_REGISTRY_ENABLED", raising=False)
    called: dict[str, bool] = {}

    def _track(_user_id: str) -> list[Any]:
        called["yes"] = True
        return []

    monkeypatch.setattr(chats_module.storage, "list_mcp_servers_for_user", _track)
    monkeypatch.setattr(
        chats_module.storage,
        "get_chat_mcp_settings",
        lambda _: ChatMCPSettings(chat_id="chat-1"),
    )
    await chats_module._build_mcp_clients_for_chat(user_id="u1", chat_id="chat-1")
    assert called.get("yes") is True


@pytest.mark.asyncio
async def test_build_mcp_clients_skips_server_when_url_revalidation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted URL that fails the turn-time validator (e.g. DNS
    now resolves to RFC1918) is silently skipped — no transport, no
    discovery, no flip of auth_status. Logs at WARNING only."""
    import socket as _socket

    monkeypatch.setattr(
        chats_module.storage,
        "list_mcp_servers_for_user",
        lambda _: [_make_server("a", "alpha")],
    )
    monkeypatch.setattr(
        chats_module.storage,
        "get_chat_mcp_settings",
        lambda _: ChatMCPSettings(chat_id="chat-1"),
    )

    # Force DNS to resolve to a private RFC1918 address — simulates
    # a DNS-rebinding scenario between registration and chat-time.
    def fake_gai(_h: str, _p: int | None, *_a: Any, **_kw: Any) -> Any:
        return [(2, 1, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(_socket, "getaddrinfo", fake_gai)
    monkeypatch.setattr(
        chats_module.mcp_auth,
        "get_valid_access_token",
        AsyncMock(side_effect=AssertionError("must not be called")),
    )
    flipped: dict[str, Any] = {}
    monkeypatch.setattr(
        chats_module.storage,
        "set_mcp_server_auth_status",
        lambda **kw: flipped.update(kw),
    )

    clients = await chats_module._build_mcp_clients_for_chat(
        user_id="u1",
        chat_id="chat-1",
    )
    assert clients == []
    # The skip does NOT flip auth_status — DNS rebinding is a network
    # issue, not a user-revocable auth state.
    assert flipped == {}


@pytest.mark.asyncio
async def test_build_mcp_clients_skips_auth_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth-failed servers are dropped (not raised) so a broken Hive
    registration doesn't block other tools."""
    from channel.mcp.auth import MCPAuthFailedError

    monkeypatch.setattr(
        chats_module.storage,
        "list_mcp_servers_for_user",
        lambda _: [_make_server("a", "alpha")],
    )
    monkeypatch.setattr(
        chats_module.storage,
        "get_chat_mcp_settings",
        lambda _: ChatMCPSettings(chat_id="chat-1"),
    )

    async def fail(**_: Any) -> str:
        raise MCPAuthFailedError("expired")

    monkeypatch.setattr(chats_module.mcp_auth, "get_valid_access_token", fail)
    flipped: dict[str, Any] = {}
    monkeypatch.setattr(
        chats_module.storage,
        "set_mcp_server_auth_status",
        lambda **kw: flipped.update(kw),
    )

    clients = await chats_module._build_mcp_clients_for_chat(
        user_id="u1",
        chat_id="chat-1",
    )
    assert clients == []
    assert flipped["status"] == MCPServerAuthStatus.EXPIRED
