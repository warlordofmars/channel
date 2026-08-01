# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the MCP-clients side of _build_tool_registry."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
    # #389 — each MCPClient is wrapped in a per-server tool-budget cap.
    assert isinstance(clients[0], chats_module._CappedMCPToolProvider)
    assert isinstance(clients[0].inner, MCPClient)
    assert clients[0]._server_id == "a"
    assert clients[0]._max_tools == chats_module._DEFAULT_MCP_MAX_TOOLS_PER_SERVER


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
    """CHANNEL_MCP_REGISTRY_ENABLED != '1' short-circuits with zero DDB
    reads."""
    monkeypatch.setenv("CHANNEL_MCP_REGISTRY_ENABLED", "0")

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
    monkeypatch.delenv("CHANNEL_MCP_REGISTRY_ENABLED", raising=False)
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


# ---------------------------------------------------------------------------
# #389 — per-server MCP tool-budget cap
# ---------------------------------------------------------------------------


class _FakeTool:
    """Minimal ``AgentTool`` stand-in — the cap only reads ``tool_name``."""

    def __init__(self, name: str) -> None:
        self.tool_name = name


class _FakeInnerClient:
    """Duck-typed ``MCPClient`` double for the tool-cap wrapper tests.

    Records consumer add/remove so the lifecycle-forwarding test can
    assert the wrapper delegates rather than swallowing the calls (which
    would leak the real client's background thread)."""

    def __init__(self, tools: list[_FakeTool]) -> None:
        self._tools = tools
        self.consumers: list[Any] = []
        self.load_calls = 0

    async def load_tools(self, **_kwargs: Any) -> list[_FakeTool]:
        self.load_calls += 1
        return list(self._tools)

    def add_consumer(self, consumer_id: Any, **_kwargs: Any) -> None:
        self.consumers.append(consumer_id)

    def remove_consumer(self, consumer_id: Any, **_kwargs: Any) -> None:
        self.consumers.remove(consumer_id)


def _capped(tools: list[_FakeTool], max_tools: int) -> Any:
    return chats_module._CappedMCPToolProvider(
        _FakeInnerClient(tools),  # type: ignore[arg-type]
        server_id="srv-1",
        max_tools=max_tools,
    )


@pytest.mark.asyncio
async def test_capped_provider_truncates_to_budget_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server advertising more than the budget contributes exactly N
    tools — the N lexicographically-smallest by name — and every drop is
    logged + counted (no silent truncation).

    The module-level ``logger`` is mocked directly rather than using
    ``caplog`` because the ``channel`` logger sets ``propagate = False``
    once ``configure_logging`` has run in the test session (same
    workaround as ``test_chats_api``'s heartbeat test)."""
    counter = AsyncMock()
    monkeypatch.setattr(chats_module, "record_mcp_tools_capped", counter)
    mock_logger = MagicMock()
    monkeypatch.setattr(chats_module, "logger", mock_logger)

    # Deliberately unsorted advertise order to prove the kept subset is
    # name-sorted, not advertise-ordered.
    tools = [_FakeTool(n) for n in ["delta", "alpha", "echo", "charlie", "bravo"]]
    provider = _capped(tools, max_tools=2)

    kept = await provider.load_tools()

    assert [t.tool_name for t in kept] == ["alpha", "bravo"]
    counter.assert_awaited_once_with()
    # The drop line names every dropped tool — the no-silent-truncation
    # contract. Args are (template, server_id_hash, advertised, kept,
    # dropped_names).
    warn = next(
        c for c in mock_logger.warning.call_args_list if c.args[0].startswith("mcp.tools_capped")
    )
    assert warn.args[2] == 5  # advertised
    assert warn.args[3] == 2  # kept
    assert warn.args[4] == ["charlie", "delta", "echo"]  # dropped, name-sorted


@pytest.mark.asyncio
async def test_capped_provider_under_budget_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server under the cap passes every tool through unchanged — no
    drop log, no counter."""
    counter = AsyncMock()
    monkeypatch.setattr(chats_module, "record_mcp_tools_capped", counter)
    mock_logger = MagicMock()
    monkeypatch.setattr(chats_module, "logger", mock_logger)

    tools = [_FakeTool("alpha"), _FakeTool("bravo")]
    provider = _capped(tools, max_tools=5)

    kept = await provider.load_tools()

    assert [t.tool_name for t in kept] == ["alpha", "bravo"]
    counter.assert_not_awaited()
    assert not any(
        c.args[0].startswith("mcp.tools_capped") for c in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_capped_provider_exactly_at_budget_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boundary: a server advertising exactly the budget is untouched
    (``<=`` not ``<``) — no drop, no counter."""
    counter = AsyncMock()
    monkeypatch.setattr(chats_module, "record_mcp_tools_capped", counter)

    tools = [_FakeTool("alpha"), _FakeTool("bravo"), _FakeTool("charlie")]
    provider = _capped(tools, max_tools=3)

    kept = await provider.load_tools()

    assert len(kept) == 3
    counter.assert_not_awaited()


@pytest.mark.asyncio
async def test_capped_provider_deterministic_across_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kept subset is identical on repeated enumeration — the model
    must not see a tool flicker in/out between turns."""
    monkeypatch.setattr(chats_module, "record_mcp_tools_capped", AsyncMock())

    tools = [_FakeTool(n) for n in ["zulu", "mike", "alpha", "tango"]]
    provider = _capped(tools, max_tools=2)

    first = [t.tool_name for t in await provider.load_tools()]
    second = [t.tool_name for t in await provider.load_tools()]

    assert first == second == ["alpha", "mike"]


@pytest.mark.asyncio
async def test_capped_provider_unlimited_when_nonpositive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``max_tools <= 0`` disables the cap — the full set passes through
    even when it far exceeds any positive budget."""
    counter = AsyncMock()
    monkeypatch.setattr(chats_module, "record_mcp_tools_capped", counter)

    tools = [_FakeTool(f"tool_{i}") for i in range(40)]
    provider = _capped(tools, max_tools=0)

    kept = await provider.load_tools()

    assert len(kept) == 40
    counter.assert_not_awaited()


@pytest.mark.asyncio
async def test_capped_provider_forwards_consumer_lifecycle() -> None:
    """``add_consumer`` / ``remove_consumer`` delegate to the inner client
    so the Agent's cleanup still releases the MCP background thread."""
    inner = _FakeInnerClient([_FakeTool("alpha")])
    provider = chats_module._CappedMCPToolProvider(
        inner,  # type: ignore[arg-type]
        server_id="srv-1",
        max_tools=5,
    )

    provider.add_consumer("registry-1")
    assert inner.consumers == ["registry-1"]
    provider.remove_consumer("registry-1")
    assert inner.consumers == []


def test_mcp_max_tools_per_server_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHANNEL_MCP_MAX_TOOLS_PER_SERVER", raising=False)
    assert (
        chats_module._mcp_max_tools_per_server() == chats_module._DEFAULT_MCP_MAX_TOOLS_PER_SERVER
    )


def test_mcp_max_tools_per_server_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHANNEL_MCP_MAX_TOOLS_PER_SERVER", "5")
    assert chats_module._mcp_max_tools_per_server() == 5


def test_mcp_max_tools_per_server_zero_disables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHANNEL_MCP_MAX_TOOLS_PER_SERVER", "0")
    assert chats_module._mcp_max_tools_per_server() == 0


def test_mcp_max_tools_per_server_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-integer env value must not silently drop coverage — fall
    back to the default and warn."""
    monkeypatch.setenv("CHANNEL_MCP_MAX_TOOLS_PER_SERVER", "not-a-number")
    mock_logger = MagicMock()
    monkeypatch.setattr(chats_module, "logger", mock_logger)

    result = chats_module._mcp_max_tools_per_server()

    assert result == chats_module._DEFAULT_MCP_MAX_TOOLS_PER_SERVER
    warn = next(
        c
        for c in mock_logger.warning.call_args_list
        if c.args[0].startswith("mcp.tool_budget_invalid")
    )
    assert warn.args[1] == "not-a-number"


@pytest.mark.asyncio
async def test_build_mcp_clients_two_servers_each_get_own_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget is per-server, not global: two active servers each get
    their own cap, so a user with two small servers isn't penalised for
    one big one."""
    monkeypatch.setenv("CHANNEL_MCP_MAX_TOOLS_PER_SERVER", "7")
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

    assert len(clients) == 2
    assert all(isinstance(c, chats_module._CappedMCPToolProvider) for c in clients)
    assert {c._server_id for c in clients} == {"a", "b"}
    assert all(c._max_tools == 7 for c in clients)
