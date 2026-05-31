# Strands AgentCore Memory adapter spike — 7b plan-time

**Date:** 2026-05-31
**Status:** Informational
**Parent spec:** `docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md`

## Strands version inspected

`strands-agents==1.41.0` (installed at
`.venv/lib/python3.12/site-packages/strands/`)

Companion packages NOT installed in this repo, and verified absent from
the dependency tree: `strands-tools`, `strands-agentcore`,
`bedrock-agentcore-starter-toolkit`. The only Strands distribution on
disk is `strands_agents-1.41.0.dist-info`.

## Summary

**Strands 1.41.0 has no AgentCore Memory adapter.** A full case-insensitive
grep of `.venv/.../strands/**/*.py` for `agentcore`, `agent_core`, and
`agent-core` returns zero matches. There is no `strands.memory` module,
no `AgentCoreMemory` class, no memory-as-a-tool wrapper, and no
session-manager subclass that talks to AgentCore. Strands' built-in
`session` package (`FileSessionManager`, `S3SessionManager`,
`RepositorySessionManager`) is for verbatim conversation persistence
only — none of them perform semantic recall.

What Strands *does* provide is a typed pre-invocation hook
(`BeforeInvocationEvent`) whose `messages` field is mutable, and the
agent loop reads the mutated `messages` back out and uses them for the
rest of the turn (`agent/agent.py:907-909`). That hook is the
documented injection point for any recall-context-into-prompt pattern.

`boto3 1.42.78` (already a transitive dependency via `strands-agents`)
exposes the `bedrock-agentcore` data-plane client with the operations
we need: `CreateEvent`, `RetrieveMemoryRecords`, `ListEvents`,
`GetMemoryRecord`, plus the `bedrock-agentcore-control` plane for
`CreateMemory` / `GetMemory` / `ListMemories`.

**Recommendation: build from boto3 + Strands hooks.** Details in
"Implication for 7c" below.

## Surface location

There is no Strands-side surface. The integration must be assembled
from:

- **`boto3.client('bedrock-agentcore')`** — data plane: `CreateEvent`,
  `RetrieveMemoryRecords`, `GetMemoryRecord`, `ListEvents`,
  `BatchCreateMemoryRecords`, `StartMemoryExtractionJob`.
- **`boto3.client('bedrock-agentcore-control')`** — control plane:
  `CreateMemory`, `GetMemory`, `UpdateMemory`, `ListMemories`.
- **`strands.hooks.BeforeInvocationEvent`** — pre-invocation injection
  point. Hook callback receives the user's messages, retrieves recall
  context, prepends it (e.g. as a system message or a fabricated
  earlier turn), and writes it back into `event.messages`.
- **`strands.hooks.AfterInvocationEvent`** or
  **`strands.hooks.MessageAddedEvent`** — write-back point. Callback
  reads the latest message off the agent and calls
  `bedrock-agentcore.CreateEvent` to persist it.

The reference pattern is the existing built-in `SessionManager`
(`session/session_manager.py:31-62`), which is itself a
`HookProvider` that subscribes to `AgentInitializedEvent`,
`MessageAddedEvent`, and `AfterInvocationEvent` to persist
conversation state. Our AgentCore Memory layer would follow the same
shape — a `HookProvider` subclass — but talk to AgentCore instead of
the file system / S3.

## Question 1 — Visibility

**Yes, fully visible** — because we own the hook callback that does
the injection, every byte we inject is in our own code path. There is
no opaque adapter doing the work behind our backs.

The mechanism (verified in source):

- **`strands/hooks/events.py:39-63`** defines `BeforeInvocationEvent`
  with `messages: Messages | None = None` and
  `_can_write(self, name: str) -> bool: return name == "messages"`.
  The hook framework's `_can_write` gate (see
  `hooks/registry.py`) means `event.messages = [...]` from a callback
  is permitted; writes to other fields raise.
- **`strands/agent/agent.py:903-909`** is the canonical caller. It
  constructs the event with `current_messages`, invokes all
  registered callbacks, and then does:

  ```python
  current_messages = (
      before_invocation_event.messages
      if before_invocation_event.messages is not None
      else current_messages
  )
  ```

  i.e. the mutated message list flows directly into
  `_append_messages` (agent.py:915) and from there into the model
  call. There's no intermediate filter.

Practical implication: a single callback like
`def inject_recall(event: BeforeInvocationEvent) -> None:` can both
log what it's about to inject (CloudWatch / EMF) and mutate
`event.messages`. Visibility is whatever we make it — we choose to
log, structure as EMF, redact PII, etc.

For belt-and-braces visibility into the *final* prompt that hits
Bedrock, `BeforeModelCallEvent`
(`strands/hooks/events.py:226-247`) fires immediately before the
model call. It exposes `invocation_state` and `projected_input_tokens`
but does **not** carry `messages` directly — to inspect the exact
messages at model-call time we'd read `event.agent.messages` from the
agent reference. Useful for assertion-style observability tests; the
injection itself should happen in `BeforeInvocationEvent` so the
mutated history is also persisted by the session manager.

## Question 2 — Gating

**Top-K only at the API level, but per-record scores are returned so
client-side score-threshold filtering is straightforward.**

`bedrock-agentcore.RetrieveMemoryRecords` (verified via
`boto3.client('bedrock-agentcore').meta.service_model.operation_model('RetrieveMemoryRecords')`):

- Input: `memoryId`, `namespace`, `searchCriteria`, `nextToken`,
  `maxResults`.
- `searchCriteria` structure members: `searchQuery: string`,
  `memoryStrategyId: string`, **`topK: integer`**,
  `metadataFilters: list`. **No `scoreThreshold` / `minScore` /
  `minRelevance` field exists** on the request side.
- Output: `memoryRecordSummaries` is a list of `MemoryRecordSummary`
  with members `memoryRecordId`, `content` (structure),
  `memoryStrategyId`, `namespaces`, `createdAt`, **`score: double`**,
  `metadata: map`.

So the gating pattern for 7c is:

```python
resp = agentcore.retrieve_memory_records(
    memoryId=mem_id,
    namespace=f"/users/{user_id}/conversations/{session_id}",
    searchCriteria={"searchQuery": last_user_text, "topK": 20},
)
records = [
    r for r in resp["memoryRecordSummaries"]
    if r["score"] >= MIN_SCORE  # client-side threshold
]
```

This is fine but the implementer should know:

- `topK` is the upper bound on what the service returns. If the
  threshold filters most of them out, we get fewer (possibly zero)
  injected records — which is the desired behaviour for "don't inject
  weak matches".
- We pay for the full `topK` regardless of how many survive the
  client-side filter, so don't set `topK` to 100 expecting "free"
  high precision.
- The `score` field documentation in the boto3 service model just
  reads "score" with no semantics — likely cosine similarity, but the
  implementer should empirically sanity-check the score distribution
  on real data before picking `MIN_SCORE`.

## Question 3 — Swallow-vs-throw

**N/A — there is no Strands adapter to configure.** The write path is
whatever we write. Failure-mode policy is ours to choose.

Concretely, the hook callback that persists messages will call
`bedrock-agentcore.CreateEvent`. boto3 client calls raise
`botocore.exceptions.ClientError` (and `EndpointConnectionError`,
etc.) on failure. We can wrap that call however we want:

```python
def persist_to_memory(event: MessageAddedEvent) -> None:
    try:
        agentcore.create_event(
            memoryId=mem_id,
            actorId=user_id,
            sessionId=session_id,
            eventTimestamp=datetime.utcnow(),
            payload=[{"conversational": {
                "role": event.message["role"],
                "content": [{"text": _flatten(event.message)}],
            }}],
        )
    except (ClientError, BotoCoreError) as exc:
        logger.warning(
            "agentcore_create_event_failed",
            extra={"error": str(exc), "memory_id": mem_id},
        )
        # swallow — recall is a soft feature, don't fail the turn
```

The 7c spec already commits to "AgentCore Memory failures must not
fail the user's turn" (parent spec §"Failure modes"). With the
manual-boto3 route there is no surprise behaviour to reverse-engineer
— our code, our policy. The downside is we own the retry / DLQ /
metric story; but those are concrete decisions, not unknowns.

## Implication for 7c

**Build from boto3.** Justification:

1. The "adopt Strands native recall" path is not available — Strands
   1.41.0 ships no AgentCore Memory adapter to adopt. The Open
   Questions in the parent spec implicitly assumed one exists; it
   does not.
2. The "Strands adapter set to write-only + manual recall" escape
   hatch collapses into the same shape as the boto3 path, since
   there's no adapter to dial back to write-only.
3. The boto3 + `BeforeInvocationEvent` + `MessageAddedEvent` shape
   gives us:
   - **Full visibility** (every byte injected is in our code).
   - **Score-threshold gating** via client-side filter on the
     `score` field of `MemoryRecordSummary`.
   - **Choose-your-own failure mode** (wrap `CreateEvent` /
     `RetrieveMemoryRecords` in try/except, log + swallow per the
     parent spec).
   - **Same architectural shape as Strands' own `SessionManager`**
     (`session/session_manager.py:31-62`) — a `HookProvider` subclass
     wired into the hook registry — so it slots into the framework
     idiomatically.

The cost is ~150-300 lines of integration code (one
`HookProvider` subclass, two callbacks, a control-plane bootstrap
helper, a small results-mapping function) plus tests. That's
proportional to the value and within the 7c budget.

## Code references for 7c implementer

Strands hook surface:

- `strands/hooks/events.py:39-63` — `BeforeInvocationEvent` definition
  + writable-field contract (`_can_write` allows `messages`).
- `strands/hooks/events.py:114-130` — `MessageAddedEvent` (read-only;
  the per-message persistence trigger).
- `strands/hooks/events.py:226-247` — `BeforeModelCallEvent` (for
  belt-and-braces visibility into the post-injection messages via
  `event.agent.messages`).
- `strands/agent/agent.py:903-909` — the agent loop reads back the
  mutated `messages` from the hook and uses them downstream.
- `strands/session/session_manager.py:31-62` — reference pattern for
  a `HookProvider` subclass that subscribes to multiple lifecycle
  events.
- `strands/hooks/__init__.py:8-26` — public-API usage example.

AWS service surface (via boto3 1.42.78):

- `boto3.client('bedrock-agentcore-control')` — `CreateMemory`,
  `GetMemory`, `ListMemories`, `UpdateMemory`, `DeleteMemory`.
  Bootstrap-time only; idempotent get-or-create on agent init.
- `boto3.client('bedrock-agentcore')` — data plane:
  - `CreateEvent(memoryId, actorId, sessionId, eventTimestamp,
    payload, branch?, clientToken?, metadata?)` — per-turn write.
  - `RetrieveMemoryRecords(memoryId, namespace, searchCriteria
    {searchQuery, memoryStrategyId?, topK?, metadataFilters?},
    maxResults?, nextToken?)` — per-turn read.
  - Response: `memoryRecordSummaries[].score` (double) — use for
    client-side threshold filter.
  - `ListEvents`, `GetEvent`, `DeleteEvent` — turn-history admin.
  - `BatchCreateMemoryRecords`, `BatchUpdateMemoryRecords`,
    `BatchDeleteMemoryRecords` — bulk admin (probably out of scope
    for 7c).
  - `StartMemoryExtractionJob`, `ListMemoryExtractionJobs` —
    background semantic-extraction jobs (worth a follow-up; not
    needed for the recall-on-read path).
