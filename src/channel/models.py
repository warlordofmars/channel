# Copyright (c) 2026 John Carter. All rights reserved.
"""Pydantic models for chat domain — Chat, Message, and request/response shapes.

These are wire-format and storage-format models.  They are intentionally
flat: nested objects (artifacts, attachments) stay as ``list[dict]`` so
schema evolution doesn't require model surgery.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


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
    created_at: str
    ttl: int | None = None
    feedback: Feedback | None = None


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
