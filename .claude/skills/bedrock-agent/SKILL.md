---
name: bedrock-agent
description: "Conventions for Channel's Strands-based agents layer (src/channel/agents/*) — Agent factory, Bedrock model, tool hooks, and AgentCore Memory/recall. Placeholder stub: the layer is implemented, but its authoring conventions are not yet codified into a full skill."
status: stub
triggers:
  paths:
    - "src/channel/agents/**.py"
  areas:
    - "api"
---
> STUB — the agents layer is built (Strands stack); the full
> authoring skill is not yet written. See §Gaps.

# bedrock-agent

Skill scope: agent-authoring conventions for Channel's chat agent —
the Strands stack under `src/channel/agents/`. Unlike the earlier
template plan (a separate AgentCore **Runtime** sidecar container,
per ADR-0004), Channel runs the agent **in-process** inside the
FastAPI Lambda using Strands' native `BedrockModel`, `MCPClient`,
and tool hooks. That decision is locked (CLAUDE.md §"Product
decisions": *"Tool integrations use Strands' native `BedrockModel`
+ `MCPClient` + tool hooks. Don't build a parallel tool-calling
shim."*), so this skill's future full form will document the
in-process Strands conventions, not a Runtime container.

The real code paths today:

- `src/channel/agents/chat_agent.py` — Strands `Agent` factory +
  `build_titler_agent()` (the Haiku one-shot used for auto-titling).
- `src/channel/agents/memory.py` — `AgentCoreMemoryHook`
  (`AfterInvocationEvent` write) + `get_or_create_memory` (one
  Memory resource per env, `channel_{env}`; `actorId =
  derive_actor_id(jwt.sub)`; `sessionId = chat_id`).
- `src/channel/agents/recall.py` — `AgentCoreRecallHook`
  (`BeforeInvocationEvent` read via `ListSessions` + `ListEvents`).
- `src/channel/agents/strands_sse.py` — Strands event → SSE byte
  translator (`translate_event` → `sse_delta` / `sse_done`).
- `src/channel/agents/tool_hooks.py` — `BeforeToolCallEvent` /
  `AfterToolCallEvent` hooks (cancel signal, META-fact writes).
- `src/channel/agents/asset_producers.py` — ASSET-row producers
  driven from the `chats.py` stream path.
- `src/channel/agents/tools/` — the registered tools (`clock`,
  `code_exec`, `web_fetch`, `web_search`, `generate_image`).

New agent work should follow the patterns already in those files
rather than re-deriving them. Two invariants worth stating up
front because they are enforced elsewhere:

- **Tool payloads never persist to AgentCore Memory** — conclusions
  and tool-use META facts may, but `toolUse` / `toolResult` content
  blocks are stripped before serializing (CLAUDE.md §"Product
  decisions"; ADR-0009; `_payload_from_messages` in `memory.py`).
- **Memory writes are fail-soft** — log + EMF counter + swallow; a
  failed Memory write must never break a chat.
- **`actorId` is derived through exactly one function** —
  `derive_actor_id` in `memory.py`. Never re-derive it locally and
  never relax it: one Memory resource serves an entire environment
  and `actorId` is its only partition, so a non-injective mapping
  puts two users in one memory store (issue #474; CLAUDE.md
  §"AgentCore Memory"). Pinned by
  `test_one_shared_derivation_across_every_call_site`.

## Gaps

### Tool-authoring conventions not codified

- **What's missing:** The step-by-step convention for adding a new
  Strands tool under `src/channel/agents/tools/` — the `@tool`
  signature shape, registration in `chats._build_tool_registry`,
  the kill-switch env-var pattern (`CHANNEL_<TOOL>_ENABLED`), and
  how tool results reach the SSE stream via `translate_event`.
- **Why deferred:** This resync (#358) repoints the stub at the
  real files; writing the full convention set from those files is a
  separate skill-authoring pass.
- **Unblocks when:** a future skill-authoring pass promotes this
  stub to `status: full`; no tracking issue is scheduled yet — file
  one per the README §"When to add a new skill" soft rule when the
  friction recurs.

### Hook lifecycle and ordering not codified

- **What's missing:** The ordering and interaction of the
  `BeforeInvocationEvent` (recall), `AfterInvocationEvent` (memory
  write), and `BeforeToolCallEvent` / `AfterToolCallEvent` (cancel
  signal, META facts) hooks — and the caching / cold-start
  behaviour each relies on.
- **Why deferred:** Same as above — repointing precedes the full
  write-up.
- **Unblocks when:** the same skill-authoring pass; no issue
  scheduled yet.

### Memory / recall write-shape conventions not codified

- **What's missing:** The canonical `[meta]`-prefixed synthetic
  ASSISTANT message shape, the recall caps
  (`_RECALL_MAX_SESSIONS`, `_RECALL_EVENTS_PER_SESSION`,
  `_RECALL_EVENT_TEXT_TRUNCATE`), and the kill-switches
  (`CHANNEL_RECALL_ENABLED`, `CHANNEL_AUTO_TITLE_ENABLED`).
- **Why deferred:** Documented in CLAUDE.md §"AgentCore Memory"
  today; folding it into a full skill is the deferred work.
- **Unblocks when:** the same skill-authoring pass; no issue
  scheduled yet.

## See also

- `src/channel/agents/chat_agent.py` — the Strands `Agent` factory
  and `build_titler_agent()`; the reference for how the agent is
  constructed today.
- `src/channel/agents/memory.py` / `recall.py` — the Memory write +
  recall hooks; the reference for AgentCore integration.
- `src/channel/agents/strands_sse.py` — `translate_event`; the
  reference for the SSE wire format (see also the `fastapi-route`
  skill's §4 streaming pattern).
- CLAUDE.md §"AgentCore Memory" — the authoritative description of
  the Memory / recall / auto-titling behaviour.
- CLAUDE.md §"Product decisions" — the Strands-native tool
  integration decision this skill graduates against.
- [ADR-0003](../../../docs/adr/0003-inline-agent-and-session-memory.md)
  — the pre-fork inline-agent decision (historical; superseded by
  the in-process Strands stack).
- [ADR-0004](../../../docs/adr/0004-agentcore-runtime-feasibility.md)
  — the AgentCore Runtime feasibility study; **not adopted** —
  Channel runs Strands in-process rather than as a Runtime sidecar.
- [ADR-0006](../../../docs/adr/0006-skills-system.md)
  §"Stub skill convention" — the schema contract this stub follows.
- [ADR-0009](../../../docs/adr/0009-unified-context-budget.md)
  — the recall pool budget / tool-payload-drop rationale.
