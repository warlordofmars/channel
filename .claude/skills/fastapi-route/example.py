# Copyright (c) 2026 John Carter. All rights reserved.
"""
Reference example for the ``fastapi-route`` skill.

This file is documentation, not application code — it lives under
``.claude/skills/`` and is not imported by the running app or tests.
Copy it into ``src/channel/api/<area>.py`` and adapt the names when
adding a real endpoint.

Mirrors every convention captured in ``SKILL.md``:

1. File location — ``src/channel/api/<area>.py``
2. Router wiring — registered from ``main.py`` with ``prefix="/api"``
3. Auth dependency — ``Depends(require_mgmt_user)``
4. Streaming pattern — ``StreamingResponse`` with the ADR-0002 event schema
5. Test structure — companion ``tests/unit/test_<area>_api.py``
6. Coverage gate — every branch has a corresponding test (see comments)
7. Copyright header — the line above
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from channel.api._auth import require_mgmt_user

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class WidgetCreateRequest(BaseModel):
    name: str
    description: str | None = None


class WidgetResponse(BaseModel):
    id: str
    name: str
    owner_id: str


# ---------------------------------------------------------------------------
# Non-streaming endpoint
# ---------------------------------------------------------------------------


@router.post("/widgets", response_model=WidgetResponse)
def create_widget(
    body: WidgetCreateRequest,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> WidgetResponse:
    """Create a widget owned by the authenticated user.

    The handler shows the canonical shape: validate the body via the Pydantic
    model, pull the caller's identity from ``claims["sub"]``, delegate to a
    storage / domain function (omitted here), and return the typed response
    model so FastAPI generates an accurate OpenAPI schema.
    """
    if not body.name.strip():
        # Test triggers this branch with an empty-name payload — see
        # ``test_create_widget_rejects_blank_name`` in the companion test file.
        raise HTTPException(status_code=400, detail="name must not be blank")

    # Replace this with a real call into ``channel.storage`` or wherever the
    # widget is persisted.
    widget_id = f"w-{body.name.lower().replace(' ', '-')}"
    return WidgetResponse(id=widget_id, name=body.name, owner_id=claims["sub"])


# ---------------------------------------------------------------------------
# Streaming endpoint (SSE per ADR-0002 §Decision #4)
# ---------------------------------------------------------------------------


@router.post("/widgets/stream")
def stream_widgets(
    _claims: dict[str, Any] = Depends(require_mgmt_user),
) -> StreamingResponse:
    """Stream widget events back to the caller as SSE.

    Two event types per ADR-0002:

    * ``{"type": "delta", "text": "..."}`` — incremental payload
    * ``{"type": "done", ...}``           — final event with metadata

    The route handler stays thin: it delegates to a generator that knows the
    domain and wraps the result in ``StreamingResponse`` with the
    ``text/event-stream`` media type. The downstream generator owns the wire
    format; the handler does not build SSE strings itself.
    """

    def _stream() -> Iterator[str]:
        # Real code yields from a domain helper (e.g. the Strands stream in
        # ``channel.api.chats``, which formats each frame via
        # ``translate_event`` in ``channel.agents.strands_sse``). Inline two
        # yields here so the example is self-contained.
        yield f"data: {json.dumps({'type': 'delta', 'text': 'first'})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'count': 1})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Wiring (lives in src/channel/api/main.py, shown here for completeness)
# ---------------------------------------------------------------------------
#
# from channel.api.widgets import router as widgets_router
#
# # Widget endpoints (require management JWT)
# app.include_router(widgets_router, prefix="/api")
#
# ---------------------------------------------------------------------------
# Companion tests live at tests/unit/test_widgets_api.py and cover:
#   - test_create_widget_requires_auth        (no token → 401/403)
#   - test_create_widget_returns_widget       (happy path, mocked storage)
#   - test_create_widget_rejects_blank_name   (HTTPException branch)
#   - test_stream_widgets_returns_event_stream_content_type
#   - test_stream_widgets_yields_delta_then_done
# See SKILL.md §5 for the full test pattern and §6 for the coverage gate.
# ---------------------------------------------------------------------------
