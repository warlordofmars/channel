# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for MCP storage helpers — fake-table-backed."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from channel import storage
from channel.models import MCPServerAuthStatus, MCPServerAuthType


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
        condition = kwargs.get("ConditionExpression")
        existing = self.items.get((Key["PK"], Key["SK"]))
        if condition == "attribute_exists(PK)" and existing is None:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        item = self.items.setdefault((Key["PK"], Key["SK"]), {"PK": Key["PK"], "SK": Key["SK"]})
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
            v for (pk, sk), v in self.items.items() if pk == pk_value and sk.startswith(sk_prefix)
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
    # Default auth_type is oauth_dcr (unchanged behaviour).
    assert server.auth_type == MCPServerAuthType.OAUTH_DCR

    fetched = storage.get_mcp_server(user_id="user-1", server_id=server.server_id)
    assert fetched is not None
    assert fetched.name == "Hive"
    assert fetched.auth_type == MCPServerAuthType.OAUTH_DCR
    assert fetched.client_id == "dcr-1"


def test_create_static_token_server_persists_type_and_null_client_id(
    fake_table: _FakeTable,
) -> None:
    """A static-token server persists with auth_type=static_token,
    client_id=None (attribute omitted from the item), and whatever
    auth_status the caller passes (ACTIVE for a pasted PAT)."""
    server = storage.create_mcp_server(
        user_id="user-1",
        name="GitHub",
        url="https://api.githubcopilot.com/mcp/",
        client_id=None,
        tool_prefix="github",
        auth_type=MCPServerAuthType.STATIC_TOKEN,
        auth_status=MCPServerAuthStatus.ACTIVE,
    )
    assert server.client_id is None
    assert server.auth_type == MCPServerAuthType.STATIC_TOKEN
    assert server.auth_status == MCPServerAuthStatus.ACTIVE

    # The persisted DynamoDB item must NOT carry a client_id attribute —
    # keep the item sparse so the read path's ``item.get("client_id")``
    # returns None rather than tripping over a null value.
    key = ("USER#user-1", storage._mcp_server_sk(server.server_id))
    stored_item = fake_table.items[key]
    assert "client_id" not in stored_item
    assert stored_item["auth_type"] == "static_token"

    fetched = storage.get_mcp_server(user_id="user-1", server_id=server.server_id)
    assert fetched is not None
    assert fetched.client_id is None
    assert fetched.auth_type == MCPServerAuthType.STATIC_TOKEN
    assert fetched.auth_status == MCPServerAuthStatus.ACTIVE


def test_mcp_server_from_item_defaults_auth_type_for_legacy_row() -> None:
    """A pre-#375 MCPSERVER row has no ``auth_type`` attribute — it must
    read back as oauth_dcr so existing servers keep working."""
    legacy_item = {
        "server_id": "srv-legacy",
        "user_id": "user-1",
        "name": "Hive",
        "url": "https://hive.example.com/mcp",
        "client_id": "dcr-1",
        "tool_prefix": "hive",
        "auth_status": "active",
        "globally_enabled": True,
        "created_at": "x",
        "updated_at": "x",
    }
    server = storage._mcp_server_from_item(legacy_item)
    assert server.auth_type == MCPServerAuthType.OAUTH_DCR
    assert server.client_id == "dcr-1"


def test_list_mcp_servers_for_user(fake_table: _FakeTable) -> None:
    storage.create_mcp_server(
        user_id="user-1",
        name="Hive",
        url="https://h.example.com/mcp",
        client_id="dcr-1",
        tool_prefix="hive",
    )
    storage.create_mcp_server(
        user_id="user-1",
        name="Acme",
        url="https://a.example.com/mcp",
        client_id="dcr-2",
        tool_prefix="acme",
    )
    storage.create_mcp_server(
        user_id="user-2",
        name="Other",
        url="https://o.example.com/mcp",
        client_id="dcr-3",
        tool_prefix="other",
    )
    listed = storage.list_mcp_servers_for_user("user-1")
    assert {s.name for s in listed} == {"Hive", "Acme"}


def test_delete_mcp_server_wipes_token_sibling(fake_table: _FakeTable) -> None:
    server = storage.create_mcp_server(
        user_id="user-1",
        name="Hive",
        url="https://h.example.com/mcp",
        client_id="dcr-1",
        tool_prefix="hive",
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
    assert storage.get_mcp_server(user_id="user-1", server_id=server.server_id) is None
    assert storage.get_mcp_token(user_id="user-1", server_id=server.server_id) is None


def test_update_mcp_server_persists_name_and_flag(fake_table: _FakeTable) -> None:
    server = storage.create_mcp_server(
        user_id="user-1",
        name="Hive",
        url="https://h.example.com/mcp",
        client_id="dcr-1",
        tool_prefix="hive",
    )
    storage.update_mcp_server(
        user_id="user-1",
        server_id=server.server_id,
        name="Hive renamed",
        globally_enabled=False,
    )
    refreshed = storage.get_mcp_server(
        user_id="user-1",
        server_id=server.server_id,
    )
    assert refreshed is not None
    assert refreshed.name == "Hive renamed"
    assert refreshed.globally_enabled is False


def test_set_auth_status(fake_table: _FakeTable) -> None:
    server = storage.create_mcp_server(
        user_id="user-1",
        name="Hive",
        url="https://h.example.com/mcp",
        client_id="dcr-1",
        tool_prefix="hive",
    )
    storage.set_mcp_server_auth_status(
        user_id="user-1",
        server_id=server.server_id,
        status=MCPServerAuthStatus.ACTIVE,
    )
    refreshed = storage.get_mcp_server(
        user_id="user-1",
        server_id=server.server_id,
    )
    assert refreshed is not None
    assert refreshed.auth_status == MCPServerAuthStatus.ACTIVE


def test_list_mcp_servers_for_user_empty_when_no_rows(fake_table: _FakeTable) -> None:
    assert storage.list_mcp_servers_for_user("user-with-no-servers") == []


def test_get_mcp_server_returns_none_when_missing(fake_table: _FakeTable) -> None:
    assert storage.get_mcp_server(user_id="u", server_id="missing") is None


def test_get_mcp_token_returns_none_when_missing(fake_table: _FakeTable) -> None:
    assert storage.get_mcp_token(user_id="u", server_id="missing") is None


def test_get_chat_mcp_settings_returns_defaults_when_missing(fake_table: _FakeTable) -> None:
    from channel.models import ChatMCPMode

    settings = storage.get_chat_mcp_settings("missing-chat")
    assert settings.mode == ChatMCPMode.INHERIT
    assert settings.explicit_server_ids == []


def test_put_chat_mcp_settings_round_trip(fake_table: _FakeTable) -> None:
    from channel.models import ChatMCPMode, ChatMCPSettings

    storage.put_chat_mcp_settings(
        ChatMCPSettings(
            chat_id="c1",
            mode=ChatMCPMode.EXPLICIT,
            explicit_server_ids=["srv-a", "srv-b"],
        )
    )
    fetched = storage.get_chat_mcp_settings("c1")
    assert fetched.mode == ChatMCPMode.EXPLICIT
    assert fetched.explicit_server_ids == ["srv-a", "srv-b"]


def test_put_mcp_token_without_refresh_token_omits_field(fake_table: _FakeTable) -> None:
    storage.put_mcp_token(
        user_id="u",
        server_id="s",
        access_token_ciphertext=b"a",
        refresh_token_ciphertext=None,
        expires_at=1_700_000_000,
        granted_scope="read",
    )
    token = storage.get_mcp_token(user_id="u", server_id="s")
    assert token is not None
    assert token.refresh_token_ciphertext is None


def test_delete_mcp_server_idempotent(fake_table: _FakeTable) -> None:
    storage.delete_mcp_server(user_id="u", server_id="never-existed")
    # second call must also not raise
    storage.delete_mcp_server(user_id="u", server_id="never-existed")


def test_update_mcp_server_swallows_lost_race(fake_table: _FakeTable) -> None:
    """If the MCPSERVER row is deleted between the route's existence
    check and the update, DynamoDB returns a ConditionalCheckFailed
    error (the ``attribute_exists(PK)`` guard). The helper logs +
    swallows so no ghost row gets created."""
    # Row never existed — the guard should fire.
    storage.update_mcp_server(
        user_id="u",
        server_id="ghost",
        name="X",
        globally_enabled=False,
    )
    # No ghost row was created.
    assert storage.get_mcp_server(user_id="u", server_id="ghost") is None


def test_set_mcp_server_auth_status_swallows_lost_race(fake_table: _FakeTable) -> None:
    """Same guard on the auth_status path — without it, a concurrent
    delete during a chat turn's EXPIRED flip would resurrect the row
    as a partial item containing only auth_status / updated_at, which
    would crash subsequent get / list calls."""
    storage.set_mcp_server_auth_status(
        user_id="u",
        server_id="ghost",
        status=MCPServerAuthStatus.EXPIRED,
    )
    assert storage.get_mcp_server(user_id="u", server_id="ghost") is None


def test_update_mcp_server_reraises_non_conditional_client_error(
    fake_table: _FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-ConditionalCheckFailedException ClientError must NOT be
    swallowed by the lost-race guard — only the very specific
    'row vanished' code is treated as a no-op."""
    from botocore.exceptions import ClientError

    def boom(**_kwargs: Any) -> Any:
        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem"
        )

    monkeypatch.setattr(fake_table, "update_item", boom)
    with pytest.raises(ClientError):
        storage.update_mcp_server(
            user_id="u",
            server_id="s",
            name="X",
        )


def test_set_mcp_server_auth_status_reraises_non_conditional_client_error(
    fake_table: _FakeTable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same — throttling / network error must bubble out so the
    chassis log sees it instead of pretending the write succeeded."""
    from botocore.exceptions import ClientError

    def boom(**_kwargs: Any) -> Any:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "UpdateItem")

    monkeypatch.setattr(fake_table, "update_item", boom)
    with pytest.raises(ClientError):
        storage.set_mcp_server_auth_status(
            user_id="u",
            server_id="s",
            status=MCPServerAuthStatus.ACTIVE,
        )
