# Copyright (c) 2026 John Carter. All rights reserved.
"""Shared candidate pool + lexical ranking over AgentCore Memory (#274).

**One retrieval primitive, two callers.** ``recall`` (the #273 tool, in
``agents/tools/memory_tools.py``) and ``AgentCoreRecallHook`` (the
always-on hook, in ``agents/recall.py``) ask AgentCore the same question —
"what has this actor stored, and which of it looks related to *this*
text?" — so they run the same code rather than two implementations that
drift. #273 shipped the primitive; #274's design pass called for *sharing*
it rather than growing a second scorer, and extracting it here is what
makes that real. A copy is how the #532 line-terminator gap survived into
#544 one module over.

**Lexical, deliberately.** No vector index, no embedding call, no second
store. Phase 7d's ``SemanticMemoryStrategy`` was retired in 8a because its
async ingestion lag (hours in real use) left recall empty for far too
long; with raw-event reads the index *is* the event log, so there is no
freshness problem to tolerate. A semantic index remains a v2 optimisation
for BOTH surfaces at once, not a per-surface bolt-on.

**Padding is the one thing the two callers disagree about**, and that
disagreement is the behavioural core of #274 — see :func:`rank_candidates`.

Nothing here trusts what it reads. Candidate text is attacker-influenced
(a user, or a model quoting one, writes it), so every consumer is
responsible for its own framing: the hook defuses and fences it
(``recall._format_recall_addendum``), the tool returns it in the
tool-result register. Selecting *which* fragments to hand on is this
module's whole job; deciding what register they land in is not, and must
not become so — ADR-0011 (#533) puts trust class at the prompt-assembly
seam, never on the record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime as _dt
from datetime import timezone as _tz
from typing import Any

__all__ = [
    "MATCH_TEXT_LIMIT",
    "MAX_QUERY_SCAN_CHARS",
    "MAX_QUERY_TOKENS",
    "MIN_TOKEN_LEN",
    "POOL_EVENTS_PER_SESSION",
    "POOL_MAX_SESSIONS",
    "Candidate",
    "collect_candidates",
    "iso_date",
    "query_tokens",
    "rank_candidates",
    "role_label",
]

# --- Candidate pool caps ------------------------------------------------
#
# The read fan-out, shared by both callers. These were the ``recall``
# TOOL's caps (#273: 10 sessions x 5 events) rather than the hook's older
# 5 x 2, and the wider pool is now the shared one for a reason that is
# specific to relevance: ranking can only surface what it was given, so a
# pool sized for "what fits in the prompt" caps *relevance* at whatever
# recency happened to hand it. The prompt-size question is answered
# downstream instead, by ``recall._select_within_budget``, which is where
# it belongs. Pre-#274 the hook fetched exactly what it injected; those
# two jobs are now separate.
POOL_MAX_SESSIONS = 10
POOL_EVENTS_PER_SESSION = 5

# --- Lexical scoring parameters -----------------------------------------

#: Shortest query token considered for matching. Kept at #273's value
#: rather than raised: three-character tokens carry most of this
#: product's actual vocabulary (``mcp``, ``api``, ``jwt``, ``ssm``,
#: ``cdk``), so raising the floor would cost more signal than it saves.
MIN_TOKEN_LEN = 3

#: Function words dropped before matching. **Load-bearing for the hook,
#: harmless for the tool** — and the reason it exists at all is #274.
#:
#: #273's ranker saw a short, deliberate phrase ("billing preferences"),
#: where a length floor alone was enough. The hook sees a whole sentence,
#: and one ``the`` in it is a substring of very nearly every stored turn —
#: so "let's revisit the MCP spike" matched the paint chat, the billing
#: chat and everything else, and ``pad=False`` gated exactly nothing. A
#: gate that only holds for messages with no common words is not a gate.
#:
#: Deliberately small, and the bar for membership is functional rather
#: than grammatical: **a word belongs here only if it cannot be what a
#: conversation is about.** That admits the closed classes (articles,
#: pronouns, prepositions, auxiliaries, conjunctions) and conversational
#: filler, and it also admits the delexical verbs — ``make``, ``say``,
#: ``tell``, ``want``, ``need``, ``like``, ``get`` — which are open-class
#: but carry no topic in a query like "tell me about the migration". It
#: excludes anything that could be a subject: an earlier draft of this
#: list held ``new``, ``old``, ``morning``, ``night``, ``today`` and
#: ``yesterday``, and every one of them can be exactly what a chat was
#: about ("the new schema", "last night's incident"). A topic word
#: wrongly listed here makes that subject unrecallable forever and does
#: so silently, which is far worse than the false match the list exists
#: to prevent — so when in doubt, leave it out.
#:
#: Matching is substring-based, so short entries do most of the work;
#: longer ones ("about", "there") earn their place by being frequent
#: openers of exactly the "what did we say about X" phrasing this ranker
#: is for.
_STOP_WORDS: frozenset[str] = frozenset(
    [
        "the",
        "and",
        "are",
        "was",
        "were",
        "you",
        "your",
        "yours",
        "our",
        "ours",
        "their",
        "theirs",
        "its",
        "his",
        "her",
        "him",
        "she",
        "they",
        "them",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "then",
        "than",
        "with",
        "without",
        "for",
        "from",
        "into",
        "onto",
        "out",
        "off",
        "over",
        "under",
        "about",
        "above",
        "below",
        "but",
        "not",
        "nor",
        "yet",
        "still",
        "just",
        "only",
        "also",
        "very",
        "much",
        "many",
        "some",
        "any",
        "all",
        "can",
        "cant",
        "could",
        "would",
        "should",
        "will",
        "wont",
        "shall",
        "may",
        "might",
        "must",
        "have",
        "has",
        "had",
        "having",
        "been",
        "being",
        "does",
        "did",
        "doing",
        "done",
        "get",
        "got",
        "gets",
        "getting",
        "make",
        "makes",
        "made",
        "making",
        "say",
        "says",
        "said",
        "saying",
        "tell",
        "tells",
        "told",
        "telling",
        "want",
        "wants",
        "wanted",
        "need",
        "needs",
        "needed",
        "like",
        "likes",
        "liked",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "lets",
        "let",
        "one",
        "two",
        "now",
        "more",
        "most",
        "less",
        "least",
        "too",
        "yes",
        "yeah",
        "nope",
        "okay",
        "okey",
        "sure",
        "thanks",
        "thank",
        "please",
        "sorry",
        "hey",
        "hello",
        "im",
        "ive",
        "ill",
        "dont",
        "doesnt",
        "didnt",
        "isnt",
        "arent",
        "wasnt",
        "werent",
    ]
)

#: Ceiling on DISTINCT query tokens, and a hot-path bound rather than a
#: quality knob. :func:`rank_candidates` is O(tokens x candidates x text),
#: and unlike the tool — invoked deliberately, with a short phrase — the
#: hook runs on EVERY turn against whatever the user typed. A pasted
#: 100 KB message would otherwise contribute thousands of needles, each
#: scanned across the whole pool, on the critical path to first token.
#: 32 distinct words is far past the point where a lexical match adds
#: signal; beyond it the extra needles only add cost and false positives.
MAX_QUERY_TOKENS = 32

#: Ceiling on how much of the message is *scanned* for those tokens, and
#: the third leg of the same bound. :data:`MAX_QUERY_TOKENS` caps the
#: needles but not the walk that finds them: a message of pure
#: punctuation yields no tokens at all, so the early break never fires and
#: ``.lower()`` plus the regex sweep run over the whole thing. Measured at
#: **298 ms for a 5 MB paste** — linear, so not a backtracking bug, but
#: still a third of a second of dead time in front of first token on every
#: turn, which is exactly the kind of cost an always-on hook must not
#: have. Slicing first makes the whole function O(1) in message size.
#:
#: 16 KB is ~4 000 tokens of prose, far beyond the 32 distinct words that
#: survive anyway — so on any message a human actually typed this changes
#: nothing, and on a paste it only ignores text whose tokens would have
#: been discarded by the token cap regardless.
MAX_QUERY_SCAN_CHARS = 16384

#: Ceiling on the per-candidate haystack, the other half of the same
#: bound. A stored turn is a whole chat message and can be ~100 KB, and
#: the pool holds up to ``POOL_MAX_SESSIONS x POOL_EVENTS_PER_SESSION``
#: events (two payload entries each), so an unbounded haystack is ~10 MB
#: rescanned per token per turn. The two caps together bound the scan at
#: ``MAX_QUERY_TOKENS x pool x MATCH_TEXT_LIMIT`` characters — a few
#: milliseconds, and independent of what anyone types or stores.
#:
#: Generous relative to what a match can actually yield: the hook
#: truncates a quoted fragment to 120 characters, so matching over the
#: first 4 096 is already ~34x more text than the reader will ever see.
MATCH_TEXT_LIMIT = 4096

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Sort-key sentinel for a session summary with no ``createdAt``.
#: Timezone-aware so it compares against boto3's datetimes.
_EPOCH = _dt(1970, 1, 1, tzinfo=_tz.utc)

#: AgentCore role -> the second-person label both surfaces render.
_ROLE_LABELS: dict[str, str] = {"USER": "You", "ASSISTANT": "Me"}


def role_label(raw_role: Any) -> str:
    """Display label for an AgentCore conversational role.

    An unrecognised role passes through as itself rather than being
    dropped or renamed — a wire change should be visible, not silently
    relabelled ``Me``. An empty/absent role degrades to ``?``.
    """
    return _ROLE_LABELS.get(str(raw_role or ""), str(raw_role or "") or "?")


def iso_date(value: Any) -> str:
    """Return ``YYYY-MM-DD`` for a datetime or string; ``""`` for None.

    boto3 deserializes AgentCore ``createdAt`` fields as
    ``datetime.datetime``. Normalising at the API boundary keeps datetime
    handling out of every downstream formatter.

    Lives here rather than in ``recall.py`` (its pre-#274 home) purely for
    import direction: ``recall`` and ``memory_tools`` both import this
    module, and ``memory_tools`` used to reach into ``recall`` for exactly
    this function.
    """
    if value is None:
        return ""
    if hasattr(value, "strftime"):  # datetime.datetime or datetime.date
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


@dataclass(frozen=True)
class Candidate:
    """One quoted turn from the actor's stored memory.

    ``text`` is the FULL stored text — truncation is a rendering decision
    each consumer makes with its own cap, so this carries the bytes and
    lets them choose.

    ``match_text`` is the lowercased, length-capped view
    :func:`rank_candidates` scans. Precomputed at collection time on
    purpose: the hook caches its pool for ``_RECALL_CACHE_REFRESH_TURNS``
    turns but re-ranks on every one of them, so lowercasing here is paid
    once per fetch instead of once per turn.

    ``order`` is the candidate's position in the collected pool
    (newest-session-first, chronological within a session). Ranking
    reorders by relevance; a consumer that needs the original reading
    order back — the hook regroups quoted turns per session — sorts on
    this rather than re-deriving it from timestamps the pool may not
    carry.

    ``event_index`` identifies the AgentCore **event** a turn came from,
    unique across the whole pool. One event is the ``messages[-2:]``
    user+assistant pair the write hook stores atomically, so it — not the
    individual turn — has always been this system's unit of a recalled
    fragment (the pre-#274 caps were counted in events). Matching happens
    per turn because that is where the words are; the hook re-widens a
    match back to its event, so recalling "what did we say about X"
    surfaces the answer and not just the question.
    """

    session_id: str
    date: str
    role: str
    text: str
    match_text: str
    order: int
    event_index: int


def collect_candidates(client: Any, memory_id: str, actor_id: str) -> list[Candidate]:
    """``ListSessions`` + per-session ``ListEvents`` -> a flat candidate pool.

    Newest session first, chronological within each session. Excludes
    nothing: the pool is deliberately **chat-independent** so the hook can
    cache one pool per actor and apply its current-chat exclusion at rank
    time (#274 design decision 6) — a warm Lambda then stops re-fetching
    the same events once per chat. The tool never excluded anything to
    begin with; a deliberate search wants everything the actor has.

    Blocking boto3 calls, one ``ListEvents`` per session. Callers dispatch
    the whole function through :func:`asyncio.to_thread`.

    Defensive throughout — a session with no id, an event with no payload,
    a payload entry with no text are each skipped rather than raised on, so
    a malformed AgentCore response degrades the pool instead of breaking a
    chat turn.
    """
    sessions_resp = client.list_sessions(memoryId=memory_id, actorId=actor_id)
    sessions = list(sessions_resp.get("sessionSummaries", []))
    # Explicit newest-first ordering; don't rely on AgentCore's default.
    sessions.sort(key=lambda s: s.get("createdAt") or _EPOCH, reverse=True)
    sessions = sessions[:POOL_MAX_SESSIONS]

    candidates: list[Candidate] = []
    event_index = 0
    for session in sessions:
        sid = session.get("sessionId")
        if not sid:
            continue
        events_resp = client.list_events(
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=sid,
            maxResults=POOL_EVENTS_PER_SESSION,
        )
        date = iso_date(session.get("createdAt"))
        # ListEvents returns newest-first; reverse for chronological read.
        for event in reversed(events_resp.get("events", [])):
            event_index += 1
            for msg in event.get("payload", []):
                conv = msg.get("conversational") or {}
                text = (conv.get("content") or {}).get("text", "")
                if not text:
                    continue
                candidates.append(
                    Candidate(
                        session_id=str(sid),
                        date=date,
                        role=str(conv.get("role", "")),
                        text=text,
                        match_text=text[:MATCH_TEXT_LIMIT].lower(),
                        order=len(candidates),
                        event_index=event_index,
                    )
                )
    return candidates


def query_tokens(query: str) -> list[str]:
    """Distinct lowercased alphanumeric content tokens from a message.

    Length >= :data:`MIN_TOKEN_LEN` and not in :data:`_STOP_WORDS`; first-seen
    order, capped at :data:`MAX_QUERY_TOKENS`. The order is deterministic
    rather than set-ordered, so the same message always produces the same
    ranking — with a ``set``, two candidates matching different tokens
    would tie-break differently between runs.

    Two bounds, because one is not enough. The token cap is why this is a
    lazy ``finditer`` walk with an early break instead of ``findall`` — a
    pasted megabyte of prose stops being scanned once 32 distinct content
    words have been seen. :data:`MAX_QUERY_SCAN_CHARS` covers the case
    that break cannot: a message that yields *few or no* tokens (pure
    punctuation, one enormous word, a base64 blob) never trips the break
    and would otherwise be lowercased and swept in full.

    An empty result means "no relevance signal", which the hook reads as
    *inject nothing* and the tool reads as *fall back to recency* — see
    :func:`rank_candidates`.
    """
    seen: dict[str, None] = {}
    for match in _TOKEN_RE.finditer(query[:MAX_QUERY_SCAN_CHARS].lower()):
        token = match.group()
        if len(token) < MIN_TOKEN_LEN or token in _STOP_WORDS or token in seen:
            continue
        seen[token] = None
        if len(seen) >= MAX_QUERY_TOKENS:
            break
    return list(seen)


def rank_candidates(
    candidates: list[Candidate],
    query: str,
    *,
    limit: int | None = None,
    pad: bool = True,
) -> list[Candidate]:
    """Float keyword matches to the top; optionally back-fill with recency.

    A candidate matches when any :func:`query_tokens` token is a substring
    of its (lowercased, capped) text. Matches keep their pool order —
    which is recency — and precede non-matches. ``limit`` caps the result;
    ``None`` means the caller bounds it some other way (the hook uses a
    token budget).

    **``pad`` is the behavioural core of #274.** With ``pad=True`` a query
    that matches nothing still returns the most recent candidates, which is
    right for a *deliberate* ``recall`` tool call: the model asked, so hand
    it something to judge. It is exactly wrong for an always-on hook, and
    it is the mechanism by which "morning" used to pull five unrelated
    chats into the system prompt every turn. With ``pad=False`` no match
    means an empty result, so the hook injects nothing at all — the wish
    as written in #274, and a strict reduction of the untrusted-data
    surface #533 governs rather than any change to it.

    A query with no usable tokens ("hi", "ok", "??") carries no relevance
    signal whatsoever, so it takes the same two branches: recency for the
    tool, nothing for the hook. Note ``pad=False`` cannot fall back to
    recency here even in principle — "no signal" and "signal, no match"
    must both mean "inject nothing", or the gate leaks on the shortest
    messages, which are precisely the low-relevance ones.

    Cost is bounded by construction — see :data:`MAX_QUERY_TOKENS` and
    :data:`MATCH_TEXT_LIMIT`. One pass over the pool, one ``in`` scan per
    (token, candidate); no regex over candidate text, no fixpoint, nothing
    quadratic in either input.

    **Known v1 limitation** (recorded in #273, unchanged by #274): this is
    keyword/recency, not semantics. A paraphrase misses ("the spike" vs
    "#207"), and a common word can match something unrelated. The hook
    reads a miss as "inject nothing", so the failure is a quiet loss of
    continuity rather than a wrong injection.
    """
    tokens = query_tokens(query)
    if not tokens:
        return list(candidates[:limit]) if pad else []
    matched: list[Candidate] = []
    unmatched: list[Candidate] = []
    for candidate in candidates:
        text = candidate.match_text
        if any(token in text for token in tokens):
            matched.append(candidate)
        else:
            unmatched.append(candidate)
    if not pad:
        return matched[:limit]
    return (matched + unmatched)[:limit]
