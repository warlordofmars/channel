# Copyright (c) 2026 John Carter. All rights reserved.
"""Asset producers for the chat stream path (#326, epic #321).

Everything that CREATES assets, per decision Q3 on the #321
design review (2026-07-12). Three producers:

1. **Upload projection** — at message-send, each verified attachment
   becomes an ``origin=upload`` ASSET row referencing the SAME S3
   object as the canonical ATTACHMENT row (metadata projection, no
   byte copy; ``kind`` derived from MIME).
2. **Code-exec images** — tool results matching the code-exec shape
   carry ``images[]`` (base64, harvested from the sandbox's /tmp);
   each image persists to S3 under ``assets/chat/{chat_id}/{asset_id}``
   as ``kind=image`` / ``origin=tool_output``.
3. **Fenced-code extraction** — the ONLY heuristic. After the stream
   settles, fenced code blocks >= :data:`FENCE_MIN_LINES` lines in the
   final assistant text (EXCLUDING ```` ```mermaid ```` fences — #278
   renders those inline) become assets with ``source.fence_index``
   recorded. CSV / TSV fences (and bare fences whose body sniffs as
   delimited tabular data) classify as ``kind=data`` so #363's inline
   table renderer picks them up; everything else stays ``kind=code``
   (#383). The message text persists UNCHANGED — the SPA swaps fence ->
   card by ordinal.

Every persist is fail-soft with per-asset isolation: a failure logs
(fingerprinted ids only — SonarCloud S5145), bumps
``AssetPersistFailures``, and skips that asset without breaking the
chat stream. The caller (``chats.py``) emits ``asset_created`` SSE
frames ONLY for assets this module actually persisted — a failed
persist never produces a frame (settled contract: a rendered card is
always durable).

Memory-guard note (settled in the #321 design review): nothing in this
module touches the Strands agent's message list. Extraction reads the
accumulated assistant text AFTER the stream settles, and code-exec
image payloads ride ``toolResult`` blocks that
``memory._payload_from_messages`` already strips — asset content never
reaches AgentCore Memory. Pinned by
``tests/unit/test_memory.py::test_payload_from_messages_never_leaks_asset_content``.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from channel import storage
from channel.logging_config import fingerprint_id
from channel.metrics import record_asset_persist_outcome
from channel.models import ASSET_INLINE_CONTENT_MAX_BYTES, Asset, Attachment

logger = logging.getLogger(__name__)

# Extraction threshold (decision Q3): fenced code blocks with at least
# this many BODY lines become assets. Below the bar the fence stays a
# plain inline code block.
FENCE_MIN_LINES = 15

# Fence languages excluded from extraction. Mermaid fences render
# inline as diagrams (#278 owns those) — turning them into code cards
# would fight that renderer.
_EXCLUDED_FENCE_LANGS = frozenset({"mermaid"})

# Opening fence: up to 3 leading spaces (CommonMark), 3+ backticks,
# optional info string. Closing fence: same indent latitude, at least
# as many backticks as the opener, nothing else on the line.
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,})(.*)$")
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")

# MIME -> Asset.kind for upload projections. Anything outside the map
# (shouldn't happen — presign enforces the same allowlist) falls back
# to "document".
_UPLOAD_MIME_TO_KIND: dict[str, str] = {
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
    "text/csv": "data",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "data",
    "application/pdf": "document",
    "text/plain": "document",
    "text/markdown": "document",
}


def _now_iso() -> str:
    """UTC ISO-8601 with microseconds — matches ``storage._now_iso`` so
    ASSET SKs sort consistently with every other timestamped row."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def asset_extraction_enabled() -> bool:
    """Kill-switch for the fenced-code heuristic (decision Q3).

    ``CHANNEL_ASSET_EXTRACTION_ENABLED`` gates ONLY the extraction
    producer — the deterministic producers (upload projection,
    code-exec images) have no flag. Default on, matching the
    ``CHANNEL_AUTO_TITLE_ENABLED`` posture.
    """
    return os.environ.get("CHANNEL_ASSET_EXTRACTION_ENABLED", "1") == "1"


def asset_card_descriptor(asset: Asset) -> dict[str, Any]:
    """Build the inline-card descriptor for one persisted asset.

    The exact shape settled by decision Q4 — ``msg_id`` is hoisted out
    of ``source`` as a first-class field because the SPA groups cards
    by message: ``{asset_id, chat_id, msg_id, kind, title, mime,
    size_bytes, origin, created_at, source}``. Payload coordinates
    (inline ``content`` / S3 keys) are deliberately absent — cards
    fetch bytes via the #325 content endpoint.
    """
    return {
        "asset_id": asset.asset_id,
        "chat_id": asset.chat_id,
        "msg_id": asset.source["msg_id"],
        "kind": asset.kind,
        "title": asset.title,
        "mime": asset.mime,
        "size_bytes": asset.size_bytes,
        "origin": asset.origin,
        "created_at": asset.created_at,
        "source": asset.source,
    }


@dataclass(frozen=True)
class CodeFence:
    """One fenced code block found in assistant text.

    ``fence_index`` counts EVERY fence in the message — including
    mermaid and sub-threshold ones — so the SPA can enumerate the
    fences in the rendered markdown and swap by ordinal without
    re-implementing the qualifying rules (decision Q3: deterministic
    fence -> card swap, no hashing).
    """

    fence_index: int
    lang: str
    title: str
    body: str
    line_count: int


def extract_code_fences(text: str) -> list[CodeFence]:
    """Scan assistant text for ALL fenced code blocks.

    Parsing rules (deterministic — the SPA mirrors this scan):

    * Backtick fences only, 3+ backticks, up to 3 leading spaces.
    * The closer needs at least as many backticks as the opener and
      nothing else on the line.
    * An unterminated fence at end-of-text captures the remaining
      lines as its body (markdown renderers show it as an open code
      block, so the ordinal contract still holds).
    * Lines inside a fence body are never treated as headings or
      fence openers.

    Title resolution, in priority order (decision Q3):

    1. Extra info-string tokens beyond the language
       (```` ```python fib.py ```` -> ``fib.py``).
    2. The nearest markdown heading above the fence.
    3. ``Code — {lang}``, or bare ``Code`` when the fence has no
       info string.
    """
    lines = text.split("\n")
    fences: list[CodeFence] = []
    last_heading: str | None = None
    fence_index = 0
    i = 0
    while i < len(lines):
        opened = _FENCE_OPEN_RE.match(lines[i])
        if not opened:
            heading = _HEADING_RE.match(lines[i])
            if heading:
                last_heading = heading.group(1).strip()
            i += 1
            continue
        ticks, info = opened.group(1), opened.group(2).strip()
        close_re = re.compile(r"^ {0,3}`{" + str(len(ticks)) + r",}\s*$")
        body_lines: list[str] = []
        j = i + 1
        closed = False
        while j < len(lines):
            if close_re.match(lines[j]):
                closed = True
                break
            body_lines.append(lines[j])
            j += 1
        tokens = info.split()
        lang = tokens[0].lower() if tokens else ""
        if len(tokens) > 1:
            title = " ".join(tokens[1:])
        elif last_heading:
            title = last_heading
        else:
            title = f"Code — {lang}" if lang else "Code"
        fences.append(
            CodeFence(
                fence_index=fence_index,
                lang=lang,
                title=title,
                body="\n".join(body_lines),
                line_count=len(body_lines),
            )
        )
        fence_index += 1
        i = j + 1 if closed else len(lines)
    return fences


def _qualifying_fences(fences: list[CodeFence]) -> list[CodeFence]:
    """Filter to extraction-eligible fences: >= FENCE_MIN_LINES body
    lines and not an excluded language (mermaid)."""
    return [
        f for f in fences if f.line_count >= FENCE_MIN_LINES and f.lang not in _EXCLUDED_FENCE_LANGS
    ]


# Fence info-string languages whose body is tabular data, not code (#383).
# These classify as ``kind=data`` (mapped to their canonical MIME) so #363's
# InlineData renderer shows an inline table instead of a code card. Mermaid
# stays in ``_EXCLUDED_FENCE_LANGS`` (never extracted at all); every other
# language is code.
_DATA_FENCE_LANGS: dict[str, str] = {
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
}


def _sniff_tabular_mime(body: str) -> str | None:
    """Best-effort content sniff for a BARE fence (no info string) that
    looks like delimited tabular data (#383).

    Deliberately conservative — reclassifying a code block as data on a
    false positive is worse than leaving a genuine CSV as a code card. A
    body qualifies only when, for a single delimiter (comma or tab):

    * every non-blank line splits into the SAME number of fields, and
    * that field count is >= 2 (at least one delimiter on every line), and
    * the header (first) row has no empty cells.

    A qualifying fence already carries >= :data:`FENCE_MIN_LINES` lines, and
    a code block hitting an identical comma/tab count on every one of those
    lines with a non-empty header row is vanishingly rare — so the check
    stays well on the safe side. Returns the matching MIME (``text/csv`` for
    comma, ``text/tab-separated-values`` for tab), else ``None``.
    """
    lines = [ln for ln in body.split("\n") if ln.strip()]
    if len(lines) < 2:  # need a header plus at least one data row
        return None
    for delim, mime in ((",", "text/csv"), ("\t", "text/tab-separated-values")):
        counts = [ln.count(delim) for ln in lines]
        if counts[0] >= 1 and len(set(counts)) == 1:
            header = [cell.strip() for cell in lines[0].split(delim)]
            if all(header):
                return mime
    return None


def _classify_fence(fence: CodeFence) -> tuple[Literal["code", "data"], str]:
    """Map a qualifying fence to its ``(kind, mime)`` (#383).

    ``csv`` / ``tsv`` info strings are tabular data; a BARE fence (no info
    string) whose body sniffs as consistent delimited data is too. Every
    other fence is code (``text/plain``). Mermaid never reaches here — it is
    excluded from qualification upstream.
    """
    data_mime = _DATA_FENCE_LANGS.get(fence.lang)
    if data_mime is not None:
        return "data", data_mime
    if not fence.lang:
        sniffed = _sniff_tabular_mime(fence.body)
        if sniffed is not None:
            return "data", sniffed
    return "code", "text/plain"


async def _record_outcome_safe(success: bool) -> None:
    """Emit the persist counter without ever raising into the stream.

    Metric emission rides the producers' fail-soft contract too: an EMF
    flush/serialization failure after a successful ``put_asset`` must
    not abort the producer loop (or the SSE stream it runs inside) —
    the row is durable and the frame must still go out. Log + swallow.
    """
    try:
        await record_asset_persist_outcome(success=success)
    except Exception:
        logger.warning("asset.metric_emit_failed success=%s", success, exc_info=True)


async def _record_persist_failure(*, producer: str, chat_id: str, exc: Exception) -> None:
    """Shared fail-soft tail: log (fingerprinted ids only) + EMF count."""
    logger.warning(
        "asset.persist_failed producer=%s chat_id_hash=%s",
        producer,
        fingerprint_id(chat_id),
        extra={"error_type": type(exc).__name__, "error_message": str(exc)},
        exc_info=True,
    )
    await _record_outcome_safe(success=False)


def _cleanup_orphaned_object(*, chat_id: str, s3_bucket: str | None, s3_key: str | None) -> None:
    """Best-effort compensating delete for a PRODUCED S3 object whose
    ASSET row write failed (Copilot round 2).

    A row-less object under ``assets/chat/*`` is invisible to both the
    chat-delete cascade and the lazy-expiry reap (each discovers
    objects via rows) and carries no lifecycle GC tag — without this,
    transient DynamoDB errors would leak S3 objects unboundedly.

    Only called with coordinates returned by ``put_asset_bytes`` —
    NEVER for upload projections, whose S3 object belongs to the
    canonical ATTACHMENT row and must survive a failed projection.
    Cleanup failure is logged + swallowed: the persist failure is
    already counted, and a doubly-failed delete just leaves the
    (rare) orphan it was trying to prevent.
    """
    if s3_bucket is None or s3_key is None:
        return
    try:
        storage.delete_asset_object(bucket=s3_bucket, key=s3_key)
    except Exception:
        logger.warning(
            "asset.orphan_cleanup_failed chat_id_hash=%s",
            fingerprint_id(chat_id),
            exc_info=True,
        )


async def persist_upload_assets(
    *,
    chat_id: str,
    owner: str,
    msg_id: str,
    attachments: list[Attachment],
) -> list[Asset]:
    """Project verified attachments into ``origin=upload`` ASSET rows.

    Metadata projection only — each row points at the SAME S3 object
    as the canonical ATTACHMENT row (no byte copy; the #109 presign /
    finalize ingestion flow stays untouched). ``kind`` derives from
    MIME; ``title`` is the original filename; ``source`` records the
    attachment id so the projection is traceable back to ingestion.

    Returns the assets that actually persisted. Per-asset failures are
    logged + counted + skipped (fail-soft).
    """
    persisted: list[Asset] = []
    for att in attachments:
        try:
            now = _now_iso()
            asset = Asset(
                asset_id=str(uuid4()),
                chat_id=chat_id,
                owner=owner,
                kind=_UPLOAD_MIME_TO_KIND.get(att.mime, "document"),  # type: ignore[arg-type]
                title=att.name,
                mime=att.mime,
                size_bytes=att.size_bytes,
                origin="upload",
                source={"msg_id": msg_id, "attachment_id": att.id},
                s3_bucket=att.s3_bucket,
                s3_key=att.s3_key,
                created_at=now,
                updated_at=now,
            )
            storage.put_asset(asset)
        except Exception as exc:
            await _record_persist_failure(producer="upload", chat_id=chat_id, exc=exc)
            continue
        await _record_outcome_safe(success=True)
        persisted.append(asset)
    return persisted


async def persist_code_exec_image_assets(
    *,
    chat_id: str,
    owner: str,
    msg_id: str,
    images_by_tool: list[tuple[str, list[dict[str, Any]]]],
) -> list[Asset]:
    """Persist code-exec output images as ``origin=tool_output`` assets.

    ``images_by_tool`` pairs each ``tool_use_id`` with the sandbox
    handler's ``images[]`` entries (``{"mime": str, "b64": str}`` —
    see ``channel.sandbox.handler._harvest_tmp_images``). Bytes go to
    S3 under ``assets/chat/{chat_id}/{asset_id}`` (all binary content
    is S3-backed per decision Q2); the row lands only after the S3 put
    succeeds. Titles enumerate across the turn (``Figure 1``,
    ``Figure 2``, ...).

    The existing base64-over-SSE live rendering in the SPA's
    ``ToolResultBlock`` is unchanged — persistence is additive, and
    history reload re-renders from the #325 content endpoint.

    Returns the assets that actually persisted. Per-image failures
    (bad base64, S3 errors, DDB errors) are logged + counted + skipped.
    """
    persisted: list[Asset] = []
    figure_ordinal = 0
    for tool_use_id, images in images_by_tool:
        for entry in images:
            figure_ordinal += 1
            try:
                # ``validate=True`` — strict alphabet check. The input
                # comes from our own sandbox handler's b64encode, so any
                # non-alphabet byte means a malformed payload; without
                # strict mode b64decode silently drops such bytes and
                # would persist garbage instead of counting a failure.
                data = base64.b64decode(entry["b64"], validate=True)
            except (KeyError, TypeError, ValueError, binascii.Error) as exc:
                await _record_persist_failure(producer="code_exec_image", chat_id=chat_id, exc=exc)
                continue
            # Re-initialised per image so a failure BEFORE this entry's
            # S3 put can never trigger cleanup against a previous
            # iteration's coordinates.
            bucket: str | None = None
            key: str | None = None
            try:
                asset_id = str(uuid4())
                mime = entry.get("mime", "application/octet-stream")
                bucket, key = storage.put_asset_bytes(
                    chat_id=chat_id, asset_id=asset_id, data=data, mime=mime
                )
                now = _now_iso()
                asset = Asset(
                    asset_id=asset_id,
                    chat_id=chat_id,
                    owner=owner,
                    kind="image",
                    title=f"Figure {figure_ordinal}",
                    mime=mime,
                    size_bytes=len(data),
                    origin="tool_output",
                    source={"msg_id": msg_id, "tool_use_id": tool_use_id},
                    s3_bucket=bucket,
                    s3_key=key,
                    created_at=now,
                    updated_at=now,
                )
                storage.put_asset(asset)
            except Exception as exc:
                await _record_persist_failure(producer="code_exec_image", chat_id=chat_id, exc=exc)
                _cleanup_orphaned_object(chat_id=chat_id, s3_bucket=bucket, s3_key=key)
                continue
            await _record_outcome_safe(success=True)
            persisted.append(asset)
    return persisted


async def persist_generated_image_assets(
    *,
    chat_id: str,
    owner: str,
    msg_id: str,
    images: list[dict[str, Any]],
) -> list[Asset]:
    """Persist ``generate_image`` (Stable Image Core) outputs as assets (#279).

    ``images`` is the per-turn sink the ``generate_image`` tool stashed on
    the invoking Agent (``generated_image_sink``); each entry is
    ``{"tool_use_id": str, "b64": str, "mime": str, "title": str}``. The
    base64 payload travels out-of-band from SSE (never on the wire —
    decision 6): bytes go to S3 under ``assets/chat/{chat_id}/{asset_id}``
    and the row lands only after the S3 put succeeds. ``kind=image``,
    ``origin=generated``, ``source={msg_id, tool_use_id}``.

    Shares every hardened pattern with
    :func:`persist_code_exec_image_assets`: strict ``b64decode`` (a
    malformed payload counts a failure instead of silently persisting
    garbage), per-image failure isolation (log + ``AssetPersistFailures``
    + skip the frame), and the compensating S3 delete when the row write
    fails after the object landed.

    Returns the assets that actually persisted — the caller emits an
    ``asset_created`` frame only for those (emit-after-persist contract).
    """
    persisted: list[Asset] = []
    for entry in images:
        try:
            # ``validate=True`` — strict alphabet check; the b64 comes from
            # the Stability image model via our own tool, so any non-alphabet
            # byte means a malformed payload rather than something to drop.
            data = base64.b64decode(entry["b64"], validate=True)
        except (KeyError, TypeError, ValueError, binascii.Error) as exc:
            await _record_persist_failure(producer="generate_image", chat_id=chat_id, exc=exc)
            continue
        # Re-initialised per image so a failure BEFORE this entry's S3 put
        # can never trigger cleanup against a previous iteration's coords.
        bucket: str | None = None
        key: str | None = None
        try:
            asset_id = str(uuid4())
            mime = entry.get("mime", "image/png")
            bucket, key = storage.put_asset_bytes(
                chat_id=chat_id, asset_id=asset_id, data=data, mime=mime
            )
            now = _now_iso()
            asset = Asset(
                asset_id=asset_id,
                chat_id=chat_id,
                owner=owner,
                kind="image",
                title=entry.get("title") or "Generated image",
                mime=mime,
                size_bytes=len(data),
                origin="generated",
                source={"msg_id": msg_id, "tool_use_id": entry["tool_use_id"]},
                s3_bucket=bucket,
                s3_key=key,
                created_at=now,
                updated_at=now,
            )
            storage.put_asset(asset)
        except Exception as exc:
            await _record_persist_failure(producer="generate_image", chat_id=chat_id, exc=exc)
            _cleanup_orphaned_object(chat_id=chat_id, s3_bucket=bucket, s3_key=key)
            continue
        await _record_outcome_safe(success=True)
        persisted.append(asset)
    return persisted


async def persist_fence_assets(
    *,
    chat_id: str,
    owner: str,
    msg_id: str,
    text: str,
) -> list[Asset]:
    """Extract qualifying fenced blocks into ``kind=code`` / ``kind=data``
    assets.

    The post-stream heuristic (decision Q3): fences with >=
    :data:`FENCE_MIN_LINES` body lines, mermaid excluded,
    ``source.fence_index`` recorded for the deterministic fence -> card
    swap. Each fence is classified by :func:`_classify_fence` — ``csv`` /
    ``tsv`` fences (and bare fences whose body sniffs as delimited tabular
    data) become ``kind=data`` with a CSV/TSV MIME so #363's inline table
    renderer picks them up; everything else stays ``kind=code`` /
    ``text/plain`` (#383). The assistant message text is NEVER mutated —
    this reads the settled text and writes rows on the side.
    ``source.lang`` carries the info-string language so the SPA's code
    renderer can highlight without sniffing.

    Inline ``content`` for bodies <= 100 KB UTF-8; larger bodies go to
    S3 (decision Q2). Short-circuits to ``[]`` when the
    ``CHANNEL_ASSET_EXTRACTION_ENABLED`` kill-switch is off.

    Returns the assets that actually persisted. Per-fence failures are
    logged + counted + skipped.
    """
    if not asset_extraction_enabled():
        return []
    persisted: list[Asset] = []
    for fence in _qualifying_fences(extract_code_fences(text)):
        kind, mime = _classify_fence(fence)
        # Initialised OUTSIDE the try (and re-initialised per fence) so
        # the except path can safely read the coordinates: set only
        # when this fence's body actually landed in S3.
        s3_bucket: str | None = None
        s3_key: str | None = None
        try:
            asset_id = str(uuid4())
            body_bytes = fence.body.encode("utf-8")
            content: str | None = fence.body
            if len(body_bytes) > ASSET_INLINE_CONTENT_MAX_BYTES:
                content = None
                s3_bucket, s3_key = storage.put_asset_bytes(
                    chat_id=chat_id, asset_id=asset_id, data=body_bytes, mime=mime
                )
            now = _now_iso()
            asset = Asset(
                asset_id=asset_id,
                chat_id=chat_id,
                owner=owner,
                kind=kind,
                title=fence.title,
                mime=mime,
                size_bytes=len(body_bytes),
                origin="generated",
                source={
                    "msg_id": msg_id,
                    "fence_index": fence.fence_index,
                    "lang": fence.lang,
                },
                content=content,
                s3_bucket=s3_bucket,
                s3_key=s3_key,
                created_at=now,
                updated_at=now,
            )
            storage.put_asset(asset)
        except Exception as exc:
            await _record_persist_failure(producer="fence", chat_id=chat_id, exc=exc)
            _cleanup_orphaned_object(chat_id=chat_id, s3_bucket=s3_bucket, s3_key=s3_key)
            continue
        await _record_outcome_safe(success=True)
        persisted.append(asset)
    return persisted
