# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for MCP storage helpers — fake-table-backed."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from channel import storage
from channel.models import MCPServer, MCPServerAuthStatus


class _FakeTable:
    """In-memory mock of the subset of boto3 Table methods we use.

    Mirrors the fake-table pattern in tests/unit/test_chats_api.py."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, Item: dict[str, Any]) -> None:
        self.items[(Item["PK"], Item["SK"])] = Item

    def get_item(self, Key: dict[str, Any]) -> dict[str, Any]:
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def delete_item(self, Key: dict[str, Any], **_: Any) -> dict[str, Any]:
        self.items.pop((Key["PK"], Key["SK"]), None)
        return {}

    def update_item(self, Key: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        item = self.items.setdefault(
            (Key["PK"], Key["SK"]), {"PK": Key["PK"], "SK": Key["SK"]}
        )
        expression = kwargs.get("UpdateExpression", "")
        values = kwargs.get("ExpressionAttributeValues") or {}
        names = kwargs.get("ExpressionAttributeNames") or {}
        if expression.startswith("SET"):
            body = expression[3:].strip()
            for assignment in [s.strip() for s in body.split(",") if s.strip()]:
                attr, _, placeholder = assignment.partition("=")
                attr = attr.strip()
                placeholder = placeholder.strip()
                resolved = names.get(attr, attr)
                item[resolved] = values[placeholder]
        return {"Attributes": item}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        # Extremely simplified: matches PK exactly + SK begins_with prefix.
        key_cond = kwargs["KeyConditionExpression"]
        pk_value = key_cond._values[0]._values[1]
        sk_prefix = key_cond._values[1]._values[1]
        items = [
            v for (pk, sk), v in self.items.items()
            if pk == pk_value and sk.startswith(sk_prefix)
        ]
        return {"Items": items}


@pytest.fixture
def fake_table(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeTable]:
    table = _FakeTable()
    monkeypatch.setattr(storage, "_get_table", lambda: table)
    yield table


def test_create_and_get_mcp_server(fake_table: _FakeTable) -> None:
    server = storage.create_mcp_server(
        user_id="user-1",
        name="Hive",
        url="https://hive.example.com/mcp",
        client_id="dcr-1",
        tool_prefix="hive",
    )
    assert server.auth_status == MCPServerAuthStatus.NEVER_AUTHED
    assert server.globally_enabled is True

    fetched = storage.get_mcp_server(user_id="user-1", server_id=server.server_id)
    assert fetched is not None
    assert fetched.name == "Hive"


def test_list_mcp_servers_for_user(fake_table: _FakeTable) -> None:
    storage.create_mcp_server(
        user_id="user-1", name="Hive", url="https://h.example.com/mcp",
        client_id="dcr-1", tool_prefix="hive",
    )
    storage.create_mcp_server(
        user_id="user-1", name="Acme", url="https://a.example.com/mcp",
        client_id="dcr-2", tool_prefix="acme",
    )
    storage.create_mcp_server(
        user_id="user-2", name="Other", url="https://o.example.com/mcp",
        client_id="dcr-3", tool_prefix="other",
    )
    listed = storage.list_mcp_servers_for_user("user-1")
    assert {s.name for s in listed} == {"Hive", "Acme"}


def test_delete_mcp_server_wipes_token_sibling(fake_table: _FakeTable) -> None:
    server = storage.create_mcp_server(
        user_id="user-1", name="Hive", url="https://h.example.com/mcp",
        client_id="dcr-1", tool_prefix="hive",
    )
    storage.put_mcp_token(
        user_id="user-1",
        server_id=server.server_id,
        access_token_ciphertext=b"x",
        refresh_token_ciphertext=b"y",
        expires_at=1_700_000_000,
        granted_scope="read",
    )
    storage.delete_mcp_server(user_id="user-1", server_id=server.server_id)
    assert storage.get_mcp_server(
        user_id="user-1", server_id=server.server_id
    ) is None
    assert storage.get_mcp_token(
        user_id="user-1", server_id=server.server_id
    ) is None


def test_update_mcp_server_persists_name_and_flag(fake_table: _FakeTable) -> None:
    server = storage.create_mcp_server(
        user_id="user-1", name="Hive", url="https://h.example.com/mcp",
        client_id="dcr-1", tool_prefix="hive",
    )
    storage.update_mcp_server(
        user_id="user-1",
        server_id=server.server_id,
        name="Hive renamed",
        globally_enabled=False,
    )
    refreshed = storage.get_mcp_server(
        user_id="user-1", server_id=server.server_id,
    )
    assert refreshed is not None
    assert refreshed.name == "Hive renamed"
    assert refreshed.globally_enabled is False


def test_set_auth_status(fake_table: _FakeTable) -> None:
    server = storage.create_mcp_server(
        user_id="user-1", name="Hive", url="https://h.example.com/mcp",
        client_id="dcr-1", tool_prefix="hive",
    )
    storage.set_mcp_server_auth_status(
        user_id="user-1",
        server_id=server.server_id,
        status=MCPServerAuthStatus.ACTIVE,
    )
    refreshed = storage.get_mcp_server(
        user_id="user-1", server_id=server.server_id,
    )
    assert refreshed is not None
    assert refreshed.auth_status == MCPServerAuthStatus.ACTIVE
