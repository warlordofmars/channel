# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the shared memory candidate pool + lexical ranker (#274).

This is the primitive #273 shipped inside ``memory_tools`` and #274
extracted so the always-on hook and the deliberate ``recall`` tool run one
implementation. The tests that matter most here are the ones about
``pad``: it is the single flag separating the two surfaces, and getting it
wrong on the hook side is precisely the "morning injects five unrelated
chats" behaviour #274 exists to remove.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from channel.agents.memory_ranking import (
    MATCH_TEXT_LIMIT,
    MAX_QUERY_SCAN_CHARS,
    MAX_QUERY_TOKENS,
    MIN_TOKEN_LEN,
    POOL_EVENTS_PER_SESSION,
    POOL_MAX_SESSIONS,
    Candidate,
    collect_candidates,
    iso_date,
    query_tokens,
    rank_candidates,
    role_label,
)


def _events_client(sessions, events_by_session):
    """MagicMock AgentCore client returning canned sessions/events."""
    client = MagicMock()
    client.list_sessions.return_value = {"sessionSummaries": sessions}

    def _list_events(*, memoryId, actorId, sessionId, maxResults):
        return {"events": events_by_session.get(sessionId, [])}

    client.list_events.side_effect = _list_events
    return client


def _conv(role, text):
    return {"conversational": {"role": role, "content": {"text": text}}}


def _cand(text, *, session_id="s-1", date="2026-06-14", role="USER", order=0):
    return Candidate(
        session_id=session_id,
        date=date,
        role=role,
        text=text,
        match_text=text[:MATCH_TEXT_LIMIT].lower(),
        order=order,
        event_index=order,
    )


# ---------------------------------------------------------------------------
# role_label / iso_date — the two helpers that moved here for import direction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("USER", "You"),
        ("ASSISTANT", "Me"),
        ("ROBOT", "ROBOT"),  # unknown role passes through, never relabelled
        ("", "?"),
        (None, "?"),
    ],
)
def test_role_label_shapes(raw, expected):
    assert role_label(raw) == expected


def test_iso_date_normalizes_datetime_and_string_and_none():
    assert iso_date(datetime(2026, 6, 14, 9, 30, tzinfo=timezone.utc)) == "2026-06-14"
    assert iso_date("2026-06-14T09:30:00Z") == "2026-06-14"
    assert iso_date(None) == ""


# ---------------------------------------------------------------------------
# collect_candidates — the shared read fan-out
# ---------------------------------------------------------------------------


def test_collect_candidates_orders_sessions_and_reverses_events():
    """Newest session first; events (ListEvents returns newest-first) are
    reversed to chronological; sessions without an id are skipped; payload
    entries without text are skipped; the RAW role is carried, not a label."""
    sessions = [
        {"sessionId": "s-old", "createdAt": datetime(2026, 6, 10, tzinfo=timezone.utc)},
        {"sessionId": "s-new", "createdAt": datetime(2026, 6, 14, tzinfo=timezone.utc)},
        {"createdAt": datetime(2026, 6, 12, tzinfo=timezone.utc)},  # no sessionId → skip
    ]
    events_by_session = {
        "s-new": [
            # ListEvents newest-first: this is the most recent event.
            {"payload": [_conv("ASSISTANT", "second")]},
            {
                "payload": [
                    _conv("USER", "first"),
                    _conv("ROBOT", "odd role"),
                    {"conversational": {"role": "USER", "content": {}}},  # no text → skip
                ]
            },
        ],
        "s-old": [{"payload": [_conv("USER", "old chat")]}],
    }
    client = _events_client(sessions, events_by_session)

    candidates = collect_candidates(client, "mem-1", "actor-1")

    assert [(c.session_id, c.date, c.role, c.text) for c in candidates] == [
        ("s-new", "2026-06-14", "USER", "first"),
        ("s-new", "2026-06-14", "ROBOT", "odd role"),
        ("s-new", "2026-06-14", "ASSISTANT", "second"),
        ("s-old", "2026-06-10", "USER", "old chat"),
    ]
    # ``order`` is the pool position, which is what the hook sorts on to
    # restore chronological reading order within a session group.
    assert [c.order for c in candidates] == [0, 1, 2, 3]
    # ``event_index`` groups the turns the write hook stored atomically —
    # the two turns from s-new's older event share one, the newer event
    # and the s-old event each get their own, and indices never repeat
    # across sessions.
    assert [c.event_index for c in candidates] == [1, 1, 2, 3]
    # Read scoping is passed through to AgentCore verbatim.
    client.list_sessions.assert_called_once_with(memoryId="mem-1", actorId="actor-1")


def test_collect_candidates_degrades_an_explicit_null_role_to_unknown():
    """``{"role": None}`` must reach ``role_label`` as empty, not ``"None"``.

    A ``get`` default only fires on a MISSING key, so an explicit null
    passes straight through to ``str()`` and becomes the truthy string
    ``"None"`` — rendering ``- None: ...`` in the recall block. Both
    pre-#274 call sites degraded it to ``?`` and the extraction lost that;
    this pins it back. Same trap the session-date handling in
    ``recall._format_recall_addendum`` already documents.
    """
    client = _events_client(
        [{"sessionId": "s", "createdAt": datetime(2026, 6, 14, tzinfo=timezone.utc)}],
        {"s": [{"payload": [{"conversational": {"role": None, "content": {"text": "hi"}}}]}]},
    )

    (candidate,) = collect_candidates(client, "mem-1", "actor-1")

    assert candidate.role == ""
    assert role_label(candidate.role) == "?"


def test_collect_candidates_precomputes_lowercased_capped_match_text():
    """``match_text`` is lowercased and capped at collection time.

    Not an optimisation detail: the hook re-ranks the SAME cached pool on
    every turn, so lowercasing here is paid once per fetch rather than once
    per turn, and the cap is what bounds the per-turn scan (see the
    adversarial timing tests below).
    """
    long_text = "A" * (MATCH_TEXT_LIMIT + 500) + "NEEDLE"
    client = _events_client(
        [{"sessionId": "s", "createdAt": datetime(2026, 6, 14, tzinfo=timezone.utc)}],
        {"s": [{"payload": [_conv("USER", long_text)]}]},
    )

    (candidate,) = collect_candidates(client, "mem-1", "actor-1")

    assert candidate.text == long_text, "the full stored bytes are carried"
    assert candidate.match_text == "a" * MATCH_TEXT_LIMIT
    assert "needle" not in candidate.match_text


def test_collect_candidates_caps_the_session_and_event_fan_out():
    sessions = [
        {"sessionId": f"s-{i:02d}", "createdAt": datetime(2026, 6, i + 1, tzinfo=timezone.utc)}
        for i in range(POOL_MAX_SESSIONS + 4)
    ]
    client = _events_client(sessions, {})

    collect_candidates(client, "mem-1", "actor-1")

    assert client.list_events.call_count == POOL_MAX_SESSIONS
    for call in client.list_events.call_args_list:
        assert call.kwargs["maxResults"] == POOL_EVENTS_PER_SESSION
    # Newest-first: the oldest 4 sessions are the ones dropped.
    asked = {call.kwargs["sessionId"] for call in client.list_events.call_args_list}
    assert "s-00" not in asked
    assert f"s-{POOL_MAX_SESSIONS + 3:02d}" in asked


def test_collect_candidates_sorts_sessions_missing_created_at_last():
    """A session with no ``createdAt`` sorts to the epoch, never crashes."""
    sessions = [
        {"sessionId": "s-undated"},
        {"sessionId": "s-dated", "createdAt": datetime(2026, 6, 14, tzinfo=timezone.utc)},
    ]
    client = _events_client(
        sessions,
        {
            "s-undated": [{"payload": [_conv("USER", "undated")]}],
            "s-dated": [{"payload": [_conv("USER", "dated")]}],
        },
    )

    assert [c.text for c in collect_candidates(client, "m", "a")] == ["dated", "undated"]


def test_collect_candidates_empty_when_no_sessions():
    assert collect_candidates(_events_client([], {}), "mem-1", "actor-1") == []


# ---------------------------------------------------------------------------
# query_tokens
# ---------------------------------------------------------------------------


def test_query_tokens_lowercases_dedupes_and_drops_noise():
    assert query_tokens("Let's revisit the MCP Spike, the SPIKE") == [
        "revisit",
        "mcp",
        "spike",
    ]


def test_query_tokens_drops_everything_below_the_minimum_length():
    assert query_tokens("a of is I8") == []
    assert len("of") < MIN_TOKEN_LEN


def test_query_tokens_drops_function_words_that_would_match_anything():
    """**The gate's other half, and the one that is easy to miss.**

    ``pad=False`` only gates if the token set is discriminating. Left in,
    a single ``the`` is a substring of very nearly every stored turn, so
    "let's revisit the MCP spike" matched the paint chat and the billing
    chat too and the gate admitted everything — a leak invisible to any
    test whose fixture happens to avoid common words.
    """
    assert query_tokens("what did we say about the deploy") == ["deploy"]
    assert query_tokens("could you tell me what we decided") == ["decided"]


def test_query_tokens_returns_nothing_for_an_all_function_word_message():
    """A pure-filler turn carries no signal at all, so ``pad=False``
    injects nothing.

    Note this is NOT the path #274's headline "morning" case takes — see
    :func:`test_morning_is_a_real_token_so_the_gate_is_tested_on_the_match_path`.
    """
    assert query_tokens("hey there, how are you doing") == []


def test_morning_is_a_real_token_so_the_gate_is_tested_on_the_match_path():
    """Guards the headline acceptance test against silently going hollow.

    "morning" is #274's own example of a low-relevance turn, and the
    honest reason it injects nothing is that it *matches nothing* — not
    that it tokenises to nothing. An earlier draft of ``_STOP_WORDS`` held
    ``morning``, which routed the whole acceptance test down the
    no-usable-tokens branch and left the branch it was written to exercise
    untested. Putting it back would do so again, silently, so this pins
    the token as real.
    """
    assert query_tokens("morning") == ["morning"]


def test_stop_words_hold_no_topical_vocabulary():
    """A topic word wrongly listed would make that subject permanently
    unrecallable — a silent, total failure, and far worse than the false
    match the list exists to prevent.

    Two groups, and the second is the one that caught a real defect. The
    first is the project's own vocabulary, which a careless addition would
    obviously hit. The second is the words that *look* like filler and are
    not: an earlier draft listed ``new``, ``old`` and the whole
    time-of-day / day-relative set, every one of which can be exactly what
    a chat was about — "the new schema", "last night's incident",
    "today's deploy".
    """
    from channel.agents.memory_ranking import _STOP_WORDS

    for word in ("mcp", "api", "spike", "deploy", "memory", "recall", "sage", "billing"):
        assert word not in _STOP_WORDS
    for word in (
        "new",
        "old",
        "morning",
        "afternoon",
        "evening",
        "night",
        "today",
        "tomorrow",
        "yesterday",
    ):
        assert word not in _STOP_WORDS


def _stop_word_source_entries() -> list[str]:
    """The literal ``_STOP_WORDS`` entries, in source order, via the AST.

    Read from source rather than from the frozenset because the frozenset
    is exactly what would swallow the duplication being checked for.

    Parsed with :mod:`ast` rather than by slicing the source between
    delimiters. A first draft did the latter — ``.split(")", 1)[0]`` after
    the assignment anchor — and it **failed open**: any future edit
    putting a paren inside the list body (an ordinary inline comment, the
    style this module uses everywhere else) truncates the block early, and
    an empty parse makes the dedup assert pass vacuously. Verified, not
    theorised: adding ``# articles (see #274)`` above the first entry and
    re-duplicating ``was``/``were`` in the same edit left the whole suite
    green. A guard that can be silently disarmed by a comment is worse
    than no guard, because it is also read as coverage.
    """
    import ast
    from pathlib import Path

    import channel.agents.memory_ranking as mr

    tree = ast.parse(Path(mr.__file__).read_text())
    for node in ast.walk(tree):
        target = getattr(node, "target", None)
        if isinstance(node, ast.AnnAssign) and getattr(target, "id", "") == "_STOP_WORDS":
            call = node.value
            assert isinstance(call, ast.Call), "_STOP_WORDS is no longer a frozenset(...) call"
            (literal,) = call.args
            assert isinstance(literal, ast.List), "_STOP_WORDS no longer wraps a list literal"
            return [ast.literal_eval(element) for element in literal.elts]
    raise AssertionError("no _STOP_WORDS annotated assignment found")


def test_stop_words_are_deduplicated():
    """The list's curation is load-bearing, so a dead entry is not inert —
    it is a line a future reader has to decide about twice.

    The count comparison doubles as the non-vacuity check: a parse that
    found nothing reads as 0 entries against a populated frozenset and
    fails loudly, rather than reporting "no duplicates found".
    """
    from channel.agents.memory_ranking import _STOP_WORDS

    entries = _stop_word_source_entries()
    assert len(entries) == len(_STOP_WORDS), sorted(
        word for word in set(entries) if entries.count(word) > 1
    )


def test_query_tokens_preserves_first_seen_order_not_set_order():
    """Deterministic ranking needs a deterministic token order.

    A ``set`` would make the token order hash-dependent, which is invisible
    until two tokens both match and the tie-break flips between runs.
    """
    assert query_tokens("zebra apple zebra mango") == ["zebra", "apple", "mango"]


def test_query_tokens_caps_the_needle_count():
    """The hot-path bound on how many needles reach the pool scan.

    Distinct from the scan-chars ceiling below: this one bounds the
    *matching* cost (needles x candidates x haystack), that one bounds the
    cost of finding the needles at all.
    """
    query = " ".join(f"word{i:04d}" for i in range(MAX_QUERY_TOKENS * 10))
    tokens = query_tokens(query)
    assert len(tokens) == MAX_QUERY_TOKENS
    # First-seen order, so the cap keeps the FRONT of the message — the
    # part a user actually wrote before any pasted bulk.
    assert tokens[0] == "word0000"


# ---------------------------------------------------------------------------
# rank_candidates — matching, and the pad flag that is #274's whole point
# ---------------------------------------------------------------------------


def test_rank_candidates_matches_are_substring_and_keep_pool_order():
    candidates = [
        _cand("nothing to see", order=0),
        _cand("the MCP spikes were fun", order=1),
        _cand("unrelated", order=2),
        _cand("another spike note", order=3),
    ]
    ranked = rank_candidates(candidates, "spike", pad=False)
    assert [c.order for c in ranked] == [1, 3]


def test_rank_candidates_matching_is_case_insensitive_both_ways():
    assert rank_candidates([_cand("The MCP Spike")], "SPIKE", pad=False)


def test_rank_candidates_pad_false_returns_nothing_when_nothing_matches():
    """**The behavioural core of #274.**

    An off-topic turn must inject NOTHING — not a shorter block, not the
    most recent chats, nothing. Asserted as an empty result rather than a
    length comparison, because "shorter" was already true of the old
    behaviour on a small pool and would pass a broken gate.
    """
    candidates = [_cand("sage green paint", order=0), _cand("the deploy script", order=1)]
    assert rank_candidates(candidates, "morning", pad=False) == []


def test_rank_candidates_pad_true_falls_back_to_recency_when_nothing_matches():
    candidates = [_cand("sage green paint", order=0), _cand("the deploy script", order=1)]
    assert rank_candidates(candidates, "morning", pad=True) == candidates


def test_rank_candidates_pad_false_returns_nothing_for_an_unusable_query():
    """ "hi" / "ok" / "??" carry no signal, and the gate must read that as
    "inject nothing" rather than falling back to recency.

    Falling back would leak the gate on the SHORTEST messages, which are
    exactly the low-relevance ones — the failure would be invisible in the
    happy-path tests and total in practice.
    """
    candidates = [_cand("sage green paint", order=0)]
    assert rank_candidates(candidates, "hi ok ??", pad=False) == []
    assert rank_candidates(candidates, "", pad=False) == []
    assert rank_candidates(candidates, "hi ok ??", pad=True) == candidates


def test_rank_candidates_pad_true_puts_matches_first_then_the_rest():
    candidates = [_cand("a", order=0), _cand("spike", order=1), _cand("b", order=2)]
    assert [c.order for c in rank_candidates(candidates, "spike", pad=True)] == [1, 0, 2]


def test_rank_candidates_limit_caps_both_branches():
    candidates = [_cand(f"spike {i}", order=i) for i in range(10)]
    assert len(rank_candidates(candidates, "spike", limit=3, pad=False)) == 3
    assert len(rank_candidates(candidates, "nomatchhere", limit=3, pad=True)) == 3
    assert len(rank_candidates(candidates, "", limit=3, pad=True)) == 3


def test_rank_candidates_limit_none_means_uncapped():
    candidates = [_cand(f"spike {i}", order=i) for i in range(10)]
    assert len(rank_candidates(candidates, "spike", pad=False)) == 10
    assert len(rank_candidates(candidates, "", pad=True)) == 10


def test_rank_candidates_matches_only_the_capped_haystack():
    """A needle past ``MATCH_TEXT_LIMIT`` does not match — deliberately.

    The cap is a hot-path bound (see the timing test below), and this pins
    its behavioural cost so it can never be "tidied away" as an
    optimisation with no observable effect.
    """
    text = "A" * (MATCH_TEXT_LIMIT + 10) + "needle"
    assert rank_candidates([_cand(text)], "needle", pad=False) == []


# ---------------------------------------------------------------------------
# Hot path — the hook runs this on EVERY turn, in front of first token
# ---------------------------------------------------------------------------


def _full_pool(text_len):
    """The worst-case pool shape: every session, every event, two turns each."""
    return [
        _cand("x" * text_len, session_id=f"s-{i}", order=i)
        for i in range(POOL_MAX_SESSIONS * POOL_EVENTS_PER_SESSION * 2)
    ]


def test_ranking_a_full_pool_against_an_adversarial_query_is_fast():
    """A pasted megabyte must not turn ranking into a visible stall.

    Without ``MAX_QUERY_TOKENS`` this is O(distinct words x pool x text):
    ~200k needles over a 200-candidate pool. The cap makes the cost a
    constant regardless of what anyone types.
    """
    pool = _full_pool(MATCH_TEXT_LIMIT)
    query = " ".join(f"needle{i:06d}" for i in range(200_000))

    started = time.perf_counter()
    ranked = rank_candidates(pool, query, pad=False)
    elapsed = time.perf_counter() - started

    assert ranked == []
    assert elapsed < 1.0, f"ranking took {elapsed:.3f}s"


def test_ranking_is_bounded_by_the_haystack_cap_not_the_stored_text():
    """A pool of 100 KB turns costs the same as a pool of 4 KB ones.

    ``match_text`` is capped at collection time, so an actor who pastes
    huge messages cannot make every subsequent turn slower.
    """
    huge = [
        Candidate(
            session_id=f"s-{i}",
            date="2026-06-14",
            role="USER",
            text="x" * 100_000,
            match_text=("x" * 100_000)[:MATCH_TEXT_LIMIT],
            order=i,
            event_index=i,
        )
        for i in range(POOL_MAX_SESSIONS * POOL_EVENTS_PER_SESSION * 2)
    ]
    query = " ".join(f"needle{i:04d}" for i in range(MAX_QUERY_TOKENS))

    started = time.perf_counter()
    rank_candidates(huge, query, pad=False)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5, f"ranking took {elapsed:.3f}s"


def test_query_tokenisation_is_constant_time_in_the_message_size():
    """Both query bounds, on the input that needs both.

    A message of pure punctuation yields no tokens, so ``MAX_QUERY_TOKENS``
    never trips its early break — without ``MAX_QUERY_SCAN_CHARS`` this
    lowercases and sweeps the whole 5 MB, measured at ~300 ms of dead time
    in front of first token on every turn.
    """
    query = ("abc " * 5) + ("!@#$%^&*()" * 500_000)

    started = time.perf_counter()
    tokens = query_tokens(query)
    elapsed = time.perf_counter() - started

    assert tokens == ["abc"]
    assert elapsed < 0.05, f"tokenisation took {elapsed:.3f}s"


def test_query_tokens_ignores_the_message_past_the_scan_ceiling():
    """The behavioural cost of the scan bound, pinned so it cannot be
    quietly removed as a no-op optimisation.

    Only reachable on a paste far past anything a human types, and only
    for tokens the ``MAX_QUERY_TOKENS`` cap would have discarded anyway.
    """
    query = ("!" * MAX_QUERY_SCAN_CHARS) + " needle"
    assert query_tokens(query) == []
    # The same word inside the ceiling is found, so the empty result above
    # is the bound doing its job rather than the tokeniser being broken.
    assert query_tokens("needle " + query) == ["needle"]
