# Phase 7c — AgentCore Memory writes via Strands hooks

**Date:** 2026-05-31
**Status:** Approved (ready for plan)
**Parent spec:** `docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md` §7c
**Companion spike:** `docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md`

## Summary

Phase 7c wires Bedrock AgentCore Memory writes into the existing Strands
agent loop. The agent starts persisting every round-trip to an AgentCore
Memory resource, but recall stays off — read-side injection lands in 7d.
The phase is **write-only by design**, so user-visible behavior does not
change. Validation comes from (1) the regression test that chats still
work end-to-end and (2) a dev-only admin endpoint that surfaces the
writes for inspection.

The spike (`2026-05-31-strands-agentcore-memory-spike.md`) confirmed
Strands 1.41.0 ships no AgentCore Memory adapter. 7c builds the
integration directly from `boto3.client('bedrock-agentcore')` and a
`HookProvider` subclass — the same architectural shape as Strands' own
`SessionManager`.

## Goals

- Every assistant turn produces an AgentCore `CreateEvent` containing
  the user+assistant pair.
- Memory write failures **never** break the chat response — log, emit
  EMF metric, swallow.
- Memory writes do not add user-perceived latency — fire-and-forget
  after the SSE stream closes.
- Lock in the AgentCore service surface (control + data plane IAM,
  Memory resource bootstrap, actor/session model) that 7d builds on.

## Non-goals

- Recall / `BeforeInvocationEvent` injection — 7d.
- `RetrieveMemoryRecords`, score-threshold gating, `topK` tuning — 7d.
- `title_suggested` SSE event, auto-titling — 7d.
- `StartMemoryExtractionJob` (background semantic extraction).
- Cross-chat or cross-session recall.
- Workspace partitioning of `actorId` (waits on Workspaces feature).

## Architecture

### Components

```
                       ┌──────────────────────┐
   POST /messages ───→ │ chats.py             │
                       │  _stream_bedrock_reply
                       │    build_agent(      │
                       │      user_id,        │
                       │      chat_id, ...)   │
                       └──────────┬───────────┘
                                  │
                       ┌──────────▼───────────┐
                       │ chat_agent.py        │
                       │  build_agent(...)    │  ← attaches hook
                       │   Agent(hooks=[...]) │
                       └──────────┬───────────┘
                                  │ AfterInvocationEvent
                       ┌──────────▼───────────┐         ┌────────────────┐
                       │ agents/memory.py     │ async  │ AgentCore      │
                       │  AgentCoreMemoryHook ├──────→  │ data plane     │
                       │    on_after_invoc()  │         │ CreateEvent    │
                       │                      │         └────────────────┘
                       │  get_or_create_      │         ┌────────────────┐
                       │  memory(env)         ├────────→│ AgentCore      │
                       │   (cold-start only)  │ sync    │ control plane  │
                       └──────────────────────┘         │ List/CreateMem │
                                                        └────────────────┘
```

### Strands integration

A `HookProvider` subclass — `AgentCoreMemoryHook` — subscribes to
`AfterInvocationEvent` only (write-only this phase). The hook reads the
final user+assistant message pair off `event.agent.messages` and fires
`asyncio.create_task(...)` calling `bedrock-agentcore.CreateEvent`. The
agent loop continues without awaiting.

Reference pattern: `strands/session/session_manager.py:31-62` (a
`HookProvider` subclass that subscribes to lifecycle events to persist
conversation state — same shape, different backend).

The hook is constructed per-Agent (per-request), so `actorId` and
`sessionId` are bound at construction time:

```python
hook = AgentCoreMemoryHook(
    memory_id=get_or_create_memory(env),  # cold-start cached
    actor_id=jwt_sub,                     # one user
    session_id=chat_id,                   # one conversation
)
agent = Agent(model=..., hooks=[hook], ...)
```

### Scope model

- **One Memory resource per environment** — `name="channel-{env}"`
  (`channel-dev`, `channel-prod`, `channel-jc`, etc.).
- **`actorId = jwt_sub`** — one actor per Channel user.
- **`sessionId = chat_id`** — one AgentCore session per Channel chat.

This matches AgentCore's canonical multi-actor pattern and aligns with
the CLAUDE.md "workspaces are the tenancy root" decision (per-workspace
partitioning becomes `actorId = f"{workspace_id}/{user_id}"` later).

### Bootstrap (lazy, idempotent)

```python
# Module-level cache, resets on Lambda cold-start
_memory_id_cache: dict[str, str] = {}

def get_or_create_memory(env: str) -> str:
    if env in _memory_id_cache:
        return _memory_id_cache[env]

    control = boto3.client("bedrock-agentcore-control")
    name = f"channel-{env}"

    # AgentCore appends an opaque suffix to memoryId
    # (e.g. ``channel-dev-A1B2C3D4``). Filter by NAME, not id.
    existing = control.list_memories()
    for mem in existing.get("memorySummaries", []):
        if mem["name"] == name:
            _memory_id_cache[env] = mem["id"]
            return mem["id"]

    created = control.create_memory(name=name, ...)
    _memory_id_cache[env] = created["memoryId"]
    return created["memoryId"]
```

The CDK stack does NOT provision the Memory resource — fewer
cross-resource dependencies, `CreateMemory` is fast (sub-second), and a
cold-start cache miss is a once-per-Lambda-instance cost.

### Per-turn write

```python
async def on_after_invocation(self, event: AfterInvocationEvent) -> None:
    # Last two messages: user (n-2) and assistant (n-1)
    msgs = event.agent.messages[-2:]
    payload = _payload_from_messages(msgs)

    async def _write() -> None:
        try:
            await asyncio.to_thread(
                self._client.create_event,
                memoryId=self._memory_id,
                actorId=self._actor_id,
                sessionId=self._session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=payload,
            )
            record_memory_write_outcome(success=True)
        except Exception as exc:
            logger.warning("agentcore.create_event_failed",
                           extra={"err": str(exc)})
            record_memory_write_outcome(success=False)

    asyncio.create_task(_write())  # fire-and-forget
```

**Why one event per turn (not one per message)** — `AfterInvocationEvent`
fires once per turn; `CreateEvent` accepts a list of messages in the
payload. One atomic event per turn means a failed LLM response never
leaves an orphan user-only event in memory.

**Why fire-and-forget** — the SSE response has already streamed all the
bytes the user will see by the time `AfterInvocationEvent` fires. Adding
a synchronous `CreateEvent` call (typically 100-300ms) would extend the
Lambda handler runtime with no user benefit. The `asyncio.create_task`
runs on the existing event loop; AWSLWA holds the Lambda invocation open
until the response finishes streaming, and we rely on the task
completing within that window. **Loss-on-freeze acceptance:** if Lambda
freezes between SSE close and task completion, the event is lost. This
is consistent with the log-and-swallow failure mode — memory writes are
best-effort, never load-bearing.

### Failure mode

`try/except Exception → log + EMF metric → swallow.` No retries, no
backoff, no DLQ. Reasoning: 7c is write-only and downstream code
(recall in 7d) treats missing recall results as "no memory" — a
harmless degradation path. Adding retry would introduce a back-pressure
question (do we let CreateEvent calls accumulate on a slow Lambda?)
that isn't worth solving for a feature whose absence is invisible to
users.

## Component changes

### New files

- `src/channel/agents/memory.py`
  - `AgentCoreMemoryHook(HookProvider)` — subscribes to
    `AfterInvocationEvent`, holds `memory_id` / `actor_id` /
    `session_id`, executes the per-turn write.
  - `get_or_create_memory(env)` — bootstrap helper with module-level
    cache.
  - `_payload_from_messages(messages)` — translates Strands message
    dicts to AgentCore `CreateEvent` payload shape.
  - ~150 LOC target.
- `tests/unit/test_memory.py`
  - Bootstrap: cache hit, cache miss + list returns match, cache miss +
    list empty + create.
  - Write: success path emits EMF success metric.
  - Write: AgentCore client raises → swallowed, EMF failure metric
    emitted, no exception propagates.
  - 100% coverage on `memory.py`.
- `src/channel/api/_debug.py` — dev-only debug router.
  - `GET /api/_debug/memory/events?chat_id=...&limit=N` —
    `bedrock-agentcore.list_events(memoryId=..., actorId=jwt_sub,
    sessionId=chat_id, maxResults=N)`. Returns event summaries.
  - Mounted by `main.py` ONLY when `STARTER_ENABLE_DEBUG_ENDPOINTS=1`.
    The mount check happens at app construction, not at request time,
    so prod never even registers the route.
- `tests/unit/test_debug_api.py` — covers the enabled path, the
  disabled path (route returns 404), and the mgmt-JWT requirement.
- `tests/e2e/test_memory_writes.py` — Playwright async API e2e test
  (see "Validation" below).

### Modified files

- `src/channel/agents/chat_agent.py`
  - `build_agent(model_id, user_id, chat_id, system_prompt=...)` —
    new positional args.
  - Constructs `AgentCoreMemoryHook(...)` and passes
    `hooks=[memory_hook]` to the `Agent` constructor.
  - Tests in `tests/unit/test_chat_agent.py` extended: assert hook
    constructor receives correct `memory_id` / `actor_id` /
    `session_id`; assert `Agent` constructed with `hooks=[mock_hook]`.
- `src/channel/api/chats.py`
  - `_stream_bedrock_reply(...)` plumbs `jwt_sub` and `chat_id` into
    `build_agent`. (Both values already in scope at the call site.)
- `src/channel/api/main.py`
  - Conditionally mounts `_debug.router` when
    `STARTER_ENABLE_DEBUG_ENDPOINTS == "1"`.
- `src/channel/metrics.py`
  - `record_memory_write_outcome(success: bool)` — emits EMF counter
    `MemoryWriteSuccesses` or `MemoryWriteFailures` under the
    `Channel/Memory` namespace. **No per-actor dimension** — cardinality
    blowup risk.
- `infra/stacks/channel_stack.py`
  - Lambda IAM additions:
    ```python
    actions=[
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:ListEvents",
        "bedrock-agentcore-control:ListMemories",
        "bedrock-agentcore-control:CreateMemory",
        "bedrock-agentcore-control:GetMemory",
    ],
    resources=[
        # Memory ARNs include an AgentCore-appended suffix.
        f"arn:aws:bedrock-agentcore:{region}:{account}:memory/channel-{env}*",
    ],
    ```
  - Environment variable: `STARTER_ENABLE_DEBUG_ENDPOINTS=1` on dev,
    not set on prod.
- `tasks.py`
  - `inv dev` sets `STARTER_ENABLE_DEBUG_ENDPOINTS=1`.
- `tests/unit/test_channel_stack.py` (or add if absent) — assertion
  that prod-stack does NOT include `STARTER_ENABLE_DEBUG_ENDPOINTS` in
  the Lambda environment. Use `aws-cdk-lib.assertions.Template`.

### Configuration changes

- `STARTER_ENABLE_DEBUG_ENDPOINTS` — new env var. Values: `1` (mount
  `/api/_debug/*`), unset / any other value (do not mount).
- `STARTER_AGENTCORE_MEMORY_NAME` (optional) — overrides the
  `channel-{env}` naming convention. Useful for testing against a
  pre-existing Memory in `jc` env without recreating.

## Validation

Three layers, in order, before opening the PR:

### Layer 1 — Unit + integration (CI gate)

Standard `inv pre-push` gate:
- `tests/unit/test_memory.py` — 100% coverage on `memory.py`.
- `tests/unit/test_debug_api.py` — covers enabled / disabled mount
  paths.
- `tests/unit/test_chat_agent.py` — extended assertions on hook
  construction.
- `tests/unit/test_channel_stack.py` — CDK assertion that prod stack
  excludes the debug-endpoints env var.

### Layer 2 — Local end-to-end (Playwright, before PR)

`tests/e2e/test_memory_writes.py` driven by `uv run inv e2e-local
--tests tests/e2e/test_memory_writes.py`. The existing `inv e2e-local`
already probes Vite ports 5173-5179 and passes the detected URL through
as `STARTER_UI_URL`.

Test flow:

1. Start the full local stack (`inv dev` in a separate terminal — DDB
   Local, FastAPI on 8001 against real Bedrock + real AgentCore in the
   developer's personal `jc` AWS env, Vite on 5173).
2. Log in via the `?test_email=playwright-7c@example.com` bypass at the
   Vite proxy (sets `localStorage.starter_mgmt_token`).
3. Create chat A; send 3 messages; wait for each assistant reply to
   settle (`status="idle"` again).
4. Create chat B; send 1 message; wait for assistant reply.
5. Hit `GET /api/_debug/memory/events?chat_id={chat_a_id}&limit=10`:
   - Exactly 3 events returned.
   - Each event payload contains both the user message text and the
     assistant message text from one of the round-trips.
6. Hit `GET /api/_debug/memory/events?chat_id={chat_b_id}&limit=10`:
   - Exactly 1 event returned.
   - Payload isolated from chat A.
7. **Cross-actor isolation** — repeat the chat-A lookup using a
   different test user's JWT (`?test_email=playwright-7c-other@...`).
   - Empty result set. Proves `actorId` scoping works.
8. Cleanup: delete the events via `bedrock-agentcore.delete_event`
   for both chats so reruns start clean.

**Why a personal AWS env, not dev** — Bedrock + AgentCore are real AWS
services; we can't mock them locally. The `jc` env is the developer's
personal AWS account configured per CLAUDE.md "Opening a PR" §4. The
test makes ~5 real Bedrock calls and ~5 AgentCore writes/reads per
run — well within free-tier and developer-budget thresholds.

### Layer 3 — CloudWatch sanity (after deploy to dev)

After PR merges and CI deploys to dev:

1. Send one chat at `https://channel-dev.warlordofmars.net/app`.
2. Run:
   ```
   aws logs tail /aws/lambda/ChannelStack-dev-Api... \
       --filter-pattern '"MemoryWriteSuccesses"' --since 5m
   ```
3. Assert at least one matching log line.
4. Inspect the EMF metric in CloudWatch console under
   `Channel/Memory/MemoryWriteSuccesses` — confirm a non-zero data point
   in the last 5 minutes.

## Risks + mitigations

### 1. AgentCore Memory ARN suffix is opaque

`CreateMemory` returns a `memoryId` like `channel-dev-A1B2C3D4`.
Bootstrap-time `ListMemories` filtering must match on `name`, not `id`.
The bootstrap helper docstring will spell this out and link to this
section.

### 2. Cold-start `ListMemories` adds ~200ms

The first `build_agent` call after Lambda freeze pays a `ListMemories`
RPC. Acceptable for a chat-app warm path; the module-level cache
ensures subsequent requests don't pay it. Flag in plan, don't optimize.

### 3. EMF metric cardinality

`MemoryWriteSuccesses` / `MemoryWriteFailures` MUST stay as
no-dimension counters. Adding `actor_id` as a CloudWatch dimension
would scale with active users and blow up the metric volume. Plan must
call this out so the implementer doesn't reflexively add a per-user
dimension. Logged context (`extra={"actor_id": ...}`) is fine — that's
structured-log fan-out, not CloudWatch dimensions.

### 4. AgentCore quotas

`CreateMemory` is rate-limited per account. The lazy bootstrap is safe
because cold starts are infrequent. If we hit quota, the
swallow-on-failure path degrades to "no memory writes for this Lambda
instance until the next cold start, when we'll retry the bootstrap" —
the desired failure mode.

### 5. `STARTER_ENABLE_DEBUG_ENDPOINTS` is honor-system

A misconfigured prod env could enable the debug endpoint. Mitigations:

- The route is mgmt-JWT-gated like all `/api/*`, so leaked enablement
  still doesn't expose data publicly — only authenticated admin users.
- The CDK assertion test (`tests/unit/test_channel_stack.py`) asserts
  prod synth excludes the env var, catching accidental enablement at
  PR time.

### 6. Loss-on-Lambda-freeze for the async write

If Lambda freezes between SSE-close and the `asyncio.create_task`
completing, the event is lost. Consistent with log-and-swallow; not
worth solving in 7c. If 7d shows the recall path needs lossless writes
to be useful, revisit then.

## Deferred to 7d

- `BeforeInvocationEvent` recall hook — `RetrieveMemoryRecords` +
  prompt injection.
- Score-threshold gating + `topK` tuning.
- `title_suggested` SSE event + cheap-model title call via Haiku.
- Read-side latency budgets.

## Out of scope entirely

- `StartMemoryExtractionJob` background semantic extraction.
- Cross-chat recall (recall is per-session in 7d).
- Workspace partitioning of `actorId` (depends on Workspaces feature
  itself, which doesn't exist yet).

## CHANGELOG entry (draft)

```
### Added
- Phase 7c — agent now persists every chat turn to Bedrock AgentCore
  Memory. Writes are fire-and-forget and never block user-visible
  responses. Recall stays disabled — 7d wires it in. Dev environments
  gain a debug endpoint (`GET /api/_debug/memory/events`) for
  inspecting writes.
```

## CLAUDE.md updates required

- `## Structure` — add `src/channel/agents/memory.py` to the
  `agents/` block and `src/channel/api/_debug.py` (with a "dev only"
  note) to the `api/` block.
- New "## AgentCore Memory" section — short paragraph on the
  one-Memory-per-env / actorId=user / sessionId=chat scope model so
  future contributors don't have to re-derive it from this spec.
