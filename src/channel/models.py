# Copyright (c) 2026 John Carter. All rights reserved.
"""Pydantic models for chat domain — Chat, Message, and request/response shapes.

These are wire-format and storage-format models.  They are intentionally
flat: nested objects (artifacts, attachments) stay as ``list[dict]`` so
schema evolution doesn't require model surgery.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MessageRole(str, Enum):
    """Roles for chat turns. ``system`` and other roles are rejected at validation time so they can't accidentally be stored."""

    USER = "user"
    ASSISTANT = "assistant"


class FeedbackKind(str, Enum):
    """Thumbs-up / thumbs-down feedback on an assistant message.

    The wire-format spelling matches what the SPA already uses on its
    UI buttons (``Good`` → ``up``, ``Bad`` → ``down``). Any other value
    fails request validation with 422.
    """

    UP = "up"
    DOWN = "down"


class Feedback(BaseModel):
    """Per-message feedback record persisted on the message DDB row.

    Stored as an inline attribute on the ``MSG#`` row — overwritten on
    each new submission for the same message (issue #146). Future
    follow-ups may surface ``note`` via a modal in the UI.
    """

    kind: FeedbackKind
    note: str | None = None
    created_at: str


class Chat(BaseModel):
    """Chat-index row — one per chat, partition key USER#{user_id}."""

    chat_id: str
    user_id: str
    title: str
    created_at: str
    last_message_at: str
    last_user_preview: str = ""
    model_default: str
    message_count: int = 0
    archived: bool = False

    @field_validator("last_user_preview")
    @classmethod
    def _cap_preview(cls, value: str) -> str:
        """Cap to 120 chars at construction time. Pydantic v2 does not validate on assignment, so direct attribute mutation bypasses this."""
        return value[:120]


class Citation(BaseModel):
    """One source citation on an assistant message turn (#174).

    Polymorphic shape pinned per Channel's 2026-06-03 design input —
    ``source_type`` is an open enum so future sources (web_search,
    code_output, memory_recall) slot in without a schema bump.
    ``location`` stays a free-form dict with per-``source_type``
    validation so DDB persistence is one column, not a discriminated
    union.

    v1 always populates ``Message.citations`` as ``None``; this model
    locks the shape so #128 (calibration), web-search, and the recall
    hook can fill it later.
    """

    source_type: Literal["attachment", "web_search", "code_output", "memory_recall"]
    source_id: str
    source_name: str
    location: dict[str, Any]
    excerpt: str | None = None
    confidence: Literal["high", "medium", "low"] | None = None

    # Compatibility table — keyed by ``source_type``, value is the set
    # of ``location.type`` strings that source type accepts. For source
    # types whose location schema hasn't been pinned yet (code_output,
    # memory_recall — owned by #128 and the recall hook), the empty set
    # means "accept any non-empty location dict with a type key" —
    # forward-compat scaffolding so those sub-issues can lock in their
    # schemas without breaking v1.
    _ALLOWED_LOCATION_TYPES: dict[str, set[str]] = {
        "attachment": {"page", "cell", "region", "timestamp"},
        "web_search": {"url"},
        "code_output": set(),
        "memory_recall": set(),
    }

    @model_validator(mode="after")
    def _validate_location_shape(self) -> Citation:
        loc_type = self.location.get("type")
        if not isinstance(loc_type, str) or not loc_type:
            raise ValueError("Citation.location must include a 'type' string")
        allowed = self._ALLOWED_LOCATION_TYPES.get(self.source_type, set())
        # Empty set = "schema not yet pinned, accept any type" — see class
        # docstring for the rationale.
        if allowed and loc_type not in allowed:
            raise ValueError(
                f"Citation.location.type={loc_type!r} not allowed for "
                f"source_type={self.source_type!r} (allowed: {sorted(allowed)})"
            )
        return self


class Attachment(BaseModel):
    """Canonical attachment row — partition key USER#{user_id} (#174).

    One row per uploaded file, written by the finalize endpoint (#175)
    after the browser PUTs to S3. The S3 object itself is the payload;
    this row carries the metadata (filename, mime, size, checksum) and
    the S3 coordinates so the cascade in
    :func:`storage.delete_chat_attachments` can wipe both sides on
    chat deletion.

    ``referenced_at`` is set the first time a message references this
    attachment (#175 finalize handler or #176's content-block builder).
    Distinct from ``created_at`` so the lifecycle rule on the bucket
    (``unreferenced=1`` tag) can garbage-collect orphans.
    """

    id: str
    user_id: str
    name: str
    mime: str
    size_bytes: int
    s3_key: str
    s3_bucket: str
    checksum_sha256: str
    created_at: str
    referenced_at: str | None = None


class Message(BaseModel):
    """One turn in a chat — partition key CHAT#{chat_id}."""

    chat_id: str
    msg_id: str
    role: MessageRole
    text: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    artifacts: list[dict[str, Any]] | None = None
    attachments: list[dict[str, Any]] | None = None
    citations: list[Citation] | None = None
    created_at: str
    ttl: int | None = None
    feedback: Feedback | None = None

    @model_validator(mode="after")
    def _citations_assistant_only(self) -> Message:
        # Citations are produced by the model; user turns never carry
        # them. Per the #174 issue body's reading of Channel's
        # 2026-06-03 input — citations point AT sources, and a user
        # turn isn't a source.
        if self.citations and self.role == MessageRole.USER:
            raise ValueError("citations may not be set on user-role messages")
        return self


class ChatCreate(BaseModel):
    """Request body for POST /api/chats."""

    title: str | None = None
    model_default: str | None = None


class ChatPatch(BaseModel):
    """Request body for PATCH /api/chats/{id}."""

    title: str | None = None
    archived: bool | None = None


class SendMessageRequest(BaseModel):
    """Request body for POST /api/chats/{id}/messages."""

    message: str = Field(min_length=1, max_length=100_000)
    model: str | None = None
    effort: str | None = None
    attachments: list[dict[str, Any]] | None = None


class RegenerateRequest(BaseModel):
    """Request body for POST /api/chats/{id}/regenerate."""

    model: str | None = None
    effort: str | None = None


class FeedbackRequest(BaseModel):
    """Request body for POST /api/chats/{id}/messages/{msg_id}/feedback.

    ``note`` is reserved for a future note-input modal — v1 always sends
    ``null`` (issue #146). The pydantic validator caps ``note`` at
    1000 chars defensively so a bad client can't smuggle a huge string
    into the row.
    """

    kind: FeedbackKind
    note: str | None = Field(default=None, max_length=1000)


class Prefs(BaseModel):
    """User preferences — single JSON-blob row at ``PK=USER#{u}, SK=PREFS``.

    All fields have defaults so a missing DDB row yields the canonical
    defaults at hydration time. ``extra="forbid"`` rejects unknown keys
    so a stale client can't silently write a typo.
    """

    theme: str = "dark"
    accent: str = "42"
    density: str = "cozy"
    shape: str = "soft"
    font: str = "figtree"
    model: str = "claude-opus-4-6"
    effort: str = "High"
    send_on_enter: bool = True
    show_reasoning: bool = False
    suggest_followups: bool = True

    model_config = {"extra": "forbid"}


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
