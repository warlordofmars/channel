# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration test: MCP registry CRUD round-trip against DynamoDB Local."""

from __future__ import annotations

from typing import Any

import pytest

from channel import storage
from channel.models import ChatMCPMode, ChatMCPSettings, MCPServerAuthStatus


@pytest.fixture(autouse=True)
def _starter_table(starter_table: Any) -> Any:  # noqa: ARG001
    """Force the shared session table fixture so the table exists."""
    return starter_table


def test_register_promote_delete_round_trip() -> None:
    server = storage.create_mcp_server(
        user_id="user-int-1",
        name="Hive",
        url="https://hive.example.com/mcp",
        client_id="dcr-hive",
        tool_prefix="hive",
    )
    assert server.auth_status == MCPServerAuthStatus.NEVER_AUTHED

    storage.put_mcp_token(
        user_id="user-int-1",
        server_id=server.server_id,
        access_token_ciphertext=b"opaque",
        refresh_token_ciphertext=b"opaque-r",
        expires_at=1_700_000_000,
        granted_scope="read",
    )
    storage.set_mcp_server_auth_status(
        user_id="user-int-1",
        server_id=server.server_id,
        status=MCPServerAuthStatus.ACTIVE,
    )
    fetched = storage.get_mcp_server(
        user_id="user-int-1",
        server_id=server.server_id,
    )
    assert fetched is not None
    assert fetched.auth_status == MCPServerAuthStatus.ACTIVE
    token = storage.get_mcp_token(
        user_id="user-int-1",
        server_id=server.server_id,
    )
    assert token is not None

    storage.delete_mcp_server(
        user_id="user-int-1",
        server_id=server.server_id,
    )
    assert (
        storage.get_mcp_server(
            user_id="user-int-1",
            server_id=server.server_id,
        )
        is None
    )
    assert (
        storage.get_mcp_token(
            user_id="user-int-1",
            server_id=server.server_id,
        )
        is None
    )


def test_chat_mcp_settings_round_trip() -> None:
    storage.put_chat_mcp_settings(
        ChatMCPSettings(
            chat_id="chat-int-1",
            mode=ChatMCPMode.EXPLICIT,
            explicit_server_ids=["srv-1", "srv-2"],
        )
    )
    fetched = storage.get_chat_mcp_settings("chat-int-1")
    assert fetched.mode == ChatMCPMode.EXPLICIT
    assert fetched.explicit_server_ids == ["srv-1", "srv-2"]


def test_chat_mcp_settings_default_when_no_row() -> None:
    fetched = storage.get_chat_mcp_settings("chat-with-no-row")
    assert fetched.mode == ChatMCPMode.INHERIT
    assert fetched.explicit_server_ids == []
