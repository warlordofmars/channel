---
name: bedrock-agent
description: "Conventions for AgentCore Runtime + Memory integration on top of the inline-agent wrapper at src/starter/agents/inline_agent.py — placeholder stub until the chat-app fork's Runtime integration lands in #70 and is codified into a full skill in #71."
status: stub
triggers:
  paths:
    - "src/starter/agents/**.py"
  areas:
    - "api"
---
> STUB — Runtime conventions blocked by #70; follow-up #71

# bedrock-agent

Skill scope: agent-endpoint authoring conventions for the AgentCore
Starter — specifically the AgentCore Runtime + Memory integration
that ADR-0004 settles for the chat-app fork. The Runtime-side
surface is not stable yet; the integration work in #70 is what
produces the conventions this skill will codify in full once #71
lands.

The current shape on `main` is the inline-agent wrapper at
`src/starter/agents/inline_agent.py` (per ADR-0003), which the
template keeps unchanged. ADR-0004 §Decision A commits the
chat-app fork to the **sidecar** pattern: a separate ARM64 Runtime
container alongside the existing FastAPI Lambda, with both code
paths coexisting (`/api/agents/*` inline-agent, pedagogical;
`/api/chat/*` Runtime). New agent work in this repo before #70
lands should follow the existing inline-agent pattern in
`src/starter/agents/inline_agent.py` rather than inventing
Runtime-shaped scaffolding ahead of the integration.

## What we know now (intent only)

These three points are settled by ADR-0004 and will carry forward
into the full skill:

- **Sidecar architecture is locked** (ADR-0004 §"Decision A —
  Runtime vs inline-agent" / migration Option A). Runtime ships
  as a separate container; the FastAPI
  Lambda stays in the request path and calls
  `bedrock-agentcore.invoke_agent_runtime` from new
  `/api/chat/*` routes. Inline-agent on `/api/agents/*` is
  preserved for the template's pedagogical role (ADR-0003 remains
  in force).
- **Memory and DynamoDB coexist** (ADR-0004 §"Decision B —
  Memory vs DynamoDB"). Memory
  is not a transcript store — it handles LLM-extracted semantic
  recall (facts, summaries, preferences) and is invoked as a
  tool via `RetrieveMemoryRecords`. The raw turn-by-turn
  transcript and the immutable audit log stay DynamoDB-backed.
- **Memory namespace tenancy is encoded inside `actorId`**
  (ADR-0004 §Findings #6, §Consequences). Namespace templates
  accept only `{actorId}`, `{sessionId}`, and
  `{strategyId}` / `{memoryStrategyId}` (per ADR-0004
  §Findings #3 — the two strategy-id forms are aliases) —
  custom variables are not supported. Workspace tenancy
  therefore lives inside `actorId` as
  `f"{workspace_id}:{user_id}"`, generalising the existing
  inline-agent convention from CLAUDE.md §"Product decisions".
  The encoding format must be locked in #37 before the first
  Memory write because events are immutable.

Everything else — the Runtime endpoint scaffolding, the
streaming consumer pattern in production, IAM scoping at the
construct level, the inline-agent-vs-Runtime decision tree, the
Memory strategy weighting and namespace template strings, the
`runtimeUserId` convention — awaits the chat-app fork's
integration work (#70) and is captured under §Gaps below.

## Gaps

### Runtime endpoint scaffolding

- **What's missing:** Concrete Runtime endpoint shape — the FastAPI route layout for `/api/chat/*`, the boto3 `invoke_agent_runtime` call site, the SSE consumer pattern (`for line in response["response"].iter_lines():` per ADR-0004 §Findings #2), and the `StreamingResponse` plumbing through AWS Lambda Web Adapter.
- **Why deferred:** Runtime requires bring-your-own ARM64 container scaffolding (ECR build, `bedrock-agentcore-control.create_agent_runtime`, CDK construct); the conventions only stabilise once an actual integration ships.
- **Unblocks when:** #70 lands the chat-app fork's Runtime integration; #71 then replaces this stub with a full skill drawn from the realised code paths.

### Inline-agent vs Runtime decision tree

- **What's missing:** The concrete decision tree for when an agent endpoint should target inline-agent versus Runtime — including the streaming-protocol differences (Bedrock event stream `for event in response["completion"]:` vs HTTP/SSE `for line in response["response"].iter_lines():` per ADR-0004 §Findings #2) and the IAM-scope differences (`bedrock:InvokeInlineAgent` per ADR-0003 vs `bedrock-agentcore:InvokeAgentRuntime` per ADR-0004 §Findings #4).
- **Why deferred:** ADR-0004 §"Decision A — Runtime vs inline-agent" assigns inline-agent to the template and Runtime to the chat-app fork, but the per-endpoint decision tree only becomes observable once both paths coexist in a realised fork.
- **Unblocks when:** #70 ships both code paths in the chat-app fork; #71 codifies the decision tree against the realised integration.

### Memory strategy weighting and namespace template strings

- **What's missing:** Which Memory strategies (`semantic`, `summary`, `userPreference`, `episodic`, `custom`) the chat-app fork uses by default, the per-strategy weighting, and the concrete namespace template strings (e.g. `workspaces/{actorId}/facts`, `workspaces/{actorId}/summaries/{sessionId}`).
- **Why deferred:** Strategy choice is product-level (which recall behaviours the chat experience needs) and must be decided during chat-app fork planning; ADR-0004 documents the constraints (6 strategies per Memory, only `{actorId}` / `{sessionId}` / `{strategyId}` (a.k.a. `{memoryStrategyId}`) template variables) but does not pick weights.
- **Unblocks when:** #70's design pass picks the default strategy set and namespace templates for the fork; #71 codifies them.

### `runtimeUserId` vs JWT `sub` convention

- **What's missing:** Whether `runtimeUserId` (a first-class field on `invoke_agent_runtime` per ADR-0004 §Findings #1) carries the JWT `sub`, the workspace-scoped `actorId` (`f"{workspace_id}:{user_id}"`), or some other encoding — and whether `runtimeSessionId` keeps the inline-agent's `f"{user_id}:{caller_session_id}"` shape per ADR-0003 or drops the user prefix because Memory's `actorId` already provides user scope (ADR-0004 §Consequences hints at the latter).
- **Why deferred:** The convention only matters once Runtime is actually invoked from a request handler; it depends on whether per-call IAM scoping uses `runtimeUserId` or relies entirely on Memory's `bedrock-agentcore:namespacePath` condition keys.
- **Unblocks when:** #70 wires the first `invoke_agent_runtime` call site and locks the `runtimeUserId` / `runtimeSessionId` shapes; #71 codifies them alongside the existing inline-agent session-namespacing convention from CLAUDE.md.

### Workspace-ID encoding format for `actorId`

- **What's missing:** The concrete workspace-ID format (UUID, slug, prefixed identifier) and the escaping rules for the `:` separator inside `actorId = f"{workspace_id}:{user_id}"`.
- **Why deferred:** ADR-0004 §Consequences calls this a hard prerequisite that must be locked in #37 before any Memory write, because events are immutable and namespace records are rebuilt only on full re-extraction. The format is a workspace-primitive concern, not an agent-endpoint concern.
- **Unblocks when:** #37 locks the workspace-ID encoding; #70 then consumes the locked format on first Memory write; #71 codifies the resulting `actorId` shape into this skill.

## Follow-up

Once #70 lands the chat-app fork's Runtime + Memory integration,
#71 (which replaces this stub with a full skill) takes over —
covering the realised endpoint scaffolding, the
inline-agent-vs-Runtime decision tree, IAM scoping at the
construct level, the locked `runtimeUserId` / `runtimeSessionId`
conventions, and the chosen Memory strategy weighting and
namespace template strings.

## See also

- `src/starter/agents/inline_agent.py` — the current
  inline-agent wrapper (per ADR-0003); the `invoke` /
  `invoke_stream` shape and the user-namespaced `sessionId` are
  the reference today and remain in force for the template's
  agent invocation surface. The scaffold FastAPI route handlers
  that previously consumed this wrapper were removed during the
  channel fork's Phase 0; new route handlers will be written in
  Phase 6 — see the `fastapi-route` skill for the route-handler
  conventions (auth dependency, `StreamingResponse`, SSE event
  schema).
- [ADR-0003](../../../docs/adr/0003-inline-agent-and-session-memory.md)
  — inline-agent decision and the user-namespaced `sessionId`
  convention; in force for the template's `/api/agents/*` path.
- [ADR-0004](../../../docs/adr/0004-agentcore-runtime-feasibility.md)
  — the architectural source for AgentCore Runtime + Memory in
  the chat-app fork; the contract this skill graduates against.
- [ADR-0006](../../../docs/adr/0006-skills-system.md)
  §"Stub skill convention" — the schema contract this stub
  follows.
- CLAUDE.md §"Product decisions" — the user-namespaced
  `sessionId` rule that Runtime/Memory generalises into the
  workspace-scoped `actorId` per ADR-0004 §Consequences.
- #70 — the chat-app fork's Runtime + Memory integration that
  stabilises the conventions this skill codifies.
- #71 — the follow-up that replaces this stub with a full skill.
- #37 — the workspace primitive whose ID encoding must be
  locked before Memory writes begin (ADR-0004 §Consequences hard
  prerequisite).
