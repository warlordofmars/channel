# Auto-titler salvage on `MaxTokensReachedException` — design

**Date:** 2026-06-01
**Status:** Approved (ready for plan)
**Closes:** #90

## Summary

Phase 7d's auto-titler hits `strands.types.exceptions.MaxTokensReachedException` for some first turns — the model's Haiku titler exceeds its `max_tokens=60` budget (typically when it emits preamble like `"Here's a 3-6 word title: ..."`). The current handler catches the exception with a generic `except Exception` and discards `title_chunks` entirely, so the chat is stuck on `"New chat"` in the sidebar even though Strands had streamed a usable partial title via `delta` events before raising.

Fix: catch `MaxTokensReachedException` specifically, post-process the already-accumulated `title_chunks` (strip colon-prefixed preamble, cap at 6 words), and persist the salvaged title if non-empty.

## Goals

- The auto-title appears in the sidebar for first chats that currently fall back to `"New chat"` due to `MaxTokensReachedException`.
- Genuinely-failed titler calls (network errors, empty output, preamble-only truncation) still record `AutoTitleFailures` and leave the chat as `"New chat"` — no false-positive titles like `"Here's a"`.
- No regression for the happy path where the titler completes within budget.

## Non-goals

- Restructuring `_TITLER_SYSTEM_PROMPT` to coerce a bare-title response (separate fix shape, not chosen).
- Switching `_DEFAULT_TITLER_MODEL` to Sonnet (separate fix shape, not chosen).
- Bumping `_TITLER_MAX_TOKENS` further (already 60; salvage handles the overshoot).
- Surfacing a "title was truncated" hint in the UI.
- Any frontend or infra change — pure backend, contained to one file.

## Architecture

```
                                    ┌──────────────────────────────┐
   _stream_bedrock_reply             │ titler.stream_async(prompt)  │
   ┌────────────────────────────┐    │  emits N delta events        │
   │ try:                       │    │  then raises MaxTokens…      │
   │   try:                     │◀───┘ Exception (or completes)
   │     async for evt in titler│
   │     stream_async(prompt):  │
   │       title_chunks.append( │
   │         delta_text)        │
   │   except MaxTokensReached  │  ◀── NEW: catch + flag truncated
   │     truncated = True       │       title_chunks still has data
   │                            │
   │   title = _postprocess(    │  ◀── NEW: helper trims preamble,
   │     "".join(title_chunks)) │       caps at 6 words
   │                            │
   │   if title:                │
   │     persist + SSE +        │
   │     metric=Successes       │
   │   else:                    │
   │     metric=Failures        │
   │ except Exception:          │
   │   log + metric=Failures    │
   └────────────────────────────┘
```

## Component changes

### `src/channel/api/chats.py`

- Add `from strands.types.exceptions import MaxTokensReachedException` to the existing import block.
- Add a new module-level helper `_postprocess_title(raw: str) -> str`:
  - If `raw` empty: return `""`.
  - If `:` in raw: take everything after the LAST colon (strips `"Here's a title:"` style preamble).
  - Strip whitespace, leading/trailing `"` and `'` and any wrapping ASCII quote chars.
  - Split on whitespace, cap to 6 words, rejoin with single spaces.
  - Strip trailing sentence punctuation (`.`, `,`, `;`, `:`, `!`, `?`).
  - Return the cleaned string (may be empty if input was preamble-only).
- Restructure the titler block in `_stream_bedrock_reply`:
  - Inner `try` wraps `async for event in titler.stream_async(...)`.
  - Inner `except MaxTokensReachedException` sets a local `truncated = True` flag and falls through to title extraction (does NOT re-raise).
  - Outer `try/except Exception` keeps the existing fail-soft pattern for non-token errors (network failures, agent build errors, etc.).
  - Title extraction goes through the new `_postprocess_title` helper unconditionally.
  - Empty post-processed title → record failure outcome (no SSE emitted).
  - Non-empty title → persist via `storage.patch_chat`, emit `sse_title_suggested`, record success.
- The `truncated` flag is included in the existing `auto_title_failed` warning's `extra` dict when the outer exception fires, for log-side observability.

### `tests/unit/test_chats_api.py`

Six new tests:

**`_postprocess_title` pure function** (three small tests):
1. `_postprocess_title("Debug pytest fixture")` → `"Debug pytest fixture"` (pass-through).
2. `_postprocess_title("Here's a 3-6 word title: Debug pytest fixture")` → `"Debug pytest fixture"` (preamble stripped).
3. `_postprocess_title("")` → `""` (empty in → empty out).
4. `_postprocess_title("One two three four five six seven")` → `"One two three four five six"` (6-word cap).
5. `_postprocess_title('"Debug pytest fixture"')` → `"Debug pytest fixture"` (quotes stripped).
6. `_postprocess_title("Here's a title:")` → `""` (preamble-only → empty).

**Streaming behavior** (three integration-shape tests using the existing `_stream_bedrock_reply` test harness from Phase 7d):
1. Titler completes normally with output `"Debug pytest fixture"` → `sse_title_suggested` emitted with that title; `record_auto_title_outcome(success=True)` awaited.
2. Titler emits `"Here's a title: Debug pytest fixture"` then raises `MaxTokensReachedException` mid-stream → SSE emitted with salvaged `"Debug pytest fixture"`; success metric.
3. Titler raises `MaxTokensReachedException` with empty `title_chunks` → NO SSE; `AutoTitleFailures` metric.
4. Titler raises an unrelated `RuntimeError` → NO SSE; failure metric; goes through outer `except`.

The existing happy-path test from Phase 7d (`test_auto_title_*` if present) continues to pass.

## Behavior + edge cases

- **Preamble + clean title, no truncation** — passes through colon split, returns the clean title.
- **Preamble + truncated title** — passes through colon split, returns however much of the title made it past the colon (e.g. `"Here's a title: Debug pytest fix"` → `"Debug pytest fix"`). Imperfect but recognizable; better than `"New chat"`.
- **No preamble, truncated** — no colon present; takes all words, caps at 6. Last word may be partial; acceptable (`"Debug pytest fixt"` is still better than `"New chat"`).
- **Preamble-only truncation** — colon present but nothing after → empty post-processed title → failure path. The user keeps `"New chat"` rather than seeing the preamble as a title.
- **Existing happy path (no max-tokens)** — `_postprocess_title` is a no-op when there's no colon and the title is ≤6 words.

## Failure mode

Unchanged from Phase 7d: any uncaught exception in the titler block falls through to the outer `except Exception` → log + EMF counter (`AutoTitleFailures`) + swallow. The user's primary chat stream has already completed and SSE'd `done` before the titler block runs; titler failures cannot break chats.

## Validation

### Layer 1 — Unit (CI gate)

`uv run pytest tests/unit/test_chats_api.py -k "title"` covers the new tests; coverage on `_postprocess_title` is 100% via the six pure-function cases; the streaming-shape tests exercise the new try/except branch in `_stream_bedrock_reply`. Project-wide 100% gate enforces the rest.

### Layer 2 — Local end-to-end (Playwright)

The existing `tests/e2e/test_memory_recall_and_titling.py::test_auto_title_fires_on_first_round_trip_only` is the regression gate. Before this fix, that test was flaky (the `inv e2e-local` runs during Phase 8a's dry-run showed it failing). After this fix:
- The titler's "Help me debug a flaky pytest fixture that uses tmp_path." prompt should produce a salvageable title even if Haiku preamble-bursts.
- 3 consecutive `inv e2e-local --n 3` runs must pass.

### Layer 3 — Dev verification (after deploy)

Start a fresh chat on `https://channel-dev.warlordofmars.net/app` with a deliberately verbose first message ("Help me debug something complicated about pytest fixtures and tmp_path behavior"). After the first assistant reply, the sidebar title must change from `"New chat"` to a 3-6 word summary. Repeat 3-5 times with varied prompts; observe the rate of `"New chat"` survivors drops from current baseline (~50%) to ~0%.

## CHANGELOG entry (draft)

```
### Fixed

- Auto-titler now salvages partial output when the Haiku titler trips
  `MaxTokensReachedException`. Previously the whole title was
  discarded and the chat stayed on "New chat" in the sidebar. The
  salvage path strips colon-prefixed preamble (e.g. "Here's a title:
  ...") and caps the result at 6 words. Closes #90.
```

## Out of scope (explicitly deferred)

- Tighter `_TITLER_SYSTEM_PROMPT` ("respond with ONLY the title text") — separate hardening, can stack with this fix later if needed.
- Switching default titler to Sonnet (`STARTER_TITLER_MODEL` already overrides; can re-evaluate after dev traffic).
- UI hint showing that a title was truncated (no product need yet).
- Background re-title for past `"New chat"` entries that never got a title (out of scope; would be a separate one-shot script).
