# Phase 7d — Memory recall + auto-titling

**Date:** 2026-05-31
**Status:** Approved (ready for plan)
**Parent spec:** `docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md` §7d
**Companion specs:**
- `docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md` (the spike that confirmed Strands 1.41 ships no AgentCore Memory adapter; we build manual injection from boto3 + `BeforeInvocationEvent`)
- `docs/superpowers/specs/2026-05-31-phase-7c-agentcore-memory-writes-design.md` (the write-side hook this phase builds on)

## Summary

Phase 7d finishes the AgentCore Memory loop. Phase 7c shipped
**writes only** — every chat turn persists to AgentCore. This phase
adds **reads + auto-titling**:

1. **Recall** — a new `AgentCoreRecallHook` subscribes to Strands'
   `BeforeInvocationEvent`, queries `bedrock-agentcore.RetrieveMemoryRecords`
   scoped to the caller's `actorId`, filters by relevance score,
   and injects the top-K records into the system prompt as a compact
   addendum. The agent "remembers" what the user said in their
   prior chats — the canonical "ask in chat B what you said in chat
   A, agent knows" user story.
2. **Auto-titling** — after the FIRST round-trip on a fresh chat,
   the server invokes a small Haiku-backed Strands `Agent` (no
   memory hooks attached) to summarise the first user message in
   3-6 words, persists the title via `storage.patch_chat`, and emits
   a new `title_suggested` SSE event in the same stream. The SPA
   picks it up and renames the sidebar row.

Both features fail-soft: a failed recall produces a normal turn
without injected context; a failed title leaves the chat with its
default name. Memory reliability never gates chat reliability.

## Goals

- Cross-session same-user recall: actor A's chat B sees relevant
  records from actor A's chat A (and chats C, D, ...).
- Auto-title fires exactly once per chat (idempotent).
- Server-side title emission via SSE — no SPA polling loop.
- Recall + title failures swallow + log + EMF counter; never
  surface as `500` to the SPA.
- Two new env-var kill-switches so a production regression in
  either feature can be disabled without a code rollback.

## Non-goals

- Cross-actor recall (privacy — never).
- Workspace partitioning of recall (depends on the Workspaces
  product feature; will materialise as
  `actorId = f"{workspace_id}/{user_id}"`).
- `StartMemoryExtractionJob` semantic extraction — out of scope for
  7d. The raw events accumulated since 7c are good enough for the
  semantic-similarity search AgentCore runs natively. Extraction
  jobs are a Phase-8 candidate.
- Streaming the title generation (Haiku call is sub-second; emit a
  single event with the finished title).
- Auto-retitling on conversation-drift (the title is the FIRST
  round-trip's summary; later drift doesn't re-title).
- Tool-result-framed recall (we go with the simpler
  system-prompt-addendum injection — see §Architecture).

## Architecture

### Recall flow

```
                          ┌──────────────────────┐
   POST /messages ───→    │ chats.py             │
                          │  _stream_bedrock_reply
                          │   loads DDB history,  │
                          │   builds agent,       │
                          │   streams reply       │
                          └──────────┬───────────┘
                                     │
                          ┌──────────▼───────────┐
                          │ chat_agent.py        │
                          │  build_agent(...)    │  ← attaches BOTH hooks
                          │   Agent(hooks=[      │
                          │     RecallHook,      │  ← Phase 7d NEW
                          │     MemoryWriteHook  │  ← Phase 7c (unchanged)
                          │   ])                 │
                          └──────────┬───────────┘
                                     │
       BeforeInvocationEvent ┌───────▼─────────────────────┐
                             │ agents/recall.py            │
                             │  AgentCoreRecallHook        │
                             │   1. Check (actor,chat) cache│
                             │   2. If stale: RetrieveMemoryRecords
                             │      query=user_msg, score>=0.7, topK=5
                             │   3. Format as system-prompt addendum
                             │      ("## What I remember about you: ...")
                             │   4. Mutate event.messages[0]
                             └─────────────────────────────┘
```

### Recall scope

`RetrieveMemoryRecords` is called with the **caller's `actorId`** and
**no `sessionId` filter** — that gives us records from ALL of the
user's prior chats, not just the current one. AgentCore's
semantic-similarity scoring + our top-K=5 cap surface the most
relevant fragments regardless of which chat they came from.

`actorId` itself uses the same `_sanitize_actor_id` helper from
Phase 7c so the email-form JWT sub maps correctly.

### Recall trigger + caching

Recall fires on **every turn**, but the result is cached per
`(actor_id, chat_id)` for `_RECALL_CACHE_REFRESH_TURNS = 5` turns
before re-querying. Concretely:

```python
_recall_cache: dict[tuple[str, str], CacheEntry] = {}

@dataclass
class CacheEntry:
    records: list[dict[str, Any]]  # the relevant MemoryRecordSummary list
    age: int                        # turns since last refresh
```

Per-turn flow:

1. Compute `key = (actor_id, chat_id)`.
2. If `key not in cache` OR `cache[key].age >= 5`: call
   `RetrieveMemoryRecords(memoryId, actorId, searchCriteria={
   searchQuery: user_msg, topK: 5})`, filter records where
   `score >= 0.7`, store `CacheEntry(records=filtered, age=0)`.
3. Else: increment `cache[key].age` and use the cached records.
4. Format the records as a system-prompt addendum (see below) and
   mutate `event.messages[0]`.

Module-level cache survives across requests within a warm Lambda
instance; cold-start invalidates. Acceptable failure mode: the
first request after a cold start pays the recall RPC; subsequent
warm requests get the cache.

### Recall injection format

Append a compact, Markdown-formatted block to the system prompt:

```
## What I remember about previous conversations

- {record 1 text}
- {record 2 text}
- {record 3 text}
```

The block goes at the END of `event.messages[0].content[0].text` so
it doesn't displace the existing `DEFAULT_SYSTEM_PROMPT`. If no
records survive the score filter, no addendum is added — the agent
sees its normal system prompt.

We considered two alternatives and rejected both:

- **Synthetic earlier turn** — fabricate a fake assistant turn
  saying "For context, in our prior conversations you mentioned X,
  Y, Z." The model treats it as part of the conversation. Harder
  to debug (the SPA renders the fake turn unless filtered) and
  weirder if the user inspects the raw transcript.
- **Tool-result frame** — inject a synthetic tool-result message.
  Adds a Strands-tool surface we haven't designed; not needed for
  what is essentially a prompt-engineering concern.

The system-prompt addendum is the cleanest path: low latency, easy
to debug, no fake-turn rendering, and `BeforeInvocationEvent`'s
documented contract explicitly supports mutating `event.messages`.

### Auto-title flow

```
   After the assistant ``done`` SSE event fires AND
   chat.message_count == 0 (i.e., this was the first round-trip):

                          ┌──────────────────────┐
                          │ chat_agent.py        │
                          │  build_titler_agent()│  ← no memory hooks,
                          │   tiny Haiku Agent   │     just a one-shot
                          │   max_tokens=20      │
                          └──────────┬───────────┘
                                     │
                          ┌──────────▼───────────┐
                          │ chats.py             │
                          │  1. invoke titler    │
                          │     with first turn  │
                          │  2. storage.patch_chat
                          │  3. emit `title_suggested` SSE event
                          └──────────────────────┘
```

The titler prompt:

```
Summarise the following exchange in 3-6 words, sentence case, no
quotes, no trailing punctuation. The summary becomes the chat's
title in the sidebar.

User: {first_user_message}
Assistant: {first_assistant_message[:500]}
```

The 500-char truncation on the assistant message keeps the
titler call cheap on token cost. Haiku's max-tokens cap of 20 +
the constraint phrasing yields ~3-6 words in practice.

### Auto-title trigger condition

`message_count == 0` at the **start** of `_stream_bedrock_reply`
(before the new user message gets persisted) means "this is the
first round-trip on a fresh chat." We capture that into the
`state` dict at the top of the function, then check it after the
assistant turn lands.

Subsequent turns leave `message_count > 0` so the auto-title path
is skipped — idempotency for free.

### Auto-title timing

The title is emitted **inline in the existing SSE stream**, AFTER
the `done` event but BEFORE stream close. Adds ~500-1000ms to the
first reply's perceived completion. The SPA's `readSse` loop
already iterates until the reader is done, so it naturally picks
up the additional frame.

We considered a fire-and-forget approach with `asyncio.create_task`
(same shape as the write hook) and rejected it: the SPA would have
to poll or open a separate notification channel to learn the new
title. Inline emission keeps the existing one-stream model.

### Failure modes

Both features use the same log + EMF + swallow pattern from 7c:

- **Recall failure**: `try/except Exception → log
  "agentcore.recall_failed" with error_type/error_message +
  exc_info=True → emit `RecallFailures` EMF counter → swallow,
  proceed with the unmodified system prompt`. The turn streams a
  normal (uninfluenced) response.
- **Auto-title failure**: same pattern, EMF counter
  `AutoTitleFailures`. The chat keeps its default "New chat"
  title; the SPA renders normally.

### Env-var kill-switches

Two new env vars give operators a fast disable path without a code
rollback:

- `STARTER_RECALL_ENABLED` (default `"1"`) — `BeforeInvocationEvent`
  hook short-circuits to a no-op when `!= "1"`.
- `STARTER_AUTO_TITLE_ENABLED` (default `"1"`) — `_stream_bedrock_reply`
  skips the titler block when `!= "1"`.

Optional model overrides:

- `STARTER_RECALL_MODEL` and `STARTER_TITLER_MODEL` — override the
  default Haiku model id for either auxiliary call. Defaults
  baked into `chat_agent.py`; useful for cost/quality A/B in dev.

## Component changes

### New files

- `src/channel/agents/recall.py`
  - `AgentCoreRecallHook(HookProvider)` — subscribes to
    `BeforeInvocationEvent` only; reads message, queries
    AgentCore, formats system-prompt addendum, mutates
    `event.messages[0]`.
  - `_format_recall_addendum(records: list[dict]) -> str` — turns
    `MemoryRecordSummary` records into the bullet-list block.
  - `_recall_cache: dict[tuple[str, str], CacheEntry]` —
    module-level cache.
  - `_RECALL_CACHE_REFRESH_TURNS = 5`, `_RECALL_SCORE_THRESHOLD = 0.7`,
    `_RECALL_TOP_K = 5` — tunable constants.
  - ~150 LOC target.
- `tests/unit/test_recall.py` — 100% coverage on `recall.py`.
- `tests/e2e/test_memory_recall_and_titling.py` — Playwright e2e
  covering both features end-to-end.

### Modified files

- `src/channel/agents/chat_agent.py`
  - `build_agent(...)` adds `AgentCoreRecallHook` to the `hooks=[]`
    list alongside the existing `AgentCoreMemoryHook`. Order
    matters: `RecallHook` first so its `BeforeInvocationEvent`
    runs before any downstream consumers; `MemoryWriteHook` second
    so its `AfterInvocationEvent` runs after.
  - New `build_titler_agent() -> Agent` — Haiku-backed `Agent`
    with `hooks=[]`, `max_tokens=20`, tight system prompt. The
    titler Agent is a one-shot, not a persistent session — it
    doesn't get the recall/write hooks. Model id from
    `STARTER_TITLER_MODEL` env (default `claude-haiku-4-5`).
- `src/channel/api/chats.py`
  - Capture `was_first_round_trip = (chat.message_count == 0)` at
    the top of `_stream_bedrock_reply`.
  - After the `done` SSE event, if `was_first_round_trip and
    os.environ.get("STARTER_AUTO_TITLE_ENABLED", "1") == "1"`:
    invoke `build_titler_agent()`, call its `stream_async` on the
    user/assistant pair, collect the text into `title`, call
    `storage.patch_chat(user_id=..., chat=..., title=title)`, emit
    `sse_title_suggested(chat_id, title)` frame.
  - Wrap in `try/except → log + EMF + swallow`.
- `src/channel/agents/strands_sse.py`
  - Add `sse_title_suggested(chat_id: str, title: str) -> bytes`
    — emits `event: title_suggested\ndata: {"chat_id": "...",
    "title": "..."}\n\n`.
- `src/channel/metrics.py`
  - Add `record_recall_outcome(success: bool)` — counter under
    `Channel/Recall`.
  - Add `record_auto_title_outcome(success: bool)` — counter
    under `Channel/AutoTitle`.
  - Both follow the locked-signature pattern from
    `record_memory_write_outcome` so a future caller can't slip
    a per-actor dimension through.
- `infra/stacks/channel_stack.py`
  - Lambda IAM grant adds `bedrock-agentcore:RetrieveMemoryRecords`
    to the existing AgentCore action list (same resource ARN).
  - Bedrock IAM already covers Haiku via the broad foundation-model
    + inference-profile pattern — no change needed for the titler.
- `tasks.py`
  - `inv dev` sets `STARTER_RECALL_ENABLED=1` and
    `STARTER_AUTO_TITLE_ENABLED=1` (default behaviour, but
    explicit so local-dev mirrors prod-default).
- `tests/unit/test_channel_stack.py`
  - Extend the required-actions assertion to include
    `bedrock-agentcore:RetrieveMemoryRecords`.
- `tests/unit/test_chat_agent.py`
  - Assert `build_agent` attaches BOTH hooks (recall + write).
  - Assert `build_titler_agent` constructs a Haiku-backed `Agent`
    with `hooks=[]` and `max_tokens=20`.
- `tests/unit/test_chats_api.py`
  - First-round-trip path emits `title_suggested` SSE.
  - Non-first-turn does not emit `title_suggested`.
  - `storage.patch_chat` called with the titler's output.
  - Titler failure is logged + swallowed; the regular `done` stream
    still completes.
  - `STARTER_AUTO_TITLE_ENABLED=0` short-circuits the titler.
- `tests/unit/test_strands_sse.py`
  - `sse_title_suggested(chat_id, title)` emits the right frame.

### SPA changes

- `ui/src/lib/sseParser.js`
  - Add `title_suggested` to the parser's recognised event types.
  - 100% coverage on the new branch.
- `ui/src/hooks/useChatStream.js`
  - Extend `readSse` to handle `event.type === "title_suggested"`.
  - Accept a new `onTitleSuggested?: (title: string) => void`
    callback param (passed in by the caller of `useChatStream`).
  - Default no-op so existing tests don't break.
- `ui/src/hooks/ChatsContext.jsx`
  - Expose `renameChat(chat_id, title)` action — calls
    `setChats` to update the chat's title locally; persists are
    already done server-side by `_stream_bedrock_reply`.
- `ui/src/app/Conversation.jsx`
  - Pass `onTitleSuggested={(t) => chats.renameChat(chatId, t)}`
    to `useChatStream`.
- Tests for all four UI files.

### Configuration additions

- `STARTER_RECALL_ENABLED` — new env var. Values: `"1"` (default,
  recall hook active), anything else (hook short-circuits to
  no-op).
- `STARTER_AUTO_TITLE_ENABLED` — same shape for auto-titling.
- `STARTER_RECALL_MODEL` — optional override for the recall
  hook's titler-style auxiliary call (none today; reserved for
  future where recall summarisation might use a cheap model).
- `STARTER_TITLER_MODEL` — optional override for the auto-titling
  Haiku call. Defaults to the env-var value or `claude-haiku-4-5`.

## Validation

Three layers, in order, before opening the PR:

### Layer 1 — Unit + integration (CI gate)

Standard `inv pre-push`:

- `tests/unit/test_recall.py` — 100% coverage on `recall.py`.
  Cases: cache hit, cache stale, score-threshold filter, topK
  cap, RetrieveMemoryRecords failure swallowed, system-prompt
  addendum format, hook attaches to `BeforeInvocationEvent` only.
- `tests/unit/test_chat_agent.py` — both-hooks-attached, titler
  is Haiku + `hooks=[]` + max_tokens=20.
- `tests/unit/test_chats_api.py` — first-round-trip emits
  `title_suggested`; non-first does not; storage.patch_chat
  called; titler failure logged+swallowed; env-var disable path.
- `tests/unit/test_channel_stack.py` — IAM grants
  `RetrieveMemoryRecords`.
- UI vitest — 4 new tests covering the `title_suggested` event
  parser branch, hook callback dispatch, Conversation wiring,
  ChatsContext rename action.

### Layer 2 — Local end-to-end (Playwright, before PR)

`tests/e2e/test_memory_recall_and_titling.py` driven by
`uv run inv e2e-local --tests tests/e2e/test_memory_recall_and_titling.py`.

**Test A — cross-session recall**

1. Open browser as user `playwright-7d-{tag}@example.com`.
2. In chat A, send `"I'm building a chess engine called Nightfall."`
   — wait for assistant settle.
3. In chat A, send `"My favourite colour is sage green."` — wait
   for settle.
4. Wait 5 seconds for the fire-and-forget AgentCore writes to land
   (AgentCore Memory has a brief eventual-consistency window after
   `CreateEvent`).
5. Verify writes via `GET /api/_debug/memory/events?chat_id=<A>&limit=10`
   — expect 2 events.
6. Start a brand-new chat B (different chat_id, same actor). Send
   `"What was the project I'm working on?"` — wait for settle.
7. Inspect chat B's final assistant turn text via the SPA's
   rendered DOM. Assert it contains `"Nightfall"`
   (case-insensitive). That's the proof recall worked.
8. Symmetric check: chat B says `"And the colour?"` — assert
   assistant turn contains `"sage"`.

**Test B — auto-titling**

1. New chat C. Send `"Help me debug a flaky pytest fixture that
   uses tmp_path."` — wait for settle.
2. Wait for `title_suggested` event by polling Sidebar text for
   the new chat row. Assert the title differs from the default
   ("New chat") within 5s of the assistant `done` event.
3. Assert the title is 3-6 words, no quotes, contains at least one
   substantive keyword from the original message (`pytest`,
   `fixture`, `debug`, or `tmp_path`).
4. Verify persistence via `GET /api/chats/{C}` — `title` field
   matches what the SPA showed.
5. Send a SECOND message in chat C. Assert the title does NOT
   change (idempotent — only first round-trip auto-titles).

**Don't skip the local Playwright pass.** Same rule that surfaced
the four spec-vs-AgentCore-API mismatches during 7c (Memory name
regex, actorId regex, ListMemories response shape, eventId URL
fragment) and the silent-failure-logging bug. Layer 1 alone won't
catch a `RetrieveMemoryRecords` response-shape surprise or a
recall-injection regression in production.

### Layer 3 — CloudWatch sanity (after deploy)

1. Send a chat with markdown-heavy content at the dev URL.
2. `aws logs tail .../ChannelStack-dev-Api... --filter-pattern
   '"RecallSuccesses"' --since 5m` — expect non-zero count.
3. `aws logs tail ... --filter-pattern '"AutoTitleSuccesses"'
   --since 5m` — expect non-zero count.
4. CloudWatch metric panel — `Channel` namespace (existing) shows
   `RecallSuccesses` / `RecallFailures` /
   `AutoTitleSuccesses` / `AutoTitleFailures` ticks.

## Risks + mitigations

### 1. AgentCore Memory eventual consistency

`CreateEvent` from Phase 7c is fire-and-forget — there's a brief
window where a freshly-written record isn't yet searchable. The
Test A pause-then-assert flow handles this in e2e by sleeping 5s
before the cross-session query. Production user-flows naturally
have minutes between chats, so this isn't a real-traffic concern.

### 2. Recall cost — RetrieveMemoryRecords per turn

`RetrieveMemoryRecords` is billed per request. Worst case (no
cache): one query per turn. With the 5-turn cache and typical
chat lengths, amortised cost is ~one query per 5 turns. Acceptable
for the value delivered. If the bill spikes, the
`STARTER_RECALL_ENABLED=0` kill-switch disables recall without a
deploy.

### 3. Recall noise — irrelevant matches surfacing

The 0.7 score threshold + topK=5 cap is conservative — only confident
matches make it through. Test A's deterministic-keyword assertion
(`"Nightfall"`) verifies relevance. If real traffic shows noisy
recall, drop the threshold to 0.8 or trim topK to 3 in a follow-up.

### 4. Auto-title latency

Inline emission adds ~500-1000ms to the first reply's perceived
completion. That window is the FIRST chat only; subsequent turns
unaffected. If user testing shows this is too disruptive, switch
to fire-and-forget + sidebar polling via the existing
`GET /api/chats` listing — same shape as Option B from the
brainstorm.

### 5. Title hallucination

Haiku given a tight prompt + max_tokens=20 + the actual exchange
text is highly constrained. Test B asserts a keyword from the
original message appears in the title (guards against the model
hallucinating an unrelated title). If real traffic shows
hallucination on edge-case messages, swap to a smaller
temperature or migrate the prompt to a JSON-schema response with
`structured_output`.

### 6. EMF metric cardinality

Same Risk #3 from 7c: `record_recall_outcome` and
`record_auto_title_outcome` use the locked-signature pattern. No
per-actor or per-session dimensions — counter-only. Codified by
unit tests on the helper signatures.

### 7. SPA double-rename

If `title_suggested` arrives BEFORE the chat row is in the local
sidebar state (e.g., because `useChatList` hasn't refetched yet),
`renameChat(chat_id, title)` no-ops. The next refetch picks up the
title from the persisted DDB row. Tested in unit + e2e.

### 8. Cold-start recall miss

Module-level cache invalidates on Lambda freeze. First request
after a cold start pays the RecallMemoryRecords RPC (~200-400ms).
Acceptable in chat-app latency budgets; cached after.

## Deferred to Phase 8 (or later)

- `StartMemoryExtractionJob` — background semantic-extraction
  jobs that derive higher-level facts from raw events.
- Tool-result-framed recall — alternative injection path if
  system-prompt-addendum proves too rigid.
- Workspace partitioning of `actorId`.
- Auto-retitling on conversation drift.
- Streaming title generation (replace the single-event emission
  with delta frames).
- A "Forget this" UX — user-initiated deletion of specific recall
  records.
- Recall-result interaction surface: user-visible "Why did you
  mention X?" reveal of the underlying records.

## Out of scope entirely

- Cross-actor recall — privacy. Never.
- Recall caching across Lambda instances (DDB-backed shared
  cache). The per-instance module cache + cold-start re-fetch is
  good enough.

## CHANGELOG entry (draft)

```
### Added
- Phase 7d — agent now recalls relevant context from the user's
  PRIOR chats via Bedrock AgentCore Memory, and auto-titles new
  chats in 3-6 words after the first reply. Recall fires per turn
  with a 5-turn cache; injection is a Markdown addendum on the
  system prompt. Auto-title uses a Haiku one-shot Strands Agent.
  Both features fail-soft: a missing recall or title never breaks a
  chat. Kill-switches via `STARTER_RECALL_ENABLED` and
  `STARTER_AUTO_TITLE_ENABLED`.
```

## CLAUDE.md updates required

- `## Structure` — add `src/channel/agents/recall.py` to the
  `agents/` block.
- `## AgentCore Memory` section — extend to document the recall
  hook + cache semantics + auto-title trigger condition. Note the
  env-var kill-switches.
