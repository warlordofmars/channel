# MCP-server registry + per-chat selection — Implementation Plan (#207)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship issue #207 — register MCP servers per-user, persist OAuth tokens, surface a Customize section + per-chat composer popover, and append `MCPClient` instances to the Strands tools list so MCP tools flow through the existing #181 chassis hooks identically to native tools.

**Architecture:** Channel owns OAuth 2.1 + DCR + refresh lifecycle. Strands gets a pre-authenticated transport thunk. Per the spike: `MCPClient(transport_callable=make_authenticated_transport(url, token))` slotted into `build_agent(tools=[...])`. Two new DDB SK prefixes (`MCPSERVER#{id}`, `MCPTOKEN#{server_id}`) plus a per-chat override row (`PK=CHAT#{chat_id}, SK=MCPSERVERS#META`). Tokens are KMS-encrypted at the application layer.

**Tech Stack:** FastAPI, Strands `MCPClient`, `mcp.client.streamable_http.streamable_http_client`, `mcp.client.auth.utils` (DCR + discovery helpers), `mcp.shared.auth` (pydantic OAuth shapes), `boto3` KMS, DynamoDB single-table, React, vitest.

**Authoritative references:**
- Spike: `docs/superpowers/specs/2026-06-06-mcp-server-registry-spike.md` — read this before starting Task 5.
- Issue: #207 — scope envelope, file list, and explicit out-of-scope items.
- CLAUDE.md §"DynamoDB single table design" — needs updating in Task 1.
- Push discipline: `.claude/agents/issue-worker.md` §"Push discipline".

---

## Pre-flight

The worktree is already on `feat/207-mcp-server-registry` off `origin/development`. Confirm with `git status` before starting Task 1.

This plan is **NOT** `agent-safe` — issue #207 does not carry the label. Open the PR but do **not** attach `gh pr merge --auto`.

## Local verification recipe

After all 14 tasks land but before opening the PR, drive the feature end-to-end in a running stack:

```bash
# Terminal 1 — stack
uv run inv dev --seed

# Terminal 2 — open the SPA
open http://localhost:5173/app/customize
```

1. Sign in with `?test_email=mcp-local@example.com` (dev auth bypass).
2. Open Customize → "MCP servers" → "Add server". Enter `https://hive.warlordofmars.net/mcp` (the spike's designated test server) with name "Hive".
3. Complete the OAuth flow in the popup. Confirm the server row flips `auth_status` to `active` and the row shows a green dot.
4. Open a chat. Click the composer's "N tools" pill. Confirm Hive is in the per-chat list with a checkbox.
5. Send a prompt: *"Use Hive to recall what we've discussed about Channel."* Watch the step list in `Conversation.jsx` show `hive_<tool>` firing through the existing #181 chassis hooks.
6. Toggle Hive off in the composer popover, send the same prompt, confirm the model does NOT attempt the tool.
7. Toggle globally_enabled off in Customize. Open a fresh chat. Confirm Hive is absent from the picker by default.
8. Delete the server in Customize. Confirm both the row and the sibling MCPTOKEN row disappear from DynamoDB Local (`aws dynamodb scan --table-name channel --endpoint-url http://localhost:8000`).

Then `uv run inv pre-push` (the standard gate).

---

## File structure

**New backend modules:**
- `src/channel/mcp/__init__.py` — package marker
- `src/channel/mcp/crypto.py` — KMS encrypt/decrypt for token blobs
- `src/channel/mcp/auth.py` — DCR + discovery + auth-code URL build + token exchange + refresh
- `src/channel/mcp/transports.py` — authenticated transport thunk
- `src/channel/api/mcp.py` — `/api/mcp/servers` CRUD + `/auth/mcp/callback`

**Modified backend:**
- `src/channel/models.py` — add `MCPServerAuthStatus`, `MCPServer`, `MCPToken`, `ChatMCPSettings`, `ChatMCPMode`
- `src/channel/storage.py` — append CRUD helpers for the three new row shapes
- `src/channel/api/main.py` — mount the new router
- `src/channel/api/chats.py` — wire MCP clients into `_build_tool_registry`
- `infra/stacks/channel_stack.py` — KMS key + env vars + IAM
- `CLAUDE.md` — update §"DynamoDB single table design" with the three new shapes

**New frontend:**
- `ui/src/app/AddMCPServerModal.jsx` (+ co-located test)
- `ui/src/app/MCPPicker.jsx` (+ co-located test)
- `ui/src/hooks/useMCPServers.js` (+ co-located test)

**Modified frontend:**
- `ui/src/api.js` — MCP REST wrappers
- `ui/src/app/Composer.jsx` — pop sibling, inline pill, per-chat override wiring
- `ui/src/app/views/Customize.jsx` — "MCP servers" section
- `ui/src/app/Login.jsx` — passthrough of `?mcp_authed=` query into `/app/customize` after sign-in (so the OAuth-completion redirect lands on the right URL even if the session expired mid-flow)

**Tests:**
- `tests/unit/test_mcp_crypto.py`
- `tests/unit/test_mcp_auth.py`
- `tests/unit/test_mcp_transports.py`
- `tests/unit/test_mcp_storage.py`
- `tests/unit/test_mcp_api.py`
- `tests/unit/test_chats_api_mcp.py` (or extend existing `test_chats_api.py`)
- `tests/integration/test_mcp_registry.py`
- `tests/e2e/test_mcp_hive_smoke.py` (skipped unless `STARTER_E2E_HIVE_ENABLED=1`)

---

## Task 1: Models + CLAUDE.md doc update

**Files:**
- Modify: `src/channel/models.py`
- Modify: `CLAUDE.md` (§"DynamoDB single table design")
- Test: `tests/unit/test_models.py` (extend if exists; else create)

- [ ] **Step 1.1: Write the failing test**

Append to `tests/unit/test_models.py` (create the file if it doesn't exist; use the existing Pydantic-test pattern from `test_chat_agent.py`):

```python
# Copyright (c) 2026 John Carter. All rights reserved.
from channel.models import (
    ChatMCPMode,
    ChatMCPSettings,
    MCPServer,
    MCPServerAuthStatus,
    MCPToken,
)


def test_mcp_server_defaults_globally_enabled():
    server = MCPServer(
        server_id="11111111-2222-3333-4444-555555555555",
        user_id="user-abc",
        name="Hive",
        url="https://hive.warlordofmars.net/mcp",
        client_id="dcr-xyz",
        tool_prefix="hive",
        auth_status=MCPServerAuthStatus.NEVER_AUTHED,
        created_at="2026-06-09T00:00:00+00:00",
        updated_at="2026-06-09T00:00:00+00:00",
    )
    assert server.globally_enabled is True


def test_mcp_token_carries_encrypted_blob():
    token = MCPToken(
        server_id="srv-1",
        user_id="user-abc",
        access_token_ciphertext=b"opaque",
        refresh_token_ciphertext=b"opaque-refresh",
        expires_at=1_700_000_000,
        granted_scope="read write",
        updated_at="2026-06-09T00:00:00+00:00",
    )
    assert token.access_token_ciphertext == b"opaque"


def test_chat_mcp_settings_inherit_is_default():
    settings = ChatMCPSettings(chat_id="chat-1")
    assert settings.mode == ChatMCPMode.INHERIT
    assert settings.explicit_server_ids == []
```

- [ ] **Step 1.2: Verify the test fails**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: `ImportError: cannot import name 'MCPServer' from 'channel.models'`.

- [ ] **Step 1.3: Add the models**

Append to `src/channel/models.py` (after the existing `Prefs` class — do NOT replace the file):

```python
class MCPServerAuthStatus(str, Enum):
    NEVER_AUTHED = "never_authed"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class MCPServer(BaseModel):
    """A user-registered MCP server. Matches the spike's MCPSERVER row."""

    model_config = ConfigDict(extra="forbid")

    server_id: str
    user_id: str
    name: str
    url: str  # e.g. "https://hive.warlordofmars.net/mcp"
    client_id: str  # DCR-issued; opaque to Channel
    tool_prefix: str  # passed to MCPClient(prefix=...) — must be [a-z0-9_]+
    auth_status: MCPServerAuthStatus
    globally_enabled: bool = True  # spike default — see §Question 2 rationale
    created_at: str
    updated_at: str


class MCPToken(BaseModel):
    """Per-(user, server) OAuth token bundle. Encrypted at application layer."""

    model_config = ConfigDict(extra="forbid")

    server_id: str
    user_id: str
    # KMS-encrypted blobs — see src/channel/mcp/crypto.py for the round-trip.
    access_token_ciphertext: bytes
    refresh_token_ciphertext: bytes | None = None
    expires_at: int  # Unix seconds — absolute, not relative
    granted_scope: str  # space-separated, as returned by the token endpoint
    updated_at: str


class ChatMCPMode(str, Enum):
    INHERIT = "inherit"
    EXPLICIT = "explicit"


class ChatMCPSettings(BaseModel):
    """Per-chat MCP override. Lives at PK=CHAT#{chat_id}, SK=MCPSERVERS#META."""

    model_config = ConfigDict(extra="forbid")

    chat_id: str
    mode: ChatMCPMode = ChatMCPMode.INHERIT
    explicit_server_ids: list[str] = Field(default_factory=list)
```

Make sure the imports near the top of `models.py` include `Enum`, `Field`, and `ConfigDict` (they should already — check).

- [ ] **Step 1.4: Re-run the test**

Run: `uv run pytest tests/unit/test_models.py -v -k mcp`
Expected: PASS for the three new tests.

- [ ] **Step 1.5: Update CLAUDE.md**

In CLAUDE.md, find the `## DynamoDB single table design` section. Add these bullets after the "Idempotency items" bullet:

```markdown
- MCP server items: `PK=USER#{user_id}`, `SK=MCPSERVER#{server_id}`
  (one row per registered MCP server; persists DCR client_id +
  tool_prefix + globally_enabled flag; no GSI projection)
- MCP token items: `PK=USER#{user_id}`, `SK=MCPTOKEN#{server_id}`
  (sibling row to MCPSERVER; access/refresh tokens KMS-encrypted at
  application layer; TTL set to `expires_at + 30 days` as a hard
  upper bound for the orphan-row case where DELETE lost a race)
- Chat MCP override items: `PK=CHAT#{chat_id}`, `SK=MCPSERVERS#META`
  (one optional row per chat; `mode=inherit` means "follow user's
  globally_enabled flags as of now", `mode=explicit` means
  "use exactly this list, ignoring future changes to globally_enabled")
```

- [ ] **Step 1.6: Run the full unit suite to make sure nothing broke**

Run: `uv run pytest tests/unit/ -x -q`
Expected: all green.

- [ ] **Step 1.7: Commit**

```bash
git add src/channel/models.py tests/unit/test_models.py CLAUDE.md
git commit -m "feat(mcp): pydantic models for MCPServer / MCPToken / ChatMCPSettings (#207)"
```

---

## Task 2: Storage — MCPSERVER CRUD

**Files:**
- Modify: `src/channel/storage.py`
- Test: `tests/unit/test_mcp_storage.py`

- [ ] **Step 2.1: Write the failing test**

Create `tests/unit/test_mcp_storage.py`:

```python
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
```

- [ ] **Step 2.2: Verify failure**

Run: `uv run pytest tests/unit/test_mcp_storage.py -v`
Expected: `AttributeError: module 'channel.storage' has no attribute 'create_mcp_server'`.

- [ ] **Step 2.3: Implement storage helpers**

Append to `src/channel/storage.py` (after `delete_chat_attachments`):

```python
# ----------------------------------------------------------------
# MCP servers / tokens (#207) — registry + per-chat override
# ----------------------------------------------------------------


def _mcp_server_sk(server_id: str) -> str:
    return f"MCPSERVER#{server_id}"


def _mcp_token_sk(server_id: str) -> str:
    return f"MCPTOKEN#{server_id}"


def _mcp_chat_override_sk() -> str:
    return "MCPSERVERS#META"


def _mcp_server_item(server: "MCPServer") -> dict[str, Any]:
    return {
        "PK": f"USER#{server.user_id}",
        "SK": _mcp_server_sk(server.server_id),
        "server_id": server.server_id,
        "user_id": server.user_id,
        "name": server.name,
        "url": server.url,
        "client_id": server.client_id,
        "tool_prefix": server.tool_prefix,
        "auth_status": server.auth_status.value,
        "globally_enabled": server.globally_enabled,
        "created_at": server.created_at,
        "updated_at": server.updated_at,
    }


def _mcp_server_from_item(item: dict[str, Any]) -> "MCPServer":
    from channel.models import MCPServer, MCPServerAuthStatus  # noqa: PLC0415

    return MCPServer(
        server_id=item["server_id"],
        user_id=item["user_id"],
        name=item["name"],
        url=item["url"],
        client_id=item["client_id"],
        tool_prefix=item["tool_prefix"],
        auth_status=MCPServerAuthStatus(item["auth_status"]),
        globally_enabled=bool(item.get("globally_enabled", True)),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
    )


def create_mcp_server(
    *,
    user_id: str,
    name: str,
    url: str,
    client_id: str,
    tool_prefix: str,
) -> "MCPServer":
    """Persist a freshly-registered MCP server row.

    Caller supplies the DCR-issued ``client_id`` and a normalized
    ``tool_prefix``. Auth status starts ``NEVER_AUTHED`` — the user
    completes the auth-code flow next and the callback handler flips
    this to ``ACTIVE``.
    """
    from channel.models import MCPServer, MCPServerAuthStatus  # noqa: PLC0415

    now = _now_iso()
    server = MCPServer(
        server_id=str(uuid.uuid4()),
        user_id=user_id,
        name=name,
        url=url,
        client_id=client_id,
        tool_prefix=tool_prefix,
        auth_status=MCPServerAuthStatus.NEVER_AUTHED,
        globally_enabled=True,
        created_at=now,
        updated_at=now,
    )
    _get_table().put_item(Item=_mcp_server_item(server))
    return server


def get_mcp_server(*, user_id: str, server_id: str) -> "MCPServer | None":
    result = _get_table().get_item(
        Key={"PK": f"USER#{user_id}", "SK": _mcp_server_sk(server_id)}
    )
    item = result.get("Item")
    return _mcp_server_from_item(item) if item else None


def list_mcp_servers_for_user(user_id: str) -> list["MCPServer"]:
    """List all registered MCP servers for one user. Newest first."""
    result = _get_table().query(
        KeyConditionExpression=(
            Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with("MCPSERVER#")
        ),
    )
    items = result.get("Items") or []
    servers = [_mcp_server_from_item(it) for it in items]
    # Sort newest first by created_at — list query has no implicit order.
    servers.sort(key=lambda s: s.created_at, reverse=True)
    return servers


def update_mcp_server(
    *,
    user_id: str,
    server_id: str,
    name: str | None = None,
    globally_enabled: bool | None = None,
) -> None:
    sets: list[str] = ["updated_at = :u"]
    values: dict[str, Any] = {":u": _now_iso()}
    if name is not None:
        sets.append("#n = :n")
        values[":n"] = name
    if globally_enabled is not None:
        sets.append("globally_enabled = :g")
        values[":g"] = globally_enabled
    kwargs: dict[str, Any] = {
        "Key": {"PK": f"USER#{user_id}", "SK": _mcp_server_sk(server_id)},
        "UpdateExpression": "SET " + ", ".join(sets),
        "ExpressionAttributeValues": values,
    }
    # ``name`` is a DynamoDB reserved word; alias when we touch it.
    if name is not None:
        kwargs["ExpressionAttributeNames"] = {"#n": "name"}
    _get_table().update_item(**kwargs)


def set_mcp_server_auth_status(
    *,
    user_id: str,
    server_id: str,
    status: "MCPServerAuthStatus",
) -> None:
    _get_table().update_item(
        Key={"PK": f"USER#{user_id}", "SK": _mcp_server_sk(server_id)},
        UpdateExpression="SET auth_status = :s, updated_at = :u",
        ExpressionAttributeValues={":s": status.value, ":u": _now_iso()},
    )


def delete_mcp_server(*, user_id: str, server_id: str) -> None:
    """Delete the MCPSERVER row + its sibling MCPTOKEN row.

    Idempotent — each delete_item no-ops if the row is already gone.
    Caller (API layer) is responsible for the best-effort revoke at the
    MCP server's token endpoint BEFORE this — but the revoke is not a
    correctness condition for this helper.
    """
    table = _get_table()
    table.delete_item(
        Key={"PK": f"USER#{user_id}", "SK": _mcp_server_sk(server_id)}
    )
    table.delete_item(
        Key={"PK": f"USER#{user_id}", "SK": _mcp_token_sk(server_id)}
    )


def put_mcp_token(
    *,
    user_id: str,
    server_id: str,
    access_token_ciphertext: bytes,
    refresh_token_ciphertext: bytes | None,
    expires_at: int,
    granted_scope: str,
) -> None:
    """Persist a token bundle. Ciphertext supplied by the caller.

    TTL is set to ``expires_at + 30 days`` as a hard upper bound for the
    orphan-row case where DELETE lost a race; the application's own
    expires_at check still drives refresh."""
    item: dict[str, Any] = {
        "PK": f"USER#{user_id}",
        "SK": _mcp_token_sk(server_id),
        "user_id": user_id,
        "server_id": server_id,
        "access_token_ciphertext": access_token_ciphertext,
        "expires_at": expires_at,
        "granted_scope": granted_scope,
        "updated_at": _now_iso(),
        "ttl": expires_at + (30 * 86400),
    }
    if refresh_token_ciphertext is not None:
        item["refresh_token_ciphertext"] = refresh_token_ciphertext
    _get_table().put_item(Item=item)


def get_mcp_token(*, user_id: str, server_id: str) -> "MCPToken | None":
    from channel.models import MCPToken  # noqa: PLC0415

    result = _get_table().get_item(
        Key={"PK": f"USER#{user_id}", "SK": _mcp_token_sk(server_id)}
    )
    item = result.get("Item")
    if not item:
        return None
    return MCPToken(
        server_id=item["server_id"],
        user_id=item["user_id"],
        access_token_ciphertext=bytes(item["access_token_ciphertext"]),
        refresh_token_ciphertext=(
            bytes(item["refresh_token_ciphertext"])
            if item.get("refresh_token_ciphertext") is not None
            else None
        ),
        expires_at=int(item["expires_at"]),
        granted_scope=item.get("granted_scope", ""),
        updated_at=item["updated_at"],
    )


def get_chat_mcp_settings(chat_id: str) -> "ChatMCPSettings":
    """Read the chat's MCP override. Returns defaults if no row exists."""
    from channel.models import ChatMCPMode, ChatMCPSettings  # noqa: PLC0415

    result = _get_table().get_item(
        Key={"PK": f"CHAT#{chat_id}", "SK": _mcp_chat_override_sk()}
    )
    item = result.get("Item")
    if not item:
        return ChatMCPSettings(chat_id=chat_id)
    return ChatMCPSettings(
        chat_id=chat_id,
        mode=ChatMCPMode(item.get("mode", "inherit")),
        explicit_server_ids=list(item.get("explicit_server_ids") or []),
    )


def put_chat_mcp_settings(settings: "ChatMCPSettings") -> None:
    _get_table().put_item(
        Item={
            "PK": f"CHAT#{settings.chat_id}",
            "SK": _mcp_chat_override_sk(),
            "mode": settings.mode.value,
            "explicit_server_ids": settings.explicit_server_ids,
            "updated_at": _now_iso(),
        }
    )
```

Make sure `from channel.models import MCPServer` is **not** added at module top — keep the lazy import inside helpers (matches the existing pattern; avoids a circular import with the `Attachment`/`Chat` imports already there).

- [ ] **Step 2.4: Verify the test passes**

Run: `uv run pytest tests/unit/test_mcp_storage.py -v`
Expected: all five tests PASS.

- [ ] **Step 2.5: Run full unit suite**

Run: `uv run pytest tests/unit/ -x -q`
Expected: all green.

- [ ] **Step 2.6: Commit**

```bash
git add src/channel/storage.py tests/unit/test_mcp_storage.py
git commit -m "feat(mcp): DDB storage for MCPSERVER + MCPTOKEN + chat override (#207)"
```

---

## Task 3: KMS crypto helper

Tokens at rest are encrypted at the application layer with KMS. Direct `kms.encrypt`/`kms.decrypt` because OAuth tokens fit well under the 4 KB raw-key limit and we don't need envelope encryption complexity for v1.

**Files:**
- Create: `src/channel/mcp/__init__.py`
- Create: `src/channel/mcp/crypto.py`
- Test: `tests/unit/test_mcp_crypto.py`

- [ ] **Step 3.1: Write the failing test**

Create `tests/unit/test_mcp_crypto.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for KMS token-blob crypto."""

from __future__ import annotations

from typing import Any

import pytest

from channel.mcp import crypto


class _FakeKMS:
    """Round-trippable fake KMS. ``encrypt`` prepends a sentinel; ``decrypt`` strips it."""

    def __init__(self) -> None:
        self.last_key_id: str | None = None

    def encrypt(self, **kwargs: Any) -> dict[str, Any]:
        self.last_key_id = kwargs["KeyId"]
        return {"CiphertextBlob": b"enc:" + kwargs["Plaintext"]}

    def decrypt(self, **kwargs: Any) -> dict[str, Any]:
        blob: bytes = kwargs["CiphertextBlob"]
        if not blob.startswith(b"enc:"):
            raise ValueError("not encrypted by this fake")
        return {"Plaintext": blob[4:]}


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    crypto._get_kms_client.cache_clear()


@pytest.fixture
def fake_kms(monkeypatch: pytest.MonkeyPatch) -> _FakeKMS:
    kms = _FakeKMS()
    monkeypatch.setattr(crypto, "_get_kms_client", lambda: kms)
    monkeypatch.setenv("STARTER_MCP_TOKEN_KMS_KEY_ID", "alias/test")
    return kms


def test_round_trip(fake_kms: _FakeKMS) -> None:
    plaintext = "the-bearer-token"
    ciphertext = crypto.encrypt_blob(plaintext)
    assert isinstance(ciphertext, bytes)
    assert plaintext != ciphertext.decode("latin-1")
    assert crypto.decrypt_blob(ciphertext) == plaintext


def test_encrypt_uses_configured_key(fake_kms: _FakeKMS) -> None:
    crypto.encrypt_blob("payload")
    assert fake_kms.last_key_id == "alias/test"


def test_missing_key_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STARTER_MCP_TOKEN_KMS_KEY_ID", raising=False)
    with pytest.raises(RuntimeError, match="STARTER_MCP_TOKEN_KMS_KEY_ID"):
        crypto.encrypt_blob("payload")
```

- [ ] **Step 3.2: Verify failure**

Run: `uv run pytest tests/unit/test_mcp_crypto.py -v`
Expected: `ModuleNotFoundError: No module named 'channel.mcp'`.

- [ ] **Step 3.3: Create package marker**

Create `src/channel/mcp/__init__.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""MCP-server registry + OAuth lifecycle (#207).

This package owns Channel's side of the OAuth 2.1 + DCR lifecycle for
MCP servers. Strands' ``MCPClient`` sees only the pre-authenticated
transport thunk constructed in :mod:`channel.mcp.transports`. See
``docs/superpowers/specs/2026-06-06-mcp-server-registry-spike.md`` for
the architectural seam.
"""
```

- [ ] **Step 3.4: Implement the crypto helper**

Create `src/channel/mcp/crypto.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""KMS encrypt/decrypt for MCP token blobs.

OAuth bearer tokens are small (<= 1 KB typically). Direct KMS
``encrypt``/``decrypt`` is sufficient — no envelope-encryption dance.
The KMS key ARN comes from ``STARTER_MCP_TOKEN_KMS_KEY_ID``; the CDK
stack creates a dedicated CMK with rotation enabled and grants the API
Lambda's role ``kms:Encrypt`` + ``kms:Decrypt`` on it.
"""

from __future__ import annotations

import functools
import os
from typing import Any


@functools.lru_cache(maxsize=1)
def _get_kms_client() -> Any:  # pragma: no cover - patched in tests
    import boto3  # noqa: PLC0415

    return boto3.client("kms", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))


def _key_id() -> str:
    key_id = os.environ.get("STARTER_MCP_TOKEN_KMS_KEY_ID")
    if not key_id:
        raise RuntimeError(
            "STARTER_MCP_TOKEN_KMS_KEY_ID is unset; MCP token storage requires "
            "a KMS key for application-layer encryption."
        )
    return key_id


def encrypt_blob(plaintext: str) -> bytes:
    """Encrypt a UTF-8 token string with the configured CMK."""
    client = _get_kms_client()
    resp = client.encrypt(KeyId=_key_id(), Plaintext=plaintext.encode("utf-8"))
    return bytes(resp["CiphertextBlob"])


def decrypt_blob(ciphertext: bytes) -> str:
    """Decrypt a ciphertext blob and return the original UTF-8 token."""
    client = _get_kms_client()
    resp = client.decrypt(CiphertextBlob=ciphertext)
    return bytes(resp["Plaintext"]).decode("utf-8")
```

- [ ] **Step 3.5: Verify test passes**

Run: `uv run pytest tests/unit/test_mcp_crypto.py -v`
Expected: all three tests PASS.

- [ ] **Step 3.6: Commit**

```bash
git add src/channel/mcp/__init__.py src/channel/mcp/crypto.py tests/unit/test_mcp_crypto.py
git commit -m "feat(mcp): KMS encrypt/decrypt helpers for token blobs (#207)"
```

---

## Task 4: DCR + OAuth discovery + token exchange

This task reuses `mcp.client.auth.utils` discovery helpers + `mcp.shared.auth` pydantic shapes. The orchestration (multi-request flow: SPA initiates, browser bounces, callback runs) lives in Channel because it spans HTTP requests — `mcp.client.auth.OAuthClientProvider` is single-process.

**Files:**
- Create: `src/channel/mcp/auth.py`
- Test: `tests/unit/test_mcp_auth.py`

- [ ] **Step 4.1: Write the failing test**

Create `tests/unit/test_mcp_auth.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for MCP DCR + token-endpoint helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from channel.mcp import auth as mcp_auth


@pytest.fixture
def fake_async_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Stub httpx.AsyncClient so we can intercept the requests this module makes.

    Returns a dict of named mocks so individual tests can configure responses."""
    posts: list[dict[str, Any]] = []
    gets: list[str] = []
    responses: list[httpx.Response] = []

    class _StubClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_StubClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get(self, url: str, **kwargs: Any) -> httpx.Response:
            gets.append(url)
            return responses.pop(0)

        async def post(self, url: str, **kwargs: Any) -> httpx.Response:
            posts.append({"url": url, **kwargs})
            return responses.pop(0)

    monkeypatch.setattr(mcp_auth.httpx, "AsyncClient", _StubClient)
    return {"posts": posts, "gets": gets, "responses": responses}


def _ok_json(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body, request=httpx.Request("GET", "https://x"))


@pytest.mark.asyncio
async def test_discover_resource_metadata(fake_async_client: dict[str, Any]) -> None:
    fake_async_client["responses"].append(
        _ok_json({
            "authorization_servers": ["https://hive.example.com"],
            "resource": "https://hive.example.com/mcp",
        })
    )
    meta = await mcp_auth.discover_resource_metadata("https://hive.example.com/mcp")
    assert meta.authorization_servers == ["https://hive.example.com"]


@pytest.mark.asyncio
async def test_register_dynamic_client(fake_async_client: dict[str, Any]) -> None:
    fake_async_client["responses"].append(
        _ok_json({
            "client_id": "dcr-issued-id",
            "redirect_uris": ["https://channel.example.com/auth/mcp/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })
    )
    info = await mcp_auth.register_dynamic_client(
        registration_endpoint="https://auth.example.com/register",
        redirect_uri="https://channel.example.com/auth/mcp/callback",
        client_name="Channel",
    )
    assert info.client_id == "dcr-issued-id"


def test_build_authorization_url() -> None:
    url = mcp_auth.build_authorization_url(
        authorization_endpoint="https://auth.example.com/authorize",
        client_id="dcr-1",
        redirect_uri="https://channel.example.com/auth/mcp/callback",
        state="opaque-state",
        code_challenge="challenge",
        scopes=["read", "write"],
    )
    assert url.startswith("https://auth.example.com/authorize?")
    assert "client_id=dcr-1" in url
    assert "code_challenge=challenge" in url
    assert "code_challenge_method=S256" in url
    assert "state=opaque-state" in url
    assert "scope=read+write" in url
    assert "response_type=code" in url


@pytest.mark.asyncio
async def test_exchange_code(fake_async_client: dict[str, Any]) -> None:
    fake_async_client["responses"].append(
        _ok_json({
            "access_token": "acc-1",
            "refresh_token": "ref-1",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "read",
        })
    )
    tok = await mcp_auth.exchange_code(
        token_endpoint="https://auth.example.com/token",
        client_id="dcr-1",
        redirect_uri="https://channel.example.com/auth/mcp/callback",
        code="auth-code",
        code_verifier="verifier",
    )
    assert tok.access_token == "acc-1"
    assert tok.refresh_token == "ref-1"
    assert tok.expires_in == 3600


@pytest.mark.asyncio
async def test_refresh_token(fake_async_client: dict[str, Any]) -> None:
    fake_async_client["responses"].append(
        _ok_json({
            "access_token": "acc-2",
            "expires_in": 3600,
            "token_type": "Bearer",
        })
    )
    tok = await mcp_auth.refresh_token(
        token_endpoint="https://auth.example.com/token",
        client_id="dcr-1",
        refresh_token="ref-1",
    )
    assert tok.access_token == "acc-2"


def test_generate_pkce_returns_43_to_128_chars() -> None:
    verifier, challenge = mcp_auth.generate_pkce()
    assert 43 <= len(verifier) <= 128
    assert 43 <= len(challenge) <= 128
    assert verifier != challenge
```

- [ ] **Step 4.2: Verify failure**

Run: `uv run pytest tests/unit/test_mcp_auth.py -v`
Expected: `ImportError`.

- [ ] **Step 4.3: Implement the auth helpers**

Create `src/channel/mcp/auth.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""OAuth 2.1 + DCR helpers for Channel's MCP-server lifecycle.

Reuses the protocol-level building blocks from ``mcp.client.auth.utils``
(discovery + DCR shapes) and ``mcp.shared.auth`` (pydantic OAuth
models). The flow orchestration lives here because Channel's auth-code
dance spans multiple HTTP requests — :mod:`mcp.client.auth.oauth2`'s
``OAuthClientProvider`` is built for a single-process flow.

Public surface:
  * :func:`discover_resource_metadata` — RFC 9728 protected-resource discovery
  * :func:`discover_auth_server_metadata` — RFC 8414 authorization-server discovery
  * :func:`register_dynamic_client` — RFC 7591 dynamic-client registration
  * :func:`build_authorization_url` — RFC 6749 §4.1.1 + PKCE (RFC 7636) URL build
  * :func:`exchange_code` — authorization-code → token exchange
  * :func:`refresh_token` — refresh-token → token exchange
  * :func:`generate_pkce` — PKCE code_verifier + S256 challenge pair
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import string
from urllib.parse import urlencode

import httpx
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)

# Discovery endpoint suffixes (RFC 8414 / RFC 9728).
_PRM_PATH = "/.well-known/oauth-protected-resource"
_AS_PATH = "/.well-known/oauth-authorization-server"

# 30s is generous for token-endpoint round-trips; longer than the chain
# wall-clock budget would mask refresh stalls behind the cancel signal.
_HTTP_TIMEOUT_SEC = 30.0


def generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for PKCE S256."""
    verifier = "".join(
        secrets.choice(string.ascii_letters + string.digits + "-._~")
        for _ in range(128)
    )
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


async def discover_resource_metadata(server_url: str) -> ProtectedResourceMetadata:
    """Fetch the protected-resource metadata for an MCP server URL.

    Uses RFC 9728 — the ``.well-known/oauth-protected-resource`` endpoint
    served at the resource's origin. Returns the parsed
    :class:`ProtectedResourceMetadata` from ``mcp.shared.auth``.
    """
    origin = _origin_of(server_url)
    url = origin + _PRM_PATH
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return ProtectedResourceMetadata.model_validate(resp.json())


async def discover_auth_server_metadata(auth_server_url: str) -> OAuthMetadata:
    """Fetch RFC 8414 authorization-server metadata."""
    origin = _origin_of(auth_server_url)
    url = origin + _AS_PATH
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return OAuthMetadata.model_validate(resp.json())


async def register_dynamic_client(
    *,
    registration_endpoint: str,
    redirect_uri: str,
    client_name: str = "Channel",
) -> OAuthClientInformationFull:
    """Register a Channel-side DCR client at the MCP server's auth server.

    Returns the issued client info — ``client_id`` is opaque to Channel
    and stored on the MCPSERVER row. Public-client posture
    (``token_endpoint_auth_method = "none"``) — Channel uses PKCE so no
    client secret is required.
    """
    body = {
        "redirect_uris": [redirect_uri],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": client_name,
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = await client.post(registration_endpoint, json=body)
    resp.raise_for_status()
    return OAuthClientInformationFull.model_validate(resp.json())


def build_authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scopes: list[str] | None = None,
) -> str:
    """Build the RFC 6749 §4.1.1 authorization URL with PKCE S256."""
    params: list[tuple[str, str]] = [
        ("response_type", "code"),
        ("client_id", client_id),
        ("redirect_uri", redirect_uri),
        ("state", state),
        ("code_challenge", code_challenge),
        ("code_challenge_method", "S256"),
    ]
    if scopes:
        params.append(("scope", " ".join(scopes)))
    sep = "&" if "?" in authorization_endpoint else "?"
    return authorization_endpoint + sep + urlencode(params)


async def exchange_code(
    *,
    token_endpoint: str,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> OAuthToken:
    """Exchange an auth code for tokens (RFC 6749 §4.1.3 + PKCE)."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = await client.post(
            token_endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    resp.raise_for_status()
    return OAuthToken.model_validate(resp.json())


async def refresh_token(
    *,
    token_endpoint: str,
    client_id: str,
    refresh_token: str,
) -> OAuthToken:
    """Exchange a refresh token for a fresh access token (RFC 6749 §6)."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = await client.post(
            token_endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    resp.raise_for_status()
    return OAuthToken.model_validate(resp.json())


def _origin_of(url: str) -> str:
    """Strip path/query from an MCP server URL to its scheme+host[:port] root."""
    from urllib.parse import urlparse  # noqa: PLC0415

    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"
```

- [ ] **Step 4.4: Add the pytest-asyncio dep marker**

Open `pyproject.toml`. Find the `[tool.pytest.ini_options]` section. Confirm `asyncio_mode = "auto"` or that the project already uses `@pytest.mark.asyncio`. If neither, add to that section:

```toml
asyncio_mode = "auto"
```

(Check `tests/unit/test_chats_api.py` first — that suite already uses async tests; whatever pattern it uses is the existing one. Don't change config unnecessarily.)

- [ ] **Step 4.5: Run the test**

Run: `uv run pytest tests/unit/test_mcp_auth.py -v`
Expected: all six tests PASS.

- [ ] **Step 4.6: Commit**

```bash
git add src/channel/mcp/auth.py tests/unit/test_mcp_auth.py
git commit -m "feat(mcp): OAuth 2.1 + DCR helpers (discover/register/auth-url/exchange/refresh) (#207)"
```

---

## Task 5: Transport thunk + token-resolution helper

The chassis seam — exactly the spike's load-bearing one-liner integration surface.

**Files:**
- Create: `src/channel/mcp/transports.py`
- Modify: `src/channel/mcp/__init__.py`
- Test: `tests/unit/test_mcp_transports.py`

- [ ] **Step 5.1: Write the failing test**

Create `tests/unit/test_mcp_transports.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the authenticated transport thunk."""

from __future__ import annotations

from typing import Any

from channel.mcp.transports import make_authenticated_transport


def test_thunk_returns_streamable_http_context_manager() -> None:
    """The thunk wraps mcp.client.streamable_http.streamable_http_client.

    Strands' MCPClient calls the thunk and ``async with``-es the result;
    that result must be the async-context-manager from mcp's transport
    module. We can't actually open the connection in a unit test (no
    real server) but we can verify the call shape."""
    thunk = make_authenticated_transport(
        server_url="https://hive.example.com/mcp",
        access_token="bearer-abc",
    )
    ctx = thunk()
    # The mcp lib's streamable_http_client returns an @asynccontextmanager
    # decorated function. Calling it yields an _AsyncGeneratorContextManager.
    assert hasattr(ctx, "__aenter__")
    assert hasattr(ctx, "__aexit__")


def test_thunk_attaches_authorization_header(monkeypatch: Any) -> None:
    """The httpx.AsyncClient handed to streamable_http_client carries the
    Authorization header for every request the MCP transport makes."""
    captured: dict[str, Any] = {}

    class _FakeAsyncClient:
        def __init__(self, *args: Any, headers: dict[str, str] | None = None, **kwargs: Any) -> None:
            captured["headers"] = headers

    from channel.mcp import transports

    monkeypatch.setattr(transports.httpx, "AsyncClient", _FakeAsyncClient)
    thunk = transports.make_authenticated_transport(
        server_url="https://hive.example.com/mcp",
        access_token="bearer-abc",
    )
    # Calling the thunk constructs the AsyncClient internally.
    thunk()
    assert captured["headers"] == {"Authorization": "Bearer bearer-abc"}
```

- [ ] **Step 5.2: Verify failure**

Run: `uv run pytest tests/unit/test_mcp_transports.py -v`
Expected: `ImportError`.

- [ ] **Step 5.3: Implement the transport helper**

Create `src/channel/mcp/transports.py`:

```python
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
```

- [ ] **Step 5.4: Run test**

Run: `uv run pytest tests/unit/test_mcp_transports.py -v`
Expected: both tests PASS.

- [ ] **Step 5.5: Commit**

```bash
git add src/channel/mcp/transports.py tests/unit/test_mcp_transports.py
git commit -m "feat(mcp): authenticated transport thunk for Strands MCPClient (#207)"
```

---

## Task 6: Token-resolution helper (lazy refresh)

Reads the MCPTOKEN row, decrypts, refreshes if within 60s of expiry, returns the plaintext access token. Reused by both the chassis-side `_build_mcp_clients_for_chat` (Task 8) and the reauth REST endpoint.

**Files:**
- Modify: `src/channel/mcp/auth.py` (extend) — add `get_valid_access_token`
- Test: `tests/unit/test_mcp_auth.py` (extend)

- [ ] **Step 6.1: Write the failing test**

Append to `tests/unit/test_mcp_auth.py`:

```python
import time

from channel import storage
from channel.models import MCPServer, MCPServerAuthStatus


class _FakeTable:
    """Minimal in-memory table for storage helpers."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, Item: dict[str, Any]) -> None:
        self.items[(Item["PK"], Item["SK"])] = Item

    def get_item(self, Key: dict[str, Any]) -> dict[str, Any]:
        it = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": it} if it else {}


@pytest.fixture
def storage_table(monkeypatch: pytest.MonkeyPatch) -> _FakeTable:
    table = _FakeTable()
    monkeypatch.setattr(storage, "_get_table", lambda: table)
    return table


@pytest.fixture
def fake_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.mcp import crypto

    monkeypatch.setattr(crypto, "encrypt_blob", lambda s: ("ENC::" + s).encode())
    monkeypatch.setattr(
        crypto, "decrypt_blob",
        lambda b: b.decode("utf-8").removeprefix("ENC::"),
    )


@pytest.mark.asyncio
async def test_get_valid_access_token_returns_cached_when_not_expiring(
    storage_table: _FakeTable, fake_crypto: None
) -> None:
    storage.put_mcp_token(
        user_id="u1",
        server_id="s1",
        access_token_ciphertext=b"ENC::acc-cached",
        refresh_token_ciphertext=b"ENC::ref-1",
        expires_at=int(time.time()) + 600,  # well above the 60s skew window
        granted_scope="read",
    )
    token = await mcp_auth.get_valid_access_token(
        user_id="u1",
        server=MCPServer(
            server_id="s1", user_id="u1", name="Hive",
            url="https://hive.example.com/mcp",
            client_id="dcr-1", tool_prefix="hive",
            auth_status=MCPServerAuthStatus.ACTIVE,
            created_at="x", updated_at="x",
        ),
    )
    assert token == "acc-cached"


@pytest.mark.asyncio
async def test_get_valid_access_token_refreshes_when_expiring(
    storage_table: _FakeTable, fake_crypto: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage.put_mcp_token(
        user_id="u1",
        server_id="s1",
        access_token_ciphertext=b"ENC::stale",
        refresh_token_ciphertext=b"ENC::ref-1",
        expires_at=int(time.time()) + 30,  # within the 60s refresh skew window
        granted_scope="read",
    )

    async def fake_discover(_url: str) -> Any:
        from mcp.shared.auth import ProtectedResourceMetadata
        return ProtectedResourceMetadata(
            authorization_servers=["https://auth.example.com"],
            resource="https://hive.example.com/mcp",
        )

    async def fake_discover_as(_url: str) -> Any:
        from mcp.shared.auth import OAuthMetadata
        return OAuthMetadata(
            issuer="https://auth.example.com",
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            response_types_supported=["code"],
        )

    async def fake_refresh(**_: Any) -> Any:
        from mcp.shared.auth import OAuthToken
        return OAuthToken(
            access_token="fresh-acc",
            refresh_token="fresh-ref",
            expires_in=3600,
            token_type="Bearer",
        )

    monkeypatch.setattr(mcp_auth, "discover_resource_metadata", fake_discover)
    monkeypatch.setattr(mcp_auth, "discover_auth_server_metadata", fake_discover_as)
    monkeypatch.setattr(mcp_auth, "refresh_token", fake_refresh)

    token = await mcp_auth.get_valid_access_token(
        user_id="u1",
        server=MCPServer(
            server_id="s1", user_id="u1", name="Hive",
            url="https://hive.example.com/mcp",
            client_id="dcr-1", tool_prefix="hive",
            auth_status=MCPServerAuthStatus.ACTIVE,
            created_at="x", updated_at="x",
        ),
    )
    assert token == "fresh-acc"
    # Confirm the refreshed token landed back in storage.
    refreshed = storage.get_mcp_token(user_id="u1", server_id="s1")
    assert refreshed is not None
    assert refreshed.access_token_ciphertext == b"ENC::fresh-acc"
```

- [ ] **Step 6.2: Verify failure**

Run: `uv run pytest tests/unit/test_mcp_auth.py::test_get_valid_access_token_returns_cached_when_not_expiring -v`
Expected: `AttributeError: ... no attribute 'get_valid_access_token'`.

- [ ] **Step 6.3: Implement the resolver**

Append to `src/channel/mcp/auth.py`:

```python
import time

from channel import storage
from channel.mcp import crypto
from channel.models import MCPServer, MCPServerAuthStatus

# Refresh skew: if a token expires within this many seconds, force a
# refresh before the next tool call. Bigger window = more refreshes;
# smaller window = higher chance of mid-call expiry. 60s matches the
# spike's "Lazy check on every tool call" recommendation.
_REFRESH_SKEW_SEC = 60


class MCPAuthFailedError(RuntimeError):
    """Raised when token refresh fails — caller should surface as
    ``sse_tool_error(error_type="auth_expired")`` and flip the
    MCPSERVER row's auth_status to EXPIRED."""


async def get_valid_access_token(
    *,
    user_id: str,
    server: MCPServer,
) -> str:
    """Resolve the current access token for ``(user_id, server)``.

    Refreshes if within :data:`_REFRESH_SKEW_SEC` of expiry. Persists
    the refreshed token transparently. Raises
    :class:`MCPAuthFailedError` when refresh fails or no token exists.
    """
    token = storage.get_mcp_token(user_id=user_id, server_id=server.server_id)
    if token is None:
        raise MCPAuthFailedError(
            f"no token row for user={user_id} server={server.server_id}"
        )
    now = int(time.time())
    if token.expires_at - now > _REFRESH_SKEW_SEC:
        return crypto.decrypt_blob(token.access_token_ciphertext)

    # Refresh required.
    if token.refresh_token_ciphertext is None:
        raise MCPAuthFailedError(
            f"token expired and no refresh token user={user_id} server={server.server_id}"
        )
    refresh_plain = crypto.decrypt_blob(token.refresh_token_ciphertext)
    try:
        prm = await discover_resource_metadata(server.url)
        # MCP servers either point at an external auth server or self-issue.
        as_url = (
            prm.authorization_servers[0]
            if prm.authorization_servers
            else server.url
        )
        as_meta = await discover_auth_server_metadata(as_url)
        new_tok = await refresh_token(
            token_endpoint=str(as_meta.token_endpoint),
            client_id=server.client_id,
            refresh_token=refresh_plain,
        )
    except httpx.HTTPError as exc:
        raise MCPAuthFailedError(
            f"refresh round-trip failed user={user_id} server={server.server_id}"
        ) from exc

    new_expires_at = now + (new_tok.expires_in or 3600)
    new_refresh = new_tok.refresh_token or refresh_plain
    storage.put_mcp_token(
        user_id=user_id,
        server_id=server.server_id,
        access_token_ciphertext=crypto.encrypt_blob(new_tok.access_token),
        refresh_token_ciphertext=crypto.encrypt_blob(new_refresh),
        expires_at=new_expires_at,
        granted_scope=new_tok.scope or token.granted_scope,
    )
    return new_tok.access_token
```

Note: keep the `import time` at the top of the module (not at the bottom — move the existing `import` lines if needed).

- [ ] **Step 6.4: Re-run tests**

Run: `uv run pytest tests/unit/test_mcp_auth.py -v`
Expected: all eight tests PASS.

- [ ] **Step 6.5: Commit**

```bash
git add src/channel/mcp/auth.py tests/unit/test_mcp_auth.py
git commit -m "feat(mcp): lazy access-token resolver with skew-based refresh (#207)"
```

---

## Task 7: REST surface — list, register, callback

The OAuth callback endpoint lives outside `/api/*` (it's a browser redirect target, not a JWT-authenticated route). The state-store payload carries `code_verifier`, `server_id`, `user_id`.

**Files:**
- Create: `src/channel/api/mcp.py`
- Modify: `src/channel/api/main.py`
- Test: `tests/unit/test_mcp_api.py`

- [ ] **Step 7.1: Write the failing test**

Create `tests/unit/test_mcp_api.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the /api/mcp/* + /auth/mcp/callback surface."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    # Tests rely on the existing FastAPI test fixtures pattern — same
    # shape as tests/unit/test_chats_api.py (look there for the JWT-
    # minting helper). Keep this fixture small; reuse those helpers.
    from channel.api import main as main_mod
    return main_mod.app


@pytest.fixture
def jwt_for(monkeypatch: pytest.MonkeyPatch) -> Any:
    # Re-use the helper from test_chats_api.py — see how that file mints
    # a test JWT against the local STARTER_JWT_SECRET env. If the helper
    # is not already shared, factor it into a tests/unit/_jwt_helpers.py
    # in Step 7.5 before this test runs.
    from tests.unit._jwt_helpers import mint_test_jwt  # noqa: PLC0415
    return mint_test_jwt


def test_list_servers_returns_empty(
    app: Any, jwt_for: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel import storage

    monkeypatch.setattr(storage, "list_mcp_servers_for_user", lambda u: [])
    client = TestClient(app)
    jwt = jwt_for("user-1")
    resp = client.get("/api/mcp/servers", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 200
    assert resp.json() == {"servers": []}


def test_register_server_runs_dcr_and_returns_auth_url(
    app: Any, jwt_for: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp.shared.auth import (
        OAuthClientInformationFull,
        OAuthMetadata,
        ProtectedResourceMetadata,
    )

    from channel import storage
    from channel.mcp import auth as mcp_auth
    from channel.models import MCPServer, MCPServerAuthStatus

    monkeypatch.setattr(
        mcp_auth, "discover_resource_metadata",
        AsyncMock(return_value=ProtectedResourceMetadata(
            authorization_servers=["https://auth.example.com"],
            resource="https://hive.example.com/mcp",
        )),
    )
    monkeypatch.setattr(
        mcp_auth, "discover_auth_server_metadata",
        AsyncMock(return_value=OAuthMetadata(
            issuer="https://auth.example.com",
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            registration_endpoint="https://auth.example.com/register",
            response_types_supported=["code"],
        )),
    )
    monkeypatch.setattr(
        mcp_auth, "register_dynamic_client",
        AsyncMock(return_value=OAuthClientInformationFull(
            client_id="dcr-new",
            redirect_uris=["https://channel.example.com/auth/mcp/callback"],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        )),
    )
    created: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> MCPServer:
        created.update(kwargs)
        return MCPServer(
            server_id="srv-1", user_id="user-1", name=kwargs["name"],
            url=kwargs["url"], client_id=kwargs["client_id"],
            tool_prefix=kwargs["tool_prefix"],
            auth_status=MCPServerAuthStatus.NEVER_AUTHED,
            created_at="x", updated_at="x",
        )

    monkeypatch.setattr(storage, "create_mcp_server", fake_create)

    monkeypatch.setenv(
        "STARTER_MCP_REDIRECT_URI", "https://channel.example.com/auth/mcp/callback",
    )

    client = TestClient(app)
    jwt = jwt_for("user-1")
    resp = client.post(
        "/api/mcp/servers",
        json={"name": "Hive", "url": "https://hive.example.com/mcp", "tool_prefix": "hive"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["server_id"] == "srv-1"
    assert body["auth_start_url"].startswith("https://auth.example.com/authorize?")
    assert created["client_id"] == "dcr-new"
```

- [ ] **Step 7.2: Extract a shared JWT minting helper for tests**

Look at `tests/unit/test_chats_api.py` near the top — it almost certainly has a `mint_test_jwt` or similar function. Two options:

  - If it's already module-private, factor it out into `tests/unit/_jwt_helpers.py` with public `mint_test_jwt(sub: str) -> str`, then import it in both test files.
  - If it doesn't exist (lookups happen via a fixture), create the helper from scratch following the JWT-minting pattern in `src/channel/auth/tokens.py`.

Run `grep -n "def mint\|def _make_jwt\|def make_jwt" tests/unit/test_chats_api.py` to find the existing shape; then either move or replicate it. Commit the test helper separately:

```bash
git add tests/unit/_jwt_helpers.py tests/unit/test_chats_api.py
git commit -m "test: extract mint_test_jwt helper for reuse (#207)"
```

(If the helper was already shared elsewhere, skip this step.)

- [ ] **Step 7.3: Verify the test fails**

Run: `uv run pytest tests/unit/test_mcp_api.py -v`
Expected: `ModuleNotFoundError: No module named 'channel.api.mcp'`.

- [ ] **Step 7.4: Implement the router**

Create `src/channel/api/mcp.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""REST surface for MCP-server registry + OAuth callback.

Routes:
  * ``GET    /api/mcp/servers`` — list the user's registered servers
  * ``POST   /api/mcp/servers`` — register new + run DCR + return auth_start_url
  * ``PATCH  /api/mcp/servers/{id}`` — rename / toggle globally_enabled
  * ``DELETE /api/mcp/servers/{id}`` — best-effort revoke + delete rows
  * ``POST   /api/mcp/servers/{id}/reauth`` — re-trigger auth-code flow
  * ``GET    /api/chats/{chat_id}/mcp`` — read per-chat override
  * ``PUT    /api/chats/{chat_id}/mcp`` — write per-chat override
  * ``GET    /auth/mcp/callback`` — browser-redirected OAuth callback

The callback router intentionally lives outside ``/api/*`` because it's
a browser-redirect target carrying ``?code=&state=``, not a
JWT-authenticated endpoint. State verification is the security barrier —
:mod:`channel.auth.state_store` provides atomic consume-once semantics.

For per-chat ownership, the read/write routes call
``_load_owned_chat(chat_id, jwt_sub)`` from :mod:`channel.api.chats`
(re-export there or move into a shared module — see Step 7.4 below).
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from channel import storage
from channel.api._auth import require_mgmt_user
from channel.api.chats import _load_owned_chat
from channel.auth import state_store
from channel.mcp import auth as mcp_auth
from channel.mcp import crypto
from channel.models import (
    ChatMCPMode,
    ChatMCPSettings,
    MCPServer,
    MCPServerAuthStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Routes that should NOT carry the /api prefix mount under this router.
callback_router = APIRouter()


_TOOL_PREFIX_OK_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def _normalize_tool_prefix(value: str | None, fallback_url: str) -> str:
    """Pick a Strands-safe tool prefix.

    User-supplied prefixes are lowercased + filtered to ``[a-z0-9_]+``.
    On empty / missing input, fall back to a sanitised host token from
    the URL (e.g. ``https://hive.warlordofmars.net/mcp`` → ``hive``).
    """
    if value:
        cleaned = "".join(c for c in value.lower() if c in _TOOL_PREFIX_OK_CHARS)
        if cleaned:
            return cleaned
    # Fallback: first label of the URL host.
    from urllib.parse import urlparse  # noqa: PLC0415

    host = urlparse(fallback_url).hostname or "mcp"
    return "".join(c for c in host.split(".")[0].lower() if c in _TOOL_PREFIX_OK_CHARS) or "mcp"


def _redirect_uri() -> str:
    uri = os.environ.get("STARTER_MCP_REDIRECT_URI")
    if not uri:
        raise HTTPException(
            status_code=503,
            detail="MCP registry is not configured (STARTER_MCP_REDIRECT_URI unset)",
        )
    return uri


def _spa_base_url() -> str:
    return os.environ.get("STARTER_SPA_BASE_URL", "http://localhost:5173")


# ---------- list / register ----------


class _ServerOut(BaseModel):
    server_id: str
    name: str
    url: str
    tool_prefix: str
    auth_status: MCPServerAuthStatus
    globally_enabled: bool
    created_at: str
    updated_at: str


class _ServerListResponse(BaseModel):
    servers: list[_ServerOut]


@router.get("/api/mcp/servers", response_model=_ServerListResponse)
def list_servers(
    response: Response,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> _ServerListResponse:
    response.headers["Cache-Control"] = "no-store"
    servers = storage.list_mcp_servers_for_user(claims["sub"])
    return _ServerListResponse(
        servers=[_to_out(s) for s in servers]
    )


def _to_out(s: MCPServer) -> _ServerOut:
    return _ServerOut(
        server_id=s.server_id, name=s.name, url=s.url,
        tool_prefix=s.tool_prefix, auth_status=s.auth_status,
        globally_enabled=s.globally_enabled,
        created_at=s.created_at, updated_at=s.updated_at,
    )


class _RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    url: str = Field(..., min_length=10, max_length=2048)
    tool_prefix: str | None = None


class _RegisterResponse(BaseModel):
    server_id: str
    auth_start_url: str


@router.post("/api/mcp/servers", response_model=_RegisterResponse)
async def register_server(
    body: _RegisterRequest,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> _RegisterResponse:
    redirect_uri = _redirect_uri()
    if not body.url.startswith(("https://", "http://localhost")):
        raise HTTPException(status_code=400, detail="MCP server URL must use https")
    try:
        prm = await mcp_auth.discover_resource_metadata(body.url)
        as_url = (
            prm.authorization_servers[0]
            if prm.authorization_servers
            else body.url
        )
        as_meta = await mcp_auth.discover_auth_server_metadata(as_url)
        if not as_meta.registration_endpoint:
            raise HTTPException(
                status_code=400,
                detail="MCP server's auth server does not support dynamic client registration",
            )
        client_info = await mcp_auth.register_dynamic_client(
            registration_endpoint=str(as_meta.registration_endpoint),
            redirect_uri=redirect_uri,
            client_name="Channel",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("mcp.register.discovery_or_dcr_failed url=%s %r", body.url, exc)
        raise HTTPException(
            status_code=502,
            detail="Failed to register with MCP server (discovery or DCR)",
        ) from exc

    tool_prefix = _normalize_tool_prefix(body.tool_prefix, body.url)
    server = storage.create_mcp_server(
        user_id=claims["sub"],
        name=body.name,
        url=body.url,
        client_id=client_info.client_id,
        tool_prefix=tool_prefix,
    )
    auth_start_url = _begin_auth_flow(
        user_id=claims["sub"],
        server=server,
        auth_endpoint=str(as_meta.authorization_endpoint),
        redirect_uri=redirect_uri,
    )
    return _RegisterResponse(server_id=server.server_id, auth_start_url=auth_start_url)


def _begin_auth_flow(
    *,
    user_id: str,
    server: MCPServer,
    auth_endpoint: str,
    redirect_uri: str,
) -> str:
    code_verifier, code_challenge = mcp_auth.generate_pkce()
    state = secrets.token_urlsafe(32)
    state_store.put_state(
        state,
        payload={
            "purpose": "mcp",
            "user_id": user_id,
            "server_id": server.server_id,
            "code_verifier": code_verifier,
            "auth_endpoint": auth_endpoint,
            "redirect_uri": redirect_uri,
        },
        ttl_seconds=900,  # 15 min — generous for the OAuth round-trip
    )
    return mcp_auth.build_authorization_url(
        authorization_endpoint=auth_endpoint,
        client_id=server.client_id,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
    )


# ---------- callback ----------


@callback_router.get("/auth/mcp/callback")
async def mcp_callback(state: str, code: str | None = None, error: str | None = None) -> Any:
    """Handle the OAuth callback. Exchanges code → tokens, stores them,
    redirects browser to the SPA's Customize view with a status param.
    """
    spa = _spa_base_url()
    if error:
        return RedirectResponse(
            url=f"{spa}/app/customize?mcp_authed=error&reason={error}",
            status_code=302,
        )
    payload = state_store.consume_state(state)
    if not payload or payload.get("purpose") != "mcp":
        return RedirectResponse(
            url=f"{spa}/app/customize?mcp_authed=error&reason=invalid_state",
            status_code=302,
        )
    if not code:
        return RedirectResponse(
            url=f"{spa}/app/customize?mcp_authed=error&reason=no_code",
            status_code=302,
        )
    user_id = payload["user_id"]
    server_id = payload["server_id"]
    code_verifier = payload["code_verifier"]
    redirect_uri = payload["redirect_uri"]

    server = storage.get_mcp_server(user_id=user_id, server_id=server_id)
    if server is None:
        return RedirectResponse(
            url=f"{spa}/app/customize?mcp_authed=error&reason=server_gone",
            status_code=302,
        )
    try:
        prm = await mcp_auth.discover_resource_metadata(server.url)
        as_url = (
            prm.authorization_servers[0] if prm.authorization_servers else server.url
        )
        as_meta = await mcp_auth.discover_auth_server_metadata(as_url)
        tok = await mcp_auth.exchange_code(
            token_endpoint=str(as_meta.token_endpoint),
            client_id=server.client_id,
            redirect_uri=redirect_uri,
            code=code,
            code_verifier=code_verifier,
        )
    except Exception as exc:
        logger.warning("mcp.callback.token_exchange_failed user=%s server=%s %r",
                       user_id, server_id, exc)
        return RedirectResponse(
            url=f"{spa}/app/customize?mcp_authed=error&reason=token_exchange",
            status_code=302,
        )

    expires_at = int(time.time()) + (tok.expires_in or 3600)
    storage.put_mcp_token(
        user_id=user_id,
        server_id=server_id,
        access_token_ciphertext=crypto.encrypt_blob(tok.access_token),
        refresh_token_ciphertext=(
            crypto.encrypt_blob(tok.refresh_token) if tok.refresh_token else None
        ),
        expires_at=expires_at,
        granted_scope=tok.scope or "",
    )
    storage.set_mcp_server_auth_status(
        user_id=user_id,
        server_id=server_id,
        status=MCPServerAuthStatus.ACTIVE,
    )
    return RedirectResponse(
        url=f"{spa}/app/customize?mcp_authed=ok&server_id={server_id}",
        status_code=302,
    )


# ---------- patch / delete / reauth ----------


class _PatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    globally_enabled: bool | None = None


@router.patch("/api/mcp/servers/{server_id}", status_code=204)
def patch_server(
    server_id: str,
    body: _PatchRequest,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> None:
    s = storage.get_mcp_server(user_id=claims["sub"], server_id=server_id)
    if s is None:
        raise HTTPException(status_code=404, detail="server not found")
    storage.update_mcp_server(
        user_id=claims["sub"],
        server_id=server_id,
        name=body.name,
        globally_enabled=body.globally_enabled,
    )


@router.delete("/api/mcp/servers/{server_id}", status_code=204)
def delete_server(
    server_id: str,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> None:
    s = storage.get_mcp_server(user_id=claims["sub"], server_id=server_id)
    if s is None:
        raise HTTPException(status_code=404, detail="server not found")
    # Best-effort revoke is out of v1 — RFC 7009 token revocation is
    # optional in OAuth 2.1 and the MCP servers we target don't advertise
    # it consistently. Delete + let the server's own token expiry handle
    # cleanup. Tracked as v2.
    storage.delete_mcp_server(user_id=claims["sub"], server_id=server_id)


@router.post("/api/mcp/servers/{server_id}/reauth", response_model=_RegisterResponse)
async def reauth_server(
    server_id: str,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> _RegisterResponse:
    s = storage.get_mcp_server(user_id=claims["sub"], server_id=server_id)
    if s is None:
        raise HTTPException(status_code=404, detail="server not found")
    redirect_uri = _redirect_uri()
    try:
        prm = await mcp_auth.discover_resource_metadata(s.url)
        as_url = (
            prm.authorization_servers[0] if prm.authorization_servers else s.url
        )
        as_meta = await mcp_auth.discover_auth_server_metadata(as_url)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Failed to refresh discovery metadata"
        ) from exc
    auth_url = _begin_auth_flow(
        user_id=claims["sub"],
        server=s,
        auth_endpoint=str(as_meta.authorization_endpoint),
        redirect_uri=redirect_uri,
    )
    return _RegisterResponse(server_id=s.server_id, auth_start_url=auth_url)


# ---------- per-chat override ----------


class _ChatMCPSettingsOut(BaseModel):
    mode: ChatMCPMode
    explicit_server_ids: list[str]


@router.get("/api/chats/{chat_id}/mcp", response_model=_ChatMCPSettingsOut)
async def get_chat_mcp_settings(
    chat_id: str,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> _ChatMCPSettingsOut:
    await _load_owned_chat(chat_id, claims["sub"])
    s = storage.get_chat_mcp_settings(chat_id)
    return _ChatMCPSettingsOut(mode=s.mode, explicit_server_ids=s.explicit_server_ids)


class _ChatMCPSettingsIn(BaseModel):
    mode: ChatMCPMode
    explicit_server_ids: list[str] = Field(default_factory=list)


@router.put("/api/chats/{chat_id}/mcp", status_code=204)
async def put_chat_mcp_settings(
    chat_id: str,
    body: _ChatMCPSettingsIn,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> None:
    await _load_owned_chat(chat_id, claims["sub"])
    storage.put_chat_mcp_settings(
        ChatMCPSettings(
            chat_id=chat_id,
            mode=body.mode,
            explicit_server_ids=body.explicit_server_ids,
        )
    )
```

- [ ] **Step 7.5: Mount the router in main.py**

Open `src/channel/api/main.py`. After the existing `app.include_router(prefs_router)` line (~line 156), add:

```python
# MCP server registry — /api/mcp/* (auth-gated) + /auth/mcp/callback
# (browser redirect target, unauthenticated, state-store guarded).
from channel.api.mcp import callback_router as mcp_callback_router  # noqa: E402
from channel.api.mcp import router as mcp_router  # noqa: E402

app.include_router(mcp_router)  # routes already include /api prefix
app.include_router(mcp_callback_router)
```

- [ ] **Step 7.6: Run tests**

Run: `uv run pytest tests/unit/test_mcp_api.py -v`
Expected: both happy-path tests PASS.

- [ ] **Step 7.7: Write a callback-path test (sad path + happy path)**

Append to `tests/unit/test_mcp_api.py`:

```python
def test_callback_with_invalid_state_redirects_with_error(
    app: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel.auth import state_store
    monkeypatch.setattr(state_store, "consume_state", lambda _: None)
    client = TestClient(app)
    resp = client.get("/auth/mcp/callback?state=bogus&code=abc", follow_redirects=False)
    assert resp.status_code == 302
    assert "mcp_authed=error" in resp.headers["location"]
    assert "reason=invalid_state" in resp.headers["location"]


def test_callback_happy_path_persists_token_and_flips_status(
    app: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata, OAuthToken
    from channel import storage
    from channel.auth import state_store
    from channel.mcp import auth as mcp_auth, crypto
    from channel.models import MCPServer, MCPServerAuthStatus

    monkeypatch.setattr(
        state_store, "consume_state",
        lambda _: {
            "purpose": "mcp",
            "user_id": "u1",
            "server_id": "srv-1",
            "code_verifier": "verifier",
            "redirect_uri": "https://channel.example.com/auth/mcp/callback",
        },
    )
    monkeypatch.setattr(
        storage, "get_mcp_server",
        lambda **_: MCPServer(
            server_id="srv-1", user_id="u1", name="Hive",
            url="https://hive.example.com/mcp",
            client_id="dcr-1", tool_prefix="hive",
            auth_status=MCPServerAuthStatus.NEVER_AUTHED,
            created_at="x", updated_at="x",
        ),
    )
    monkeypatch.setattr(
        mcp_auth, "discover_resource_metadata",
        AsyncMock(return_value=ProtectedResourceMetadata(
            authorization_servers=["https://auth.example.com"],
            resource="https://hive.example.com/mcp",
        )),
    )
    monkeypatch.setattr(
        mcp_auth, "discover_auth_server_metadata",
        AsyncMock(return_value=OAuthMetadata(
            issuer="https://auth.example.com",
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            response_types_supported=["code"],
        )),
    )
    monkeypatch.setattr(
        mcp_auth, "exchange_code",
        AsyncMock(return_value=OAuthToken(
            access_token="acc-1", refresh_token="ref-1",
            expires_in=3600, token_type="Bearer", scope="read",
        )),
    )
    monkeypatch.setattr(crypto, "encrypt_blob", lambda s: ("ENC::" + s).encode())
    persisted: dict[str, Any] = {}
    monkeypatch.setattr(
        storage, "put_mcp_token",
        lambda **kw: persisted.update(kw),
    )
    flipped: dict[str, Any] = {}
    monkeypatch.setattr(
        storage, "set_mcp_server_auth_status",
        lambda **kw: flipped.update(kw),
    )

    client = TestClient(app)
    resp = client.get(
        "/auth/mcp/callback?state=ok&code=auth-code", follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "mcp_authed=ok" in resp.headers["location"]
    assert "server_id=srv-1" in resp.headers["location"]
    assert persisted["access_token_ciphertext"] == b"ENC::acc-1"
    assert flipped["status"] == MCPServerAuthStatus.ACTIVE
```

- [ ] **Step 7.8: Re-run**

Run: `uv run pytest tests/unit/test_mcp_api.py -v`
Expected: all four tests PASS.

- [ ] **Step 7.9: Commit**

```bash
git add src/channel/api/mcp.py src/channel/api/main.py tests/unit/test_mcp_api.py
git commit -m "feat(mcp): REST surface — list/register/callback/reauth/per-chat (#207)"
```

---

## Task 8: Chassis wiring — append MCPClient instances to `tools=[...]`

This is the spike's load-bearing one-liner integration, plus the per-chat override resolution.

**Files:**
- Modify: `src/channel/api/chats.py`
- Test: `tests/unit/test_chats_api_mcp.py` (new file — prefer this over bloating the existing `test_chats_api.py`).

> **Deviation from issue #207 file list — read this:** the issue says
> `src/channel/agents/chat_agent.py` should resolve active MCP servers.
> In practice the integration point is `chats.py`, not `chat_agent.py`,
> because (1) `build_agent` is sync and the token-refresh path is async,
> (2) `chat_agent.build_agent` is also called from `build_titler_agent`
> / `build_followups_agent` which must NOT carry MCP clients, and
> (3) the existing `_build_tool_registry` helper already lives in
> chats.py at the natural call site that owns `(user_id, chat_id)`.
> The seam stays the same — Strands sees `tools=[*natives, *mcp]` —
> just composed one layer closer to the SSE generator. Note this in
> the PR body.

- [ ] **Step 8.1: Write the failing test**

Create `tests/unit/test_chats_api_mcp.py`:

```python
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
```

- [ ] **Step 8.2: Verify failure**

Run: `uv run pytest tests/unit/test_chats_api_mcp.py -v`
Expected: `AttributeError: module 'channel.api.chats' has no attribute '_build_mcp_clients_for_chat'`.

- [ ] **Step 8.3: Implement chassis wiring**

Open `src/channel/api/chats.py`. Add at the top, with the other imports:

```python
from channel.mcp import auth as mcp_auth
from channel.mcp.auth import MCPAuthFailedError
from channel.mcp.transports import make_authenticated_transport
from channel.models import ChatMCPMode, MCPServerAuthStatus
from strands.tools.mcp import MCPClient
```

(Add only what isn't already there. `MCPServerAuthStatus` should come from `..models`, not the duplicate import.)

Below `_build_tool_registry` (around line 522), add:

```python
async def _build_mcp_clients_for_chat(
    *, user_id: str, chat_id: str,
) -> list[MCPClient]:
    """Resolve active MCP servers + tokens for one chat turn.

    Per the spike (§Question 2): chats inherit user-level
    ``globally_enabled`` servers by default; ``mode="explicit"`` rows
    capture an exact list at override time. Per-tool-call
    re-instantiation (spike §Question 3 v1 strategy) — every turn
    builds fresh ``MCPClient`` instances bound to the bearer token
    valid at turn start. Mid-chain refresh stays a v2 follow-up.

    Auth-resolution failures are best-effort logged + the MCPSERVER row
    flipped to ``EXPIRED`` so the SPA can surface the Reconnect
    affordance on the next Customize visit — but they do NOT raise into
    the streaming generator. A broken Hive registration must not block
    the chain's other tools.
    """
    registered = storage.list_mcp_servers_for_user(user_id)
    settings = storage.get_chat_mcp_settings(chat_id)
    if settings.mode == ChatMCPMode.EXPLICIT:
        allowed = set(settings.explicit_server_ids)
        active = [s for s in registered if s.server_id in allowed]
    else:
        active = [s for s in registered if s.globally_enabled]

    clients: list[MCPClient] = []
    for server in active:
        try:
            token = await mcp_auth.get_valid_access_token(
                user_id=user_id, server=server,
            )
        except MCPAuthFailedError as exc:
            logger.warning(
                "mcp.token_resolution_failed user=%s server=%s %s",
                user_id, server.server_id, exc,
            )
            storage.set_mcp_server_auth_status(
                user_id=user_id,
                server_id=server.server_id,
                status=MCPServerAuthStatus.EXPIRED,
            )
            continue
        clients.append(
            MCPClient(
                make_authenticated_transport(
                    server_url=server.url, access_token=token,
                ),
                prefix=server.tool_prefix,
            )
        )
    return clients
```

Then modify `_stream_bedrock_reply` — find the line `tool_registry = _build_tool_registry()` (~line 626) and replace it + the `build_agent(...)` call:

```python
    tool_registry = _build_tool_registry()
    # #207 — append MCPClient instances. Resolution failures are
    # swallowed inside _build_mcp_clients_for_chat (the helper flips
    # the MCPSERVER row to EXPIRED so the SPA can surface Reconnect).
    mcp_clients = await _build_mcp_clients_for_chat(
        user_id=claims["sub"], chat_id=chat.chat_id,
    )
    tool_registry = [*tool_registry, *mcp_clients]

    agent = build_agent(
```

The `tools=tool_registry` line below is unchanged — it now passes the combined list.

Now find the existing `try: async for event in agent.stream_async(user_payload):` block (~line 676) and wrap its `except (asyncio.CancelledError, GeneratorExit):` block by adding a `finally:` that explicitly cleans up tool providers (MCPClient holds a background thread + httpx client that must be released when the turn ends):

```python
    try:
        async for event in agent.stream_async(user_payload):
            ...  # unchanged
    except (asyncio.CancelledError, GeneratorExit):
        set_cancel_signal(chat.chat_id)
        raise
    finally:
        # #207 — Strands' MCPClient holds a background thread + httpx
        # client that must be released when the turn ends. agent.cleanup()
        # is a no-op for natives; safe to call unconditionally. Without
        # this we rely on the GC finalizer which isn't deterministic in
        # async generators and can leak the MCP background thread
        # across warm Lambda invocations.
        agent.cleanup()
```

Place the `finally:` AFTER the existing `except`, not inside it. Both clauses attach to the same `try`.

- [ ] **Step 8.4: Run test**

Run: `uv run pytest tests/unit/test_chats_api_mcp.py -v`
Expected: all three tests PASS.

- [ ] **Step 8.5: Run full chats test suite to confirm no regression**

Run: `uv run pytest tests/unit/test_chats_api.py -v`
Expected: all green.

- [ ] **Step 8.6: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api_mcp.py
git commit -m "feat(mcp): chassis wiring — append MCPClient instances to tools=[...] (#207)"
```

---

## Task 9: Frontend api.js — MCP REST wrappers

**Files:**
- Modify: `ui/src/api.js`
- Test: `ui/src/api.test.js` (extend if exists; co-locate new file otherwise)

- [ ] **Step 9.1: Write the failing test**

Find the existing api test pattern (likely `ui/src/api.test.js`). If it doesn't exist, create one. Add:

```javascript
// Copyright (c) 2026 John Carter. All rights reserved.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  deleteMCPServer,
  getChatMCPSettings,
  listMCPServers,
  patchMCPServer,
  putChatMCPSettings,
  reauthMCPServer,
  registerMCPServer,
} from "./api.js";

const TOKEN_KEY = "starter_mgmt_token";

describe("MCP API client", () => {
  beforeEach(() => {
    localStorage.setItem(TOKEN_KEY, "test-token");
    global.fetch = vi.fn();
  });

  afterEach(() => {
    localStorage.removeItem(TOKEN_KEY);
    vi.restoreAllMocks();
  });

  it("listMCPServers GETs with auth", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ servers: [] }),
    });
    const data = await listMCPServers();
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/mcp/servers"),
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer test-token" }),
      }),
    );
    expect(data).toEqual({ servers: [] });
  });

  it("registerMCPServer POSTs JSON body and returns auth_start_url", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ server_id: "srv-1", auth_start_url: "https://x" }),
    });
    const out = await registerMCPServer({
      name: "Hive",
      url: "https://hive.example.com/mcp",
      tool_prefix: "hive",
    });
    expect(out.auth_start_url).toBe("https://x");
  });

  it("patchMCPServer PATCHes a subset of fields", async () => {
    global.fetch.mockResolvedValueOnce({ ok: true, status: 204 });
    await patchMCPServer("srv-1", { globally_enabled: false });
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/mcp/servers/srv-1"),
      expect.objectContaining({ method: "PATCH" }),
    );
  });

  it("deleteMCPServer DELETEs", async () => {
    global.fetch.mockResolvedValueOnce({ ok: true, status: 204 });
    await deleteMCPServer("srv-1");
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/mcp/servers/srv-1"),
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("reauthMCPServer POSTs and returns auth_start_url", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ server_id: "srv-1", auth_start_url: "https://x" }),
    });
    const out = await reauthMCPServer("srv-1");
    expect(out.auth_start_url).toBe("https://x");
  });

  it("getChatMCPSettings + putChatMCPSettings round trip", async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ mode: "inherit", explicit_server_ids: [] }),
      })
      .mockResolvedValueOnce({ ok: true, status: 204 });
    const settings = await getChatMCPSettings("chat-1");
    expect(settings.mode).toBe("inherit");
    await putChatMCPSettings("chat-1", {
      mode: "explicit",
      explicit_server_ids: ["srv-1"],
    });
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });
});
```

- [ ] **Step 9.2: Verify failure**

Run: `npm --prefix ui test -- api.test.js`
Expected: import error / undefined function names.

- [ ] **Step 9.3: Add the wrappers**

Append to `ui/src/api.js`:

```javascript
// ---- MCP servers (#207) ---------------------------------------------------

export async function listMCPServers() {
  const response = await fetch(`${BASE}/api/mcp/servers`, {
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`listMCPServers ${response.status}`);
  return response.json();
}

export async function registerMCPServer({ name, url, tool_prefix = null }) {
  const response = await fetch(`${BASE}/api/mcp/servers`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ name, url, tool_prefix }),
  });
  if (!response.ok) throw new Error(`registerMCPServer ${response.status}`);
  return response.json();
}

export async function patchMCPServer(serverId, updates) {
  const response = await fetch(`${BASE}/api/mcp/servers/${serverId}`, {
    method: "PATCH",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!response.ok) throw new Error(`patchMCPServer ${response.status}`);
}

export async function deleteMCPServer(serverId) {
  const response = await fetch(`${BASE}/api/mcp/servers/${serverId}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`deleteMCPServer ${response.status}`);
}

export async function reauthMCPServer(serverId) {
  const response = await fetch(`${BASE}/api/mcp/servers/${serverId}/reauth`, {
    method: "POST",
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`reauthMCPServer ${response.status}`);
  return response.json();
}

export async function getChatMCPSettings(chatId) {
  const response = await fetch(`${BASE}/api/chats/${chatId}/mcp`, {
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`getChatMCPSettings ${response.status}`);
  return response.json();
}

export async function putChatMCPSettings(chatId, settings) {
  const response = await fetch(`${BASE}/api/chats/${chatId}/mcp`, {
    method: "PUT",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!response.ok) throw new Error(`putChatMCPSettings ${response.status}`);
}
```

- [ ] **Step 9.4: Verify tests pass**

Run: `npm --prefix ui test -- api.test.js`
Expected: all six new tests pass + existing API tests still pass.

- [ ] **Step 9.5: Commit**

```bash
git add ui/src/api.js ui/src/api.test.js
git commit -m "feat(mcp): frontend api.js wrappers for MCP server CRUD (#207)"
```

---

## Task 10: Customize.jsx — MCP servers section + AddMCPServerModal

This task ships the Customize-tab UI: list of servers, "Add server" button opening the modal, per-row toggle + reauth + delete buttons.

**Files:**
- Create: `ui/src/app/AddMCPServerModal.jsx`
- Create: `ui/src/app/AddMCPServerModal.test.jsx`
- Modify: `ui/src/app/views/Customize.jsx`
- Modify: `ui/src/app/views/Customize.test.jsx` (extend)

- [ ] **Step 10.1: Write the failing test for the modal**

Create `ui/src/app/AddMCPServerModal.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AddMCPServerModal from "./AddMCPServerModal.jsx";

vi.mock("../api.js", () => ({
  registerMCPServer: vi.fn(),
}));

import { registerMCPServer } from "../api.js";

describe("AddMCPServerModal", () => {
  beforeEach(() => {
    vi.spyOn(window, "open").mockImplementation(() => null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("submits name + url and opens the returned auth_start_url", async () => {
    registerMCPServer.mockResolvedValueOnce({
      server_id: "srv-1",
      auth_start_url: "https://auth.example/authorize?...",
    });
    const onClose = vi.fn();
    render(<AddMCPServerModal open onClose={onClose} onRegistered={() => {}} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Hive" } });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://hive.example.com/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add server/i }));

    await waitFor(() => expect(registerMCPServer).toHaveBeenCalled());
    expect(window.open).toHaveBeenCalledWith(
      "https://auth.example/authorize?...",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("shows the consent disclosure before submit", () => {
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    expect(
      screen.getByText(
        /We will register Channel with this server using OAuth/i,
      ),
    ).toBeInTheDocument();
  });

  it("surfaces error from registerMCPServer", async () => {
    registerMCPServer.mockRejectedValueOnce(new Error("registerMCPServer 502"));
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "X" } });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://x.example/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add server/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/couldn't register that server/i),
      ).toBeInTheDocument(),
    );
  });
});
```

- [ ] **Step 10.2: Verify failure**

Run: `npm --prefix ui test -- AddMCPServerModal.test.jsx`
Expected: module not found.

- [ ] **Step 10.3: Implement the modal**

Create `ui/src/app/AddMCPServerModal.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import { registerMCPServer } from "../api.js";
import Modal from "../components/Modal.jsx";

/**
 * Register-server modal.
 *
 * Spike §Question 2 "OAuth scopes UX — pinned as open question":
 * v1 surfaces a generic disclosure on this page (the consent moment),
 * not per-scope detail. Real scope rendering is v2 once we see what
 * confuses users.
 *
 * On successful register, opens the returned `auth_start_url` in a new
 * tab. The new tab carries the OAuth flow; the user comes back to
 * `/app/customize?mcp_authed=ok&server_id=…` via the Channel
 * redirect target (`/auth/mcp/callback`). The Customize view watches
 * for that query param and refreshes the server list.
 */
export default function AddMCPServerModal({ open, onClose, onRegistered }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    if (busy || !name.trim() || !url.trim()) return;
    setBusy(true);
    setError("");
    try {
      const out = await registerMCPServer({ name: name.trim(), url: url.trim() });
      window.open(out.auth_start_url, "_blank", "noopener,noreferrer");
      onRegistered?.(out.server_id);
      onClose?.();
    } catch (e) {
      console.error("registerMCPServer failed", e);
      setError("Couldn't register that server. Check the URL and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose}>
      <div className="mcp-add-form">
        <h3 className="modal-h">Add MCP server</h3>
        <label className="mcp-field">
          <span>Name</span>
          <input
            aria-label="Name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Hive"
            autoFocus
          />
        </label>
        <label className="mcp-field">
          <span>Server URL</span>
          <input
            aria-label="Server URL"
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://hive.example.com/mcp"
          />
        </label>
        <p className="mcp-consent">
          We will register Channel with this server using OAuth Dynamic
          Client Registration, then open the server's authorization page
          in a new tab. You'll review the permissions the server requests
          before granting access. Tokens are encrypted at rest in
          Channel's database.
        </p>
        {error && (
          <div className="mcp-err" role="alert">
            {error}
          </div>
        )}
        <div className="mcp-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={busy || !name.trim() || !url.trim()}
            onClick={submit}
          >
            {busy ? "Registering…" : "Add server"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
```

The `Modal` import path matches `ui/src/components/Modal.jsx` per CLAUDE.md.

Also add CSS classes used above to `ui/src/styles/app.css` — find a section near other form-modal CSS (the rename / delete confirm modals); follow the same style.

```css
/* MCP — Add server modal (#207) */
.mcp-add-form { display: flex; flex-direction: column; gap: 12px; }
.mcp-add-form .modal-h { margin: 0 0 4px; font-size: 1.05rem; font-weight: 600; color: var(--ink); }
.mcp-field { display: flex; flex-direction: column; gap: 4px; }
.mcp-field > span { font-size: 0.85rem; color: var(--ink-muted); }
.mcp-field > input {
  background: var(--canvas);
  color: var(--ink);
  border: 1px solid var(--border);
  border-radius: var(--radius-s);
  padding: 8px 10px;
}
.mcp-consent {
  font-size: 0.85rem;
  color: var(--ink-muted);
  background: var(--raised);
  border-radius: var(--radius-s);
  padding: 10px 12px;
}
.mcp-err {
  color: var(--accent);
  font-size: 0.9rem;
}
.mcp-actions {
  display: flex; justify-content: flex-end; gap: 8px;
}
```

- [ ] **Step 10.4: Run modal test**

Run: `npm --prefix ui test -- AddMCPServerModal.test.jsx`
Expected: all three tests pass.

- [ ] **Step 10.5: Wire Customize section**

Open `ui/src/app/views/Customize.jsx`. Add at top with other imports:

```jsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  deleteMCPServer,
  listMCPServers,
  patchMCPServer,
  reauthMCPServer,
} from "../../api.js";
import AddMCPServerModal from "../AddMCPServerModal.jsx";
```

(merge with existing React import — Customize already imports `useEffect`/`useState`.)

Inside the `Customize` function, before the `return`, add:

```jsx
  const [mcpServers, setMcpServers] = useState([]);
  const [mcpLoading, setMcpLoading] = useState(true);
  const [mcpError, setMcpError] = useState("");
  const [showAddMCP, setShowAddMCP] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  const refreshMCP = useCallback(async () => {
    setMcpLoading(true);
    setMcpError("");
    try {
      const { servers } = await listMCPServers();
      setMcpServers(servers);
    } catch (e) {
      console.error("listMCPServers", e);
      setMcpError("Couldn't load MCP servers.");
    } finally {
      setMcpLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshMCP();
  }, [refreshMCP]);

  // Refresh after the OAuth round-trip lands us back here.
  useEffect(() => {
    if (searchParams.get("mcp_authed")) {
      refreshMCP();
      const next = new URLSearchParams(searchParams);
      next.delete("mcp_authed");
      next.delete("server_id");
      next.delete("reason");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams, refreshMCP]);

  async function toggleGlobal(server) {
    await patchMCPServer(server.server_id, {
      globally_enabled: !server.globally_enabled,
    });
    refreshMCP();
  }

  async function removeServer(server) {
    if (!window.confirm(`Remove ${server.name}?`)) return;
    await deleteMCPServer(server.server_id);
    refreshMCP();
  }

  async function reauth(server) {
    const { auth_start_url } = await reauthMCPServer(server.server_id);
    window.open(auth_start_url, "_blank", "noopener,noreferrer");
  }
```

Then add a new `<div className="set-group">` block after the existing "Behavior" group:

```jsx
        <div className="set-group">
          <h3>MCP servers</h3>
          <p className="hint">
            Connect external Model Context Protocol servers that this account
            can call from any chat. Toggle off to keep a server registered
            without making its tools available by default.
          </p>
          {mcpError && (
            <div className="hint" role="alert">{mcpError}</div>
          )}
          {mcpLoading ? (
            <div className="hint">Loading…</div>
          ) : mcpServers.length === 0 ? (
            <div className="hint">No MCP servers yet.</div>
          ) : (
            <ul className="mcp-list">
              {mcpServers.map((s) => (
                <li key={s.server_id} className="mcp-row">
                  <div className="mcp-row-main">
                    <div className="mcp-row-name">
                      <span className={`mcp-dot mcp-dot-${s.auth_status}`} />
                      {s.name}
                    </div>
                    <div className="mcp-row-url">{s.url}</div>
                  </div>
                  <div className="mcp-row-actions">
                    <button
                      type="button"
                      className={"toggle" + (s.globally_enabled ? " on" : "")}
                      onClick={() => toggleGlobal(s)}
                      aria-label={
                        s.globally_enabled
                          ? `Disable ${s.name} globally`
                          : `Enable ${s.name} globally`
                      }
                    >
                      <span className="knob" />
                    </button>
                    <button
                      type="button"
                      className="ck"
                      onClick={() => reauth(s)}
                    >
                      Reconnect
                    </button>
                    <button
                      type="button"
                      className="ck"
                      onClick={() => removeServer(s)}
                    >
                      Remove
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div className="set-row">
            <div /> {/* spacer for the right-justified Add button */}
            <div className="ctl">
              <button
                type="button"
                className="btn-primary"
                onClick={() => setShowAddMCP(true)}
              >
                Add server
              </button>
            </div>
          </div>
        </div>
        <AddMCPServerModal
          open={showAddMCP}
          onClose={() => setShowAddMCP(false)}
          onRegistered={() => refreshMCP()}
        />
```

Add CSS to `ui/src/styles/app.css`:

```css
/* MCP — Customize section list (#207) */
.mcp-list { display: flex; flex-direction: column; gap: 8px; list-style: none; padding: 0; margin: 8px 0 0; }
.mcp-row {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  background: var(--raised); border: 1px solid var(--border);
  border-radius: var(--radius-s); padding: 10px 12px;
}
.mcp-row-main { flex: 1; min-width: 0; }
.mcp-row-name { display: flex; align-items: center; gap: 8px; font-weight: 500; }
.mcp-row-url { font-size: 0.8rem; color: var(--ink-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mcp-row-actions { display: flex; align-items: center; gap: 8px; }
.mcp-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.mcp-dot-active { background: oklch(0.70 0.16 150); }
.mcp-dot-expired, .mcp-dot-revoked { background: oklch(0.65 0.18 30); }
.mcp-dot-never_authed { background: var(--ink-muted); }
```

- [ ] **Step 10.6: Extend Customize tests**

Open `ui/src/app/views/Customize.test.jsx`. Add at the top:

```jsx
vi.mock("../../api.js", async (importOriginal) => ({
  ...(await importOriginal()),
  listMCPServers: vi.fn().mockResolvedValue({ servers: [] }),
  patchMCPServer: vi.fn().mockResolvedValue(undefined),
  deleteMCPServer: vi.fn().mockResolvedValue(undefined),
  reauthMCPServer: vi.fn().mockResolvedValue({ auth_start_url: "https://x" }),
}));
```

Add a new test case at the bottom:

```jsx
it("renders the MCP servers section with empty state by default", async () => {
  render(<MemoryRouter><Customize /></MemoryRouter>);
  expect(await screen.findByText(/no mcp servers yet/i)).toBeInTheDocument();
});
```

(Assumes the existing test file already wraps `Customize` in a router; if not, wrap it as shown above.)

- [ ] **Step 10.7: Run all Customize tests**

Run: `npm --prefix ui test -- Customize.test.jsx`
Expected: all tests pass.

- [ ] **Step 10.8: Commit**

```bash
git add ui/src/app/AddMCPServerModal.jsx ui/src/app/AddMCPServerModal.test.jsx \
        ui/src/app/views/Customize.jsx ui/src/app/views/Customize.test.jsx \
        ui/src/styles/app.css
git commit -m "feat(mcp): Customize section + AddMCPServerModal (#207)"
```

---

## Task 11: MCPPicker composer popover + Composer wiring + inline pill

**Files:**
- Create: `ui/src/app/MCPPicker.jsx`
- Create: `ui/src/app/MCPPicker.test.jsx`
- Modify: `ui/src/app/Composer.jsx`
- Modify: `ui/src/app/Composer.test.jsx` (extend)

- [ ] **Step 11.1: Failing test for the picker**

Create `ui/src/app/MCPPicker.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import MCPPicker from "./MCPPicker.jsx";

describe("MCPPicker", () => {
  const SERVERS = [
    { server_id: "a", name: "Alpha", tool_prefix: "alpha", globally_enabled: true,  auth_status: "active" },
    { server_id: "b", name: "Beta",  tool_prefix: "beta",  globally_enabled: true,  auth_status: "active" },
    { server_id: "c", name: "Gone",  tool_prefix: "gone",  globally_enabled: false, auth_status: "active" },
  ];

  it("renders the inline pill with active count", () => {
    render(
      <MCPPicker servers={SERVERS} mode="inherit" explicitIds={[]} onChange={() => {}} />,
    );
    expect(screen.getByRole("button", { name: /2 tool servers/i })).toBeInTheDocument();
  });

  it("opens the popover and lists registered servers", () => {
    render(
      <MCPPicker servers={SERVERS} mode="inherit" explicitIds={[]} onChange={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers/i }));
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("Gone")).toBeInTheDocument();
  });

  it("toggling a server in inherit mode flips to explicit mode with the new list", () => {
    const onChange = vi.fn();
    render(
      <MCPPicker servers={SERVERS} mode="inherit" explicitIds={[]} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers/i }));
    fireEvent.click(screen.getByLabelText(/^Alpha$/));
    expect(onChange).toHaveBeenCalledWith({
      mode: "explicit",
      explicit_server_ids: ["b"],
    });
  });
});
```

- [ ] **Step 11.2: Verify failure**

Run: `npm --prefix ui test -- MCPPicker.test.jsx`
Expected: module not found.

- [ ] **Step 11.3: Implement the picker**

Create `ui/src/app/MCPPicker.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useMemo, useState } from "react";
import Icon from "../components/Icon.jsx";

/**
 * Composer popover for picking MCP servers active in this chat.
 *
 * Mirrors ModelPicker / AttachMenu's backdrop+pop pattern (CLAUDE.md
 * §"Chat-app popovers"). The caller owns the persistence — this
 * component is dumb about the underlying API.
 *
 * Props:
 *   - servers: [{ server_id, name, tool_prefix, globally_enabled,
 *       auth_status }] — full registered set (from /api/mcp/servers)
 *   - mode: "inherit" | "explicit"
 *   - explicitIds: string[] — only used when mode === "explicit"
 *   - onChange({ mode, explicit_server_ids }) — caller persists via
 *     putChatMCPSettings
 */
export default function MCPPicker({ servers, mode, explicitIds, onChange }) {
  const [open, setOpen] = useState(false);

  const activeIds = useMemo(() => {
    if (!servers || servers.length === 0) return [];
    if (mode === "explicit") {
      return explicitIds.filter((id) => servers.some((s) => s.server_id === id));
    }
    return servers.filter((s) => s.globally_enabled).map((s) => s.server_id);
  }, [servers, mode, explicitIds]);

  function toggle(serverId) {
    const next = activeIds.includes(serverId)
      ? activeIds.filter((id) => id !== serverId)
      : [...activeIds, serverId];
    onChange({ mode: "explicit", explicit_server_ids: next });
  }

  function resetToInherit() {
    onChange({ mode: "inherit", explicit_server_ids: [] });
  }

  const label = `${activeIds.length} tool ${activeIds.length === 1 ? "server" : "servers"}`;

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        className="mcp-pill"
        onClick={() => setOpen((o) => !o)}
        aria-label={label}
      >
        <Icon name="plug" size={14} />
        <span>{label}</span>
        <Icon name="chevron-down" size={14} />
      </button>
      {open && (
        <>
          <div className="backdrop" onClick={() => setOpen(false)} />
          <div className="pop" style={{ bottom: "calc(100% + 8px)", right: 0 }}>
            <div className="pop-h">MCP servers for this chat</div>
            {(!servers || servers.length === 0) && (
              <div className="opt" data-testid="mcp-empty">
                <div style={{ flex: 1 }}>
                  <div className="ds">
                    No servers registered. Add one from Customize.
                  </div>
                </div>
              </div>
            )}
            {servers && servers.map((s) => {
              const checked = activeIds.includes(s.server_id);
              const disabled = s.auth_status !== "active";
              return (
                <label
                  key={s.server_id}
                  className={"opt" + (disabled ? " disabled" : "")}
                  aria-label={s.name}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={() => toggle(s.server_id)}
                  />
                  <div style={{ flex: 1 }}>
                    <div className="nm">{s.name}</div>
                    <div className="ds">
                      {disabled
                        ? "Needs reconnect"
                        : `Tools available as ${s.tool_prefix}_*`}
                    </div>
                  </div>
                </label>
              );
            })}
            {mode === "explicit" && (
              <div className="pop-h" style={{ marginTop: 4 }}>
                <button
                  type="button"
                  className="ck"
                  onClick={resetToInherit}
                >
                  Reset to global defaults
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
```

Add CSS in `ui/src/styles/app.css`:

```css
/* MCP picker (#207) */
.mcp-pill {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--raised); border: 1px solid var(--border);
  border-radius: var(--radius-s); padding: 4px 8px;
  font-size: 0.85rem; color: var(--ink);
  cursor: pointer;
}
.mcp-pill:hover { background: var(--canvas); }
```

Also make sure `Icon.jsx` exposes a `plug` icon. If not, add one — open `ui/src/components/Icon.jsx`, find the icon-name switch, add a `plug` SVG. The Lucide `plug` glyph is a fine source (24×24 stroke).

- [ ] **Step 11.4: Run picker tests**

Run: `npm --prefix ui test -- MCPPicker.test.jsx`
Expected: all three tests pass.

- [ ] **Step 11.5: Wire MCPPicker into Composer**

Open `ui/src/app/Composer.jsx`. Add at top:

```jsx
import MCPPicker from "./MCPPicker.jsx";
```

Composer needs three new props: `mcpServers`, `mcpSettings`, `setMcpSettings`. Find the existing prop list in the `forwardRef` call (~line 99) and add them.

In the composer-row JSX (~line 442), after `<AttachMenu>` and before `<div className="spacer" />`, insert:

```jsx
          {mcpServers && (
            <MCPPicker
              servers={mcpServers}
              mode={mcpSettings?.mode || "inherit"}
              explicitIds={mcpSettings?.explicit_server_ids || []}
              onChange={setMcpSettings}
            />
          )}
```

- [ ] **Step 11.6: Wire MCP state in the chat shell**

The chat-shell wrapper (look for where `Composer` is rendered — probably `ui/src/app/Conversation.jsx` or `ui/src/app/Shell.jsx` — `grep -rn "<Composer " ui/src/app/`) needs to hold MCP state and pass props down. Find the existing chat-state hook (probably `useChatStream` or a wrapper) and add MCP loading.

Search for the right insertion point:

```bash
grep -rn "<Composer\b" ui/src/app/
```

In the parent component:

```jsx
import { useEffect, useState } from "react";
import { getChatMCPSettings, listMCPServers, putChatMCPSettings } from "../api.js";

// inside the component:
const [mcpServers, setMcpServers] = useState(null);
const [mcpSettings, setMcpSettings] = useState(null);

useEffect(() => {
  let cancelled = false;
  listMCPServers().then(({ servers }) => {
    if (!cancelled) setMcpServers(servers);
  }).catch(() => { /* tolerate; the pill will simply not render */ });
  return () => { cancelled = true; };
}, []);

useEffect(() => {
  if (!chatId) return;
  let cancelled = false;
  getChatMCPSettings(chatId).then((s) => {
    if (!cancelled) setMcpSettings(s);
  }).catch(() => { if (!cancelled) setMcpSettings({ mode: "inherit", explicit_server_ids: [] }); });
  return () => { cancelled = true; };
}, [chatId]);

async function handleMcpChange(next) {
  setMcpSettings(next);
  try {
    await putChatMCPSettings(chatId, next);
  } catch (e) {
    console.error("putChatMCPSettings", e);
  }
}

// In <Composer .../>:
<Composer
  ...
  mcpServers={mcpServers}
  mcpSettings={mcpSettings}
  setMcpSettings={handleMcpChange}
/>
```

The exact insertion is parent-component-specific; this is the shape, not the literal patch.

- [ ] **Step 11.7: Extend Composer test**

Open `ui/src/app/Composer.test.jsx`. Add a test:

```jsx
it("renders the MCPPicker pill when mcpServers prop is non-null", () => {
  render(
    <Composer
      model={{ id: "m", short: "M", desc: "" }}
      effort="medium"
      setModel={() => {}}
      setEffort={() => {}}
      onSend={() => {}}
      mcpServers={[]}
      mcpSettings={{ mode: "inherit", explicit_server_ids: [] }}
      setMcpSettings={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: /0 tool servers/i })).toBeInTheDocument();
});
```

(Pad the call with whatever required props the existing tests already mock.)

- [ ] **Step 11.8: Run all UI tests**

Run: `npm --prefix ui test`
Expected: all green; 100% coverage gate holds.

- [ ] **Step 11.9: Commit**

```bash
git add ui/src/app/MCPPicker.jsx ui/src/app/MCPPicker.test.jsx \
        ui/src/app/Composer.jsx ui/src/app/Composer.test.jsx \
        ui/src/components/Icon.jsx \
        ui/src/styles/app.css \
        ui/src/app/Conversation.jsx  # or whichever parent holds the state
git commit -m "feat(mcp): MCPPicker composer popover + per-chat override wiring (#207)"
```

---

## Task 12: CDK — KMS key + env vars + IAM + assertion test

**Files:**
- Modify: `infra/stacks/channel_stack.py`
- Modify: `tests/unit/test_channel_stack.py` (add an assertion for the new posture)

- [ ] **Step 12.1: Write the failing assertion**

Open `tests/unit/test_channel_stack.py`. Find the existing assertion pattern for `STARTER_WEB_SEARCH_ENABLED` / `STARTER_CODE_EXEC_ENABLED`. Add a sibling test:

```python
def test_api_lambda_carries_mcp_env_vars(synthesized_template) -> None:
    """API Lambda must receive STARTER_MCP_REDIRECT_URI + STARTER_SPA_BASE_URL
    + STARTER_MCP_TOKEN_KMS_KEY_ID + STARTER_MCP_REGISTRY_ENABLED."""
    fn = _api_function(synthesized_template)
    env = fn["Properties"]["Environment"]["Variables"]
    assert "STARTER_MCP_REDIRECT_URI" in env
    assert "STARTER_SPA_BASE_URL" in env
    assert "STARTER_MCP_TOKEN_KMS_KEY_ID" in env
    assert env["STARTER_MCP_REGISTRY_ENABLED"] == "1"


def test_kms_key_exists_for_mcp_tokens(synthesized_template) -> None:
    keys = synthesized_template.get("Resources", {}).values()
    kms_keys = [k for k in keys if k["Type"] == "AWS::KMS::Key"]
    descriptions = [k["Properties"].get("Description", "") for k in kms_keys]
    assert any("MCP" in d for d in descriptions), (
        f"Expected an MCP token-encryption KMS key in stack; found descriptions: {descriptions}"
    )
```

(Use whatever fixture `synthesized_template` is — look at the existing tests in this file; reuse the cdk synth fixture.)

- [ ] **Step 12.2: Verify failure**

Run: `uv run pytest tests/unit/test_channel_stack.py -k mcp -v`
Expected: assertion failures.

- [ ] **Step 12.3: Implement the CDK changes**

Open `infra/stacks/channel_stack.py`. Find the existing `STARTER_WEB_SEARCH_ENABLED` block (~line 493). Add immediately after:

```python
        # #207 MCP registry — dedicated CMK + redirect-URI env + IAM
        mcp_token_key = kms.Key(
            self,
            "MCPTokenKey",
            description=f"Channel {env_name} — encrypts MCP OAuth token blobs at rest",
            enable_key_rotation=True,
            removal_policy=data_removal,
        )
        mcp_token_key.grant_encrypt_decrypt(api_role)
        common_env["STARTER_MCP_TOKEN_KMS_KEY_ID"] = mcp_token_key.key_arn
        common_env["STARTER_MCP_REGISTRY_ENABLED"] = "1"
        # The redirect URI is the API origin's /auth/mcp/callback path.
        # MCP servers persist this in their DCR client record; changing
        # it later requires re-registering. ``api_origin`` is built
        # earlier in this method (see the ``api_origin = f"https://{custom_domain}"``
        # assignment near the top of the stack).
        common_env["STARTER_MCP_REDIRECT_URI"] = f"{api_origin}/auth/mcp/callback"
        common_env["STARTER_SPA_BASE_URL"] = api_origin
```

Make sure `from aws_cdk import aws_kms as kms` is imported near the other `aws_cdk` imports at the top of the file.

- [ ] **Step 12.4: Synth check**

Run: `uv run inv synth`
Expected: clean synth.

- [ ] **Step 12.5: Re-run tests**

Run: `uv run pytest tests/unit/test_channel_stack.py -k mcp -v`
Expected: PASS.

- [ ] **Step 12.6: tasks.py — pass MCP env in `inv dev`**

`inv dev` needs the new env vars set against DynamoDB Local. Open `tasks.py`, find the `dev` task. Around the existing `STARTER_BYPASS_GOOGLE_AUTH=1` settings, add (use a dummy KMS key for local — we'll mock crypto in tests, but local dev needs a real value or the helper raises):

For local dev, the simplest path is **a wrapped crypto that's a passthrough when `STARTER_MCP_TOKEN_KMS_KEY_ID` is the literal `"local"`**. Add to `src/channel/mcp/crypto.py` (modify the `encrypt_blob`/`decrypt_blob` to short-circuit on `local`):

```python
_LOCAL_DEV_SENTINEL = "local"


def encrypt_blob(plaintext: str) -> bytes:
    key_id = _key_id()
    if key_id == _LOCAL_DEV_SENTINEL:
        return ("LOCAL::" + plaintext).encode("utf-8")
    client = _get_kms_client()
    resp = client.encrypt(KeyId=key_id, Plaintext=plaintext.encode("utf-8"))
    return bytes(resp["CiphertextBlob"])


def decrypt_blob(ciphertext: bytes) -> str:
    key_id = _key_id()
    if key_id == _LOCAL_DEV_SENTINEL:
        return ciphertext.decode("utf-8").removeprefix("LOCAL::")
    client = _get_kms_client()
    resp = client.decrypt(CiphertextBlob=ciphertext)
    return bytes(resp["Plaintext"]).decode("utf-8")
```

Add a unit test for the local-dev short-circuit in `tests/unit/test_mcp_crypto.py`:

```python
def test_local_dev_sentinel_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STARTER_MCP_TOKEN_KMS_KEY_ID", "local")
    enc = crypto.encrypt_blob("plain")
    assert crypto.decrypt_blob(enc) == "plain"
```

Then in `tasks.py` `dev` task, set:

```python
env["STARTER_MCP_TOKEN_KMS_KEY_ID"] = "local"
env["STARTER_MCP_REDIRECT_URI"] = "http://localhost:8001/auth/mcp/callback"
env["STARTER_SPA_BASE_URL"] = "http://localhost:5173"
env["STARTER_MCP_REGISTRY_ENABLED"] = "1"
```

(Replicate the existing env-set pattern around the `STARTER_BYPASS_GOOGLE_AUTH` assignment.)

- [ ] **Step 12.7: Run full unit suite**

Run: `uv run pytest tests/unit/ -x -q`
Expected: all green.

- [ ] **Step 12.8: Commit**

```bash
git add infra/stacks/channel_stack.py tests/unit/test_channel_stack.py \
        src/channel/mcp/crypto.py tests/unit/test_mcp_crypto.py tasks.py
git commit -m "feat(mcp): CDK KMS key + env vars + local-dev sentinel for token crypto (#207)"
```

---

## Task 13: Integration test against DynamoDB Local

**Files:**
- Create: `tests/integration/test_mcp_registry.py`

- [ ] **Step 13.1: Write the test**

```python
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
        user_id="user-int-1", server_id=server.server_id,
    )
    assert fetched is not None
    assert fetched.auth_status == MCPServerAuthStatus.ACTIVE
    token = storage.get_mcp_token(
        user_id="user-int-1", server_id=server.server_id,
    )
    assert token is not None

    storage.delete_mcp_server(
        user_id="user-int-1", server_id=server.server_id,
    )
    assert storage.get_mcp_server(
        user_id="user-int-1", server_id=server.server_id,
    ) is None
    assert storage.get_mcp_token(
        user_id="user-int-1", server_id=server.server_id,
    ) is None


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
```

- [ ] **Step 13.2: Run against DynamoDB Local**

Make sure DynamoDB Local is running:

```bash
docker ps | grep dynamodb-local || \
  docker run -d -p 8000:8000 amazon/dynamodb-local:latest
```

Run: `uv run pytest tests/integration/test_mcp_registry.py -v`
Expected: all three tests pass.

- [ ] **Step 13.3: Commit**

```bash
git add tests/integration/test_mcp_registry.py
git commit -m "test(mcp): integration tests against DDB Local (#207)"
```

---

## Task 14: E2e smoke test (gated against real Hive)

This is the "drive the feature end-to-end" check. Skipped unless `STARTER_E2E_HIVE_ENABLED=1` because it needs a real Hive registration + a way to complete OAuth headlessly (Playwright). v1 ships the test file with a skip guard; CI doesn't run it.

**Files:**
- Create: `tests/e2e/test_mcp_hive_smoke.py`

- [ ] **Step 14.1: Write the e2e test**

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""E2e smoke test against the real Hive MCP server (#207).

Skipped unless ``STARTER_E2E_HIVE_ENABLED=1`` AND Playwright is installed
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
    os.environ.get("STARTER_E2E_HIVE_ENABLED") != "1",
    reason="Hive e2e smoke requires STARTER_E2E_HIVE_ENABLED=1 + a Playwright install",
)


def test_hive_register_auth_use_delete() -> None:
    pytest.skip("Implementation deferred to a follow-up — see #207 comment for status")
```

The skipped placeholder is intentional — the real Playwright body is a follow-up tracked in a child issue. The skipped marker keeps the test file in the suite as a reminder, and the docstring documents the manual procedure for the local-verification recipe at the top of this plan.

- [ ] **Step 14.2: Commit**

```bash
git add tests/e2e/test_mcp_hive_smoke.py
git commit -m "test(mcp): placeholder e2e smoke against Hive (gated) (#207)"
```

---

## Task 15: Local verification + CHANGELOG + PR

- [ ] **Step 15.1: Run the local verification recipe at the top of this plan**

Drive the feature end-to-end per the recipe. Capture a screenshot of the Customize MCP section and another of the composer with the popover open. Save them under `/tmp/207-customize.png` and `/tmp/207-picker.png`.

- [ ] **Step 15.2: Run `uv run inv pre-push`**

Expected: all green.

- [ ] **Step 15.3: Add CHANGELOG entry**

Open `CHANGELOG.md`. Under `## [Unreleased]`, in the `### Added` subsection (create the subsection if not present), add:

```markdown
- MCP-server registry. Users can register external Model Context
  Protocol servers from `/app/customize`; each chat inherits the
  globally-enabled set or opts into an explicit list via the new
  composer popover. Tokens are KMS-encrypted at rest and refreshed
  lazily. (#207, #184 spike, epic #128 part D)
```

- [ ] **Step 15.4: Push the branch**

Per CLAUDE.md push discipline (W1–W7). First push, explicit refspec:

```bash
git fetch origin
git log --oneline origin/development..HEAD  # sanity-check the commits about to land
git push -u origin feat/207-mcp-server-registry:feat/207-mcp-server-registry
```

- [ ] **Step 15.5: Open the PR**

```bash
gh pr create --base development \
  --title "feat(mcp): MCP-server registry + per-chat selection — Closes #207" \
  --body "$(cat <<'EOF'
## Summary

- Implements the MCP-server registry + per-chat selection described in
  the 2026-06-06 spike (`docs/superpowers/specs/2026-06-06-mcp-server-registry-spike.md`).
- Channel owns OAuth 2.1 + DCR + refresh; Strands' `MCPClient`
  consumes a pre-authenticated transport thunk — one-line integration at
  the `build_agent(tools=[...])` seam.
- Two new DDB SK prefixes (`MCPSERVER#{id}`, `MCPTOKEN#{server_id}`) + a
  per-chat override row. Tokens KMS-encrypted at the application layer.

## Test plan

- [x] Unit tests for crypto, auth helpers, storage, REST surface, chassis wiring (Tasks 1–9)
- [x] Integration test against DDB Local for the registry round trip (Task 13)
- [x] Local verification recipe completed (Customize section + composer popover screenshots attached)
- [ ] Hive e2e smoke is shipped as a skipped placeholder (real implementation deferred)

Closes #207
EOF
)"
```

- [ ] **Step 15.6: Do NOT attach auto-merge**

Issue #207 does not carry `agent-safe`. Per the user's
`feedback_skip_auto_merge_on_non_agent_safe` rule, stop after PR creation
and wait for human merge.

- [ ] **Step 15.7: Watch CI**

Run: `gh pr checks --watch`

Fix anything that fails. Iterate.

---

## Out of scope / explicitly deferred (per spike)

- OAuth scopes UX (server-provided vs Channel-side registry vs hybrid) — v2; raw scopes only on the consent disclosure today.
- Per-workspace token isolation — v2; depends on workspaces shipping.
- Server marketplace — out of epic #128.
- Per-server tool subsetting (`MCPClient.tool_filters` in the UI) — v2.
- `MCPClient.elicitation_callback` wiring — v2; v1 fails gracefully via the existing `sse_tool_error` path.
- MCP-as-Sampling-server — out of epic #128; separate design pass.
- RFC 7009 best-effort token revocation on delete — v2.
- Real Playwright e2e against Hive — deferred to a follow-up child issue.
