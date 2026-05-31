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

This is the first phase past the UI-only MVP. It introduces persistent
chat state, real model calls, the first DynamoDB schema for chats, and
the first AgentCore Memory wiring in the project.

## Scope

### In scope

- DynamoDB schema for chats (chat-index rows + message rows + one GSI).
- New `/api/chats/*` REST + SSE endpoints.
- Replacement of `useMockStream` with a real SSE-driven hook.
- Real Bedrock inline-agent streaming via the existing
  `src/channel/agents/inline_agent.py` wrapper, with a small extension
  for memory wiring and recall context.
- AgentCore Memory: lazy per-user memory creation, event auto-write
  during invocation, explicit fallback writes, three memory strategies
  (summary / user preferences / semantic), retrieval at inference time
  with system-prompt injection.
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
3. **Bedrock `InvokeInlineAgent`** — per-turn inference, already
   wrapped by `src/channel/agents/inline_agent.py`. Extended to accept
   a recall-augmented system prompt and to wire `memoryConfiguration`
   for automatic event writes.

### Identity mapping

- `user_id` (JWT `sub`) → `memoryId` (1-to-1, cached on the user row)
- `chat_id` (UUID) → both DDB `PK=CHAT#{id}` AND AgentCore Memory
  `sessionId`. The user-namespacing for Bedrock Agents' own session
  tracking (`f"{user_id}:{chat_id}"`) stays inside the inline-agent
  wrapper.
- `actorId = user_id` inside AgentCore Memory.

### Per-turn flow

```text
client POST /api/chats/{id}/messages
  → write user msg row to DDB                              (W1)
  → update chat-index row last_message_at + count          (W2, atomic with W1)
  → SSE: user_persisted
  → RetrieveMemories(memoryId, /users/{u}, query=text)
  → invoke_inline_agent(message, sessionId=chat_id, augmented_system_prompt,
                        memoryConfiguration=auto-write events)
  → stream SSE deltas to client AND to internal buffer
  → on stream complete:
      → write assistant msg row to DDB                     (W4)
      → update chat-index row last_message_at + count      (W5, atomic with W4)
      → SSE: done
  → async post-response:
      → fallback CreateEvent if Bedrock auto-write missed   (W3/W6)
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

## AgentCore Memory

### Lifecycle

One memory per user, created lazily on first chat (asynchronously
after user-record bootstrap). `memoryId` cached on the user row. First
chat tolerates missing `memoryId` and skips recall; subsequent chats
get full memory.

```python
client.create_memory(
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
(three strategies, namespaces parameterised by `actorId` / `sessionId`)
is the load-bearing part.

### Three strategies, three purposes

| Strategy | Namespace | Purpose | Read when |
|---|---|---|---|
| `summaryMemoryStrategy` | `/users/{actorId}/sessions/{sessionId}` | Rolling per-chat summary | Resuming a long chat |
| `userPreferenceMemoryStrategy` | `/users/{actorId}` | Extracted user facts | Every turn |
| `semanticMemoryStrategy` | `/users/{actorId}` | Embedded past turns | Every turn, top-K by score threshold |

### Event-write shape

Per turn, one event in the conversational shape (so strategies
recognise it):

```python
client.create_event(
    memoryId=memory_id,
    actorId=user_id,
    sessionId=chat_id,
    eventTimestamp=datetime.now(UTC),
    payload=[{
        "conversational": {
            "role": "USER",  # or "ASSISTANT"
            "content": [{"text": text}],
        }
    }],
)
```

Display metadata (artifacts, model, attachments) is NOT packed into
the event payload — that's DynamoDB's job.

### Inference-time recall

```python
results = client.retrieve_memories(
    memoryId=memory_id,
    namespace=f"/users/{user_id}",
    searchCriteria={"searchQuery": user_message, "topK": 5},
)
prefs = client.retrieve_memories(
    memoryId=memory_id,
    namespace=f"/users/{user_id}/preferences",
    searchCriteria={"searchQuery": "", "topK": 10},
)
```

Results render into a system-prompt suffix that the wrapper appends to
the caller's `instruction`:

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

### Why not use Bedrock Agents' built-in recall

`InvokeInlineAgent`'s native `memoryConfiguration` can both write
events AND auto-recall. We use it for **writes only** because:
- We want to log what was injected for debugging.
- We want to tune the prompt template ourselves.
- We want preferences on every turn but semantic recall gated on a
  score threshold.

If at implementation time the API can't be configured as "write but
don't recall", we fall back to explicit `CreateEvent` (operational
shape is identical) and disable `memoryConfiguration` entirely.

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

Seven writes per turn:

```text
Phase 1 — accept
  W1  Put MSG row (user)               → DDB TransactWriteItems
  W2  Update CHAT index row            → atomic with W1
                                        ↑ SSE: user_persisted

Phase 2 — invoke
  W3  CreateEvent (user)               → AgentCore (Bedrock auto-write
                                                    via memoryConfiguration)
  →   stream deltas to client + buffer

Phase 3 — finalise
  W4  Put MSG row (assistant)          → DDB TransactWriteItems
  W5  Update CHAT index row            → atomic with W4
                                        ↑ SSE: done

Phase 4 — async
  W6  CreateEvent (assistant)          → AgentCore (Bedrock auto;
                                                    fallback explicit)
  W7  Title chat (new + first turn)    → Haiku invoke + UpdateItem
```

### Failure-mode matrix

| Failure | Behavior |
|---|---|
| W1+W2 | 5xx; client preserves draft |
| W3 (Bedrock auto-write disabled / errors) | Explicit fallback in Phase 4; otherwise log + metric |
| Bedrock invoke fails mid-stream | SSE `error`; user message stays persisted |
| W4+W5 | Retry x3 with backoff; if all fail: SSE `error` + dead-letter row |
| W6 | Log + metric; accept divergence |
| W7 | No retry; next turn re-titles |
| Client disconnect mid-stream | Lambda continues; W4+W5 still run |

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
Bedrock's `memoryConfiguration` already gives us auto-write. Revisit
if auto-write proves unreliable.

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
- `tests/unit/test_inline_agent.py` — extended for `recall_context`,
  `memoryConfiguration` plumbing, fallback `CreateEvent`.
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
| **7a. DDB chats + CRUD API** | DDB schema, GSI, `POST/GET/PATCH /api/chats`, `POST /messages` returns SSE backed by a **fake deterministic stream** (canned reply, server-side). Sidebar uses real `useChatList`; chats persist across reloads. | Sidebar shows real chats; reload restores conversation; send still returns canned text. | Decouples persistence from Bedrock — biggest schema + API risk in its own PR. |
| **7b. Real Bedrock streaming** | Replace fake stream with `invoke_inline_agent`. CDK: `RESPONSE_STREAM` mode + Bedrock IAM. `/api/models` endpoint + real model picker. `/regenerate`. | Send returns real model output; model picker actually switches models. | Bedrock is the second-biggest risk surface; orthogonal to persistence. |
| **7c. AgentCore Memory writes** | `CreateMemory` (lazy + async on user bootstrap); `memoryConfiguration` on invoke; fallback `CreateEvent`. **No recall yet.** | Invisible to users; metrics + logs show events flowing. | Validate write path in real traffic before agent behavior depends on it. |
| **7d. AgentCore Memory recall + auto-title** | `RetrieveMemories` per turn; system-prompt injection; `title_suggested` SSE + cheap-model title call. | Agent remembers preferences and past chats; new chats auto-title within the first reply. | "Magic" features; built on memory that's been accumulating since 7c shipped. |

Each phase has its own GitHub issue with a `## Files to touch`
section. 7a is `size:l`; 7b–7d are each `size:m`.

## Open questions / decisions

- **AgentCore Memory API verification.** Strategy field shapes and the
  exact `memoryConfiguration` semantics for "write-only" mode need
  verification against current `boto3` at plan time. Fallback plan
  documented (explicit `CreateEvent`).
- **Lambda timeout.** Currently 30s (`channel_stack.py` line 376);
  must be raised to 5 min for streaming chats in 7b.
- **Mangum vs. alternate adapter.** Mangum doesn't pass SSE through
  streaming Function URLs cleanly; plan to switch to
  `aws-lambda-web-adapter` in 7b. Spike at 7b plan time to confirm.
- **Bedrock IAM extensions.** `bedrock:InvokeModel{,WithResponseStream}`
  is already granted (`channel_stack.py` line 333). 7b adds
  `bedrock:InvokeInlineAgent`; 7c adds AgentCore Memory actions
  (`bedrock-agentcore:CreateMemory`, `:CreateEvent`,
  `:RetrieveMemories`, `:GetMemory`, scoped to the per-env memory
  resource).

## References

- Predecessor spec: `2026-05-29-channel-mvp-design.md`
- Existing agent wrapper: `src/channel/agents/inline_agent.py`
- Existing Bedrock wrapper: `src/channel/agents/bedrock.py`
- Existing mock stream being replaced: `ui/src/hooks/useMockStream.js`
- CLAUDE.md "Product decisions" — workspaces as tenancy root, agents
  swap tokens for context, agent session IDs are user-namespaced.
