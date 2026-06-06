# Epic #128 (tool use & agentic workflows) — sequencing strategy

**Date:** 2026-06-06
**Status:** Approved (ready for plan)

## Summary

Epic [#128] is fully designed at the meta level — see the [2026-06-03 decisions comment](https://github.com/warlordofmars/channel/issues/128#issuecomment-4617554628) for the resolved questions (vendor: Exa; sandbox: per-execution Lambda; host: stay on Lambda for v1; SSE event names; EMF counter names; memory invariant; CLAUDE.md invariants — the latter already landed via PR #185). This spec is **one level above that**: how does the epic actually ship? It commits to a two-stream parallel plan with a sequential tail, codifies six load-bearing operating policies that don't fit in any single sub-issue's body, and pre-empts six risks that surfaced during sequencing review.

This spec is the prerequisite read for every #128 sub-issue plan that follows.

## Goals

- A single source of truth for "what ships when" across #181, #182, #183, #184, and the out-of-band #131 swap-in.
- Operating policies that span sub-issues (`current_time` smoke-test tool, budget-line rendering with the stub, `ToolResultBlock` forward-compat) recorded once so they don't need re-derivation per sub-issue plan.
- A risk slate the implementers can read before starting any of the four streams.

## Non-goals

- Re-deriving anything resolved in the decisions comment. Vendor choice, sandbox choice, SSE/EMF names, memory invariant — all decided.
- Designing the chassis internals. The `## Files to touch` lists in #181/#182/#183/#184 stand; the chassis design doc (phenomenology preamble, hook architecture details, addendum text) is a separate spec produced when #181's session starts.
- Producing implementation plans. Each sub-issue gets its own plan via `writing-plans` when its turn comes.

## Plan

### Two parallel streams (start together)

**Stream A — #181 chassis** (`size:l`)

The substrate: Strands `tools=[...]` wiring, four new SSE event types (`sse_tool_started` / `sse_tool_progress` / `sse_tool_finished` / `sse_tool_error`), `tool_hooks.py` with three handlers (`BeforeModelCallEvent` addendum, `BeforeToolCallEvent` budget gate + chain-cap + interrupt, `AfterToolCallEvent` memory META write + chain counter + EMF), memory-invariant docstring lift + hardened test, SPA collapsible step list, EMF counters.

Ships with two stubs and one trivial tool — see "Operating policies" below.

**Stream B — #184 MCP-registry spike** (`size:s`, `p3`)

Pure design output at `docs/superpowers/specs/2026-06-XX-mcp-server-registry-spike.md` answering the four questions in #184's body (SPA discovery, per-chat selection UX, token-swap mechanics, workspaces coupling). Wall-clock ~1 week including review.

Independent of Stream A in terms of code surface — but mid-stream alignment checkpoint codified in P5 below.

### Sequential tail (after Stream A merges)

1. **#182 Exa web search** (`size:m`) — pin `strands-agents-tools`, wrap native `exa` tool with rate-limit guard + citation-shape mapping, register, citation rendering in `Conversation.jsx` via the new `ToolResultBlock` component (see R4 / policy P3 below).
2. **#183 Lambda code-exec sandbox** (`size:l`) — `CodeExecLambda` CDK construct + `src/channel/sandbox/` package + Strands wrapper + SPA code-output renderer (extends `ToolResultBlock`) + containment e2e test.

### Out-of-band (not on epic #128's critical path)

- **`RemainingBudget` stub-swap** — single PR replacing the stub call with the real primitive when [#131] lands. Tracked under #131, not #128. Closing the epic does not wait on this.

### Closing condition

Epic #128 closes when:
- #181 merged to `development`
- #182 merged to `development`
- #183 merged to `development`
- #184's design doc landed in `docs/superpowers/specs/` (the spike's follow-up implementation `#128-D`, if filed, ships under its own track and does not gate the epic close)

## Operating policies

These are the cross-sub-issue rules that wouldn't naturally fit in any single sub-issue body. Each implementer reads them before starting their plan.

### P1 — `RemainingBudget` stub returns "available," addendum **omits** the budget line

The chassis depends on [#131]'s `RemainingBudget` primitive (per [ADR-0009]). #131 is `status:design-needed`; we proceed with a stub.

- **Stub shape:** module-level function returning a deterministic sentinel — `{"available": True, "limit": None, "used": None}`.
- **Addendum behavior with stub:** the `BeforeModelCallEvent` handler renders **only** the chain-progress line (`tool calls used: N of 8`). It **does not** render a "Remaining context budget: unknown" line. Reason: leaking "unknown" into the system prompt shapes model behavior in ways that diverge from what real-#131 will produce, and the swap-in PR should add the line for the first time rather than change its meaning.
- **Stub-swap PR:** a single PR under #131 swaps the stub call for the real primitive AND adds the budget-line rendering branch. Net: addendum gains a new line; existing chain-progress line unchanged.

### P2 — `current_time` is the chassis exit test, default-off in prod

#181 explicitly says "no actual tools shipped." We honor the spirit (no user-facing tool) but ship one trivial tool to give the chassis a real end-to-end exercise path in CI + jc/dev:

- **Tool:** `current_time` from `strands-agents-tools` (or a 5-line `src/channel/agents/tools/clock.py` if pinning the whole package isn't desired for #181's scope — defer that call to the chassis design doc).
- **Feature flag:** `STARTER_CLOCK_TOOL_ENABLED`. **Default `1` in jc/dev, default `0` in prod.** Codified in CDK env config.
- **Policy:** `current_time` is a smoke-test, not a product feature. If a future feature wants real time-aware tools, that's a separate design pass. Reviewer enforcement: any PR that flips `STARTER_CLOCK_TOOL_ENABLED=1` in prod must justify it as a product decision, not an implementation convenience.

### P3 — #182 introduces `ToolResultBlock`; #183 extends it

#182 and #183 both render tool output in `Conversation.jsx`. Sequential ordering already prevents merge conflicts, but #182 ships first, so its citation-rendering must anticipate #183's code-output rendering:

- #182 introduces `ui/src/app/ToolResultBlock.jsx` — a discriminated-union renderer keyed on `tool_result.kind` (or equivalent). #182 ships the `kind: "web-search-citations"` branch.
- #183 adds the `kind: "code-output"` branch in the same component.
- Reviewer enforcement on #182: a one-branch `ToolResultBlock` whose extension shape is obvious from the JSX. Not a layer of abstraction that future-#183 has to fight.

### P4 — #181 lands as three sequential PRs, not one

`size:l` with six concerns (Strands wiring, SSE, hooks, memory invariant, SPA, EMF) is too wide for one PR. Land #181's body as:

1. **PR-1: backend chassis** — Strands wiring in `chat_agent.py`, new `tool_hooks.py`, memory-invariant docstring + hardened test, EMF counters (`metrics.py`), `current_time` registered behind feature flag. **No SSE protocol changes, no SPA changes.** Hooks are exercised by unit tests; no end-to-end stream yet.
2. **PR-2: SSE protocol** — four new event types in `strands_sse.py` `translate_event()`, server-side wiring through `chats.py`, integration tests asserting the byte shape. Frontend still ignores them.
3. **PR-3: SPA step list + smoke test** — `useChatStream.js` handlers, `Conversation.jsx` collapsible step list, e2e test exercising `current_time` end-to-end (or a Playwright fixture if e2e is too slow).

Each PR is independently revertible. #181 closes when PR-3 merges.

### P5 — Spike sync checkpoint

When Stream B's spike doc enters review, Stream A pauses for ~30 min:
- Read the spike doc.
- Ask: "does this want `build_agent()` shaped differently? Does it want a different per-tool-provider hook scope?"
- If **yes and small** (e.g., parameter rename, signature tweak): roll into the in-progress chassis PR.
- If **yes and large** (e.g., MCP wants registry-managed `ToolProvider` lifecycle): close the in-progress chassis PR at a clean boundary, file a follow-up "chassis revision for MCP" issue blocked by #128-D, continue chassis as planned.
- If **no**: resume chassis.

Strands' `MCPClient` is itself a `ToolProvider`, so a flat `tools=[...]` list is already polymorphic on the MCP axis. Most spike outcomes fit without surgery.

### P6 — CloudWatch dashboards for chain telemetry

Part of #181's EMF additions (folded into PR-1 above, alongside the counters they read):
- Chain-length distribution (p50/p95/p99 of `tool_calls_used` per chain)
- Chain wall-clock distribution
- Per-tool success/failure ratios (derivable from `ToolCallSuccesses` / `ToolCallFailures` plus tool name as a log-extracted metric — **not** as an EMF dimension, per the cardinality discipline)

When p95 chain length trends above 6 (out of 8-cap) after #182 + #183 are live, that's the cue to revisit AgentCore Runtime migration per [ADR-0004].

## Risks

### R1 — Spike output reshapes chassis mid-flight

Stream B might conclude the chassis needs different hook scopes or `build_agent()` shape. Mitigated by P5 (sync checkpoint) — most outcomes fit a flat `ToolProvider` list; large outcomes queue a follow-up rather than expanding #181.

### R2 — `current_time` becomes load-bearing accidentally

If it works, it's tempting to flip the prod feature flag. Mitigated by P2 (explicit policy + reviewer enforcement on flag flips in prod).

### R3 — Stub `RemainingBudget` leaks "unknown" into the addendum

Mitigated by P1 (addendum **omits** the budget line under stub; swap-in PR adds the line for the first time).

### R4 — #182 + #183 share too much `Conversation.jsx` surface

Mitigated by P3 (`ToolResultBlock` introduced by #182, extended by #183).

### R5 — `size:l` chassis hides three latent PRs

Mitigated by P4 (three sequential PRs).

### R6 — Lambda 5-min ceiling on long chains, only visible post-launch

Mitigated by P6 (dashboards land with the chassis) — we'll know when to act before users notice.

## Cross-references

- Epic tracker: [#128](https://github.com/warlordofmars/channel/issues/128) + decisions comment
- Sub-issues: [#181](https://github.com/warlordofmars/channel/issues/181) (chassis), [#182](https://github.com/warlordofmars/channel/issues/182) (Exa), [#183](https://github.com/warlordofmars/channel/issues/183) (code-exec), [#184](https://github.com/warlordofmars/channel/issues/184) (MCP spike)
- Out-of-band dependency: [#131](https://github.com/warlordofmars/channel/issues/131) (`RemainingBudget` primitive)
- [ADR-0009] — unified context budget (PR #180, merged 2026-06-04 on development)
- [ADR-0004] — AgentCore Runtime feasibility (revisited under R6)
- PR #185 — CLAUDE.md tool-payloads + Strands-native invariants (already merged; #181's "CLAUDE.md fallback ship path" task is satisfied)
- Strands 1.41.0 paths cited in decisions comment §5

[#128]: https://github.com/warlordofmars/channel/issues/128
[#131]: https://github.com/warlordofmars/channel/issues/131
[ADR-0004]: ../../adr/0004-agentcore-runtime-feasibility.md
[ADR-0009]: ../../adr/0009-unified-context-budget.md

## Follow-up

After this spec is approved, the next actions are:

1. **Issue body updates** (separate, low-risk passes — respect "Pause when parallel work in flight" memory; confirm no parallel session first):
   - #181 — append "see strategy spec [link]"; clarify three-sub-PR shape; remove the CLAUDE.md task (#185 satisfied it); add the `current_time` tool task with the flag policy.
   - #182 — add `ToolResultBlock` task per P3.
   - #184 — add the P5 sync-checkpoint expectation in the body.
2. **Stream A kickoff** — `writing-plans` produces the implementation plan for #181 PR-1 (backend chassis). Chassis design doc (`2026-06-XX-tool-use-chassis-design.md`) gets written in that session as the phenomenology preamble + hook architecture details.
3. **Stream B kickoff** — separate session for the MCP-registry spike doc.
