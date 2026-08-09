# Copyright (c) 2026 John Carter. All rights reserved.
"""Shared AgentCore Memory **read model** — enumerate, classify, flag (#475).

The honest answer to "what does Channel remember about me". Epic #129
decisions live at
https://github.com/warlordofmars/channel/issues/129#issuecomment-5158517210;
this module implements the enumeration half so ``api/memory.py`` (and the
later forget / edit / export surfaces — #476 / #477 / #479) all share one
definition of a *record*.

Three things it does, none of which belong in a router:

1. **Enumerate** — ``ListSessions`` + per-session ``ListEvents`` for one
   actor, flattened into per-turn records. Every blocking boto3 call goes
   through :func:`asyncio.to_thread`, same as ``agents/recall.py`` and
   ``agents/tools/memory_tools.py``.
2. **Classify** — provenance (:data:`KIND_CONVERSATION` /
   :data:`KIND_REMEMBERED` / :data:`KIND_META`) from the event text's
   prefix, ASSISTANT-only. See :func:`classify_kind`.
3. **Flag** — ``used_in_recall``, computed from the live pool constants in
   ``agents/memory_ranking`` rather than copied numbers. See
   :func:`recall_window_session_ids` and :func:`list_session_records`.

Plus the two safety rails the read side needs:

- :func:`resolve_owned_chats` — ownership verification against the **raw**
  JWT sub. #485 made ``derive_actor_id`` injective, closing #474, but the
  gate stays: a read boundary must not depend on a derivation's
  properties, and it is what keeps this surface from inheriting the
  *next* such bug.
- :func:`encode_record_id` / :func:`decode_record_id` — opaque record ids
  that survive a URL path segment.

**Record text is data, not markup.** ``MemoryRecord.text`` is the full
stored bytes of a turn, which is attacker-influenced content: a user (or a
model quoting one) can write ``## Heading`` or a fake delimiter into it.
Consumers MUST render it as plain text (``white-space: pre-wrap`` on a text
node) and never through ``ui/src/app/renderMarkdown.jsx`` — epic #129
decision 11, and the same forgery surface #465 tracks on the system-prompt
side. Nothing in this module escapes, strips or "defuses" the text, by
design: an audit panel's job is to show the bytes.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

from channel import storage
from channel.agents.memory import _META_PREFIX
from channel.agents.memory_ranking import (
    _EPOCH,
    POOL_EVENTS_PER_SESSION,
    POOL_MAX_SESSIONS,
    iso_date,
)
from channel.agents.recall import _RECALL_EVENT_TEXT_TRUNCATE
from channel.agents.tools.memory_tools import _REMEMBER_PREFIX
from channel.models import Chat

logger = logging.getLogger(__name__)

__all__ = [
    "KIND_CONVERSATION",
    "KIND_META",
    "KIND_REMEMBERED",
    "MAX_EVENTS_PER_SESSION",
    "MAX_FORGET_EVENT_PAGES",
    "MemoryRecord",
    "classify_kind",
    "count_session_records",
    "create_session_event",
    "decode_record_id",
    "delete_session_event",
    "encode_record_id",
    "get_session_event",
    "group_created_at",
    "list_forgettable_event_ids",
    "list_session_records",
    "list_sessions_page",
    "recall_window",
    "recall_window_session_ids",
    "resolve_owned_chats",
]

# ── Provenance classes ────────────────────────────────────────────────────

#: A raw chat turn, written by the always-on ``AgentCoreMemoryHook``.
KIND_CONVERSATION = "conversation"
#: A deliberate note written by the ``remember`` tool (#273).
KIND_REMEMBERED = "remembered"
#: A tool-use META fact written by ``ToolCallTelemetryHook``.
KIND_META = "meta"

_ASSISTANT_ROLE = "ASSISTANT"

# Hard per-session event cap, so one pathological chat can't blow the
# response (epic #129 decision 12). ListEvents returns newest-first, so
# this truncates the OLDEST events of a very long chat — the right
# direction: the newest turns are the ones recall can still reach.
MAX_EVENTS_PER_SESSION = 100

# ``record_id`` field separator. Neither field can contain it: session ids
# are chat UUIDs and AgentCore event ids are ``<digits>#<hex>``.
_RECORD_ID_SEP = "|"
_RECORD_ID_FIELDS = 3


# ── The AgentCore read seam ───────────────────────────────────────────────

#: The one AgentCore error code this module absorbs — see
#: :func:`_agentcore_read`. Everything else re-raises.
_NO_PARTITION_CODE = "ResourceNotFoundException"


def _is_absent(exc: ClientError) -> bool:
    """Whether ``exc`` is AgentCore's "that doesn't exist" answer.

    **The code is matched, never the message.** AWS error codes are a stable
    contract and message prose is not, so matching on "Actor ... not found"
    would re-break this for new users the first time the string is reworded.
    ``getattr`` guards a malformed exception carrying no ``response`` at all,
    which must re-raise rather than read as an absence.

    One definition, shared by the read seam (:func:`_agentcore_read`) and the
    delete seam (:func:`delete_session_event`), so the two can never disagree
    about what "absent" means — the drift that would let a *forget* endpoint
    report success against a partition a *read* endpoint still 500s on.
    """
    return (getattr(exc, "response", None) or {}).get("Error", {}).get("Code") == _NO_PARTITION_CODE


async def _agentcore_read(
    call: Callable[..., dict[str, Any]], *, what: str, **kwargs: Any
) -> dict[str, Any] | None:
    """One blocking AgentCore read → its response, or ``None`` when the
    actor has no memory partition (#527).

    Every AgentCore call in this module goes through here, so all three
    reads answer an absent partition the same way. Blocking boto3 work runs
    in :func:`asyncio.to_thread`, same as ``agents/recall.py``.

    **Why ``None`` rather than an exception.** An actor partition is created
    lazily by the first memory *write*, so a user who has signed in but
    never had a chat turn has none, and every read against them raises::

        ResourceNotFoundException: ... Actor <derived-actor-id> not found

    That is not a failure — a user with no partition genuinely has no
    records, which is the posture ``AgentCoreRecallHook`` has always taken.
    Until #527 it propagated out of the read model untouched (the only
    ``except`` here covered ``record_id`` decoding), so
    ``GET /api/memory/records`` and ``GET /api/memory/export`` returned 500
    to every user who had not yet chatted — the exact users most likely to
    be checking what Channel knows about them. Each caller supplies its own
    empty value; this function does not invent one, because "no sessions"
    and "no events" are different shapes.

    **Only that one error code is absorbed.** Any other ``ClientError`` —
    throttling, access denied, a service fault — re-raises and still
    surfaces as a 5xx. The distinction is the whole point on an *export*
    endpoint: answering "you have no memories" during an AgentCore outage
    would be a data-integrity claim the product cannot stand behind. A
    blanket ``except Exception`` is what the recall hook can afford, because
    it degrades a prompt; here it would falsify an answer.

    **The code is matched, never the message** — see :func:`_is_absent`. The
    other condition that shares this code — an unknown ``memoryId`` —
    resolves to the same honest answer anyway: ``get_or_create_memory``
    resolves-or-creates the environment's Memory resource on cold start, and
    a freshly created Memory holds nothing.
    """
    try:
        return await asyncio.to_thread(call, **kwargs)
    except ClientError as exc:
        if not _is_absent(exc):
            raise
        # ``what`` only — no identifiers. The derived actor id carries a
        # human-legible label built from the user's email (see
        # ``memory.derive_actor_id``), so it does not belong in a log line.
        logger.info("memory.actor_partition_absent read=%s", what)
        return None


# ── Record identity ───────────────────────────────────────────────────────


def encode_record_id(session_id: str, event_id: str, payload_index: int) -> str:
    """Opaque, URL-path-safe id for one memory record.

    base64url of ``f"{session_id}|{event_id}|{payload_index}"``, padding
    stripped. Epic #129 decision 7 specifies the encoding and its two
    reasons: AgentCore event ids are ``<digits>#<hex>`` and the ``#``
    silently truncates in path position (this already bit ``_debug``,
    which worked around it with a query param), and deleting an event
    needs its ``sessionId`` too, which a bare event id doesn't carry.

    **``payload_index`` extends that decision**, which predates noticing
    that one AgentCore event can carry more than one record. The
    ``AgentCoreMemoryHook`` writes the user+assistant pair as a single
    atomic ``CreateEvent`` with a two-entry ``payload``
    (``memory.py::_on_after_invocation_async``), and a record is one
    *payload entry* — it has a single ``role``, which is what the
    ASSISTANT-only classification rule keys off. Without the index two
    records in the same event would share a ``record_id``. The scheme,
    its rationale and its opacity are unchanged; it just carries the
    third field it needs to be unique.

    Deletion (#476) uses ``session_id`` + ``event_id`` and ignores the
    index — ``DeleteEvent`` is AgentCore's only granularity, so forgetting
    one half of a turn pair is not an operation the vendor offers.
    """
    raw = f"{session_id}{_RECORD_ID_SEP}{event_id}{_RECORD_ID_SEP}{payload_index}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_record_id(record_id: str) -> tuple[str, str, int]:
    """Inverse of :func:`encode_record_id` → ``(session_id, event_id, index)``.

    Raises :class:`ValueError` on anything malformed — not base64url, not
    UTF-8, wrong field count, empty field, or a non-numeric index. Callers
    in the API layer turn that into a 400; it must never reach AgentCore as
    a ``ValidationException`` (a 500).
    """
    padded = record_id + "=" * (-len(record_id) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
    except (ValueError, binascii.Error) as exc:
        # The tuple is deliberately redundant: both concrete failures here —
        # ``binascii.Error`` (not base64) and ``UnicodeDecodeError`` (not
        # UTF-8) — already subclass ``ValueError``, so ``except ValueError``
        # alone would catch them. Naming them says what can actually go
        # wrong instead of leaving the next reader to work it out, and
        # mirrors the ``chats.py`` cursor precedent.
        raise ValueError("malformed record_id") from exc
    parts = raw.split(_RECORD_ID_SEP)
    if len(parts) != _RECORD_ID_FIELDS or not all(parts) or not parts[2].isdigit():
        raise ValueError("malformed record_id")
    return parts[0], parts[1], int(parts[2])


# ── Classification ────────────────────────────────────────────────────────


def classify_kind(role: str, text: str) -> str:
    """Provenance class for one record — epic #129 decision 1.

    ASSISTANT + ``[remember]`` → :data:`KIND_REMEMBERED`; ASSISTANT +
    ``[meta]`` → :data:`KIND_META`; everything else (including a USER turn
    whose text happens to start with either prefix) →
    :data:`KIND_CONVERSATION`. Both prefixes are imported from the modules
    that write them so the three copies can't drift.

    **Known limitation, documented rather than fixed:** this is a text
    *prefix heuristic*, not stored provenance. AgentCore's conversational
    payload has no metadata field, so a model that emits a literal
    ``[remember]`` at the start of ordinary prose is mislabelled. The
    ASSISTANT-only gate is what keeps that from being a *security*
    problem — a user cannot make their own message masquerade as something
    Channel decided to remember — so the worst case is a wrong badge.
    """
    if role == _ASSISTANT_ROLE:
        if text.startswith(_REMEMBER_PREFIX):
            return KIND_REMEMBERED
        if text.startswith(_META_PREFIX):
            return KIND_META
    return KIND_CONVERSATION


# ── Record model ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MemoryRecord:
    """One stored memory record — a single conversational payload entry."""

    record_id: str
    kind: str
    role: str
    text: str
    created_at: str
    used_in_recall: bool

    @property
    def editable(self) -> bool:
        """Only ``remembered`` records are editable — epic #129 decision 8.

        ``PATCH`` is ``DeleteEvent`` + ``CreateEvent``; rewriting a
        ``conversation`` turn would falsify the transcript, and a ``meta``
        fact is telemetry. Both return 409 in #477. Exposed on the read
        model so the panel can render the affordance without re-deriving
        the rule.
        """
        return self.kind == KIND_REMEMBERED

    def to_dict(self) -> dict[str, Any]:
        """Wire shape. ``text`` is the FULL stored text — never truncated to
        ``_RECALL_EVENT_TEXT_TRUNCATE``, which is a recall-injection cap,
        not a storage cap; truncating here would misrepresent what is
        stored."""
        return {
            "record_id": self.record_id,
            "kind": self.kind,
            "role": self.role,
            "text": self.text,
            "created_at": self.created_at,
            "used_in_recall": self.used_in_recall,
            "editable": self.editable,
        }


# ── Ownership verification (the read-boundary gate) ───────────────────────


def resolve_owned_chats(session_ids: Iterable[str], *, user_id: str) -> dict[str, Chat]:
    """Map each session id to its chat row, keeping only rows ``user_id`` owns.

    ``sessionId == chat_id`` by design (CLAUDE.md §AgentCore Memory), so a
    memory session is verifiable against the chat-index row it came from.
    ``user_id`` is the **raw** JWT ``sub``, compared against the chat row's
    own ``user_id`` — never a derived actor id. That is what makes this a
    real boundary rather than a restatement of the actor scope.

    Why it exists: **a read boundary must not depend on a derivation's
    properties.** #474 is the demonstration — the actor-id derivation was
    not injective (``jc+work@x.com`` and ``jc_work@x.com`` collided), so an
    endpoint scoped by ``actorId`` alone would hand one user another user's
    private memory. #485 makes the derivation injective, which fixes the
    *partitioning*; this function guards the *read*, and the two are
    complementary rather than redundant. Sessions that fail verification are
    dropped by the caller and counted, so the count stays a standing canary
    on the partition regardless of how the actor id is derived.

    A session with **no** chat row is also unverifiable and is therefore
    withheld, not shown with a null title. That is the conservative call
    twice over: an orphaned AgentCore session is what a failed
    ``DELETE /api/chats/{id}`` wipe leaves behind
    (``chats._wipe_agentcore_session`` is best-effort), so surfacing it
    would show the user data they already asked to delete.

    Synchronous — one ``ChatByIdIndex`` point read per distinct session id,
    the same batch-verification shape as ``assets._partition_by_chat_liveness``.
    Call it through :func:`asyncio.to_thread`.
    """
    owned: dict[str, Chat] = {}
    for session_id in dict.fromkeys(session_ids):
        chat = storage.get_chat_by_id(session_id)
        if chat is not None and chat.user_id == user_id:
            owned[session_id] = chat
    return owned


# ── Recall window ─────────────────────────────────────────────────────────


def recall_window(*, enabled: bool) -> dict[str, Any]:
    """The live recall window, read from the modules that define it.

    Never re-literal these numbers (epic #129 decision 2) — the panel's
    whole claim is that it reports what the hook actually does, and a
    hand-copied ``5`` would make that a lie the first time a cap moves.
    It already would have: #274 widened the pool from 5x2 to
    ``POOL_MAX_SESSIONS`` x ``POOL_EVENTS_PER_SESSION``, and this sentence
    followed automatically because it was never a literal.

    **``ordering`` is ``"relevance"`` since #274**, where it read
    ``"recency"`` before. The window described here is the *candidate
    pool* — the records the hook can reach — and which of them actually
    reach a prompt is now decided per turn by ranking them against what
    the user just said. Reporting ``"recency"`` would understate that in
    the one direction that matters: it would tell a user their most recent
    chats are always loaded, when an off-topic message now loads nothing
    at all.

    The pool caps stay part of the answer rather than being replaced by
    ``recall._RECALL_TOKEN_BUDGET``: the budget bounds how much of a
    matched pool is rendered, but it is the pool that determines what
    Channel could ever recall, which is the question this panel exists to
    answer honestly.
    """
    return {
        "max_sessions": POOL_MAX_SESSIONS,
        "events_per_session": POOL_EVENTS_PER_SESSION,
        "text_truncate": _RECALL_EVENT_TEXT_TRUNCATE,
        "ordering": "relevance",
        "enabled": enabled,
    }


async def recall_window_session_ids(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
) -> set[str]:
    """Session ids the recall hook could draw from for a **brand-new** chat.

    Mirrors ``memory_ranking.collect_candidates`` step 1 exactly — one
    un-paginated ``ListSessions``, explicit newest-first sort (never trust
    AgentCore's default order), top :data:`POOL_MAX_SESSIONS`. Fidelity
    to the hook matters more than completeness here: if the hook only ever
    sees one ``ListSessions`` page, so does this.

    **"Could", not "would", since #274.** This is the candidate pool the
    hook ranks over, and relevance decides per turn which of it is
    actually injected — a question with no answer until someone types
    something, so a per-record flag cannot express it. Reporting the pool
    is the honest fixed point: a record outside it can never be recalled,
    and a record inside it can. The panel's sentence says so in the same
    breath (:func:`recall_window` reports ``ordering: "relevance"``).

    No current-chat exclusion. The hook drops the *current* chat because
    PR #73 already feeds that history into Strands directly; the panel is
    not chat-scoped, so it models the window as it would apply to a chat
    that doesn't exist yet. Since #274 the hook applies that exclusion at
    rank time rather than at fetch time, which makes this an even closer
    mirror of the fetch than it was.

    An actor with no memory partition has no recall window — the empty set,
    not an error. See :func:`_agentcore_read`.
    """
    resp = await _agentcore_read(
        client.list_sessions,
        what="recall_window",
        memoryId=memory_id,
        actorId=actor_id,
    )
    if resp is None:
        return set()
    sessions = resp.get("sessionSummaries", [])
    ordered = sorted(sessions, key=lambda s: s.get("createdAt") or _EPOCH, reverse=True)
    return {s["sessionId"] for s in ordered[:POOL_MAX_SESSIONS] if s.get("sessionId")}


# ── Enumeration ───────────────────────────────────────────────────────────


async def list_sessions_page(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    limit: int,
    next_token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of the actor's sessions → ``(summaries, next_token)``.

    Pagination is **by session, not by record** (epic #129 decision 12):
    the ``ListSessions`` / ``ListEvents`` fan-out makes a record-level
    cursor fragile, and the panel groups by chat anyway.

    An actor with no memory partition reads as one empty page with no
    continuation — see :func:`_agentcore_read`. Handling it here as well as
    in :func:`recall_window_session_ids` is not belt-and-braces: with
    ``CHANNEL_RECALL_ENABLED=0`` the recall probe is skipped entirely, so
    this is the *first* AgentCore call the page makes, and it is also the
    one the export's session walk drains.
    """
    kwargs: dict[str, Any] = {
        "memoryId": memory_id,
        "actorId": actor_id,
        "maxResults": limit,
    }
    if next_token:
        kwargs["nextToken"] = next_token
    resp = await _agentcore_read(client.list_sessions, what="sessions_page", **kwargs)
    if resp is None:
        return [], None
    return resp.get("sessionSummaries", []), resp.get("nextToken")


def _iso_timestamp(value: Any) -> str:
    """Full ISO-8601 for an event timestamp; ``""`` for None.

    Deliberately NOT ``memory_ranking.iso_date``: a group header wants a date, but
    an individual record wants its time-of-day so the panel can order and
    disambiguate turns within one chat. boto3 deserializes AgentCore
    timestamps as ``datetime``; anything else is passed through as a string.
    """
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _iter_payload_texts(
    events: list[dict[str, Any]],
) -> Iterator[tuple[int, str, str, int, str, str]]:
    """Walk ``ListEvents`` output → ``(pos, event_id, created_at, idx, role, text)``.

    ``pos`` is the event's position in the newest-first response, which is
    what :func:`list_session_records` compares against
    :data:`POOL_EVENTS_PER_SESSION`. Entries with no text (or an event with
    no id) are skipped — matching ``recall._format_recall_addendum``, which
    also drops them, so ``used_in_recall`` stays truthful. Shared by the
    record builder and the withheld counter so the two can't disagree about
    what counts as a record.
    """
    for pos, event in enumerate(events):
        event_id = event.get("eventId") or ""
        if not event_id:
            continue
        created_at = _iso_timestamp(event.get("eventTimestamp"))
        for idx, entry in enumerate(event.get("payload", [])):
            conv = entry.get("conversational") or {}
            text = (conv.get("content") or {}).get("text", "")
            if not text:
                continue
            yield pos, event_id, created_at, idx, conv.get("role", ""), text


async def _list_events(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    session_id: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Newest-first events for one session → ``(events, more_exist)``.

    Capped at :data:`MAX_EVENTS_PER_SESSION`. ``more_exist`` is AgentCore's
    own ``nextToken`` rather than a ``len(events) == cap`` heuristic — the
    vendor is the authority on whether the session has more, and the
    heuristic would false-positive on a session holding exactly the cap.

    An absent actor **or** session reads as no events, not an error (see
    :func:`_agentcore_read`). This one is reachable even after the two
    ``ListSessions`` call sites are fixed: ``/records?chat_id=...`` skips
    session enumeration entirely and comes straight here for a chat the
    caller demonstrably owns, so a chat with no memory events — a
    partition-less user's first chat, or one whose session a best-effort
    ``chats._wipe_agentcore_session`` already removed — would otherwise
    still 500.
    """
    resp = await _agentcore_read(
        client.list_events,
        what="events",
        memoryId=memory_id,
        actorId=actor_id,
        sessionId=session_id,
        maxResults=MAX_EVENTS_PER_SESSION,
    )
    if resp is None:
        return [], False
    return resp.get("events", []), bool(resp.get("nextToken"))


async def list_session_records(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    session_id: str,
    in_recall_window: bool,
) -> tuple[list[MemoryRecord], bool]:
    """Every enumerable record in one session → ``(records, truncated)``.

    ``used_in_recall`` is true when the session is one of the
    :data:`POOL_MAX_SESSIONS` most recent (``in_recall_window``, from
    :func:`recall_window_session_ids`) **and** the record's event is among
    that session's :data:`POOL_EVENTS_PER_SESSION` most recent. Since
    ``ListEvents`` returns newest-first, "most recent M events" is exactly
    positions ``0..M-1`` of the response — the same slice
    ``memory_ranking.collect_candidates`` gets by passing
    ``maxResults=POOL_EVENTS_PER_SESSION``.

    Post-#274 the flag means "in the pool recall ranks over", not "in
    every prompt": see :func:`recall_window_session_ids`. The boundary it
    reports is unchanged in kind — outside it, a record is unreachable —
    and it is still computed from the live constants rather than copied
    numbers, so it moved with the pool without being touched here.

    Ordering: events are reversed to chronological (oldest first, matching
    ``recall._format_recall_addendum``) while payload entries keep their
    within-event order, so a turn still reads USER then ASSISTANT.

    ``truncated`` is true when the session holds more than
    :data:`MAX_EVENTS_PER_SESSION` events, so the OLDEST are not in
    ``records``. It is returned rather than swallowed on principle: this
    surface's whole argument is that a partial view presented as a complete
    one is a silent lie (#227), and a hard cap that reports nothing would be
    exactly that.
    """
    events, truncated = await _list_events(
        client, memory_id=memory_id, actor_id=actor_id, session_id=session_id
    )
    by_event: dict[int, list[MemoryRecord]] = {}
    for pos, event_id, created_at, idx, role, text in _iter_payload_texts(events):
        by_event.setdefault(pos, []).append(
            MemoryRecord(
                record_id=encode_record_id(session_id, event_id, idx),
                kind=classify_kind(role, text),
                role=role,
                text=text,
                created_at=created_at,
                used_in_recall=in_recall_window and pos < POOL_EVENTS_PER_SESSION,
            )
        )
    records = [record for pos in sorted(by_event, reverse=True) for record in by_event[pos]]
    return records, truncated


async def count_session_records(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    session_id: str,
) -> int:
    """How many records a session holds, **without materializing their text**.

    Used only for the ``withheld_record_count`` on sessions that failed
    :func:`resolve_owned_chats`. Deliberately returns an ``int`` and nothing
    else: the count is the signal worth surfacing, the content is not ours
    to return. Post-#485 that signal should read 0 — a non-zero value means
    a partition anomaly or an orphaned session left by a failed
    chat-delete wipe, both worth knowing. The AgentCore response is
    transient within this call — never logged, never built into a
    :class:`MemoryRecord`, never sent to a client.

    Counts only the capped page, same as :func:`list_session_records` — so
    on a withheld session holding more than :data:`MAX_EVENTS_PER_SESSION`
    events the count is a floor, not a total. That is the right direction:
    it is a "this happened, and at least this much" alarm, and paging
    another user's memory to get an exact figure would be the opposite of
    the point.
    """
    events, _more = await _list_events(
        client, memory_id=memory_id, actor_id=actor_id, session_id=session_id
    )
    return sum(1 for _ in _iter_payload_texts(events))


# ── The AgentCore forget seam (#477) ──────────────────────────────────────

#: Ceiling on ``ListEvents`` pages per session during a forget walk — a
#: backstop against a paginator that keeps handing back the same token,
#: which in a synchronous Lambda handler is a burnt function timeout rather
#: than a slow response. 200 pages × :data:`MAX_EVENTS_PER_SESSION` is
#: 20 000 events in one chat, far above any plausible real session. A walk
#: that hits it reports it rather than silently truncating.
MAX_FORGET_EVENT_PAGES = 200


def _event_at_or_after(value: Any, cutoff: datetime) -> bool:
    """Whether an ``eventTimestamp`` falls at or after ``cutoff``.

    boto3 deserializes AgentCore timestamps as ``datetime``; anything else
    (``None``, or a string from some future wire change) is **excluded**
    from a ``since`` forget. An undatable event cannot be *shown* to fall in
    the requested range, and a forget that guessed would delete records the
    user did not ask to lose — the one direction of error that is not
    recoverable. ``?all=true`` remains the path that reaches them.

    A naive timestamp is read as UTC rather than rejected: AgentCore stamps
    in UTC, and refusing would make the whole filter depend on a boto3
    deserialization detail.
    """
    if not isinstance(value, datetime):
        return False
    at = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return at >= cutoff


async def list_forgettable_event_ids(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    session_id: str,
    since: datetime | None = None,
) -> tuple[list[str], bool]:
    """Event ids to forget in one session → ``(event_ids, drained)``.

    **Drained across every page, unlike :func:`_list_events`.** The read
    model caps at :data:`MAX_EVENTS_PER_SESSION` and *reports* the cap via
    ``records_truncated``, which is honest for a list. A forget cannot make
    the same trade: "forget everything in this chat" that quietly kept the
    oldest events would be exactly the silent lie this module family exists
    to stop, and the user has no ``records_truncated`` to read on a
    destructive action they believe completed. Same full walk
    ``chats._wipe_agentcore_session`` performs on chat deletion.

    ``since`` filters to events at or after the cutoff (see
    :func:`_event_at_or_after`); ``None`` takes the whole session. Events
    with no id are skipped — there is nothing to address a ``DeleteEvent``
    at.

    An absent actor **or** session yields no ids and counts as drained (see
    :func:`_agentcore_read`): a partition that does not exist holds nothing
    to forget, which is the honest answer rather than a 500. That is the
    #527 condition, reached here by a signed-in user who has never chatted
    pressing "forget everything".

    ``drained`` is false when the walk stopped short of the end — the page
    ceiling was hit, or the paginator stalled (below). It is **returned**,
    not merely logged, so the caller can say so rather than reporting a
    partial forget as a complete one.

    **Two guards against a stuck paginator, because the ceiling alone is
    not one.** A vendor that keeps handing back the *same* ``nextToken``
    would otherwise run the full ceiling and hand the caller the same ids
    200 times over — thousands of redundant ``DeleteEvent`` calls, which in
    a synchronous Lambda handler is the burnt timeout the ceiling exists to
    prevent, arriving by a different route. So:

    1. **A repeated ``nextToken`` ends the walk immediately** with
       ``drained=False``. The caller still learns the forget was partial;
       it just learns it after two round trips instead of two hundred.
    2. **Ids are de-duplicated.** Belt and braces for the above, and
       independently correct: a **concurrent** mutator — the
       ``AgentCoreMemoryHook`` writing a turn from another tab, or an
       overlapping forget — shifts the paging window, and ``ListEvents``
       can then show one event on two pages. Returning it twice would
       spend a second ``DeleteEvent`` to be told it is already gone.

       Note the mutator is *concurrent*, not this walk:
       ``api/memory._forget_sessions`` drains this function completely for
       a session and only then enters its delete loop, so no
       ``DeleteEvent`` is ever in flight against the session being listed.
       Stated precisely because the obvious-sounding wrong cause ("this
       walk is deleting from it") is checkable, doesn't hold, and would
       lead a future reader to conclude the guard is dead weight and
       remove something that is doing real work.

    Neither guard can drop a record. ``seen_ids`` is added to only *after*
    the ``since`` filter passes, so an event excluded by the cutoff is
    never memoised as seen and is re-evaluated freely if it reappears on a
    later page.
    """
    event_ids: list[str] = []
    seen_ids: set[str] = set()
    seen_tokens: set[str] = set()
    token: str | None = None
    for _ in range(MAX_FORGET_EVENT_PAGES):
        kwargs: dict[str, Any] = {
            "memoryId": memory_id,
            "actorId": actor_id,
            "sessionId": session_id,
            "maxResults": MAX_EVENTS_PER_SESSION,
        }
        if token:
            kwargs["nextToken"] = token
        resp = await _agentcore_read(client.list_events, what="forget", **kwargs)
        if resp is None:
            return event_ids, True
        for event in resp.get("events", []):
            event_id = event.get("eventId") or ""
            if not event_id or event_id in seen_ids:
                continue
            if since is not None and not _event_at_or_after(event.get("eventTimestamp"), since):
                continue
            seen_ids.add(event_id)
            event_ids.append(event_id)
        token = resp.get("nextToken")
        if not token:
            return event_ids, True
        if token in seen_tokens:
            logger.warning("memory.forget_paginator_stalled")
            return event_ids, False
        seen_tokens.add(token)
    logger.warning("memory.forget_page_cap_hit pages=%d", MAX_FORGET_EVENT_PAGES)
    return event_ids, False


async def delete_session_event(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    session_id: str,
    event_id: str,
) -> bool:
    """``DeleteEvent`` one AgentCore event → whether it was there to delete.

    ``True`` when AgentCore accepted the delete; ``False`` when the event,
    session or actor partition is already absent. The mirror image of
    :func:`_agentcore_read` and it absorbs exactly the same one error code
    (:func:`_is_absent`) for the same reason: an actor partition is created
    lazily by the first memory *write*, so deleting from one that was never
    created is a no-op, not a failure. Every other ``ClientError`` —
    throttling, access denied, a service fault — re-raises and still
    surfaces as a 5xx.

    That narrowness matters more here than on the read side. A blanket
    ``except Exception`` would let this report "forgotten" during an
    AgentCore outage, and a user who is told their data is gone stops asking
    — which is a worse outcome than the error they can act on. ``False`` is
    deliberately distinct from an exception so the caller can answer
    "already gone" (404, or a no-op in a batch) separately from "we failed"
    (a counted failure).

    Returning a bool rather than raising a bespoke exception keeps this a
    seam over the vendor call: AgentCore's ``DeleteEvent`` is idempotent in
    effect, and the caller decides what that means for its status code.
    """
    try:
        await asyncio.to_thread(
            client.delete_event,
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=session_id,
            eventId=event_id,
        )
    except ClientError as exc:
        if not _is_absent(exc):
            raise
        # No identifiers: the derived actor id carries a label built from
        # the user's email (``memory.derive_actor_id``), and an event id
        # addresses one record of it.
        logger.info("memory.delete_event_absent")
        return False
    return True


# ── The AgentCore edit seam (#478) ────────────────────────────────────────


async def get_session_event(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    session_id: str,
    event_id: str,
) -> dict[str, Any] | None:
    """One AgentCore event in full → its raw shape, or ``None`` when absent.

    The read the *edit* surface needs and no other surface does. ``PATCH`` is
    ``DeleteEvent`` + ``CreateEvent`` (epic #129 decision 8), so it has to
    reproduce two things an enumerated :class:`MemoryRecord` deliberately
    drops: the event's raw ``eventTimestamp`` — a ``datetime``, which the
    replacement re-uses so a corrected record keeps its chronological
    position instead of jumping to the top of the recall window — and its
    **whole** ``payload``, so sibling entries survive a delete that is
    necessarily event-granular (``AgentCoreMemoryHook`` writes a
    user+assistant pair as one two-entry event; see
    :func:`encode_record_id`).

    Goes through :func:`_agentcore_read`, so an absent actor partition
    (#527), session or event all read as ``None`` rather than a 500. The
    caller turns that into the 404 that "unknown record" already means on
    the delete path — an id that addresses nothing is exactly what an
    unknown id is.
    """
    resp = await _agentcore_read(
        client.get_event,
        what="event",
        memoryId=memory_id,
        actorId=actor_id,
        sessionId=session_id,
        eventId=event_id,
    )
    if resp is None:
        return None
    return resp.get("event") or None


async def create_session_event(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
    session_id: str,
    event_timestamp: datetime,
    payload: list[dict[str, Any]],
) -> str:
    """``CreateEvent`` one AgentCore event → the new event id.

    The write half of the edit seam, and deliberately **not** routed through
    :func:`_agentcore_read`. That helper absorbs the absent-partition code
    because a read against a partition that does not exist has a truthful
    empty answer; a *write* to one creates it, so there is nothing to
    absorb. Nothing is swallowed here at all — every failure reaches the
    caller, which is what lets ``PATCH`` hand the user back the text it has
    already deleted rather than report a correction that did not happen.

    ``event`` and its ``eventId`` are both **required** members of the
    service model's ``CreateEvent`` response, so the subscripts are the
    vendor contract rather than optimism. A response missing either raises,
    and the caller already treats that as a failed create — the safe
    direction, since a new event nobody can address is indistinguishable
    from one that was never written.
    """
    resp = await asyncio.to_thread(
        client.create_event,
        memoryId=memory_id,
        actorId=actor_id,
        sessionId=session_id,
        eventTimestamp=event_timestamp,
        payload=payload,
    )
    return str(resp["event"]["eventId"])


def group_created_at(chat: Chat) -> str:
    """``YYYY-MM-DD`` group header date for a verified chat.

    Sourced from the **chat row**, not the AgentCore session summary: the
    chat row is the one date both the filtered (single ``chat_id``) and
    unfiltered paths always have, and it's the date the user recognises.
    """
    return iso_date(chat.created_at)
