# Copyright (c) 2026 John Carter. All rights reserved.
"""Models allowlist endpoint.

``GET /api/models`` returns the server-allowlisted model IDs the UI
ModelPicker can offer.  Keeping this server-side prevents the SPA from
allowing arbitrary models without IAM coverage.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from channel.api._auth import require_mgmt_user

router = APIRouter(tags=["models"])

_MODEL_DISPLAY: list[dict[str, Any]] = [
    {
        "id": "claude-opus-4-6",
        "label": "Claude Opus 4.6",
        "family": "anthropic",
        "tier": "Flagship",
        "supports_streaming": True,
    },
    {
        "id": "claude-sonnet-4-6",
        "label": "Claude Sonnet 4.6",
        "family": "anthropic",
        "tier": "Balanced",
        "supports_streaming": True,
    },
    {
        "id": "claude-haiku-4-5",
        "label": "Claude Haiku 4.5",
        "family": "anthropic",
        "tier": "Fast",
        "supports_streaming": True,
    },
]


@router.get("/models")
async def list_models(
    _claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Server allowlist of model IDs the picker can choose from."""

    return {"models": _MODEL_DISPLAY}
