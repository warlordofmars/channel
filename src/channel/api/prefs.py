# Copyright (c) 2026 John Carter. All rights reserved.
"""User preferences API — GET / PUT /api/me/prefs.

Single JSON-blob row at ``PK=USER#{u}, SK=PREFS``. Defaults are baked
into the ``Prefs`` model so a missing row returns the canonical
defaults at hydration time. Partial PUTs are allowed; unknown keys
are rejected (422) to prevent stale-client silent drift.
"""

from __future__ import annotations

from typing import Any

import pydantic
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from .. import storage
from ..models import Prefs
from ._auth import require_mgmt_user

router = APIRouter()


class PrefsEnvelope(BaseModel):
    prefs: Prefs


class PrefsUpdateRequest(BaseModel):
    prefs: dict[str, Any]


@router.get("/api/me/prefs", response_model=PrefsEnvelope)
def get_my_prefs(
    response: Response,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> PrefsEnvelope:
    # Without an explicit no-store, Chromium (browser + Electron renderer)
    # heuristic-caches the response. Cross-device sync then sees stale
    # values until the heuristic TTL elapses.
    response.headers["Cache-Control"] = "no-store"
    return PrefsEnvelope(prefs=storage.get_prefs(claims["sub"]))


@router.put("/api/me/prefs", status_code=204)
def put_my_prefs(
    body: PrefsUpdateRequest,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> None:
    try:
        storage.put_prefs(claims["sub"], body.prefs)
    except pydantic.ValidationError as exc:
        # Surface as 422 with the offending key(s).
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
