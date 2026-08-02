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
3. **Flag** — ``used_in_recall``, computed from ``recall.py``'s own cap
   constants rather than copied numbers. See :func:`recall_window_session_ids`
   and :func:`list_session_records`.

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
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from channel import storage
from channel.agents.memory import _META_PREFIX
from channel.agents.recall import (
    _EPOCH,
    _RECALL_EVENT_TEXT_TRUNCATE,
    _RECALL_EVENTS_PER_SESSION,
    _RECALL_MAX_SESSIONS,
    _iso_date,
)
from channel.agents.tools.memory_tools import _REMEMBER_PREFIX
from channel.models import Chat

__all__ = [
    "KIND_CONVERSATION",
    "KIND_META",
    "KIND_REMEMBERED",
    "MAX_EVENTS_PER_SESSION",
    "MemoryRecord",
    "classify_kind",
    "count_session_records",
    "decode_record_id",
    "encode_record_id",
    "group_created_at",
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
    """The live recall caps, read from ``recall.py``'s constants.

    Never re-literal these numbers (epic #129 decision 2) — the panel's
    whole claim is that it reports what the hook actually does, and a
    hand-copied ``5`` would make that a lie the first time the cap moves.
    ``ordering`` is ``"recency"``, not relevance: the Phase 7d semantic
    strategy was retired in 8a and relevance ordering is #274's job — this
    surface deliberately *reports* the recency ordering rather than fixing
    it.
    """
    return {
        "max_sessions": _RECALL_MAX_SESSIONS,
        "events_per_session": _RECALL_EVENTS_PER_SESSION,
        "text_truncate": _RECALL_EVENT_TEXT_TRUNCATE,
        "ordering": "recency",
        "enabled": enabled,
    }


async def recall_window_session_ids(
    client: Any,
    *,
    memory_id: str,
    actor_id: str,
) -> set[str]:
    """Session ids the recall hook would draw from for a **brand-new** chat.

    Mirrors ``AgentCoreRecallHook._fetch_records`` step 1 exactly — one
    un-paginated ``ListSessions``, explicit newest-first sort (never trust
    AgentCore's default order), top :data:`_RECALL_MAX_SESSIONS`. Fidelity
    to the hook matters more than completeness here: if the hook only ever
    sees one ``ListSessions`` page, so does this.

    No current-chat exclusion. The hook drops the *current* chat because
    PR #73 already feeds that history into Strands directly; the panel is
    not chat-scoped, so it models the window as it would apply to a chat
    that doesn't exist yet.
    """
    resp = await asyncio.to_thread(
        client.list_sessions,
        memoryId=memory_id,
        actorId=actor_id,
    )
    sessions = resp.get("sessionSummaries", [])
    ordered = sorted(sessions, key=lambda s: s.get("createdAt") or _EPOCH, reverse=True)
    return {s["sessionId"] for s in ordered[:_RECALL_MAX_SESSIONS] if s.get("sessionId")}


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
    """
    kwargs: dict[str, Any] = {
        "memoryId": memory_id,
        "actorId": actor_id,
        "maxResults": limit,
    }
    if next_token:
        kwargs["nextToken"] = next_token
    resp = await asyncio.to_thread(client.list_sessions, **kwargs)
    return resp.get("sessionSummaries", []), resp.get("nextToken")


def _iso_timestamp(value: Any) -> str:
    """Full ISO-8601 for an event timestamp; ``""`` for None.

    Deliberately NOT ``recall._iso_date``: a group header wants a date, but
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
    ``_RECALL_EVENTS_PER_SESSION``. Entries with no text (or an event with
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
    """
    resp = await asyncio.to_thread(
        client.list_events,
        memoryId=memory_id,
        actorId=actor_id,
        sessionId=session_id,
        maxResults=MAX_EVENTS_PER_SESSION,
    )
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
    ``_RECALL_MAX_SESSIONS`` most recent (``in_recall_window``, from
    :func:`recall_window_session_ids`) **and** the record's event is among
    that session's ``_RECALL_EVENTS_PER_SESSION`` most recent. Since
    ``ListEvents`` returns newest-first, "most recent M events" is exactly
    positions ``0..M-1`` of the response — the same slice the hook gets by
    passing ``maxResults=_RECALL_EVENTS_PER_SESSION``.

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
                used_in_recall=in_recall_window and pos < _RECALL_EVENTS_PER_SESSION,
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


def group_created_at(chat: Chat) -> str:
    """``YYYY-MM-DD`` group header date for a verified chat.

    Sourced from the **chat row**, not the AgentCore session summary: the
    chat row is the one date both the filtered (single ``chat_id``) and
    unfiltered paths always have, and it's the date the user recognises.
    """
    return _iso_date(chat.created_at)
