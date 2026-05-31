# Bedrock + AgentCore Memory chat backend — design

**Date:** 2026-05-30
**Status:** Approved
**Predecessor:** [2026-05-29-channel-mvp-design.md](2026-05-29-channel-mvp-design.md) (Phase 6 — UI-only MVP)
**Successor phases:** Phase 7a–7d (see §7)

## Summary

Replace the SPA's `useMockStream` canned reply with a real Bedrock-backed
chat pipeline that persists transcripts to DynamoDB and feeds long-term
memory into Bedrock AgentCore Memory. DynamoDB is the source of truth
for what the user sees; AgentCore Memory provides cross-conversation
recall and learned user preferences at inference time. The two systems
are decoupled — transcript reads never hit AgentCore; AgentCore failures
never break a chat.

**Agent layer:** [Strands Agents](https://strandsagents.com/) (AWS's
open-source Apache-2.0 agent SDK) on top of Bedrock, replacing direct
use of `InvokeInlineAgent`. Strands provides first-class AgentCore
Memory integration, normalised streaming events, and ready-for-tools
plumbing. The existing `src/channel/agents/inline_agent.py` and
`src/channel/agents/bedrock.py` wrappers are superseded by a new thin
Strands construction helper.

This is the first phase past the UI-only MVP. It introduces persistent
chat state, real model calls, the first DynamoDB schema for chats, the
first AgentCore Memory wiring in the project, and Strands as the
agentic-runtime substrate that future tool-using features will build on.

## Scope

### In scope

- DynamoDB schema for chats (chat-index rows + message rows + one GSI).
- New `/api/chats/*` REST + SSE endpoints.
- Replacement of `useMockStream` with a real SSE-driven hook.
- **Strands Agents** as the agent runtime. New
  `src/channel/agents/chat_agent.py` constructs a Strands `Agent` per
  turn with `BedrockModel`, system prompt, and (from 7c) an
  `AgentCoreMemory` adapter. `inline_agent.py` is **deleted** —
  Strands replaces its responsibilities. `bedrock.py` is kept only if
  needed for the Haiku title call (see §4); otherwise also deleted.
- AgentCore Memory: lazy per-user memory creation, writes via Strands'
  `AgentCoreMemory` adapter, three memory strategies (summary / user
  preferences / semantic). Recall injection format is owned by Strands
  if the spike confirms acceptable visibility; otherwise we set Strands'
  memory adapter to write-only and inject recall ourselves via system
  prompt (escape hatch documented in §3).
- Sidebar Recents driven by real chat list; chat archive + rename.
- Server-side model allowlist via `GET /api/models`.
- Auto-titling of new chats via a separate cheap-model call.
- Idempotency keys on the streaming POST.
- CDK: Lambda timeout raised from 30s to 5 min for streaming chats;
  IAM extended with `bedrock:InvokeInlineAgent` and AgentCore Memory
  actions. (Function URL `InvokeMode: RESPONSE_STREAM` is already set;
  Bedrock `InvokeModel{,WithResponseStream}` IAM already granted.)
- CloudWatch EMF metrics for turn duration, AgentCore write failures,
  Bedrock invoke failures.

### Out of scope (explicit deferrals)

- **Live cross-device sync.** Sidebar stays stale across devices until
  user-initiated refresh or window focus. Polling-on-focus is a clean
  follow-up; WebSocket fanout is a different shape of work.
- **Real artifact generation.** The model can mention code but the
  inline artifact card does not render. The `artifact` SSE event is
  reserved in the protocol for a future phase.
- **Chat search** (semantic or text). Surfacing AgentCore Memory recall
  to the end user is a separate UX.
- **Workspace tenancy.** Single user-scoped memory only. Revisits when
  workspaces ship per the CLAUDE.md product decision.
- **Hard delete / GDPR purge.** Soft archive only; hard delete requires
  coordinated DDB removal + AgentCore Memory tombstone and ships later.
- **Cost-runaway alarms** and abuse rate-limiting. Recommended as
  follow-ups before non-dev users hit the system.
- **Conversation branching, message edit, reactions, threading.**
- **Projects / Artifacts views** stay on mock data. Only the chat-home
  + chat-conversation flow becomes real this phase.

## Architecture

### Three layers

1. **DynamoDB** — source of truth for the chat record. Two row families
   on the existing single table:
   - **Chat-index row** per chat: `PK=USER#{u}, SK=CHAT#{ts}#{id}` —
     title, last_message_at, model_used, message_count.
   - **Message row** per turn: `PK=CHAT#{id}, SK=MSG#{ts}#{seq}` —
     role, text, model, artifacts, attachments, token usage.
2. **AgentCore Memory** — one memory store per user, lifelong. Every
   completed turn is written as an event (`actorId = user_id`,
   `sessionId = chat_id`). Three memory strategies run over the events.
   Retrieval is **only** at inference time — never for display.
3. **Strands `Agent` over Bedrock** — per-turn inference. New
   `src/channel/agents/chat_agent.py` constructs a `strands.Agent`
   with `BedrockModel(model_id=...)`, a system prompt, and an
   `AgentCoreMemory` adapter wired with `memory_id`, `actor_id`, and
   `session_id`. Strands handles tool-use, streaming, AgentCore Memory
   read/write, and (in future phases) MCP tool plumbing. The agent is
   constructed fresh per turn — Strands `Agent` instances are
   lightweight config wrappers, not long-lived state.

### Identity mapping

- `user_id` (JWT `sub`) → `memoryId` (1-to-1, cached on the user row)
- `chat_id` (UUID) → both DDB `PK=CHAT#{id}` AND AgentCore Memory
  `session_id` passed to Strands' `AgentCoreMemory` adapter. Strands
  doesn't expose Bedrock Agents' own session-id semantics directly, so
  the user-namespacing pattern from `inline_agent.py` no longer
  applies — AgentCore Memory's `actor_id` + `session_id` pair is
  scoped by `memory_id` (which is per-user), so cross-user bleed is
  prevented at the memory boundary.
- `actor_id = user_id` inside AgentCore Memory.

### Per-turn flow

```text
client POST /api/chats/{id}/messages
  → write user msg row to DDB                              (W1)
  → update chat-index row last_message_at + count          (W2, atomic with W1)
  → SSE: user_persisted
  → construct strands.Agent(
        model=BedrockModel(model_id=request.model),
        system_prompt=base_prompt,
        memory=AgentCoreMemory(memory_id, actor_id, session_id=chat_id),
    )
  → async for event in agent.stream_async(user_message):
      → translate Strands events → SSE delta / artifact / done
      → accumulate text into internal buffer
      → Strands' AgentCoreMemory adapter handles user+assistant event
        writes to AgentCore (W3/W6 — owned by Strands; we monitor for
        adapter errors via metric)
  → on stream complete:
      → write assistant msg row to DDB                     (W4)
      → update chat-index row last_message_at + count      (W5, atomic with W4)
      → SSE: done
  → async post-response:
      → if new chat: auto-title via Haiku invoke + UpdateItem (W7)
```

### Consistency model

DynamoDB writes are loud — failures surface as 5xx and dead-letter
rows. AgentCore Memory writes are quiet — failures log + increment a
metric but do not fail the request. The transcript is never lost; the
long-term memory is eventually consistent and is allowed to drift.

### Mid-stream client disconnect

The Lambda handler does NOT terminate the Bedrock stream when the
client disconnects. The internal buffer continues filling, W4+W5 still
run, and the assistant turn is written. Bounded by Lambda timeout —
**currently 30s, must be raised to 5 min in this phase** (CDK change in
`infra/stacks/channel_stack.py` line 376).

## DynamoDB schema

### Chat-index row (one per chat)

```text
PK = USER#{user_id}
SK = CHAT#{created_at_iso}#{chat_id}   (sortable; newest via ScanIndexForward=False)

title              : str               (default "New chat" until first auto-title)
chat_id            : str (UUID)
created_at         : str (ISO-8601 UTC)
last_message_at    : str (ISO-8601 UTC)
last_user_preview  : str (<= 120 chars, sidebar)
model_default      : str               (chat's default model; per-turn override on msg row)
message_count      : int
archived           : bool              (default False; UI filters out)
```

Common queries:
- Sidebar Recents — `Query(PK=USER#u, SK begins_with CHAT#, Limit=50, ScanIndexForward=False)`
- Open one chat — `GetItem` via the GSI (below) so the client doesn't need `created_at`
- Soft archive — `UpdateItem(... SET archived = :true)`

### Message row (one per turn)

```text
PK = CHAT#{chat_id}
SK = MSG#{ts_iso}#{seq:05d}            (seq disambiguates sub-millisecond writes)

chat_id, msg_id, role, text,
model, input_tokens, output_tokens,    (assistant only)
artifacts, attachments,                (optional)
created_at, ttl
```

Common queries:
- Render a chat — `Query(PK=CHAT#{id}, ScanIndexForward=True)`,
  paginated at 1 MB.

### GSI: ChatByIdIndex

```text
PK = CHAT_ID#{chat_id}
SK = META                              (constant; chat-index row only)
```

Used for direct chat_id → metadata lookup so URL routing doesn't need
to encode `created_at`. Only chat-index rows write to this GSI; cost
is one extra write per chat (not per message).

### Schema notes

- **No hour-sharded log pattern** — chats are user-bounded, not global.
- **No CHAT_USER GSI** — chat-load always goes through `PK=USER#u`
  first via AuthGate.
- **`ttl` reserved but unused** — keeps the door open for retention
  policies without a migration.
- **`message_count` is denormalized** — updated atomically with
  `last_message_at` in the same transaction.

## AgentCore Memory (via Strands)

### Lifecycle

One memory per user, created lazily on first chat (asynchronously
after user-record bootstrap). `memory_id` cached on the user row.
First chat tolerates missing `memory_id` and skips memory wiring;
subsequent chats get full memory.

Memory creation uses the raw AgentCore Memory API (boto3) since this
is a one-shot bootstrap operation, not a per-turn concern. Strands
does NOT manage memory creation — only memory **usage**:

```python
client = boto3.client("bedrock-agentcore-control")
response = client.create_memory(
    name=f"channel-user-{user_id[:8]}",
    description=f"Long-term memory for Channel user {user_id}",
    eventExpiryDuration=90,
    memoryStrategies=[
        {"summaryMemoryStrategy":        {"name": "session-summary",
                                          "namespaces": ["/users/{actorId}/sessions/{sessionId}"]}},
        {"userPreferenceMemoryStrategy": {"name": "user-prefs",
                                          "namespaces": ["/users/{actorId}"]}},
        {"semanticMemoryStrategy":       {"name": "semantic-recall",
                                          "namespaces": ["/users/{actorId}"]}},
    ],
)
```

The exact `memoryStrategies` field shapes must be verified against
current `boto3` at implementation time — the strategy objects' nested
field names have shifted across preview → GA. The architectural shape
(three strategies, namespaces parameterised by `actor_id` /
`session_id`) is the load-bearing part.

### Three strategies, three purposes

| Strategy | Namespace | Purpose | Used by |
|---|---|---|---|
| `summaryMemoryStrategy` | `/users/{actorId}/sessions/{sessionId}` | Rolling per-chat summary | Strands' adapter on long-chat resume |
| `userPreferenceMemoryStrategy` | `/users/{actorId}` | Extracted user facts | Strands' adapter every turn |
| `semanticMemoryStrategy` | `/users/{actorId}` | Embedded past turns | Strands' adapter every turn |

### Strands `AgentCoreMemory` adapter — primary path

Strands' `AgentCoreMemory` adapter is wired into the per-turn `Agent`
construction:

```python
from strands import Agent
from strands.memory import AgentCoreMemory   # API verification at impl time
from strands.models import BedrockModel

agent = Agent(
    model=BedrockModel(model_id=request.model),
    system_prompt=base_prompt,
    memory=AgentCoreMemory(
        memory_id=user_memory_id,
        actor_id=user_id,
        session_id=chat_id,
    ),
)

async for event in agent.stream_async(user_message):
    ...   # translate to SSE
```

On each `stream_async`, Strands' adapter:
- Calls `retrieve_memories` against the configured namespaces (or
  similar) and injects results into the model context.
- Persists the user message and the assistant reply as events via
  `create_event`.

Display metadata (artifacts, model, attachments) is NOT routed through
AgentCore at all — that's DynamoDB's job and lives on the message row.

### Escape hatch — manual recall injection

If the 7b spike reveals that Strands' `AgentCoreMemory` adapter
either (a) auto-injects in a format we can't inspect or tune, or
(b) doesn't gate semantic recall by score, we fall back to a
**write-only** memory adapter configuration and inject recall
ourselves via `system_prompt`:

```python
# Manual recall (escape hatch)
results = boto3_agentcore.retrieve_memories(
    memoryId=user_memory_id,
    namespace=f"/users/{user_id}",
    searchCriteria={"searchQuery": user_message, "topK": 5},
)
prefs = boto3_agentcore.retrieve_memories(
    memoryId=user_memory_id,
    namespace=f"/users/{user_id}/preferences",
    searchCriteria={"searchQuery": "", "topK": 10},
)

augmented_prompt = base_prompt + format_recall_block(results, prefs)

agent = Agent(
    model=BedrockModel(model_id=request.model),
    system_prompt=augmented_prompt,
    memory=AgentCoreMemory(..., mode="write_only"),  # if supported
    # else: omit memory= entirely and write events ourselves post-turn
)
```

Where `format_recall_block` produces:

```text
<long_term_memory>
You have access to the following long-term memory about this user:

User preferences:
- Prefers TypeScript and concise responses.

Possibly relevant from past conversations:
- [3 weeks ago] Discussed DynamoDB single-table design tradeoffs.

Use this context only when relevant. Do not reference it explicitly
unless asked.
</long_term_memory>
```

Both paths share the same write-side semantics (events go to the same
memory; same three strategies process them). The escape hatch only
changes read-side injection. We commit to one path in 7d, after the
7b spike.

### Cost shape (order of magnitude)

- `create_event` — ~$1 per million turns. Negligible.
- Strategy compute (summarization + extraction) — ~$0.50–2.00 per
  active user per month. **Dominant cost.**
- `retrieve_memories` — ~$0.20 per million turns. Negligible.

First lever if cost becomes a concern: drop the semantic strategy
(keep summary + preferences).

### Cost shape (order of magnitude)

- `CreateEvent` — ~$1 per million turns. Negligible.
- Strategy compute (summarization + extraction) — ~$0.50–2.00 per
  active user per month. **Dominant cost.**
- `RetrieveMemories` — ~$0.20 per million turns. Negligible.

First lever if cost becomes a concern: drop the semantic strategy
(keep summary + preferences).

## API surface

All endpoints require a valid mgmt JWT (existing `require_mgmt_user`
dependency). New router at `src/channel/api/chats.py`, mounted under
`/api`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/chats` | Create chat. Returns `{chat_id, created_at, title}`. |
| `GET` | `/api/chats?limit=50&cursor=…` | List user's chats; sidebar Recents. |
| `GET` | `/api/chats/{chat_id}` | Chat metadata + paginated message history. |
| `POST` | `/api/chats/{chat_id}/messages` | **SSE.** Send + stream reply. |
| `PATCH` | `/api/chats/{chat_id}` | Rename / archive. Body: `{title?, archived?}`. |
| `POST` | `/api/chats/{chat_id}/regenerate` | **SSE.** Drop last assistant turn; re-stream. |
| `GET` | `/api/models` | Server allowlist of model IDs + display metadata. |

Ownership is enforced via `ChatByIdIndex` GSI lookup; unowned chats
return 404 (not 403) to avoid leaking existence.

### Streaming POST `/messages` — SSE events

```text
data: {"type":"user_persisted","msg_id":"...","seq":12}\n\n
data: {"type":"delta","text":"Hello"}\n\n
data: {"type":"delta","text":", world"}\n\n
data: {"type":"title_suggested","title":"Greeting test"}\n\n
data: {"type":"artifact","artifact":{...}}\n\n             (reserved; producer is no-op this phase)
data: {"type":"done","msg_id":"...","seq":13,"model":"...",
       "input_tokens":412,"output_tokens":89,"stop_reason":"end_turn"}\n\n
```

`user_persisted` first gives the UI confirmation that its optimistic
user-turn render is now authoritative. `title_suggested` arrives only
on new untitled chats once enough text is in to title from. `done`'s
`model` field reports the **actual** model Bedrock used (post any
fallback routing).

### SSE event translation from Strands

Strands' `agent.stream_async()` yields a typed event stream — at
minimum text deltas, tool-use events (future), and completion
metadata. The SSE producer is a thin translator:

| Strands event | Our SSE event |
|---|---|
| `text_delta` (or equivalent) | `delta` |
| `message_stop` / completion | `done` (with `model`, `input_tokens`, `output_tokens`, `stop_reason` populated from Strands' metadata) |
| `error` | `error` |
| `tool_use` (future phases) | `artifact` or reserved `tool_use` event |

Exact Strands event names verified at 7b implementation time (the
streaming event schema is documented at
`https://strandsagents.com/` and in the `strands` Python package).
This translation layer replaces the manual `_stream_chunks` byte
parsing from the old `inline_agent.py`.

### Why `/regenerate` is a separate endpoint

Different write semantics (must delete last assistant row + AgentCore
event), and accepts model/effort override without a new user turn.
Cleaner to split than to overload `/messages`.

### Lambda Function URL streaming

Function URL `InvokeMode: RESPONSE_STREAM` is **already set** in
`channel_stack.py` (line 399) — no CDK change needed there. Mangum
(confirmed as the current FastAPI ↔ Lambda adapter — see
`channel_stack.py` comment line 7) does NOT natively pass SSE chunks
through streaming Function URLs at the time of writing. **Likely
required:** switch the API Lambda to `aws-lambda-web-adapter` (runs
FastAPI as a normal HTTP server inside Lambda; native streaming
support). The Lambda timeout also needs to rise from 30s → 5 min for
long replies. Both are 7b CDK changes; spike Mangum-vs-adapter at 7b
plan time.

## Write path & dual-write semantics

Seven writes per turn — three owned by Strands' memory adapter (W3,
W6) or Strands itself (the model invocation), four owned by our code
(W1, W2, W4, W5, W7):

```text
Phase 1 — accept
  W1  Put MSG row (user)               → DDB TransactWriteItems
  W2  Update CHAT index row            → atomic with W1
                                        ↑ SSE: user_persisted

Phase 2 — invoke (Strands)
  W3  AgentCoreMemory.create_event     → AgentCore (Strands adapter,
       (user)                                       per turn)
  →   agent.stream_async(message)
  →   translate Strands events → SSE deltas + buffer

Phase 3 — finalise
  W4  Put MSG row (assistant)          → DDB TransactWriteItems
  W5  Update CHAT index row            → atomic with W4
                                        ↑ SSE: done

Phase 4 — async
  W6  AgentCoreMemory.create_event     → AgentCore (Strands adapter)
       (assistant)
  W7  Title chat (new + first turn)    → Haiku invoke + UpdateItem
```

### Failure-mode matrix

| Failure | Behavior |
|---|---|
| W1+W2 | 5xx; client preserves draft |
| W3 (Strands adapter user-event write fails) | Adapter raises; we catch + log + metric; turn proceeds (adapter must support throw-vs-swallow knob, else we wrap it) |
| Strands `stream_async` fails mid-stream | SSE `error`; user message stays persisted |
| W4+W5 | Retry x3 with backoff; if all fail: SSE `error` + dead-letter row |
| W6 (Strands adapter assistant-event write fails) | Caught + log + metric; transcript intact |
| W7 | No retry; next turn re-titles |
| Client disconnect mid-stream | Lambda continues; `stream_async` drains; W4+W5 still run |

### Preserving the asymmetric write semantics under Strands

Goal: AgentCore Memory failures must NEVER fail a chat turn (memory is
allowed to drift; transcript is not). If Strands' `AgentCoreMemory`
adapter raises on write failure, we wrap it with a swallowing
decorator that logs + increments `AgentCoreWriteFailures` and lets
the agent continue. Verified at 7c implementation — if the adapter
doesn't expose hooks for this, we subclass it.

### Idempotency

`POST /messages` accepts `Idempotency-Key` header. Mapped to
`PK=IDEMP#{user_id}, SK={key}` with 1h TTL. Duplicate within window:
replay stored stream, no Bedrock re-invoke.

### Observability

Three CloudWatch EMF metrics (existing `metrics.py`):

| Metric | Dimensions | Why |
|---|---|---|
| `ChatTurnDurationMs` | `model` | p50/p95/p99 latency per model |
| `AgentCoreWriteFailures` | `phase` | Tracks transcript-vs-memory divergence rate |
| `BedrockInvokeFailures` | `reason` | Drives throttle / fallback decisions |

Plus structured log lines per turn: `chat_id`, hashed `user_id`,
`model`, token counts, duration. No payload logging.

### Why not DDB Streams → AgentCore

Considered: have DDB Streams trigger a Lambda that writes to
AgentCore, eliminating dual-write from the request path. Decided
against — adds a Lambda, extra IAM, and a second failure surface;
Strands' adapter already gives us in-band write. Revisit if the
adapter proves unreliable.

## UI changes

### New hooks

**`useChatStream(chatId)`** — replaces `useMockStream`. Same fundamental
shape (`{ turns, send, ... }`) but takes a `chatId`, loads history on
mount, parses real SSE.

```js
const { turns, send, regenerate, abort, status, error } = useChatStream(chatId);
// status: "idle" | "loading-history" | "sending" | "streaming" | "error"
```

`send()` flow:
1. Push optimistic user turn (tempId, `pending: true`).
2. Push optimistic assistant turn (tempId, `streaming: true`).
3. POST to `/api/chats/{id}/messages` with `Idempotency-Key`.
4. Parse SSE: `user_persisted` swaps user tempId; `delta` appends to
   assistant text; `title_suggested` calls onTitleUpdate; `artifact`
   attaches; `done` finalises; `error` marks errored.
5. `abort()` triggers `AbortController.abort()`. Server-side disconnect
   handling means the assistant message still saves.

**`useChatList()`** — drives sidebar Recents.

```js
const { chats, status, refresh, createChat, archiveChat, renameChat } = useChatList();
```

- Initial fetch on mount.
- `createChat()` is optimistic; navigates to `/app/c/{chat_id}`.
- No live updates across devices — refresh-on-focus only (one-line
  follow-up).

### Routing changes

- **`Shell.jsx`** uses `useChatList`; passes chats + actions to
  Sidebar.
- **`Conversation.jsx`** pulls `chatId` from URL params; instantiates
  `useChatStream`; removes the `kickedIdRef` StrictMode guard (real
  SSE doesn't have the rAF semantics it was protecting against).
- **`ChatHome.jsx`** — first-message-creates-chat: empty composer's
  send calls `createChat()` then navigates with the message in
  `state.firstMessage`; `Conversation` picks it up on mount and
  invokes `send()`.

### `data.js` deletions

- `SAMPLE_REPLY`, `SAMPLE_USER`, `RECENTS` — **deleted**.
- `MODELS`, `EFFORTS` — kept as display metadata; valid IDs come from
  `GET /api/models` and are merged at runtime.
- `QUICK_ACTIONS` — kept.
- `PROJECTS`, `ARTIFACTS`, `PROJECT_DOCS` — kept (still mock; Projects
  and Artifacts remain static this phase).

### Component-level summary

| File | Change |
|---|---|
| `hooks/useMockStream.js` | Delete |
| `hooks/useChatStream.js` | New |
| `hooks/useChatList.js` | New |
| `lib/sseParser.js` | New (small reusable SSE parser) |
| `api.js` | New wrappers for all `/api/chats/*` + `/api/models` |
| `app/Shell.jsx` | Wire useChatList |
| `app/Sidebar.jsx` | Render from prop, not RECENTS |
| `app/ChatHome.jsx` | First-message-creates-chat |
| `app/Conversation.jsx` | URL param `chatId`; real hook; remove kickedIdRef |
| `app/Composer.jsx` | Per-send Idempotency-Key |
| `app/data.js` | Delete SAMPLE_*, RECENTS |

## Testing strategy

### Unit (no AWS)

- `tests/unit/test_chats_api.py` — every endpoint, every status,
  every validation branch; AgentCore + Bedrock mocked; DDB faked.
- `tests/unit/test_chat_log.py` — dual-write success, AgentCore fail
  (DDB ok), DDB transaction fail (5xx + dead-letter).
- `tests/unit/test_memory.py` — lazy memory create, retrieval prompt
  format, namespace construction, no-memory graceful degrade.
- `tests/unit/test_chat_agent.py` — Strands `Agent` construction:
  correct `BedrockModel(model_id=...)`, system prompt assembly,
  `AgentCoreMemory` adapter wired when `memory_id` present and skipped
  when absent, stream-event translation to SSE shapes. Strands' `Agent`
  is mocked at the construction boundary; we don't run real model
  calls in unit tests.
- `ui/src/hooks/useChatStream.test.js` — history load, SSE parse
  (every event type), optimistic render + server msg_id swap, abort,
  error display, regenerate.
- `ui/src/hooks/useChatList.test.js` — fetch, optimistic create,
  archive, rename, refresh, error paths.
- `ui/src/lib/sseParser.test.js` — multi-event chunks, split-event
  boundaries, `:` keepalive comments, malformed JSON.

### Integration (DDB Local, mock Bedrock)

- `tests/integration/test_chat_persistence.py` — create + N messages;
  verify rows + chat-index updates.
- `tests/integration/test_chat_ownership.py` — user A can't read
  user B's chat; 404 not 403; GSI path covered.
- `tests/integration/test_streaming_disconnect.py` — simulated abort;
  Bedrock drains; assistant row written; index updates.

### E2e (deployed dev)

- `tests/e2e/test_chat_smoke.py` — log in, send "say hi", assert
  assistant text + SSE `done`. Tagged `e2e-{timestamp}` per convention.

**No e2e tests for AgentCore Memory recall.** Memory-strategy
extraction is async and non-deterministic in shape; e2e-testing it
would be flaky. Unit + integration coverage with mocked retrieve,
plus eyeball verification on dev, plus the `AgentCoreWriteFailures`
metric, is the pragmatic combination.

### Coverage

100% on both Python (pytest-cov) and JS (vitest v8) per CLAUDE.md.

## Phase sequencing — recommended PR breakdown

Too large for one PR. Four shippable phases; each ships green-CI to
`development` independently; each reviewable in one sitting.

| Phase | Ships | User-visible change | Boundary rationale |
|---|---|---|---|
| **7a. DDB chats + CRUD API** | DDB schema, GSI, `POST/GET/PATCH /api/chats`, `POST /messages` returns SSE backed by a **fake deterministic stream** (canned reply, server-side). Sidebar uses real `useChatList`; chats persist across reloads. | Sidebar shows real chats; reload restores conversation; send still returns canned text. | Decouples persistence from Bedrock + Strands — biggest schema + API risk in its own PR. |
| **7b. Strands + real Bedrock streaming** | Add `strands` dep + `strands-tools` if needed. New `chat_agent.py` constructs a Strands `Agent` with `BedrockModel`. Delete `inline_agent.py`. SSE producer translates Strands events. `/api/models` endpoint + real model picker. `/regenerate`. CDK: Lambda timeout 30s → 5min; IAM adds `bedrock:InvokeInlineAgent` (Strands' BedrockModel may use it); switch FastAPI adapter from Mangum to `aws-lambda-web-adapter` if Mangum can't stream. **No memory wiring yet** — `memory=None` on the Agent. **Strands recall-injection spike happens here** (see Open Questions). | Send returns real model output; model picker actually switches models; regenerate works. | Bedrock + Strands is the second-biggest risk surface; orthogonal to persistence. Memory wiring deferred so a Strands integration bug doesn't entangle a memory bug. |
| **7c. AgentCore Memory writes via Strands adapter** | `CreateMemory` (lazy + async on user bootstrap; raw boto3). `chat_agent.py` wires `AgentCoreMemory` adapter on each Agent construction. Wrap the adapter (or subclass) to swallow write failures into a metric per §5. **Read-side behavior depends on 7b spike outcome** — either let Strands auto-inject (do nothing extra) or set adapter to write-only and skip recall this phase. | Invisible to users if recall stays disabled; if Strands auto-injection is acceptable per 7b spike, agent starts remembering immediately. Metrics + logs show events flowing. | Validate write path in real traffic before agent behavior depends on it heavily. |
| **7d. Memory recall finalised + auto-titling** | Lock in recall behavior: either confirm Strands' auto-injection is good (no code change) or implement the manual-injection escape hatch from §3 (system-prompt format + `retrieve_memories` calls). `title_suggested` SSE + cheap-model title call via `BedrockModel(model_id="anthropic.claude-haiku-...")` invoked through a small Strands `Agent` (no memory). | Agent remembers preferences and past chats reliably; new chats auto-title within the first reply. | "Magic" features; built on memory that's been accumulating since 7c shipped. |

Each phase has its own GitHub issue with a `## Files to touch`
section. 7a is `size:l`; 7b is `size:l` (Strands integration +
adapter switch + spike); 7c is `size:m`; 7d is `size:s` to `size:m`
depending on spike outcome.

## Open questions / decisions

- **Strands recall-injection spike (7b plan-time).** Two things to
  verify on the Strands `AgentCoreMemory` adapter:
  (a) **Visibility** — can we log / inspect what the adapter injected
  into the model context per turn? If not, debugging memory behavior
  is opaque and we lean toward the manual-injection escape hatch.
  (b) **Gating** — does the adapter let us threshold semantic recall
  by relevance score, or does it always inject top-K? If only the
  latter, irrelevant memories may pollute every turn.
  Outcome drives whether 7c+7d use Strands' built-in recall or the
  manual escape hatch from §3.
- **Strands swallow-vs-throw on memory write failure.** Need to
  verify whether the `AgentCoreMemory` adapter exposes a
  swallow-failure mode, or whether we must wrap/subclass it to
  preserve the §5 asymmetric semantics (transcript loud, memory quiet).
- **Strands API stability.** Strands shipped Q2 2025 and is on an
  active release cadence. Pin to a known-good version in
  `pyproject.toml`; review the changelog at each version bump.
- **AgentCore Memory boto3 API verification.** Strategy field shapes
  on `CreateMemory` need verification at 7c plan time. Fallback plan
  documented (the architectural shape — three strategies, namespaced
  events — is what matters).
- **Lambda timeout.** Currently 30s (`channel_stack.py` line 376);
  must be raised to 5 min for streaming chats in 7b.
- **Mangum vs. alternate adapter.** Mangum doesn't pass SSE through
  streaming Function URLs cleanly; plan to switch to
  `aws-lambda-web-adapter` in 7b. Spike at 7b plan time to confirm.
- **Bedrock + AgentCore IAM extensions.**
  `bedrock:InvokeModel{,WithResponseStream}` is already granted
  (`channel_stack.py` line 333).
  - 7b adds `bedrock:InvokeInlineAgent` if Strands' BedrockModel
    routes through inline-agent runtime, or remains unchanged if it
    uses the converse API directly (verify at spike time).
  - 7c adds AgentCore Memory actions: `bedrock-agentcore:CreateMemory`,
    `:CreateEvent`, `:RetrieveMemories`, `:GetMemory`, scoped to the
    per-env memory resource ARN pattern.

## References

- Predecessor spec: `2026-05-29-channel-mvp-design.md`
- Strands Agents — `https://strandsagents.com/` (Apache-2.0; AWS-maintained)
- Existing agent wrapper being replaced: `src/channel/agents/inline_agent.py`
- Existing Bedrock wrapper (kept or trimmed in 7b): `src/channel/agents/bedrock.py`
- Existing mock stream being replaced: `ui/src/hooks/useMockStream.js`
- CLAUDE.md "Product decisions" — workspaces as tenancy root, agents
  swap tokens for context, agent session IDs are user-namespaced (the
  last decision is restated in §1 — Strands' `AgentCoreMemory` adapter
  takes `actor_id`+`session_id` and cross-user bleed is prevented at
  the `memory_id` boundary, not at the session-id format).
