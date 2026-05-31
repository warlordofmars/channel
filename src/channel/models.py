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
