# Copyright (c) 2026 John Carter. All rights reserved.
"""Memory read surface — ``GET /api/memory/records`` (#475, epic #129).

The user-facing answer to "what does Channel remember about me". Built
fresh rather than promoting ``GET /api/_debug/memory/events`` (epic #129
decision 6): the debug router is unpaginated, returns raw AgentCore wire
shapes, and the Phase 7c/8a Playwright suites depend on it — promoting it
would either leak the vendor shape into a public contract or force a
permission model onto a surface designed not to have one. ``_debug`` is
deliberately untouched by this module.

Enumeration, classification and the ``used_in_recall`` computation all live
in :mod:`channel.agents.memory_records` so the later forget / edit / export
endpoints (#476 / #477 / #479) inherit one definition of a record.

## Response

``{groups, summaries, recall_window, withheld_record_count, next_cursor}``

- ``groups`` — one per chat, newest chat first, each with the chat's
  records oldest-first. ``records[].text`` is the FULL stored text; the
  ``recall_window.text_truncate`` cap is a recall-*injection* limit, not a
  storage limit, so truncating here would misrepresent what is stored.
  ``records_truncated`` says whether the chat holds more events than the
  per-session cap, so the oldest are missing from ``records`` — a hard cap
  that reported nothing would be the same silent lie this surface exists
  to stop.
- ``summaries`` — the #245 rolling head summaries for the page's chats.
  Read-only, no ``record_id``: they are derived and regenerate whenever the
  history window slides, so offering a delete that silently reappears would
  be a worse lie than showing them read-only (epic #129 decision 4). They
  belong in "what Channel remembers" because they are injected into the
  system prompt on *every turn* of their chat — more load-bearing than most
  AgentCore records, which are injected into nothing.
- ``recall_window`` — the live caps, read from ``recall.py``'s constants.
  Showing stored records alone implies Channel uses far more than it does;
  showing only the recalled slice implies it has forgotten far more than it
  has. Both are silent lies in opposite directions (#227), so the response
  carries both and every record carries ``used_in_recall``. When
  ``recall_window.enabled`` is false the hook injects nothing, so every
  ``used_in_recall`` is false too — the flag and the envelope can never
  contradict each other.
- ``withheld_record_count`` — a **lower bound**, not an exact total; see
  §Scoping.
- ``next_cursor`` — opaque; page on it, never on ``len(groups)``.

**Rendering contract:** record and summary ``text`` is data. Render it as
plain text; never through ``renderMarkdown.jsx``. Stored turns are
attacker-influenced and can contain forged Markdown structure (#465 tracks
the same forgery surface on the system-prompt side). Epic #129 decision 11.

## Scoping

Two independent gates, deliberately not relying on each other:

1. ``actorId = derive_actor_id(claims["sub"])`` — no actor / user /
   workspace parameter exists on this endpoint, per the standing "agents
   swap tokens to switch context" product decision. When #283 makes
   ``actorId`` ``{workspace_id}/{user_id}``, no signature changes.
2. **Per-session ownership verification against the raw JWT sub.** Gate 1
   scopes the *partition*; gate 2 guards the *read*, comparing the chat
   row's own ``user_id`` to ``claims["sub"]`` — the raw sub, never a
   derived value. Every session on the page goes through
   ``resolve_owned_chats`` (``sessionId`` *is* a ``chat_id``, so
   ``get_chat_by_id(...).user_id == claims["sub"]`` is decisive), and
   anything unverified is dropped before the response is built.

   Gate 2 exists because a read boundary must not depend on a derivation's
   properties. #474 is the demonstration: the actor-id derivation was not
   injective (``jc+work@x.com`` and ``jc_work@x.com`` collided), so gate 1
   alone would have handed one user another user's private memory. #485
   makes that derivation injective — which repairs gate 1 but does not
   make gate 2 redundant, because gate 2 is what keeps this endpoint from
   inheriting the *next* such bug. ``withheld_record_count`` is the
   standing canary: with #485 landed it should read 0 in normal operation,
   and a non-zero value means either a partition anomaly or an orphaned
   session left by a failed chat-delete wipe. Both are worth knowing.

   **Read that count as "at least this many", never as an exact total.**
   ``count_session_records`` deliberately counts only the first
   ``MAX_EVENTS_PER_SESSION`` events of a withheld session, so a withheld
   session larger than the cap undercounts. That is the right trade: the
   count exists to raise an alarm ("a foreign session is in this actor's
   partition"), and paging deeper through another user's memory to make the
   number exact would be the opposite of the point. Alert on ``> 0``, not
   on the magnitude.

A supplied ``chat_id`` goes through ``_load_owned_chat``, so a miss is a
404 — never a 403 — matching the rest of the chat surface so chat existence
isn't leaked.

No caching. The recall hook's 5-turn cache exists to keep a hot path off
the network; this is admin-style usage, and a stale "what do you remember
about me" list is worse than a slow one.

## ``GET /api/memory/export`` (#476)

The other half of the Privacy page's standing promise — "you can export or
delete your data at any time" — which shipped to production before anything
implemented it.

**"Your data" is not "your AgentCore events",** so this is a full *account*
export: ``memory_records`` (the same read model ``/records`` serves),
``chat_summaries`` (#245 head summaries) and ``chats`` with their messages.
Every collection is walked to completion rather than paged, because an
export that silently stopped at page one would be the same silent lie the
rest of this module exists to stop. Both scoping gates above apply
unchanged — actor partition, then per-session ownership against the raw
JWT sub — so a foreign session in the partition is dropped here exactly as
it is there.

The ``manifest`` is what makes the file self-describing a year later: it
carries the live ``recall_window`` caps and the note that only the
``used_in_recall`` slice is ever loaded into a new conversation, so a
reader can tell stored-but-unused from actually-used without reading this
repo. It also carries ``truncated_chat_ids`` — the chats whose AgentCore
history exceeded ``MAX_EVENTS_PER_SESSION``, so their oldest records are
absent. Reporting that is the same principle as ``records_truncated`` on
``/records``: a partial export presented as complete is worse than a
partial export that says so.

One synchronous JSON response, not a job plus a presigned URL. Current
volumes fit the Lambda response budget; speculative export infrastructure
is its own issue if that stops being true.

**The audit write fails the request** (``memory.exported``, counts only —
never record text). That is deliberately the *opposite* call from
``sessions._audit_revocation``, which is best-effort: there the action has
already been applied, so failing the response would claim a sign-out
didn't happen when it did. Here nothing has been disclosed yet, so the
export can fail closed and keep the invariant that every whole-account
read left a trail. The caller sees a 5xx and can retry.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from functools import partial
from typing import Any, TypeVar

import boto3
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from channel import storage
from channel.agents.memory import derive_actor_id, get_or_create_memory
from channel.agents.memory_records import (
    count_session_records,
    group_created_at,
    list_session_records,
    list_sessions_page,
    recall_window,
    recall_window_session_ids,
    resolve_owned_chats,
)
from channel.api._auth import require_mgmt_user
from channel.api.chats import _load_owned_chat
from channel.logging_config import fingerprint_id
from channel.metrics import record_memory_export_outcome
from channel.models import Chat, Message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])


def _agentcore_client() -> Any:
    """Lazy boto3 client construction — patched in unit tests."""
    return boto3.client("bedrock-agentcore")


def _memory_id_for_env() -> str:
    """Resolve the current env's AgentCore memoryId. Cached by
    ``get_or_create_memory``; cheap to call per request."""
    return get_or_create_memory(os.environ.get("CHANNEL_ENV", "unknown"))


def _recall_enabled() -> bool:
    """Whether the recall hook is live — the ``recall_window.enabled`` flag.

    Read per-request rather than at import so a flipped kill-switch is
    reflected without a redeploy, and so the panel can honestly say
    "nothing is being recalled right now".
    """
    return os.environ.get("CHANNEL_RECALL_ENABLED", "1") == "1"


def _encode_cursor(next_token: str, actor_id: str) -> str:
    """Wrap AgentCore's ``nextToken`` in an opaque base64url-JSON envelope.

    The vendor token never appears raw in our contract — same reasoning as
    ``record_id``'s encoding (epic #129 decision 7) and the same envelope
    shape ``chats.py`` / ``assets.py`` already use for DynamoDB keys. The
    envelope is also what lets :func:`_decode_cursor` reject garbage as a
    400 instead of forwarding it to AgentCore for a ``ValidationException``
    (a 500).

    The envelope is **bound to the actor it was minted for**, matching
    ``chats._decode_cursor``'s chat-scoping and ``assets._decode_cursor``'s
    owner-scoping: a foreign ``nextToken`` is a malformed request, not
    something to hand to the vendor. The binding is a
    :func:`fingerprint_id` digest, so the cursor stays opaque and doesn't
    carry the caller's identity around in a query string.
    """
    return base64.urlsafe_b64encode(
        json.dumps({"t": next_token, "a": fingerprint_id(actor_id)}, separators=(",", ":")).encode()
    ).decode()


def _decode_cursor(cursor: str, actor_id: str) -> str:
    """Inverse of :func:`_encode_cursor`. A corrupt cursor is a 400.

    Four failure modes fold together: not valid base64url-JSON
    (``ValueError`` — which covers ``UnicodeDecodeError`` — or
    ``binascii.Error``), not an envelope dict, an empty/non-string token,
    or an actor binding that doesn't match the caller. The cursor is opaque
    to clients and only ever issued by this endpoint for this actor, so any
    deviation is a malformed request.
    """
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("t"), str)
        or not payload["t"]
        or payload.get("a") != fingerprint_id(actor_id)
    ):
        raise HTTPException(status_code=400, detail="invalid cursor")
    return payload["t"]


async def _summaries_for(chats: list[Chat]) -> list[dict[str, Any]]:
    """#245 head summaries for the page's verified chats, in page order.

    One point read per chat. Chats without a ``SUMMARY`` row are simply
    absent — every chat short enough to fit the token budget has none.
    """
    out: list[dict[str, Any]] = []
    for chat in chats:
        summary = await asyncio.to_thread(storage.get_chat_summary, chat.chat_id)
        if summary is None:
            continue
        out.append(
            {
                "chat_id": summary.chat_id,
                "chat_title": chat.title,
                "text": summary.text,
                "covers_through": summary.covers_through,
                "updated_at": summary.updated_at,
            }
        )
    return out


@router.get(
    "/records",
    responses={
        400: {"description": "Malformed cursor"},
        404: {"description": "chat_id filter names a chat the caller doesn't own"},
    },
)
async def list_memory_records(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    chat_id: str | None = Query(default=None),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Enumerate the caller's stored memory, grouped by chat.

    ``limit`` counts **sessions** per page, not records (epic #129 decision
    12). With ``chat_id`` supplied the page is that one chat and
    ``next_cursor`` is null; ``cursor`` is still validated so a malformed
    one is a 400 either way, but it is not otherwise used — there is
    nothing to page through.
    """
    user_id = claims["sub"]
    actor_id = derive_actor_id(user_id)
    # Validated before any AgentCore call, so a malformed or foreign cursor
    # is a locally-decided 400 rather than a vendor ValidationException (500).
    next_token = _decode_cursor(cursor, actor_id) if cursor else None

    client = _agentcore_client()
    memory_id = await asyncio.to_thread(_memory_id_for_env)

    # With the kill-switch off the hook injects nothing, so NOTHING is in the
    # recall window and every ``used_in_recall`` must read false. Reporting
    # the window a disabled hook *would* have used would be precisely the
    # kind of confident-but-wrong claim this surface exists to stop —
    # `recall_window.enabled: false` alone would leave the per-record flag
    # contradicting it. Short-circuiting also saves the extra ListSessions.
    recall_enabled = _recall_enabled()
    window: set[str] = (
        await recall_window_session_ids(client, memory_id=memory_id, actor_id=actor_id)
        if recall_enabled
        else set()
    )

    if chat_id is not None:
        chat = await _load_owned_chat(chat_id, user_id)
        session_ids: list[str] = [chat_id]
        owned: dict[str, Chat] = {chat_id: chat}
        page_token: str | None = None
    else:
        # ``session_summaries``, not ``summaries`` — the response's
        # ``summaries`` field is the #245 chat head summaries, a completely
        # different thing. These are AgentCore ListSessions rows.
        session_summaries, page_token = await list_sessions_page(
            client,
            memory_id=memory_id,
            actor_id=actor_id,
            limit=limit,
            next_token=next_token,
        )
        session_ids = [s["sessionId"] for s in session_summaries if s.get("sessionId")]
        # Ownership gate — see §Scoping. Sessions that fail it never reach
        # the enumeration below.
        owned = await asyncio.to_thread(resolve_owned_chats, session_ids, user_id=user_id)

    withheld_records = 0
    withheld_sessions = 0
    for session_id in session_ids:
        if session_id in owned:
            continue
        withheld_sessions += 1
        withheld_records += await count_session_records(
            client, memory_id=memory_id, actor_id=actor_id, session_id=session_id
        )

    # Newest chat first, matching the sidebar's Recents ordering and the
    # recency ordering ``recall_window`` reports. Ties break on chat_id so
    # the page order is deterministic.
    verified = sorted(
        (owned[sid] for sid in session_ids if sid in owned),
        key=lambda c: (c.created_at, c.chat_id),
        reverse=True,
    )

    groups: list[dict[str, Any]] = []
    for chat_row in verified:
        records, truncated = await list_session_records(
            client,
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=chat_row.chat_id,
            in_recall_window=chat_row.chat_id in window,
        )
        if not records:
            # A session with no text-bearing events would render as an
            # empty header; skip it. The chat's head summary (if any) still
            # surfaces under ``summaries``, which covers every verified
            # chat on the page rather than only those with records.
            continue
        groups.append(
            {
                "chat_id": chat_row.chat_id,
                "chat_title": chat_row.title,
                "created_at": group_created_at(chat_row),
                "records": [r.to_dict() for r in records],
                # True when the chat holds more than the per-session event
                # cap, so its OLDEST records aren't here. Reported rather
                # than swallowed: a partial list presented as complete is
                # exactly the silent lie this surface exists to stop (#227).
                "records_truncated": truncated,
            }
        )

    if withheld_sessions:
        # The read-boundary canary. A non-zero count means this actor
        # partition holds a session whose chat this caller does not own —
        # an actor-id collision (#474, repaired by #485) or an orphan left
        # by a failed chat-delete wipe. Either way it is worth an alert;
        # the raw sub is an email, so it is fingerprinted.
        logger.warning(
            "memory.records_withheld user_hash=%s sessions=%d records=%d",
            fingerprint_id(user_id),
            withheld_sessions,
            withheld_records,
        )

    return {
        "groups": groups,
        "summaries": await _summaries_for(verified),
        "recall_window": recall_window(enabled=recall_enabled),
        "withheld_record_count": withheld_records,
        "next_cursor": _encode_cursor(page_token, actor_id) if page_token else None,
    }


# ── Full account export (#476) ────────────────────────────────────────────

#: Bumped when the export's SHAPE changes in a way a reader must notice —
#: a field removed or re-meaning, not a field added. It exists so a file
#: found on a disk in a year identifies its own vintage.
_EXPORT_SCHEMA_VERSION = 1

#: Page sizes for the three walks. Larger than the interactive
#: ``/records`` page because nobody is reading this incrementally: the
#: whole account is materialised before a byte is written, so the only
#: thing page size buys is fewer round trips.
_EXPORT_SESSION_PAGE_SIZE = 50
_EXPORT_CHAT_PAGE_SIZE = 50
_EXPORT_MESSAGE_PAGE_SIZE = 100

#: Ceiling on pages per walk — a backstop against a paginator that keeps
#: handing back the same token, which in a synchronous Lambda handler is
#: not a slow response but a burnt function timeout. Set far above any
#: plausible real account (200 × 100 = 20 000 messages in one chat), and a
#: walk that hits it logs rather than silently truncating.
_EXPORT_MAX_PAGES = 200

#: Verbatim in the manifest. The single sentence that stops a reader
#: mistaking "Channel stored this" for "Channel reads this every turn" —
#: the confusion #227 was, and the reason every record carries
#: ``used_in_recall``.
_EXPORT_NOTE = (
    "Channel stores every record below, but only the slice marked "
    "used_in_recall is loaded into a new conversation."
)

_T = TypeVar("_T")


async def _paginate(
    fetch: Callable[[Any], Awaitable[tuple[list[_T], Any]]], *, what: str
) -> list[_T]:
    """Drain a ``(page, next_token)`` paginator to completion.

    One helper for all three walks (AgentCore sessions, DynamoDB chats,
    DynamoDB messages) because they differ only in their fetch closure, and
    a single drain is a single place for the page ceiling to live. ``fetch``
    takes the previous token (``None`` first) and returns the next page
    with the token after it.

    Exiting through the ``for``/``else`` means the ceiling was reached with
    a token still outstanding — the one case where the result is
    incomplete, so it is logged rather than returned as if whole.
    """
    items: list[_T] = []
    token: Any = None
    for _ in range(_EXPORT_MAX_PAGES):
        page, token = await fetch(token)
        items.extend(page)
        if not token:
            return items
    logger.warning("memory.export_page_cap_hit what=%s pages=%d", what, _EXPORT_MAX_PAGES)
    return items


async def _export_memory_records(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    user_id: str,
    window: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Every enumerable memory record the caller owns → ``(records, truncated)``.

    The same enumeration ``/records`` serves, drained across every
    ``ListSessions`` page instead of one, and flattened: the export is a
    file, so each record carries its own ``chat_id`` rather than being
    nested under a group. ``truncated`` lists the sessions whose history
    exceeded ``MAX_EVENTS_PER_SESSION``.

    The ownership gate is the same one §Scoping describes, and it matters
    more here than on ``/records``: this response is a downloadable file, so
    a foreign session leaking into it leaks into wherever that file ends up.
    Withheld sessions are logged (the standing partition canary) but not
    counted per-record — that would cost one ``ListEvents`` per foreign
    session to enrich an alarm that fires on ``> 0`` either way.
    """
    session_summaries = await _paginate(
        lambda tok: list_sessions_page(
            client,
            memory_id=memory_id,
            actor_id=actor_id,
            limit=_EXPORT_SESSION_PAGE_SIZE,
            next_token=tok,
        ),
        what="sessions",
    )
    session_ids = list(
        dict.fromkeys(s["sessionId"] for s in session_summaries if s.get("sessionId"))
    )
    owned = await asyncio.to_thread(resolve_owned_chats, session_ids, user_id=user_id)

    records: list[dict[str, Any]] = []
    truncated_chat_ids: list[str] = []
    for session_id in session_ids:
        if session_id not in owned:
            continue
        session_records, truncated = await list_session_records(
            client,
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=session_id,
            in_recall_window=session_id in window,
        )
        if truncated:
            truncated_chat_ids.append(session_id)
        records.extend({**record.to_dict(), "chat_id": session_id} for record in session_records)

    withheld = len(session_ids) - len(owned)
    if withheld:
        logger.warning(
            "memory.export_sessions_withheld user_hash=%s sessions=%d",
            fingerprint_id(user_id),
            withheld,
        )
    return records, truncated_chat_ids


def _message_page(chat_id: str, token: Any) -> Awaitable[tuple[list[Message], Any]]:
    """One page of a chat's messages, oldest first — a :func:`_paginate` fetcher.

    Module-level (rather than a closure inside the chat loop) so ``chat_id``
    binds at ``partial`` time and mypy can see the signature.
    """
    return asyncio.to_thread(
        storage.list_messages, chat_id, limit=_EXPORT_MESSAGE_PAGE_SIZE, cursor=token
    )


async def _export_chats(user_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """The caller's chats with their messages → ``(chats, summaries, message_count)``.

    ``include_archived=True``: an archived chat is still the user's data,
    and the export carries the flag so the distinction survives rather than
    the rows vanishing. Newest chat first, matching ``list_chats_for_user``
    and the sidebar.

    Head summaries (#245) are collected in the same pass because they are
    per-chat point reads. They belong in the export at all because they are
    injected into their chat's system prompt on *every* turn, which makes
    them more load-bearing than most AgentCore records.
    """
    chats = await _paginate(
        lambda tok: asyncio.to_thread(
            storage.list_chats_for_user,
            user_id,
            limit=_EXPORT_CHAT_PAGE_SIZE,
            cursor=tok,
            include_archived=True,
        ),
        what="chats",
    )

    exported: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    message_count = 0
    for chat in chats:
        # ``partial`` rather than a closure over ``chat``: the fetcher is
        # called from inside ``_paginate`` while the loop variable is still
        # live, so late binding would be correct today and wrong the first
        # time this loop changes shape (ruff's B023 says the same).
        messages = await _paginate(partial(_message_page, chat.chat_id), what="messages")
        message_count += len(messages)
        exported.append(
            {
                "chat_id": chat.chat_id,
                "title": chat.title,
                "created_at": chat.created_at,
                "archived": chat.archived,
                "messages": [
                    {"role": m.role.value, "text": m.text, "created_at": m.created_at}
                    for m in messages
                ],
            }
        )
        summary = await asyncio.to_thread(storage.get_chat_summary, chat.chat_id)
        if summary is not None:
            summaries.append(
                {
                    "chat_id": summary.chat_id,
                    "text": summary.text,
                    "covers_through": summary.covers_through,
                    "updated_at": summary.updated_at,
                }
            )
    return exported, summaries, message_count


async def _build_export(user_id: str) -> tuple[dict[str, Any], str]:
    """Assemble the export document → ``(payload, export_date)``.

    Separate from the route so the route is only the metric wrapper and the
    download headers, and so every read *and* the audit write sit inside one
    try/except that can count a failure exactly once.
    """
    actor_id = derive_actor_id(user_id)
    client = _agentcore_client()
    memory_id = await asyncio.to_thread(_memory_id_for_env)

    # Identical short-circuit to ``/records``: a disabled hook injects
    # nothing, so no record can honestly be flagged ``used_in_recall`` and
    # the extra ListSessions is wasted. The manifest reports the same
    # ``enabled`` flag, so the file can never contradict itself.
    recall_enabled = _recall_enabled()
    window: set[str] = (
        await recall_window_session_ids(client, memory_id=memory_id, actor_id=actor_id)
        if recall_enabled
        else set()
    )

    records, truncated_chat_ids = await _export_memory_records(
        client,
        memory_id=memory_id,
        actor_id=actor_id,
        user_id=user_id,
        window=window,
    )
    chats, summaries, message_count = await _export_chats(user_id)

    now = datetime.now(timezone.utc)
    payload = {
        "manifest": {
            "exported_at": now.isoformat(),
            "actor_id": actor_id,
            "schema_version": _EXPORT_SCHEMA_VERSION,
            "recall_window": recall_window(enabled=recall_enabled),
            "truncated_chat_ids": truncated_chat_ids,
            "note": _EXPORT_NOTE,
        },
        "memory_records": records,
        "chat_summaries": summaries,
        "chats": chats,
    }

    # Counts only — never record text. An audit row is a compliance trail,
    # not a second copy of the data it is recording, and this trail has a
    # 365-day TTL against content that a delete (#478) is meant to remove.
    await asyncio.to_thread(
        storage.put_audit_event,
        event_type="memory.exported",
        actor_id=user_id,
        details={
            "memory_records": len(records),
            "chat_summaries": len(summaries),
            "chats": len(chats),
            "messages": message_count,
        },
    )
    return payload, now.strftime("%Y-%m-%d")


@router.get(
    "/export",
    responses={200: {"description": "Full account data export as a JSON download"}},
)
async def export_account_data(
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> JSONResponse:
    """Download everything Channel holds for the caller.

    No actor / user parameter, by the same rule as ``/records``: scope comes
    from the token claim ("agents swap tokens to switch context"), so there
    is nothing to tamper with.

    ``Content-Disposition: attachment`` so a browser saves the file instead
    of rendering it — which also keeps this attacker-influenced text out of
    a top-level document context, reinforced by ``nosniff``.
    """
    try:
        payload, export_date = await _build_export(claims["sub"])
    except Exception:
        # Anything that reaches the client as a 5xx counts as a failed
        # export, the audit write included — see the module docstring on
        # why that one is allowed to fail the request.
        await record_memory_export_outcome(success=False)
        raise
    await record_memory_export_outcome(success=True)
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f'attachment; filename="channel-export-{export_date}.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )
