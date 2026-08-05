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
repo. It also answers "is this everything?" outright: ``complete`` is false
whenever any walk stopped short, and ``truncated_chat_ids`` names the chats
whose AgentCore history exceeded ``MAX_EVENTS_PER_SESSION`` so their oldest
records are absent. Both live in the *file* rather than only in a log line,
because a log line does not travel with a download — same principle as
``records_truncated`` on ``/records``: a partial export presented as
complete is worse than a partial export that says so.

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

## Forget — ``DELETE /api/memory/records[/{record_id}]`` (#477)

The destructive half of the epic, in four forms: one record, one chat
(``?chat_id=``), everything since a timestamp (``?since=``), and everything
(``?all=true``). Exactly one filter may be supplied; **no filter is a 400,
never a full wipe**, so the most destructive action can only be reached by
asking for it by name.

**Blast radius is the AgentCore event only** (epic #129 decision 9). The
DynamoDB chat message is untouched and stays visible in the chat, as does
the ``SK=SUMMARY`` head summary. A click in a settings panel must not
silently punch a hole in visible chat history — it would break user /
assistant turn pairing and strand #245's ``covers_through`` pointer.
Deleting the chat remains the total-erasure path.

Both scoping gates from §Scoping apply, and on a *destructive* surface the
second one is doing the real work: every session is resolved through
``resolve_owned_chats`` (bulk) or ``_load_owned_chat`` (per-record and
``?chat_id=``) against the **raw** JWT sub before a single ``DeleteEvent``
is issued, so a foreign session in the partition is skipped rather than
erased. A record whose chat the caller does not own is a **404, not a
403** — the same shape the rest of the chat surface uses, so a probe cannot
distinguish "someone else owns this" from "this never existed".

Three properties are load-bearing enough to state outright:

- **The event walk is drained, not capped.** ``/records`` caps at
  ``MAX_EVENTS_PER_SESSION`` and *says so* via ``records_truncated``; a
  forget cannot make that trade, because the user is told the action
  completed and has nothing to read. See ``list_forgettable_event_ids``.
- **Bulk deletes are partial-failure tolerant** — one refused
  ``DeleteEvent`` does not abandon the batch, since stopping early forgets
  *less*. The response carries ``{"deleted", "failed", "complete"}``;
  ``complete`` is false only if a walk hit its page ceiling. A batch where
  **nothing** was deleted and something failed is a 5xx rather than a
  cheerful ``{"deleted": 0}``: telling a user their data is gone when it is
  not is the one error this endpoint must never make, and it is exactly
  what an over-broad handler produces.
- **The audit write is best-effort**, the *opposite* call from
  ``/export`` above and the same one ``sessions._audit_revocation``
  makes — by the time it runs the records are already gone, so failing the
  response would report a forget that did not happen when it did.
  ``details`` carries counts, filters and opaque identifiers only; copying
  the text a user just asked to forget into a 365-day immutable audit
  partition would defeat the feature.

## A caller with no memory partition (#527)

Applies to all three endpoints. An AgentCore actor partition is created lazily
by the first memory *write*, so a user who has signed in but never had a
chat turn has none, and every AgentCore read against them raises
``ResourceNotFoundException``. Both endpoints answer that as **empty**,
because it is: no partition means no records. The decision is made once, in
``memory_records._agentcore_read``, so ``/records`` and ``/export`` cannot
drift apart on it.

Until #527 it was a **500 on both**, for exactly the users most likely to
be asking what Channel knows about them — someone who has just signed up
and gone straight to the Privacy page's export link.

The narrowness is the point, and it is a data-integrity property rather
than a style preference: only that one error code is absorbed, and every
other AgentCore failure still surfaces as a 5xx. An export that reported
"you have no memories" because AgentCore was throttling or refusing would
be a claim about the user's own data that this module has no basis to
make — and worse than the 500 it replaced, because a 500 is legible as a
failure while a confident empty file is not. Same reasoning as
``records_truncated`` and ``manifest.complete``: never present an unknown
or partial result as a complete one.

``manifest.complete`` therefore stays true for a partition-less user. It
answers "did I get everything there is?", not "is there anything?" — the
walks did finish, and there genuinely was nothing to walk.

For the forget surface the same condition is a **no-op success**: a bulk
form answers ``{"deleted": 0, "failed": 0, "complete": true}``, and the
per-record form answers 404, because an id that addresses nothing is
exactly what "unknown id" means. Neither is a 500, and neither is reached
by absorbing anything wider than the one error code.
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
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from channel import storage
from channel.agents.memory import derive_actor_id, get_or_create_memory
from channel.agents.memory_records import (
    count_session_records,
    decode_record_id,
    delete_session_event,
    group_created_at,
    list_forgettable_event_ids,
    list_session_records,
    list_sessions_page,
    recall_window,
    recall_window_session_ids,
    resolve_owned_chats,
)
from channel.api._auth import require_mgmt_user
from channel.api.chats import _load_owned_chat
from channel.logging_config import fingerprint_id
from channel.metrics import (
    record_memory_bulk_forget_outcome,
    record_memory_export_outcome,
    record_memory_record_delete_outcome,
)
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
) -> tuple[list[_T], bool]:
    """Drain a ``(page, next_token)`` paginator → ``(items, drained)``.

    One helper for all three walks (AgentCore sessions, DynamoDB chats,
    DynamoDB messages) because they differ only in their fetch closure, and
    a single drain is a single place for the page ceiling to live. ``fetch``
    takes the previous token (``None`` first) and returns the next page
    with the token after it.

    Falling out of the loop means the ceiling was reached with a token still
    outstanding, so ``drained`` is false. It is **returned**, not just
    logged: a log line does not travel with a downloaded file, and an
    incomplete export that renders as a complete one is precisely the silent
    lie this surface exists to stop. The caller propagates it to
    ``manifest.complete``.
    """
    items: list[_T] = []
    token: Any = None
    for _ in range(_EXPORT_MAX_PAGES):
        page, token = await fetch(token)
        items.extend(page)
        if not token:
            return items, True
    logger.warning("memory.export_page_cap_hit what=%s pages=%d", what, _EXPORT_MAX_PAGES)
    return items, False


async def _export_memory_records(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    user_id: str,
    window: set[str],
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Owned memory records → ``(records, truncated, drained)``.

    The same enumeration ``/records`` serves, drained across every
    ``ListSessions`` page instead of one, and flattened: the export is a
    file, so each record carries its own ``chat_id`` rather than being
    nested under a group. ``truncated`` lists the sessions whose history
    exceeded ``MAX_EVENTS_PER_SESSION``; ``drained`` is false if the session
    walk hit the page ceiling.

    The ownership gate is the same one §Scoping describes, and it matters
    more here than on ``/records``: this response is a downloadable file, so
    a foreign session leaking into it leaks into wherever that file ends up.
    Withheld sessions are logged (the standing partition canary) but not
    counted per-record — that would cost one ``ListEvents`` per foreign
    session to enrich an alarm that fires on ``> 0`` either way.
    """
    session_summaries, drained = await _paginate(
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
    return records, truncated_chat_ids, drained


def _message_page(chat_id: str, token: Any) -> Awaitable[tuple[list[Message], Any]]:
    """One page of a chat's messages, oldest first — a :func:`_paginate` fetcher.

    Module-level (rather than a closure inside the chat loop) so ``chat_id``
    binds at ``partial`` time and mypy can see the signature.
    """
    return asyncio.to_thread(
        storage.list_messages, chat_id, limit=_EXPORT_MESSAGE_PAGE_SIZE, cursor=token
    )


async def _export_chats(
    user_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, bool]:
    """Chats + messages → ``(chats, summaries, message_count, drained)``.

    ``include_archived=True``: an archived chat is still the user's data,
    and the export carries the flag so the distinction survives rather than
    the rows vanishing. Newest chat first, matching ``list_chats_for_user``
    and the sidebar.

    Head summaries (#245) are collected in the same pass because they are
    per-chat point reads. They belong in the export at all because they are
    injected into their chat's system prompt on *every* turn, which makes
    them more load-bearing than most AgentCore records.

    ``drained`` is the AND over the chat walk and every per-chat message
    walk — one incomplete walk anywhere makes the whole export incomplete,
    which is what ``manifest.complete`` has to answer.
    """
    chats, drained = await _paginate(
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
        messages, messages_drained = await _paginate(
            partial(_message_page, chat.chat_id), what="messages"
        )
        drained = drained and messages_drained
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
    return exported, summaries, message_count, drained


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

    records, truncated_chat_ids, records_drained = await _export_memory_records(
        client,
        memory_id=memory_id,
        actor_id=actor_id,
        user_id=user_id,
        window=window,
    )
    chats, summaries, message_count, chats_drained = await _export_chats(user_id)

    now = datetime.now(timezone.utc)
    payload = {
        "manifest": {
            "exported_at": now.isoformat(),
            "actor_id": actor_id,
            "schema_version": _EXPORT_SCHEMA_VERSION,
            "recall_window": recall_window(enabled=recall_enabled),
            # The single answer to "is this everything?", because a log line
            # does not travel with a downloaded file. False when a walk hit
            # the page ceiling OR a chat's memory history exceeded the
            # per-session cap; ``truncated_chat_ids`` says which chats, and
            # the page-cap case additionally logs ``export_page_cap_hit``.
            "complete": records_drained and chats_drained and not truncated_chat_ids,
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

    ``no-store`` for the same reason ``/api/me/prefs`` and
    ``/api/me/sessions`` set it, with rather more at stake: without it
    Chromium (browser and Electron renderer alike) heuristic-caches the
    response, leaving a whole account's data — every chat message the user
    has ever sent — sitting in an on-disk HTTP cache.
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
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ── Forget (#477) ─────────────────────────────────────────────────────────

#: Audit event types — epic #129 decision 3. Three rather than four,
#: because ``?chat_id=`` and ``?since=`` are the same operation under
#: different filters and the filter itself is in ``details``; wiping
#: everything is genuinely a different act and reads as one in the trail.
_AUDIT_RECORD_DELETED = "memory.record_deleted"
_AUDIT_BULK_FORGET = "memory.bulk_forget"
_AUDIT_FORGET_ALL = "memory.forget_all"

#: ``ListSessions`` page size for the bulk walks. Matches the export's:
#: nobody reads a forget incrementally, so the only thing page size buys
#: is fewer round trips.
_FORGET_SESSION_PAGE_SIZE = 50


async def _write_forget_audit(event_type: str, user_id: str, details: dict[str, Any]) -> None:
    """Best-effort audit row for one forget — counts and filters only.

    **Best-effort, unlike ``/export``'s.** By the time this runs the records
    are already gone, so failing the response would tell the user their
    forget did not happen when it did — and a user who believes their data
    survived behaves differently from one who knows it is gone. Losing an
    audit row degrades forensics; failing the response degrades the truth.
    The same call ``sessions._audit_revocation`` makes, for the same reason.

    ``details`` carries counts, filters, and opaque identifiers
    (``chat_id`` / ``record_id``) — **never record text**. The audit
    partition has a 365-day TTL, so copying in the very text a user asked to
    forget would quietly outlive the deletion it records.
    """
    try:
        await asyncio.to_thread(
            storage.put_audit_event,
            event_type=event_type,
            actor_id=user_id,
            details=details,
        )
    except Exception:
        logger.exception("%s audit write failed", event_type)


def _parse_since(value: str) -> datetime:
    """``?since=`` → an aware UTC datetime. Unparseable is a 400.

    ``Z`` is rewritten because Python 3.10's ``fromisoformat`` rejects it
    while ``Date.prototype.toISOString`` — what any browser client will
    send — emits nothing else. A naive timestamp is read as UTC, matching
    ``memory_records._event_at_or_after``, so the two ends of the comparison
    can't disagree about what an unqualified time means.

    Rejecting rather than defaulting is the point (epic #129 decision 9's
    sibling rule): a ``since`` this endpoint could not understand must never
    quietly widen into "everything".
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid since timestamp") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


async def _owned_forget_session_ids(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    user_id: str,
) -> tuple[list[str], bool]:
    """Sessions the caller owns, across every ``ListSessions`` page.

    The ownership gate from §Scoping, applied before anything is deleted:
    a session whose chat row this caller does not own is dropped, never
    erased. On a read that gate withholds data; here it is the difference
    between forgetting your own memory and forgetting someone else's, so it
    runs even though the partition is already actor-scoped — the endpoint
    must not inherit the next ``derive_actor_id`` bug the way #474 would
    have handed one user another's memory.

    Drained rather than paged, and the drain flag is returned: a "forget
    everything" that stopped at page one would be the silent lie this
    module exists to stop.
    """
    session_summaries, drained = await _paginate(
        lambda tok: list_sessions_page(
            client,
            memory_id=memory_id,
            actor_id=actor_id,
            limit=_FORGET_SESSION_PAGE_SIZE,
            next_token=tok,
        ),
        what="forget_sessions",
    )
    session_ids = list(
        dict.fromkeys(s["sessionId"] for s in session_summaries if s.get("sessionId"))
    )
    owned = await asyncio.to_thread(resolve_owned_chats, session_ids, user_id=user_id)
    withheld = len(session_ids) - len(owned)
    if withheld:
        # The same partition canary ``/records`` and ``/export`` log, and
        # the one place it means "we declined to delete these".
        logger.warning(
            "memory.forget_sessions_withheld user_hash=%s sessions=%d",
            fingerprint_id(user_id),
            withheld,
        )
    return [sid for sid in session_ids if sid in owned], drained


async def _forget_sessions(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    session_ids: list[str],
    since: datetime | None,
) -> tuple[int, int, bool]:
    """Delete every selected event across ``session_ids`` → ``(deleted, failed, complete)``.

    Partial-failure tolerant by design: a refused ``DeleteEvent`` is counted
    and the batch continues, because abandoning it would forget *less* than
    the user asked for — the wrong direction to fail in. An event AgentCore
    reports as already absent counts as neither, since nothing was deleted
    and nothing broke; the end state the caller wanted already holds.

    **The per-record catch is ``ClientError``, not ``Exception``.** It is
    narrow deliberately: the batch tolerates a vendor refusal, but a
    ``TypeError`` or an ``AttributeError`` is a bug in this code and must
    reach the client as a 500 rather than being silently folded into a
    ``failed`` count that reads like an AWS blip. The ``ListEvents`` walk is
    outside the catch for the same reason — a listing that fails means the
    selection is unknown, and deleting an unknown selection is not something
    to paper over.

    ``complete`` is the AND over every session's walk: false only when a
    walk hit its page ceiling with a token outstanding.
    """
    deleted = 0
    failed = 0
    complete = True
    for session_id in session_ids:
        event_ids, drained = await list_forgettable_event_ids(
            client,
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=session_id,
            since=since,
        )
        complete = complete and drained
        for event_id in event_ids:
            try:
                if await delete_session_event(
                    client,
                    memory_id=memory_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    event_id=event_id,
                ):
                    deleted += 1
            except ClientError as exc:
                failed += 1
                # ``chat_id`` is fingerprinted for the same reason the
                # asset wipe fingerprints it; the event id is omitted
                # entirely — it addresses one record's content.
                logger.warning(
                    "memory.forget_event_failed chat_id_hash=%s",
                    fingerprint_id(session_id),
                    extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                )
    return deleted, failed, complete


@router.delete(
    "/records/{record_id}",
    status_code=204,
    responses={
        400: {"description": "Malformed record_id"},
        404: {"description": "Unknown record, or one whose chat the caller doesn't own"},
    },
)
async def forget_memory_record(
    record_id: str,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Forget one memory record.

    The ``record_id`` decodes to ``(session_id, event_id, payload_index)``
    and the index is deliberately ignored: ``DeleteEvent`` is AgentCore's
    only granularity, so forgetting one half of a user/assistant turn pair
    is not an operation the vendor offers (see ``encode_record_id``). The
    id is opaque, so a caller cannot tell the difference; what they asked to
    forget is forgotten, plus its pair.

    **Ownership is checked before anything is deleted**, via the same
    ``_load_owned_chat`` the chat surface uses — ``sessionId`` *is* a
    ``chat_id``. A record belonging to another user is therefore
    indistinguishable from one that never existed: both 404. A record in an
    *orphaned* AgentCore session (one whose chat row is gone, which a failed
    ``chats._wipe_agentcore_session`` can leave behind) also 404s, matching
    ``resolve_owned_chats``, which withholds those from every read — there
    is nothing in the panel to press.

    204 rather than a body: this is a single-resource delete, the shape
    ``DELETE /api/chats/{chat_id}`` and ``DELETE /api/me/sessions/{id}``
    already use. The bulk form returns counts because there is something to
    count.
    """
    user_id = claims["sub"]
    try:
        session_id, event_id, _payload_index = decode_record_id(record_id)
    except ValueError as exc:
        # Never forward a malformed id to AgentCore — it would come back as
        # a ValidationException, i.e. a 500 for what is a client error.
        raise HTTPException(status_code=400, detail="malformed record_id") from exc

    await _load_owned_chat(session_id, user_id)

    actor_id = derive_actor_id(user_id)
    client = _agentcore_client()
    memory_id = await asyncio.to_thread(_memory_id_for_env)
    try:
        deleted = await delete_session_event(
            client,
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=session_id,
            event_id=event_id,
        )
    except Exception:
        await record_memory_record_delete_outcome(success=False)
        raise

    if not deleted:
        # Unknown id — the event is gone, or the actor partition was never
        # created (#527: it is created lazily by the first memory *write*).
        # Neither is an operational failure, so neither counter fires; a 4xx
        # is a statement about the request, and counting it would put a
        # floor under the failure rate.
        raise HTTPException(status_code=404, detail="Record not found")

    await record_memory_record_delete_outcome(success=True)
    await _write_forget_audit(
        _AUDIT_RECORD_DELETED,
        user_id,
        {"chat_id": session_id, "record_id": record_id, "deleted": 1},
    )
    return Response(status_code=204)


@router.delete(
    "/records",
    responses={
        400: {"description": "No filter, more than one filter, or an unparseable since"},
        404: {"description": "chat_id names a chat the caller doesn't own"},
        500: {"description": "Nothing could be forgotten and something failed"},
    },
)
async def forget_memory_records(
    chat_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
    forget_all: bool = Query(default=False, alias="all"),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Forget a chat's memory, everything since a timestamp, or everything.

    **Exactly one of ``chat_id`` / ``since`` / ``all=true``.** No filter is
    a 400, never a full wipe: the most destructive action this API offers
    must be reachable only by naming it, so a client that drops a query
    param mid-request cannot escalate into "forget everything". More than
    one filter is also a 400 rather than an intersection or a union —
    guessing which the caller meant is not a guess to make here.

    ``all=false`` alone is no filter at all, so it 400s like the bare form.
    That falls out of the same rule rather than being a special case: the
    filter is *``all=true``*, and anything else is its absence.

    Returns ``{"deleted", "failed", "complete"}``. ``complete`` is false
    only when an event walk hit its page ceiling, so some records were never
    even considered — reported in the body rather than only in a log line,
    because a log line is not visible to the person who just asked for their
    data to be gone. A batch that deleted nothing while something failed is
    a 5xx instead: ``{"deleted": 0}`` with a 200 reads as "there was nothing
    to forget", which during an AgentCore outage is a claim about the user's
    own data that this endpoint has no basis to make.
    """
    user_id = claims["sub"]
    filters = [chat_id is not None, since is not None, forget_all]
    if sum(filters) != 1:
        raise HTTPException(
            status_code=400,
            detail="supply exactly one of chat_id, since, all=true",
        )
    cutoff = _parse_since(since) if since is not None else None

    actor_id = derive_actor_id(user_id)
    client = _agentcore_client()
    memory_id = await asyncio.to_thread(_memory_id_for_env)

    try:
        if chat_id is not None:
            # Ownership first, and by the same 404-not-403 gate the rest of
            # the chat surface uses.
            await _load_owned_chat(chat_id, user_id)
            session_ids, sessions_drained = [chat_id], True
            event_type, details = _AUDIT_BULK_FORGET, {"filter": "chat_id", "chat_id": chat_id}
        else:
            session_ids, sessions_drained = await _owned_forget_session_ids(
                client, memory_id=memory_id, actor_id=actor_id, user_id=user_id
            )
            event_type, details = (
                (_AUDIT_BULK_FORGET, {"filter": "since", "since": since})
                if since is not None
                else (_AUDIT_FORGET_ALL, {"filter": "all"})
            )

        deleted, failed, events_drained = await _forget_sessions(
            client,
            memory_id=memory_id,
            actor_id=actor_id,
            session_ids=session_ids,
            since=cutoff,
        )
    except HTTPException:
        # A 4xx decided here (an unowned ``chat_id``) is a statement about
        # the request, not a forget that failed — same reasoning as the
        # per-record 404 above, so no counter fires.
        raise
    except Exception:
        await record_memory_bulk_forget_outcome(success=False)
        raise

    complete = sessions_drained and events_drained
    await record_memory_bulk_forget_outcome(success=(failed == 0 and complete))
    await _write_forget_audit(
        event_type, user_id, {**details, "deleted": deleted, "failed": failed, "complete": complete}
    )
    if deleted == 0 and failed:
        raise HTTPException(status_code=500, detail="no records could be forgotten")
    return {"deleted": deleted, "failed": failed, "complete": complete}
