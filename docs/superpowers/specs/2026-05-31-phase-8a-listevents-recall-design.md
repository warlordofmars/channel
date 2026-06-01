# Phase 8a — ListEvents-based synchronous recall

**Date:** 2026-05-31
**Status:** Approved (ready for plan)
**Parent specs:**
- `docs/superpowers/specs/2026-05-31-phase-7d-memory-recall-auto-titling-design.md` (the recall path being replaced)
- `docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md` §7d (original recall direction)

## Summary

Phase 7d implemented recall via AgentCore's `RetrieveMemoryRecords` +
`SemanticMemoryStrategy`. Live testing on dev showed the strategy's
ingestion lag is much longer than docs suggest — events sit in the
memory unindexed for hours, so `RetrieveMemoryRecords` returns empty
results and the user-visible recall UX is broken.

Pivot to **synchronous recall via `ListSessions` + `ListEvents`**.
We retrieve the raw conversation events directly instead of relying
on an async semantic-extraction layer. Trade-offs in §Architecture.

## Goals

- Recall works immediately — no async ingestion window between
  planting a fact in chat A and recalling it in chat B.
- Same hook surface (`BeforeInvocationEvent` → system-prompt addendum).
- Same kill-switch (`STARTER_RECALL_ENABLED`).
- The cross-session-recall e2e test goes back to asserting actual
  keyword surfaces (the assertion 7d had to weaken because of the
  ingestion lag).
- Memory strategy creation backed out — we're no longer using it,
  so don't pay the AWS overhead.

## Non-goals

- Bringing back any `SemanticMemoryStrategy` work — completely
  abandoned for now. If the lag improves later, can add semantic
  search as an additional injection source.
- Cross-actor recall — never.
- Workspace partitioning — same plan as 7d, depends on Workspaces
  product feature.

## Architecture

```
                          ┌──────────────────────┐
   POST /messages ───→    │ chats.py             │
                          │  _stream_bedrock_reply
                          └──────────┬───────────┘
                                     │
                          ┌──────────▼───────────┐
                          │ chat_agent.py        │
                          │  build_agent(...)    │  unchanged
                          │   Agent(hooks=[      │
                          │     RecallHook,      │
                          │     MemoryWriteHook  │
                          │   ])                 │
                          └──────────┬───────────┘
                                     │
       BeforeInvocationEvent ┌───────▼──────────────────────────┐
                             │ agents/recall.py                 │
                             │  AgentCoreRecallHook             │
                             │   1. Check (actor, chat) cache    │
                             │   2. If stale:                    │
                             │      a. ListSessions(actorId)     │
                             │      b. filter out current chat   │
                             │      c. cap last 5 sessions       │
                             │      d. for each session,         │
                             │         ListEvents(maxResults=2)  │
                             │   3. Format as Markdown addendum  │
                             │      grouped by session           │
                             │   4. Mutate event.messages[0]     │
                             └──────────────────────────────────┘
```

### Trade-offs vs the 7d approach

| Aspect | RetrieveMemoryRecords (7d, retired) | ListSessions + ListEvents (8a) |
|---|---|---|
| **Latency** | Async — minutes to hours of strategy ingestion | Synchronous |
| **Selectivity** | Semantic similarity, top-K by score | All prior sessions/events, FIFO |
| **Token cost** | Cheap — only top-K relevant records injected | Higher — could inject many turns |
| **Cost per call** | 1 RPC | 1 ListSessions + N ListEvents (N ≤ 5) |
| **Works today?** | No (empty for hours after CreateEvent) | Yes |

The selectivity loss is real — irrelevant prior conversations get
mixed in with relevant ones. Mitigations:
- Cap aggressively: 5 most-recent prior sessions, 2 events per session.
- Truncate each event's text at ~120 chars to control prompt size.
- Group by session in the addendum so the model can decide which
  prior conversation is relevant per turn.

If the noise hurts answer quality on real traffic, we can add a
simple LLM-based filter step in a follow-up (summarise a session
into 1-2 sentences instead of injecting raw turns) — but that's
out of scope for this pivot.

### Cap parameters

- `_RECALL_MAX_SESSIONS = 5` — most recent prior sessions only.
- `_RECALL_EVENTS_PER_SESSION = 2` — most recent events per session.
- `_RECALL_EVENT_TEXT_TRUNCATE = 120` — chars per quoted text.
- `_RECALL_CACHE_REFRESH_TURNS = 5` — same as 7d; the cache layer
  is unchanged.

Worst case prompt overhead: 5 sessions × 2 events × 2 turns × 120
chars = ~2,400 chars of recall context per request. Tuneable up or
down once we see real traffic.

### Current chat exclusion

PR #73 already feeds the current chat's DDB-stored history into
Strands via `Agent(messages=...)`. If recall also surfaces the
current chat's events from AgentCore, we double-feed.

The recall hook reads `event.agent.chat_id` (set by
`chat_agent.build_agent` per Phase 7d) and excludes that
`sessionId` from the ListSessions result before iterating
ListEvents.

### Addendum format

```
## What we've talked about before

**Earlier conversation (2026-05-31)**
- You: i want to tell you about my favorite color: blue
- Me: Cool! Blue is a great choice — it's one of the most universally liked colors...

**Earlier conversation (2026-05-28)**
- You: ...
- Me: ...
```

Date comes from `sessionSummaries[].createdAt`. Role mapping:
- AgentCore `role: "USER"` → `**You**`
- AgentCore `role: "ASSISTANT"` → `**Me**`

When no prior sessions exist (or all events filtered out by the
truncation), the addendum is the empty string — exact same
behaviour as the 7d implementation. The caller appends only when
non-empty so the model sees an unmodified system prompt for users
with no recall context.

### Failure mode

Unchanged from 7d. Wrap the API calls in `try/except → log + EMF
counter (`RecallFailures`) + swallow`. Either ListSessions or any
of the ListEvents calls failing produces no addendum and the
chat proceeds normally.

### Strategy backout

Phase 7d's `memory.py` configures `SemanticMemoryStrategy` when
creating a Memory. Since we're not using semantic search anymore,
back this out — `memoryStrategies=[]` again. Existing memories
(`channel_dev-TQebnkFYrN`, `channel_jc-…`) don't need migration:
- The strategy was harmless (we just weren't getting useful output)
- New memories created post-deploy will be strategy-less, cheaper
  to provision, and faster to reach `ACTIVE` (no strategy resources)

The wait-for-ACTIVE polling stays — even strategy-less memories
take ~30 seconds to become active after CreateMemory.

## Component changes

### Modified files

- `src/channel/agents/recall.py`
  - Replace `_RECALL_SCORE_THRESHOLD` + `_RECALL_TOP_K` constants
    with `_RECALL_MAX_SESSIONS`, `_RECALL_EVENTS_PER_SESSION`,
    `_RECALL_EVENT_TEXT_TRUNCATE`.
  - Rewrite `_get_or_fetch_records` to call `list_sessions` then
    `list_events` per session, excluding the current chat's
    session.
  - Rewrite `_format_recall_addendum` to group events by session
    with a date header and turn bullets.
  - Keep the cache, fire-and-forget pattern, sanitize_actor_id,
    kill-switch, and failure-swallow path — all unchanged.
- `src/channel/agents/memory.py`
  - Remove the `SemanticMemoryStrategy` from `CreateMemory` — back
    to `memoryStrategies=[]`.
  - Keep `_wait_for_memory_active`.
- `tests/unit/test_recall.py`
  - Drop score-threshold and topK tests.
  - Add tests for: 0 sessions, current-chat exclusion, session cap,
    events-per-session cap, multi-session aggregation, addendum
    formatting (snapshot-style), text truncation.
  - Keep cache, sanitization, kill-switch, failure-swallow tests.
- `tests/unit/test_memory.py`
  - Drop the `memoryStrategies` assertion. Existing wait-for-active
    tests stay.
- `tests/e2e/test_memory_recall_and_titling.py`
  - Restore Test A's deterministic-keyword assertion (the strong
    version 7d had to weaken).
  - Auto-title test (Test B) unchanged — still passes.
- `CHANGELOG.md` + `CLAUDE.md`
  - Update the `AgentCore Memory` section: recall now uses
    `ListSessions + ListEvents` synchronously. Strategy lazy-load
    is gone. Document why (ingestion lag).

### Files NOT modified

- `chat_agent.py`, `chats.py`, `strands_sse.py`, `metrics.py` — recall
  hook is a black box from their perspective.
- `infra/stacks/channel_stack.py` — IAM already grants `ListSessions`
  and `ListEvents`.
- All SPA files — no SPA-facing contract change.
- Both kill-switches stay — same shape.

### Configuration

No new env vars. No tasks.py change.

## Validation

### Layer 1 — Unit + integration (CI gate)

`uv run inv pre-push`:
- `tests/unit/test_recall.py` — 100% coverage on `recall.py` with the
  new test set.
- `tests/unit/test_memory.py` — passes without the strategy
  assertion.
- All other suites unchanged.

### Layer 2 — Local end-to-end (Playwright, mandatory before PR)

`uv run inv e2e-local --tests tests/e2e/test_memory_recall_and_titling.py`:

**Test A — cross-session recall** (back to the strong assertion)
1. Open browser as user `playwright-8a-{tag}@example.com`.
2. In chat A, plant `"I'm building a chess engine called Nightfall."`
   and `"My favourite colour is sage green."`.
3. Wait 3s for CreateEvent eventual consistency.
4. Verify via `GET /api/_debug/memory/events?chat_id=<A>` — 2 events.
5. New chat B. Send `"What was the project I'm working on?"`.
6. Assert reply contains `"Nightfall"` (case-insensitive).
7. Send `"And the colour I like?"`.
8. Assert reply contains `"sage"` (case-insensitive).

**Test B — auto-titling** unchanged.

3 consecutive runs to catch flakiness, same gate as 7c/7d.

### Layer 3 — CloudWatch sanity (after dev deploy)

1. Plant `"my favourite colour is blue"` in chat A on the dev URL.
2. Start chat B, ask `"what's my favourite colour?"`.
3. Assert the agent says `"blue"`. This is the end-to-end demo we've
   been unable to ship until now.
4. `RecallSuccesses` EMF metric increments per turn (unchanged
   semantics from 7d).

## Risks + mitigations

### 1. Noisy recall context

Irrelevant prior conversations get mixed in with relevant ones.
Mitigated by aggressive caps (5 sessions × 2 events × 120 chars).
If real traffic shows quality regression, follow-up is an
LLM-based pre-filter or per-session summary.

### 2. Token cost growth with chat history

Active users with hundreds of chats still only inject 5 sessions
worth of context — bounded.

### 3. API call volume

Per-turn worst case: 1 ListSessions + 5 ListEvents = 6 RPCs.
Cached for 5 turns. Amortised: 1.2 RPCs per turn. Acceptable.

### 4. Strategy removal — existing memories

Existing dev/jc memories retain their `SemanticMemoryStrategy`
(no UpdateMemory call). They cost some AWS resources but cause
no functional issues. They can be deleted manually if cost
becomes a concern; bootstrap will recreate without strategy.

### 5. Eventual consistency on CreateEvent

`CreateEvent` itself is eventually consistent — a brief window
exists where a freshly-written event isn't yet returned by
`ListEvents`. Test A sleeps 3s before asserting. Real users
chatting in chat B minutes after chat A won't notice.

## Deferred / out of scope

- LLM-based pre-filter or per-session summary (if noise becomes
  a real problem).
- Bringing semantic search back once AgentCore's ingestion lag
  improves.
- `StartMemoryExtractionJob` admin path (still a valid future
  capability if needed).
- Workspace partitioning of `actorId`.
- A "Forget this" UX (user-initiated event deletion).

## CHANGELOG entry (draft)

```
### Changed
- Phase 8a — memory recall now uses synchronous `ListSessions` +
  `ListEvents` instead of `RetrieveMemoryRecords`. AgentCore's
  `SemanticMemoryStrategy` had a multi-hour ingestion lag in real
  use, leaving recall empty long after events were written. The
  new path retrieves raw prior-chat events directly (cap: 5
  sessions × 2 events each) and works immediately. New Memory
  resources are no longer created with a strategy.
```

## CLAUDE.md updates required

- `## AgentCore Memory` → `### Recall` subsection:
  - Replace "RetrieveMemoryRecords + score filter" description
    with the new `ListSessions + ListEvents` flow.
  - Document the caps: 5 sessions, 2 events each, 120-char text
    truncate.
  - Note the trade-off: synchronous + simple, less selective than
    semantic search.
- `### Auto-titling` unchanged.
