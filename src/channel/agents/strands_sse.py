# Copyright (c) 2026 John Carter. All rights reserved.
"""Strands stream event → SSE byte translator.

The chat router consumes ``strands.Agent.stream_async()`` events and
must emit our existing SSE shapes (``user_persisted`` / ``delta`` /
``done``) that the SPA's ``useChatStream`` hook already understands.

This module is pure: no I/O, no Strands invocation.  The router calls
``translate_event`` per Strands event then ``sse_delta`` / ``sse_done``
to render the byte form.
"""

from __future__ import annotations

import json
from typing import Any


def translate_event(event: dict[str, Any]) -> tuple[str, Any]:
    """Classify one Strands stream event.

    Returns a ``(kind, payload)`` tuple where ``kind`` is one of:

    * ``"delta"`` — incremental text chunk; ``payload`` is a ``str``.
    * ``"stop"`` — turn boundary; ``payload`` is ``{"stop_reason": str}``.
    * ``"usage"`` — token-count metadata; ``payload`` is
      ``{"input_tokens": int, "output_tokens": int}``.
    * ``"skip"`` — uninteresting event (content block start, telemetry,
      etc.); ``payload`` is ``None``.
    """

    # Strands emits each text chunk twice: once as a top-level ``data``
    # shorthand and once inside the canonical ``event.contentBlockDelta``
    # envelope.  We process the canonical form only so the SPA doesn't
    # see duplicated deltas.
    inner = event.get("event") or {}

    if "contentBlockDelta" in inner:
        text = inner["contentBlockDelta"].get("delta", {}).get("text")
        if text:
            return ("delta", text)

    if "messageStop" in inner:
        return (
            "stop",
            {"stop_reason": inner["messageStop"].get("stopReason", "end_turn")},
        )

    if "metadata" in inner:
        usage = inner["metadata"].get("usage") or {}
        return (
            "usage",
            {
                "input_tokens": int(usage.get("inputTokens", 0)),
                "output_tokens": int(usage.get("outputTokens", 0)),
            },
        )

    return ("skip", None)


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def sse_user_persisted(*, msg_id: str, seq: int) -> bytes:
    return _sse({"type": "user_persisted", "msg_id": msg_id, "seq": seq})


def sse_delta(text: str) -> bytes:
    return _sse({"type": "delta", "text": text})


def sse_title_suggested(*, chat_id: str, title: str) -> bytes:
    """Emit a ``title_suggested`` SSE event.

    Phase 7d auto-title trigger. The SPA's useChatStream parses the
    payload and forwards the title to ChatsContext.renameChat for the
    sidebar.
    """
    return _sse({"type": "title_suggested", "chat_id": chat_id, "title": title})


def sse_attachment_error(*, attachment_id: str, filename: str, reason: str) -> bytes:
    """Emit an ``attachment_error`` SSE event (#176).

    Surfaces fetch-time S3 failures (object not found / inaccessible)
    to the SPA so it can prompt the user to re-upload. Distinct from
    tool failures because the user fix differs: re-upload vs. retry.
    """
    return _sse(
        {
            "type": "attachment_error",
            "attachment_id": attachment_id,
            "filename": filename,
            "reason": reason,
        }
    )


def sse_follow_ups_suggested(*, chat_id: str, message_id: str, suggestions: list[str]) -> bytes:
    """Emit a ``follow_ups_suggested`` SSE event.

    Carries 2-3 short prompts the user might want to send next. The
    SPA's ``useChatStream`` attaches them to the matching assistant
    turn so ``Conversation`` can render a chip row below it.
    """
    return _sse(
        {
            "type": "follow_ups_suggested",
            "chat_id": chat_id,
            "message_id": message_id,
            "suggestions": suggestions,
        }
    )


def sse_done(
    *,
    msg_id: str,
    seq: int,
    model: str,
    input_tokens: int,
    output_tokens: int,
    stop_reason: str,
) -> bytes:
    return _sse(
        {
            "type": "done",
            "msg_id": msg_id,
            "seq": seq,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "stop_reason": stop_reason,
        }
    )
