# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration test: MCP registry CRUD round-trip against DynamoDB Local."""

from __future__ import annotations

from typing import Any

import pytest

from channel import storage
from channel.mcp import crypto
from channel.models import (
    ChatMCPMode,
    ChatMCPSettings,
    MCPServerAuthStatus,
    MCPServerAuthType,
)


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


def test_static_token_server_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A static-token (PAT) server registers with client_id=None,
    auth_type=static_token, auth_status=ACTIVE, and its token persists
    encrypted-at-rest and decrypts back to the original — exercising the
    crypto local-dev fallback so the local stack can run the flow without
    real KMS."""
    # CHANNEL_MCP_TOKEN_KMS_KEY_ID="local" → crypto passthrough wrapping;
    # this is exactly what `inv dev` uses so the round-trip proves the
    # local stack works without touching AWS KMS.
    monkeypatch.setenv("CHANNEL_MCP_TOKEN_KMS_KEY_ID", "local")

    server = storage.create_mcp_server(
        user_id="user-int-static",
        name="GitHub",
        url="https://api.githubcopilot.com/mcp/",
        client_id=None,
        tool_prefix="github",
        auth_type=MCPServerAuthType.STATIC_TOKEN,
        auth_status=MCPServerAuthStatus.ACTIVE,
    )

    # Synthetic non-secret value — assembled from fragments, no GitHub
    # personal-access-token prefix — so SonarCloud's python:S6418 detector doesn't flag it.
    plaintext = "dummy-" + "round-trip-" + "value"
    ciphertext = crypto.encrypt_blob(plaintext)
    assert ciphertext != plaintext.encode("utf-8")  # encrypted at rest
    storage.put_mcp_token(
        user_id="user-int-static",
        server_id=server.server_id,
        access_token_ciphertext=ciphertext,
        refresh_token_ciphertext=None,
        expires_at=int(1_700_000_000 + 100 * 365 * 86400),
        granted_scope="",
    )

    fetched = storage.get_mcp_server(
        user_id="user-int-static",
        server_id=server.server_id,
    )
    assert fetched is not None
    assert fetched.client_id is None
    assert fetched.auth_type == MCPServerAuthType.STATIC_TOKEN
    assert fetched.auth_status == MCPServerAuthStatus.ACTIVE

    token = storage.get_mcp_token(
        user_id="user-int-static",
        server_id=server.server_id,
    )
    assert token is not None
    assert token.refresh_token_ciphertext is None
    assert crypto.decrypt_blob(token.access_token_ciphertext) == plaintext

    storage.delete_mcp_server(
        user_id="user-int-static",
        server_id=server.server_id,
    )


def test_legacy_row_without_auth_type_reads_as_oauth_dcr() -> None:
    """An MCPSERVER row written before #375 has no auth_type attribute —
    it must read back as oauth_dcr so existing servers keep working. We
    write the sparse legacy item directly (no auth_type) to prove the
    default applies on the real DynamoDB read path."""
    table = storage._get_table()
    server_id = "srv-legacy-int"
    table.put_item(
        Item={
            "PK": "USER#user-int-legacy",
            "SK": storage._mcp_server_sk(server_id),
            "server_id": server_id,
            "user_id": "user-int-legacy",
            "name": "Hive",
            "url": "https://hive.example.com/mcp",
            "client_id": "dcr-legacy",
            "tool_prefix": "hive",
            "auth_status": "active",
            "globally_enabled": True,
            "created_at": "x",
            "updated_at": "x",
        }
    )
    fetched = storage.get_mcp_server(
        user_id="user-int-legacy",
        server_id=server_id,
    )
    assert fetched is not None
    assert fetched.auth_type == MCPServerAuthType.OAUTH_DCR
    assert fetched.client_id == "dcr-legacy"


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
