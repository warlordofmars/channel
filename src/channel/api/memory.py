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
- ``recall_window`` — the live window, read from the constants that define
  it rather than re-literalled. Showing stored records alone implies
  Channel uses far more than it does; showing only the recalled slice
  implies it has forgotten far more than it has. Both are silent lies in
  opposite directions (#227), so the response carries both and every record
  carries ``used_in_recall``. When ``recall_window.enabled`` is false the
  hook injects nothing, so every ``used_in_recall`` is false too — the flag
  and the envelope can never contradict each other.

  Since #274 the window is the recall hook's *candidate pool* and
  ``ordering`` reads ``"relevance"``: which of the pool reaches a prompt is
  decided per turn against what the user just said, so ``used_in_recall``
  means "reachable by recall", not "in every prompt". The pool is the
  honest fixed point — outside it a record can never be recalled — and it
  is a question the panel can answer without knowing what the user will
  type next.
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
whenever any walk stopped short, ``truncated_chat_ids`` names the chats
whose AgentCore history exceeded ``MAX_EVENTS_PER_SESSION`` so their oldest
records are absent, and ``withheld_record_count`` says how much the
ownership gate declined to export (§Withheld). All three live in the *file*
rather than only in a log line, because a log line does not travel with a
download — same principle as ``records_truncated`` on ``/records``: a
partial export presented as complete is worse than a partial export that
says so.

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
  *less*. The response carries
  ``{"deleted", "failed", "complete", "withheld_record_count"}``;
  ``complete`` is false only if a walk hit its page ceiling, and the
  withheld count is what the ownership gate declined to delete
  (§Withheld). A batch where
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

## Correct a note — ``PATCH /api/memory/records/{record_id}`` (#478)

The verb between keeping and forgetting. When ``remember`` stored something
that has since gone stale — "prefers Postgres" after the user moved to
DynamoDB — forgetting it loses the true half along with the false half.

**Replace, not update** (epic #129 decision 8). AgentCore has no
``UpdateEvent``, so this is ``DeleteEvent`` + ``CreateEvent``, which has
three consequences the contract has to carry rather than hide:

- **The replacement re-uses the original ``eventTimestamp``**, so a
  corrected note keeps its place in chronological order instead of jumping
  to the top of the recall window. Correcting a fact must not also promote
  it.
- **A new event means a new ``record_id``**, which is why this route
  returns the updated record rather than 204. The old id addresses an
  event that no longer exists; a client that keeps using it gets a 404 on
  its next edit or forget.
- **The whole payload is re-created, not just the edited entry.**
  ``DeleteEvent`` is event-granular while a record is one *payload entry*
  (see ``memory_records.encode_record_id``), so re-creating only the
  corrected entry would silently drop its siblings.

**Ordering is delete-then-create, and the failure that ordering admits is
reported rather than swallowed.** If the create refuses after the delete
succeeded, the response is a 500 whose body carries ``original_text`` — the
exact bytes that were removed — so the panel can offer them back instead of
the user losing a note by pressing "save". The reverse order would trade
that for a duplicate, which is the harder failure to explain and the harder
one to clean up.

**Only ``kind == "remembered"`` may be rewritten.** ``conversation`` and
``meta`` are a 409 with a plain-language reason. Rewriting a conversation
turn falsifies the transcript — the user said what they said, and that is a
forget-or-keep decision, not an edit — and a ``meta`` fact is telemetry
about a tool call that either happened or did not. #475's read model
already exposes this as ``editable`` so the panel never offers the
affordance; the 409 is the server-side backstop, not the primary control.

Empty or whitespace-only ``content`` is a **400, never a delete**. "Save an
empty note" is far more likely to be a slipped keystroke than a request to
erase, and ``DELETE`` is one call away for the case where it isn't.

The stored text is re-formatted through
``memory_tools._format_remember_text``, the same function the ``remember``
tool writes with, so a corrected note is byte-shaped exactly like an
originally-written one — which is what keeps it classifiable by #475's read
model and its tags findable by the ``recall`` tool's inline-tag matching.

## Withheld records — reported on every surface (#552)

All three endpoints drop sessions the ownership gate cannot verify, and all
three say so, in **one unit under one name**: ``withheld_record_count``, a
lower bound on the records that gate declined to touch. ``/records`` has
carried it since #475; #552 added it to ``manifest`` on ``/export`` and to
the bulk forget response, computed once in :func:`_withheld_record_count`.

**``complete`` deliberately did NOT widen to cover it.** Before #552 both
endpoints reported only ``complete`` — true whenever no walk was
truncated — so an export could claim completeness while omitting records,
and a forget could report finished while something remained. Folding
withholding into ``complete`` would have been honest about the word and
useless in practice: "we hit a page ceiling" is a **retry**, "we could not
verify ownership of these sessions" is an **investigation** (most likely an
orphaned AgentCore session left behind by a best-effort
``chats._wipe_agentcore_session``), and one flag saying something is wrong
without saying which is un-actionable. Two fields, two conditions, and a
caller reads both to answer "did I get everything?".

**The count is a lower bound twice over**, and deliberately so:
``count_session_records`` stops at ``MAX_EVENTS_PER_SESSION`` *within* a
session, and :data:`_MAX_WITHHELD_SESSIONS_COUNTED` bounds how many withheld
sessions are counted *at all* — without which the drained ``/export`` and
forget walks would spend a whole Lambda timeout enumerating records they are
never going to return. Alert on ``> 0``, never on the magnitude; a capped
request says so with its own ``memory.withheld_count_capped`` canary.

**What gets withheld did not change, and must not.** Surfacing an
unverifiable session would show a user data they already asked to delete —
see §Scoping. This is a reporting change only. For the same reason the
count is a **count**: naming the unverifiable session ids would leak the
existence of rows the ownership check could not confirm, which is precisely
the conservatism the withholding exists to preserve.

## A caller with no memory partition (#527)

Applies to every endpoint here. An AgentCore actor partition is created lazily
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

``manifest.withheld_record_count`` reads 0 for the same reason, and it is
not merely defaulted there: no partition means no sessions, so nothing
reaches the ownership gate to be withheld.

For the forget surface the same condition is a **no-op success**: a bulk
form answers
``{"deleted": 0, "failed": 0, "complete": true, "withheld_record_count": 0}``,
and the
per-record form answers 404, because an id that addresses nothing is
exactly what "unknown id" means. Neither is a 500, and neither is reached
by absorbing anything wider than the one error code.

``PATCH`` answers 404 for the same reason and by the same route — its
``GetEvent`` read goes through ``memory_records._agentcore_read``, so an
absent partition reads as an absent record. Its ``CreateEvent`` is
deliberately *outside* that absorption (``create_session_event``): a write
to a partition that does not exist creates it, so there is nothing to
absorb, and a swallowed create is precisely the "we saved your correction"
lie this module is built to refuse.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
from collections.abc import Awaitable, Callable, Container
from datetime import datetime, timezone
from functools import partial
from typing import Any, TypeVar

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from channel import storage
from channel.agents.memory import derive_actor_id, get_or_create_memory
from channel.agents.memory_records import (
    KIND_REMEMBERED,
    MemoryRecord,
    classify_kind,
    count_session_records,
    create_session_event,
    decode_record_id,
    delete_session_event,
    encode_record_id,
    get_session_event,
    group_created_at,
    list_forgettable_event_ids,
    list_session_records,
    list_sessions_page,
    recall_window,
    recall_window_session_ids,
    resolve_owned_chats,
)
from channel.agents.tools.memory_tools import _format_remember_text
from channel.api._auth import require_mgmt_user
from channel.api.chats import _load_owned_chat
from channel.logging_config import fingerprint_id
from channel.metrics import (
    record_memory_bulk_forget_outcome,
    record_memory_export_outcome,
    record_memory_record_delete_outcome,
    record_memory_record_edit_outcome,
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


#: Ceiling on how many withheld sessions are actually *counted* per request.
#: Counting costs one ``ListEvents`` per withheld session, and the walks
#: feeding ``/export`` and the bulk forget are **drained** rather than paged
#: (``_EXPORT_MAX_PAGES`` × page size ≈ 10 000 sessions), so a badly collided
#: or corrupted partition would spend the whole Lambda timeout counting
#: records it is not going to return anyway — turning a reporting field into
#: an outage on the two endpoints that must not have one.
#:
#: Capping is *free* precisely because the number was never exact: it is
#: already documented as "at least this many" (``count_session_records``
#: stops at ``MAX_EVENTS_PER_SESSION`` within a single session for the same
#: reason). Past the cap the answer to "was anything withheld?" is unchanged
#: and only the magnitude degrades, which is the figure callers are told not
#: to alert on. Set far above any plausible anomaly — post-#485 the expected
#: value is 0 — and hitting it gets its own canary, since "we withheld more
#: than we were willing to count" is a different event from "we withheld".
_MAX_WITHHELD_SESSIONS_COUNTED = 100


async def _withheld_record_count(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    session_ids: list[str],
    owned: Container[str],
    user_id: str,
    event: str,
) -> int:
    """Records withheld by the ownership gate → the ``withheld_record_count``
    every one of these endpoints reports, plus the partition-canary log line.

    **One definition for all three surfaces** (#552). ``/records``,
    ``/export`` and the bulk forget each drop sessions they cannot verify,
    and each has to say so; computing that in three places is how the field
    would come to mean three things — the precise failure this function
    exists to make impossible, and a smaller version of the one #552 fixed
    (``complete`` meaning "no walk was truncated" while a caller read it as
    "you got everything").

    The unit is **records, not sessions**, because that is the unit
    ``/records`` already reports under this name and a caller comparing the
    panel against a download must not have to know they are different
    quantities. Same lower-bound caveat, for the same reason:
    :func:`count_session_records` counts only the first
    ``MAX_EVENTS_PER_SESSION`` events, so a very large withheld session
    undercounts. Alert on ``> 0``, never on the magnitude — paging deeper
    through memory this caller could not be shown to own would be the
    opposite of what withholding is for.

    **It counts every record in a withheld session**, never a filtered
    subset — there is no ``since`` parameter here even though the bulk
    forget has one. Narrowing the count to a request's filter would mean
    reading the timestamps of a session the ownership check could not
    confirm belongs to this caller, in order to report a *smaller* number;
    over-reporting is the safe direction for an alarm. See
    ``forget_memory_records`` for what that means to a caller.

    ``event`` names the canary; the three names stay distinct because
    "we declined to show these", "we declined to export these" and "we
    declined to delete these" are different operations, and one shared log
    event covering all three is what makes a canary useless (the same
    lesson ``_paginate``'s ``what`` records).

    Costs one ``ListEvents`` per **withheld** session and nothing at all in
    the normal case, where there are none. ``/export`` previously declined
    to pay it, on the grounds that an alarm firing at ``> 0`` learns nothing
    from the magnitude. That reasoning held while the count was only an
    alarm; it does not once the number is part of a completeness claim made
    to the user in the response body — but it is why the fan-out is bounded
    by :data:`_MAX_WITHHELD_SESSIONS_COUNTED` rather than left to follow a
    drained walk. Sessions past the cap are still *detected* (they count
    toward the canary's ``sessions=``, which is free set arithmetic); only
    their records go uncounted.
    """
    withheld_sessions = 0
    withheld_records = 0
    counted = 0
    for session_id in session_ids:
        if session_id in owned:
            continue
        withheld_sessions += 1
        if counted >= _MAX_WITHHELD_SESSIONS_COUNTED:
            # Keep walking — the session tally costs nothing and is what the
            # canary reports — but stop paying an API call per session.
            continue
        counted += 1
        withheld_records += await count_session_records(
            client, memory_id=memory_id, actor_id=actor_id, session_id=session_id
        )
    if withheld_sessions:
        # The standing partition canary. A non-zero count means this actor
        # partition holds a session whose chat this caller does not own — an
        # actor-id collision (#474, repaired by #485) or an orphan left by a
        # failed chat-delete wipe. Either way it is worth an alert; the raw
        # sub is an email, so it is fingerprinted.
        logger.warning(
            event + " user_hash=%s sessions=%d records=%d",
            fingerprint_id(user_id),
            withheld_sessions,
            withheld_records,
        )
    if withheld_sessions > _MAX_WITHHELD_SESSIONS_COUNTED:
        # A distinct event, not a field on the one above: "we withheld more
        # than we were willing to count" means the returned magnitude is
        # truncated, and an operator reading the number needs to know that
        # from the log rather than inferring it from the cap constant.
        logger.warning(
            "memory.withheld_count_capped user_hash=%s sessions=%d counted=%d",
            fingerprint_id(user_id),
            withheld_sessions,
            counted,
        )
    return withheld_records


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

    withheld_records = await _withheld_record_count(
        client,
        memory_id=memory_id,
        actor_id=actor_id,
        session_ids=session_ids,
        owned=owned,
        user_id=user_id,
        event="memory.records_withheld",
    )

    # Newest chat first, matching the sidebar's Recents ordering and the
    # recency order the recall *pool* is drawn in (what the hook then does
    # with that pool is per-turn relevance — see ``recall_window``). Ties
    # break on chat_id so the page order is deterministic.
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

    One helper for every walk — the export's AgentCore sessions, DynamoDB
    chats and DynamoDB messages, plus #477's forget-session walk — because
    they differ only in their fetch closure, and a single drain is a single
    place for the page ceiling to live. ``fetch`` takes the previous token
    (``None`` first) and returns the next page with the token after it.

    ``what`` names the walk in the cap-hit log line, and it is the **only**
    thing that does: the event name is deliberately neutral
    (``memory.page_cap_hit``, not ``memory.export_page_cap_hit``) because a
    shared helper must not label a forget as an export. It was the export
    name until #477 added a fourth, non-export caller — at which point one
    log event silently meant two very different operations, which is
    exactly what makes a canary useless.

    Falling out of the loop means the ceiling was reached with a token still
    outstanding, so ``drained`` is false. It is **returned**, not just
    logged: a log line does not travel with a downloaded file (nor reach the
    person who just asked for their data to be forgotten), and an incomplete
    result that renders as a complete one is precisely the silent lie this
    surface exists to stop. Callers propagate it to ``manifest.complete``
    and to the forget response's ``complete``.
    """
    items: list[_T] = []
    token: Any = None
    for _ in range(_EXPORT_MAX_PAGES):
        page, token = await fetch(token)
        items.extend(page)
        if not token:
            return items, True
    logger.warning("memory.page_cap_hit what=%s pages=%d", what, _EXPORT_MAX_PAGES)
    return items, False


async def _export_memory_records(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    user_id: str,
    window: set[str],
) -> tuple[list[dict[str, Any]], list[str], bool, int]:
    """Owned memory records → ``(records, truncated, drained, withheld)``.

    The same enumeration ``/records`` serves, drained across every
    ``ListSessions`` page instead of one, and flattened: the export is a
    file, so each record carries its own ``chat_id`` rather than being
    nested under a group. ``truncated`` lists the sessions whose history
    exceeded ``MAX_EVENTS_PER_SESSION``; ``drained`` is false if the session
    walk hit the page ceiling.

    The ownership gate is the same one §Scoping describes, and it matters
    more here than on ``/records``: this response is a downloadable file, so
    a foreign session leaking into it leaks into wherever that file ends up.
    ``withheld`` is what that gate cost this export, in records
    (:func:`_withheld_record_count`) — reported in the manifest since #552,
    because an export that omitted records while calling itself complete is
    the same silent lie the rest of this module exists to stop.
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

    withheld = await _withheld_record_count(
        client,
        memory_id=memory_id,
        actor_id=actor_id,
        session_ids=session_ids,
        owned=owned,
        user_id=user_id,
        event="memory.export_sessions_withheld",
    )
    return records, truncated_chat_ids, drained, withheld


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

    records, truncated_chat_ids, records_drained, withheld_records = await _export_memory_records(
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
            # "Did every walk finish?" — narrowly that and nothing more.
            # False when a walk hit the page ceiling OR a chat's memory
            # history exceeded the per-session cap; ``truncated_chat_ids``
            # says which chats, and the page-cap case additionally logs
            # ``memory.page_cap_hit``. In the file rather than only in a log
            # line, because a log line does not travel with a download.
            "complete": records_drained and chats_drained and not truncated_chat_ids,
            "truncated_chat_ids": truncated_chat_ids,
            # The other half of "is this everything?" (#552), and the reason
            # ``complete`` above is safe to keep narrow. Records the
            # ownership gate declined to export, in the same unit and under
            # the same name ``/records`` reports — a **lower bound**, so
            # alert on ``> 0`` rather than on the magnitude. Kept separate
            # from ``complete`` rather than folded into it because the two
            # conditions call for different responses: a truncated walk is a
            # retry, an unverifiable session is an investigation (most
            # likely an orphan left by a best-effort chat-delete wipe), and
            # one flag saying "something is wrong" without saying which is
            # un-actionable.
            "withheld_record_count": withheld_records,
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


async def _write_memory_audit(event_type: str, user_id: str, details: dict[str, Any]) -> None:
    """Best-effort audit row for one memory *mutation* — forget or edit.

    **Best-effort, unlike ``/export``'s.** By the time this runs the write
    has already happened, so failing the response would tell the user their
    forget or correction did not happen when it did — and a user who
    believes their data survived behaves differently from one who knows it
    is gone. Losing an audit row degrades forensics; failing the response
    degrades the truth. The same call ``sessions._audit_revocation`` makes,
    for the same reason.

    ``details`` carries counts, filters, and opaque identifiers
    (``chat_id`` / ``record_id``) — **never record text**, on either verb.
    The audit partition has a 365-day TTL, so copying in the very text a
    user asked to forget would quietly outlive the deletion it records, and
    copying in a corrected note's before-and-after would preserve exactly
    the stale claim the correction exists to retire.
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
) -> tuple[list[str], bool, int]:
    """Sessions the caller owns → ``(session_ids, drained, withheld)``.

    The ownership gate from §Scoping, applied before anything is deleted:
    a session whose chat row this caller does not own is dropped, never
    erased. On a read that gate withholds data; here it is the difference
    between forgetting your own memory and forgetting someone else's, so it
    runs even though the partition is already actor-scoped — the endpoint
    must not inherit the next ``derive_actor_id`` bug the way #474 would
    have handed one user another's memory.

    Drained rather than paged, and the drain flag is returned: a "forget
    everything" that stopped at page one would be the silent lie this
    module exists to stop. So is the withheld count (#552) — the user is
    told the forget finished, and without it nothing in the response says
    that records they asked to lose are still there.
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
    # The same partition canary ``/records`` and ``/export`` log, and the one
    # place it means "we declined to DELETE these".
    withheld = await _withheld_record_count(
        client,
        memory_id=memory_id,
        actor_id=actor_id,
        session_ids=session_ids,
        owned=owned,
        user_id=user_id,
        event="memory.forget_sessions_withheld",
    )
    return [sid for sid in session_ids if sid in owned], drained, withheld


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
    await _write_memory_audit(
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

    Returns ``{"deleted", "failed", "complete", "withheld_record_count"}``.
    Both of the last two are reported in the body rather than only in a log
    line, because a log line is not visible to the person who just asked for
    their data to be gone — and they answer **different** questions, which
    is why #552 added a field instead of widening ``complete``:

    - ``complete`` is false only when an event walk hit its page ceiling, so
      some records were never even considered. That is a retry.
    - ``withheld_record_count`` is what the ownership gate declined to touch
      — a session with no verifiable owning chat row, most likely an orphan
      left by a best-effort ``chats._wipe_agentcore_session``. That is an
      investigation, not a retry, and no amount of retrying will move it.
      A **lower bound**, in the same unit and under the same name
      ``/records`` and the export manifest report.

      **It counts records in withheld sessions, not records this request
      would have deleted** — the two differ under ``?since=``, where the
      cutoff narrows what gets deleted but is deliberately NOT applied to
      the count. Reading an unverifiable session's timestamps closely
      enough to filter it would be inspecting data the ownership check
      could not confirm is the caller's, to make an alarm number smaller;
      over-reporting is the safe direction for "something here is
      unaccounted for". So under ``?since=`` treat it as "at least this
      many records sit in sessions we could not verify", not as a
      would-have-deleted tally.

    Until #552 a forget could answer ``complete: true`` having skipped
    records the user asked to lose, telling them the action finished while
    something remained.

    A batch that deleted nothing while something failed is a 5xx instead:
    ``{"deleted": 0}`` with a 200 reads as "there was nothing to forget",
    which during an AgentCore outage is a claim about the user's own data
    that this endpoint has no basis to make. Withholding is deliberately
    *not* that case — nothing failed, and surfacing the withheld session
    itself would show the user data they already asked to delete.
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
            # Nothing can be withheld here: the gate has already passed on
            # the one session in scope, so a 404 is the only other outcome.
            session_ids, sessions_drained, withheld_records = [chat_id], True, 0
            event_type, details = _AUDIT_BULK_FORGET, {"filter": "chat_id", "chat_id": chat_id}
        else:
            session_ids, sessions_drained, withheld_records = await _owned_forget_session_ids(
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
    # Withholding is deliberately NOT a metric failure: this counter answers
    # "is forget working?", and the ownership gate declining an unverifiable
    # session is forget working exactly as designed. The operator signal for
    # that condition is the ``memory.forget_sessions_withheld`` warning, and
    # the user-visible one is the field below.
    await record_memory_bulk_forget_outcome(success=(failed == 0 and complete))
    await _write_memory_audit(
        event_type,
        user_id,
        {
            **details,
            "deleted": deleted,
            "failed": failed,
            "complete": complete,
            "withheld_record_count": withheld_records,
        },
    )
    if deleted == 0 and failed:
        raise HTTPException(status_code=500, detail="no records could be forgotten")
    return {
        "deleted": deleted,
        "failed": failed,
        "complete": complete,
        "withheld_record_count": withheld_records,
    }


# ── Correct a note (#478) ─────────────────────────────────────────────────

#: Audit event type for a correction — epic #129 decision 3. Its own name
#: rather than a ``verb`` field on the forget events: rewriting a note and
#: erasing one are different acts, and a trail that needs a field read to
#: tell them apart is a trail that will be read wrongly.
_AUDIT_RECORD_EDITED = "memory.record_edited"

#: The 409 body for a record whose class forbids rewriting. Plain language
#: rather than an error code, because this reaches a settings panel and the
#: *reason* is the whole point — a user who is told "409" learns that the
#: product said no, not that the transcript is deliberately immutable.
_NOT_EDITABLE_DETAIL = (
    "Conversation turns can be forgotten, but not rewritten. "
    "Only notes Channel chose to remember can be corrected."
)


class _RecordEditRequest(BaseModel):
    """``PATCH`` body — the corrected note, in the ``remember`` tool's own shape.

    ``content`` is the note as the user wants it stored; ``tags`` replaces
    the record's tags wholesale (omit or pass ``null`` to clear them). Both
    go through ``memory_tools._format_remember_text``, so what lands in
    AgentCore is byte-identical to what the tool itself would have written.
    """

    content: str
    tags: list[str] | None = None


async def _record_is_in_recall(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    session_id: str,
    record_id: str,
) -> bool:
    """``used_in_recall`` for the record about to be replaced.

    Read from the record's **pre-edit** view and carried onto the
    replacement rather than recomputed after the write. Two reasons, and
    the first is the load-bearing one:

    - **The answer is identical by construction.** The replacement re-uses
      the original ``eventTimestamp``, so the session's events are
      unchanged in both count and ordering and the corrected record sits at
      exactly the position the original held. Recomputing would spend a
      second enumeration to learn something already known.
    - **A post-write read would be at the mercy of read-after-write
      timing** on an event created milliseconds earlier, and would report
      ``false`` for a record that is in fact recalled — the response's one
      chance to mislead the panel.

    Computed through the same :func:`list_session_records` the panel is
    served from, so the flag on a corrected row means exactly what it meant
    on the row the user was looking at when they pressed save. A record
    absent from that (capped) page reads ``false``, which is **exact rather
    than a fallback**: the page holds the newest ``MAX_EVENTS_PER_SESSION``
    events and the pool only the newest ``POOL_EVENTS_PER_SESSION``, and
    the former is far larger — so anything outside the page is
    provably outside the pool.

    The kill-switch short-circuits before either call, matching ``/records``
    and ``/export``: a disabled hook injects nothing, so no record can
    honestly be flagged and the two probes are wasted.
    """
    if not _recall_enabled():
        return False
    window = await recall_window_session_ids(client, memory_id=memory_id, actor_id=actor_id)
    records, _truncated = await list_session_records(
        client,
        memory_id=memory_id,
        actor_id=actor_id,
        session_id=session_id,
        in_recall_window=session_id in window,
    )
    return any(record.record_id == record_id and record.used_in_recall for record in records)


async def _replace_remembered_record(
    record_id: str, body: _RecordEditRequest, user_id: str
) -> tuple[dict[str, Any], str]:
    """Do the replace → ``(updated record, session_id)``.

    Separate from the route so the route is only the metric wrapper and the
    audit write, and so every AgentCore call sits inside one try/except that
    can count a failure exactly once — the same split ``_build_export`` /
    ``export_account_data`` uses.

    **Everything that can refuse, refuses before the delete.** The id
    decode, the empty-content check, the ownership gate, the record lookup,
    the ``remembered``-only gate and the timestamp check all run first, so a
    rejected edit leaves the record exactly as it was. Only two things can
    fail after the delete — the create itself, and that is what the
    ``original_text`` in the 500 body is for.
    """
    try:
        session_id, event_id, payload_index = decode_record_id(record_id)
    except ValueError as exc:
        # Never forward a malformed id to AgentCore — it would come back as
        # a ValidationException, i.e. a 500 for what is a client error.
        raise HTTPException(status_code=400, detail="malformed record_id") from exc

    if not body.content.strip():
        # A 400, deliberately not a delete: an empty save is far more likely
        # to be a slipped keystroke than an erase request, and DELETE is one
        # call away for the case where it isn't.
        raise HTTPException(status_code=400, detail="content must not be empty")

    # Ownership before anything is read, by the same 404-not-403 gate the
    # rest of the chat surface uses — ``sessionId`` *is* a ``chat_id``.
    await _load_owned_chat(session_id, user_id)

    actor_id = derive_actor_id(user_id)
    client = _agentcore_client()
    memory_id = await asyncio.to_thread(_memory_id_for_env)

    event = await get_session_event(
        client,
        memory_id=memory_id,
        actor_id=actor_id,
        session_id=session_id,
        event_id=event_id,
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Record not found")

    payload = list(event.get("payload") or [])
    if payload_index >= len(payload):
        # The event exists but holds no entry at that index — an id from a
        # stale panel, or one hand-built. Indistinguishable from an unknown
        # record, and answered as one.
        raise HTTPException(status_code=404, detail="Record not found")
    conversational = payload[payload_index].get("conversational") or {}
    role = conversational.get("role", "")
    original_text = (conversational.get("content") or {}).get("text", "")
    if not original_text:
        # A text-less entry is not a record: the read model skips it, so the
        # panel never showed it and there is nothing to correct.
        raise HTTPException(status_code=404, detail="Record not found")

    if classify_kind(role, original_text) != KIND_REMEMBERED:
        raise HTTPException(status_code=409, detail=_NOT_EDITABLE_DETAIL)

    event_timestamp = event.get("eventTimestamp")
    if not isinstance(event_timestamp, datetime):
        # boto3 deserializes AgentCore timestamps as ``datetime`` and the
        # service model makes ``eventTimestamp`` required, so this is a wire
        # anomaly rather than an expected shape. It refuses BEFORE the
        # delete, so the record survives: preserving chronological position
        # is the whole reason the timestamp is re-used, and creating the
        # replacement at "now" would silently promote a corrected note to
        # the top of the recall window.
        raise HTTPException(status_code=500, detail="record has no usable timestamp")

    used_in_recall = await _record_is_in_recall(
        client,
        memory_id=memory_id,
        actor_id=actor_id,
        session_id=session_id,
        record_id=record_id,
    )

    new_text = _format_remember_text(body.content, body.tags)

    if not await delete_session_event(
        client,
        memory_id=memory_id,
        actor_id=actor_id,
        session_id=session_id,
        event_id=event_id,
    ):
        # Raced with a forget between the GetEvent above and here. The
        # record is gone, which is what a 404 says.
        raise HTTPException(status_code=404, detail="Record not found")

    # Sibling entries are carried over untouched — ``DeleteEvent`` took the
    # whole event, so anything not re-created here is lost.
    payload[payload_index] = {"conversational": {"role": role, "content": {"text": new_text}}}
    try:
        new_event_id = await create_session_event(
            client,
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=session_id,
            event_timestamp=event_timestamp,
            payload=payload,
        )
    except Exception as exc:
        # The one failure this ordering admits. The original bytes go back
        # in the body so the panel can offer them to the user rather than
        # losing a note to a "save" — see the module docstring.
        raise HTTPException(
            status_code=500,
            detail={
                "message": (
                    "the correction could not be saved and the original was already removed"
                ),
                "original_text": original_text,
            },
        ) from exc

    updated = MemoryRecord(
        record_id=encode_record_id(session_id, new_event_id, payload_index),
        # Re-classified rather than assumed: ``_format_remember_text`` and
        # ``classify_kind`` are the write and read halves of one convention,
        # and asserting the round trip here is free.
        kind=classify_kind(role, new_text),
        role=role,
        text=new_text,
        created_at=event_timestamp.isoformat(),
        used_in_recall=used_in_recall,
    )
    return updated.to_dict(), session_id


@router.patch(
    "/records/{record_id}",
    responses={
        400: {"description": "Malformed record_id, or empty content"},
        404: {"description": "Unknown record, or one whose chat the caller doesn't own"},
        409: {"description": "The record is a conversation turn or a meta fact"},
        500: {"description": "The replacement could not be written after the original was removed"},
    },
)
async def edit_memory_record(
    record_id: str,
    body: _RecordEditRequest,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Correct one ``remembered`` note, in place, keeping its position.

    Returns the updated record in the same shape ``/records`` serves —
    including its **new** ``record_id``, which the caller must adopt: the
    replacement is a new AgentCore event, so the id it was addressed by no
    longer exists.
    """
    user_id = claims["sub"]
    try:
        updated, session_id = await _replace_remembered_record(record_id, body, user_id)
    except HTTPException as exc:
        # A 4xx is a statement about the request, not an edit that failed —
        # the same rule the per-record forget follows, so counting it would
        # put a floor under the failure rate that no fix could lower. A 5xx
        # is an operational failure and does count, including the
        # create-after-delete case that is the one worth alarming on.
        if exc.status_code >= 500:
            await record_memory_record_edit_outcome(success=False)
        raise
    except Exception:
        await record_memory_record_edit_outcome(success=False)
        raise

    await record_memory_record_edit_outcome(success=True)
    # Best-effort, and counts + opaque ids only. The correction has already
    # landed, so failing the response would report an edit that did not
    # happen — and copying the before/after text into a 365-day audit
    # partition would preserve exactly the stale claim it just retired.
    await _write_memory_audit(
        _AUDIT_RECORD_EDITED,
        user_id,
        {
            "chat_id": session_id,
            "record_id": record_id,
            "new_record_id": updated["record_id"],
            "edited": 1,
        },
    )
    return updated
