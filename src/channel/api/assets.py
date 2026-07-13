# Copyright (c) 2026 John Carter. All rights reserved.
"""Asset REST surface — per-chat list/get/content/delete + browse (#325, epic #321).

Read/serve/delete surface over the #324 asset rows. Producers land
separately (#326); this router never creates assets — there is
deliberately no ``POST`` (assets are server-created only in v1, per
the #321 design review).

Response contract (consumed by the #327 inline cards and the #328
Artifacts browse view — keep in sync with decision Q4 on the #321
design-review comment). Every list/get route returns **card
descriptors**, never payload bytes and never S3 coordinates:

``{asset_id, chat_id, msg_id, kind, title, mime, size_bytes, origin,
created_at, source}``

- ``kind ∈ code | document | data | image | diagram``
- ``origin ∈ upload | generated | tool_output``
- ``msg_id`` is hoisted from ``source["msg_id"]`` so the SPA can group
  cards by message without digging into ``source``.

Routes (all behind the shared mgmt-JWT dependency):

- ``GET /api/chats/{chat_id}/assets`` → ``{"items": [card, ...]}``
  oldest-first (the SPA reattaches cards on history load).
- ``GET /api/chats/{chat_id}/assets/{asset_id}`` → ``card`` | 404.
- ``GET /api/chats/{chat_id}/assets/{asset_id}/content`` → raw bytes
  with the asset's ``Content-Type``. Content is **API-mediated** — no
  presigned GET URLs (decision Q2): the SPA fetches with the Bearer
  header and blob-URLs images, keeping auth in headers.
- ``DELETE /api/chats/{chat_id}/assets/{asset_id}`` → 204, idempotent
  (a second delete of the same id is also 204).
- ``GET /api/assets?limit=&cursor=`` → ``{"items": [card, ...],
  "next_cursor": str | null}`` newest-first across chats via
  ``AssetOwnerIndex``; owner comes from the JWT ``sub`` claim only
  (never a query param — token-scoping product decision). Pages
  batch-verify chat liveness, silently drop orphaned rows, and
  best-effort reap them (decision Q5), counting
  ``AssetLazyExpiryReaps``.

Ownership: every chat-scoped route goes through
:func:`channel.api.chats._load_owned_chat` — mismatch or missing chat
is a 404, never a 403, so chat existence isn't leaked (established
product decision). Cursors are opaque base64url-JSON tokens validated
on decode; DDB key shapes never leak raw (per the #235/#338 admin
pagination precedent).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from channel import storage
from channel.api._auth import require_mgmt_user
from channel.api.chats import _load_owned_chat
from channel.logging_config import fingerprint_id
from channel.metrics import record_asset_lazy_expiry_reaps
from channel.models import Asset

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assets"])

# The exact key set of an ``AssetOwnerIndex`` ``LastEvaluatedKey`` —
# GSI cursors carry both the index keys and the base-table keys.
_CURSOR_KEYS = frozenset({"PK", "SK", "owner_pk", "owner_sk"})


# ----------------------------------------------------------------
# Card descriptors — the one response row shape (#321 decision Q4)
# ----------------------------------------------------------------


def _clean_source(source: dict[str, Any]) -> dict[str, Any]:
    """Normalise DDB ``Decimal`` values (e.g. ``fence_index``) for JSON.

    Integral Decimals become ints so consumers see ``3``, not ``3.0``.
    """
    return {
        k: (int(v) if v == v.to_integral_value() else float(v)) if isinstance(v, Decimal) else v
        for k, v in source.items()
    }


def _card_from_asset(asset: Asset) -> dict[str, Any]:
    source = _clean_source(asset.source)
    return {
        "asset_id": asset.asset_id,
        "chat_id": asset.chat_id,
        "msg_id": source.get("msg_id"),
        "kind": asset.kind,
        "title": asset.title,
        "mime": asset.mime,
        "size_bytes": asset.size_bytes,
        "origin": asset.origin,
        "created_at": asset.created_at,
        "source": source,
    }


def _card_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Card descriptor from a raw browse row (content-projected, so it
    can't hydrate the XOR-validated :class:`~channel.models.Asset` —
    see :func:`channel.storage.list_assets_by_owner`)."""
    source = _clean_source(dict(row.get("source") or {}))
    return {
        "asset_id": row.get("asset_id"),
        "chat_id": row.get("chat_id"),
        "msg_id": source.get("msg_id"),
        "kind": row.get("kind"),
        "title": row.get("title"),
        "mime": row.get("mime"),
        "size_bytes": int(row.get("size_bytes", 0)),
        "origin": row.get("origin"),
        "created_at": row.get("created_at"),
        "source": source,
    }


# ----------------------------------------------------------------
# Cursor codec — opaque base64url-JSON, never raw DDB key shapes
# ----------------------------------------------------------------


def _encode_cursor(key: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(key, separators=(",", ":")).encode()).decode()


def _decode_cursor(token: str, owner: str) -> dict[str, Any]:
    """Decode an opaque browse cursor; 400 on anything malformed.

    A corrupt or foreign cursor must be a client error, never a 500.
    Beyond shape validation (the exact GSI key set, all-string values —
    anything else would raise a DDB ``ValidationException`` as
    ``ExclusiveStartKey``), the embedded ``owner_pk`` must be the
    caller's own partition so a tampered cursor can never resume
    another owner's listing.
    """
    try:
        key = json.loads(base64.urlsafe_b64decode(token.encode()))
    except ValueError as exc:
        # Covers bad base64 (binascii.Error subclasses ValueError),
        # non-UTF-8 bytes, and malformed JSON alike.
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if (
        not isinstance(key, dict)
        or set(key) != _CURSOR_KEYS
        or not all(isinstance(v, str) for v in key.values())
        or key["owner_pk"] != f"ASSETOWNER#{owner}"
    ):
        raise HTTPException(status_code=400, detail="invalid cursor")
    return key


# ----------------------------------------------------------------
# Chat-scoped routes — ownership via _load_owned_chat (404, never 403)
# ----------------------------------------------------------------


@router.get(
    "/chats/{chat_id}/assets",
    responses={404: {"description": "Chat not found (missing or not owned by the caller)"}},
)
async def list_chat_assets(
    chat_id: str,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """A chat's assets, oldest first (the history-reattach call)."""
    await _load_owned_chat(chat_id, claims["sub"])
    assets = await asyncio.to_thread(storage.list_chat_assets, chat_id)
    return {"items": [_card_from_asset(a) for a in assets]}


@router.get(
    "/chats/{chat_id}/assets/{asset_id}",
    responses={404: {"description": "Chat or asset not found"}},
)
async def get_chat_asset(
    chat_id: str,
    asset_id: str,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Single asset metadata (card descriptor)."""
    await _load_owned_chat(chat_id, claims["sub"])
    asset = await asyncio.to_thread(storage.get_asset, chat_id=chat_id, asset_id=asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return _card_from_asset(asset)


@router.get(
    "/chats/{chat_id}/assets/{asset_id}/content",
    responses={
        404: {"description": "Chat, asset, or asset payload not found"},
        502: {"description": "Asset content unavailable (upstream S3 failure)"},
    },
)
async def get_chat_asset_content(
    chat_id: str,
    asset_id: str,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """The asset payload, served with its stored ``Content-Type``.

    Inline assets are served straight from the row; S3-backed assets
    stream through :func:`channel.storage.get_asset_bytes` (same
    failure vocabulary as attachments). ``nosniff`` pins the declared
    type; text payloads are UTF-8 (inline content is UTF-8-encoded by
    the storage layer, and producers write UTF-8 to S3).
    """
    await _load_owned_chat(chat_id, claims["sub"])
    asset = await asyncio.to_thread(storage.get_asset, chat_id=chat_id, asset_id=asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    data, reason = await asyncio.to_thread(storage.get_asset_bytes, asset)
    if data is None:
        if reason == "S3 object not found":
            # The row outlived its S3 object (raced cascade / manual
            # wipe) — from the caller's side the asset is gone.
            raise HTTPException(status_code=404, detail="Asset content not found")
        logger.warning(
            # Both ids are caller-supplied path params — fingerprint
            # them rather than logging user-controlled data (S5145).
            "asset.content_fetch_failed chat_id_hash=%s asset_id_hash=%s reason=%s",
            fingerprint_id(chat_id),
            fingerprint_id(asset_id),
            reason,
        )
        raise HTTPException(status_code=502, detail="Asset content unavailable")
    media_type = asset.mime
    if media_type.startswith("text/") and "charset=" not in media_type:
        media_type += "; charset=utf-8"
    return Response(
        content=data,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete(
    "/chats/{chat_id}/assets/{asset_id}",
    status_code=204,
    responses={404: {"description": "Chat not found (missing or not owned by the caller)"}},
)
async def delete_chat_asset(
    chat_id: str,
    asset_id: str,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Delete one asset (S3 object then row). Idempotent — deleting an
    id that no longer exists is a 204, not a 404.

    Storage deletes the S3 object BEFORE the row, so a failed S3 side
    (which surfaces as a 500 here) leaves the row for a retry or the
    lazy-expiry reap — never an orphaned S3 object.
    """
    await _load_owned_chat(chat_id, claims["sub"])
    asset = await asyncio.to_thread(storage.get_asset, chat_id=chat_id, asset_id=asset_id)
    if asset is not None:
        await asyncio.to_thread(storage.delete_asset, asset)
    return Response(status_code=204)


# ----------------------------------------------------------------
# Cross-chat browse — owner GSI, scoped by the JWT sub claim only
# ----------------------------------------------------------------


def _partition_by_chat_liveness(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a browse page into (live, orphaned) by chat liveness.

    One ``ChatByIdIndex`` point read per distinct ``chat_id`` on the
    page (decision Q5's batch verification); rows whose chat row is
    gone are orphans from a failed delete cascade.
    """
    live: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    liveness: dict[str, bool] = {}
    for row in rows:
        chat_id = str(row.get("chat_id"))
        alive = liveness.get(chat_id)
        if alive is None:
            alive = storage.get_chat_by_id(chat_id) is not None
            liveness[chat_id] = alive
        (live if alive else orphans).append(row)
    return live, orphans


async def _reap_orphans(orphans: list[dict[str, Any]], owner: str) -> None:
    """Best-effort lazy-expiry reap — must never fail the browse.

    :func:`channel.storage.reap_orphaned_assets` re-verifies chat
    liveness internally before deleting anything, so a stale liveness
    read here can never reap a living chat's assets. Outcome is
    reported via ``AssetLazyExpiryReaps`` / ``AssetLazyExpiryReapFailures``
    (no per-actor dimensions — cardinality rule).
    """
    try:
        reaped, failed = await asyncio.to_thread(storage.reap_orphaned_assets, orphans)
    except Exception as exc:
        logger.warning(
            "asset.browse_reap_failed owner_hash=%s orphans=%d",
            fingerprint_id(owner),
            len(orphans),
            extra={"error_type": type(exc).__name__, "error_message": str(exc)},
            exc_info=True,
        )
        await record_asset_lazy_expiry_reaps(0, len(orphans))
        return
    logger.info(
        "asset.browse_reap owner_hash=%s reaped=%d failed=%d",
        fingerprint_id(owner),
        reaped,
        failed,
    )
    await record_asset_lazy_expiry_reaps(reaped, failed)


@router.get(
    "/assets",
    responses={400: {"description": "Malformed or foreign cursor"}},
)
async def browse_assets(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Cross-chat recency-ordered browse of the caller's assets.

    ``next_cursor`` is non-null while more rows may exist; orphan
    dropping can legitimately return a short (even empty) ``items``
    page alongside a live cursor — clients page on ``next_cursor``,
    never on ``len(items)``.
    """
    owner = claims["sub"]
    start_key = _decode_cursor(cursor, owner) if cursor else None
    rows, last_key = await asyncio.to_thread(
        storage.list_assets_by_owner, owner, limit=limit, cursor=start_key
    )
    live_rows, orphan_rows = await asyncio.to_thread(_partition_by_chat_liveness, rows)
    if orphan_rows:
        await _reap_orphans(orphan_rows, owner)
    return {
        "items": [_card_from_row(r) for r in live_rows],
        "next_cursor": _encode_cursor(last_key) if last_key else None,
    }
