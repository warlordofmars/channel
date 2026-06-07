# Tool-use chassis (epic #128 / #181) — design

**Date:** 2026-06-06
**Status:** Shipped (via PR-1 [#209], PR-2 [#219], PR-3 this PR (#220))

## Phenomenology preamble: "like having hands"

The chassis exists so the model can reach for the world when it needs
to, without ceremony. It is not a parallel tool-calling shim layered
beside Strands; it is the wiring that makes Strands' native machinery
present in our SSE protocol, our memory writes, our system prompts,
and our SPA. The model decides to call a tool. The chassis carries
the consequence — telemetry, budget visibility, cancellation,
collapse-on-failure — back through every layer that needs to know.

The framing came out of Channel-the-product's 2026-06-03 design
input: "tool use, attachments, memory, long-chat handling — they're
all faces of the same problem: what occupies my attention, and how
do I allocate limited context wisely?" That observation became
[ADR-0009] (unified context budget). The chassis is the first epic
that consumes that ADR: it surfaces per-chain progress
(`tool calls used: N of 8`) to the model via a
`BeforeModelCallEvent` addendum so the model can economize. The
parallel `RemainingBudget` slot from ADR-0009 stays omitted under
the v1 stub (policy P1 below — the #131 swap-in PR adds the line
for the first time).

The chassis exit test is `current_time`. The smoke test on dev on
2026-06-07 was the first time the model reached out and checked the
clock without being told to — without a "here is the current time"
preamble in the system prompt. Hands. The tool itself is trivial.
The plumbing is the product. See P2 below for why `current_time`
ships behind a feature flag and stays off in prod.

## MCP-Sampling vs MCP-as-tool-server

CLAUDE.md's "client-side LLM preferred" product decision governs MCP
**Sampling** (the server-asks-client-for-completion direction). This
epic ships MCP-as-tool-server (server provides a capability, the LLM
is us). The product decision does not constrain this direction. See
the [#128 decisions comment §8][#128-decisions] for the explicit
ruling.

## Architecture overview

Three hook handlers, four SSE event types, one SPA collapsible step
list. The model never sees any of it directly — it sees Strands'
native tool-use stop reason, an addendum to its system prompt, and
the tool results Strands feeds back into the next model call. The
chassis is the layer that makes that loop legible to operators,
durable in CloudWatch, and visible to the user.

```
User → SPA → POST /api/chats/{id}/messages → _stream_bedrock_reply
  → build_agent(...) returns Strands Agent with:
      tools = [current_time, ...]
      hooks = [
        ModelVisibilityAddendumHook,   # BeforeModelCallEvent
        ToolCallGuardHook,             # BeforeToolCallEvent
        ToolCallTelemetryHook,         # AfterToolCallEvent
        AgentCoreRecallHook,           # BeforeInvocationEvent
        AgentCoreMemoryHook,           # AfterInvocationEvent
      ]
      agent.chain_state = ChainState(...)   # monkey-patched, parallel to agent.chat_id
  → Strands event_loop fires:
      BeforeModelCallEvent → addendum hook injects "tool calls used: N of 8"
      model emits stop_reason: "tool_use"
      BeforeToolCallEvent → guard hook checks cancel-signal | chain_cap | wall_clock
      tool runs
      AfterToolCallEvent → telemetry hook: increment counter, EMF, [meta] memory write
      Strands stream events translate to SSE:
        translate_event → ("tool_started" | "tool_progress" |
                           "tool_finished" | "tool_error", payload)
        → sse_tool_* emitters → SSE bytes on the wire
  → SPA's useChatStream collects payloads onto activeMessage.toolSteps[]
  → Conversation.jsx renders collapsible step list under the assistant turn
```

## Hook architecture

Three hooks, each subscribed to exactly one Strands event. Each
conforms structurally to the `HookProvider` protocol
(`strands.hooks.registry`) — no explicit base class, matching the
existing pattern in `src/channel/agents/memory.py` and
`src/channel/agents/recall.py`.

### `ModelVisibilityAddendumHook` — `BeforeModelCallEvent`

Reads: `event.agent.chain_state` (the `ChainState` dataclass set by
`build_agent`).

Writes: `event.agent.system_prompt` — strips any existing addendum
block bounded by `<tool-use-budget>` / `</tool-use-budget>` sentinels,
then re-appends the current line:

```
\n\n<tool-use-budget>
tool calls used: N of M
</tool-use-budget>
```

The XML-ish sentinel is for two reasons: Claude models pattern-match
them in instructions, and the strip-and-reappend pass keeps the
prompt from compounding across the multiple `BeforeModelCallEvent`
fires that happen inside a single chain (one per tool-use round trip).

**Policy P1** (epic #128 strategy spec): the `RemainingBudget` line is
OMITTED under the v1 stub. The #131 swap-in PR adds it for the first
time without changing the existing line. Reason from the strategy
spec: leaking "unknown" into the system prompt shapes model behavior
in ways that diverge from what real-#131 will produce.

Failure mode: fail-soft. If `chain_state` is missing the hook returns
without mutating the prompt — this allows the chassis to no-op when
exercised by code paths that haven't been threaded yet.

See `src/channel/agents/tool_hooks.py` (`ModelVisibilityAddendumHook`).

### `ToolCallGuardHook` — `BeforeToolCallEvent`

Three gates, evaluated in order of decreasing user-intent fidelity:

1. **`cancelled`** — the chat's entry in the cancel-signal registry is
   set; the user already decided to stop. Other reasons would be
   misleading if the user disconnected.
2. **`chain_cap`** — `ChainState.is_chain_cap_exhausted()`; the
   structural ceiling on tool-call depth.
3. **`wall_clock`** — `ChainState.is_wall_clock_exhausted()`; soft
   budget. Last because it is the fuzziest signal and tends to fire
   after the chain has already produced useful work.

**Cancellation API.** The key probe finding from PR-1 Task 6:
`BeforeToolCallEvent.cancel_tool` in Strands 1.41.0 is a **writable
field**, not a method (`_can_write` returns True for the set
`["cancel_tool", "selected_tool", "tool_use"]` — see
`strands/hooks/events.py`). Assigning a string sets the cancellation
reason; Strands wraps the cancelled call into a `ToolResult` whose
`content` carries the reason as a text block.

That reason string is the contract between the guard and the SSE
layer. `translate_event` extracts the text from
`ToolResult.content` and surfaces it as `error_type` in the
`tool_error` SSE payload — so the guard's vocabulary (`cancelled` /
`chain_cap` / `wall_clock`) flows verbatim to the SPA, which can
render the right affordance.

**One-shot consume.** When the guard observes a `cancelled` signal it
clears the entry immediately. This is defense in depth alongside the
entry-time clear in `_stream_bedrock_reply` and the TTL prune —
without it, a `cancelled` signal could survive into a subsequent
turn on the same chat in a warm Lambda.

The `RemainingBudget` gate is intentionally absent from v1 — the
#131 swap-in PR adds it once the context-budget plumbing lands. See
[ADR-0009] for the cross-cutting reason.

### `ToolCallTelemetryHook` — `AfterToolCallEvent`

Three side effects on every tool call (success or failure):

1. **Increment `ChainState.tool_calls_used`.** Failures count too — the
   chain budget is spent whether the tool succeeded, raised, or was
   cancelled by the guard. Surfacing failures as "free" would let a
   misbehaving tool loop indefinitely. `increment()` clamps at the
   max so the addendum stays well-formed past the cap.
2. **Emit `ToolCallSuccesses` / `ToolCallFailures` EMF** via
   `record_tool_call_outcome`. See "EMF cardinality discipline" below.
3. **Best-effort `write_meta_event`** to AgentCore Memory:
   `"used <tool_name>"` on success, `"tried <tool_name>"` on failure.
   The verb split keeps the success/failure signal legible in raw
   recall text; the existing `[meta]` prefix is added by
   `AgentCoreMemoryHook.write_meta_event`.

**Failure detection** OR's three independent Strands signals:
`event.exception is None AND event.cancel_message is None AND
event.result.status != "error"`. Any one collapses to `success=False`.

**Synchronous callback, async EMF.** Strands' `HookRegistry` doesn't
await callbacks. `record_tool_call_outcome` is `async`, so the hook
fires-and-forgets via `asyncio.create_task(...)`. A strong-ref
`_pending_tasks: set[asyncio.Task]` with `add_done_callback(discard)`
keeps the task alive past GC — same pattern as
`AgentCoreMemoryHook._on_after_invocation` (Sonar `python:S7502`). The
inner coroutine `_record_outcome_safe` swallows + logs any exception
so the task never raises into asyncio's "Task exception was never
retrieved" warning channel.

See `src/channel/agents/tool_hooks.py` (`ToolCallTelemetryHook`).

## `ChainState` lifecycle

One instance per `build_agent()` call (per chat turn), attached to
the Strands `Agent` as `agent.chain_state` — parallel to the
existing `agent.chat_id` monkey-patch.

```python
@dataclass
class ChainState:
    tool_calls_used: int = 0
    tool_calls_max: int = 8
    wall_clock_budget_sec: int = 120
    started_at: float = field(default_factory=time.monotonic)
```

- `increment()` clamps at `tool_calls_max` (`min(used + 1, max)`).
  The cap predicate `is_chain_cap_exhausted` still fires the moment
  the cap is reached; clamping prevents misleading addenda like "tool
  calls used: 9 of 8" if the model keeps attempting calls after the
  cap.
- `is_chain_cap_exhausted()` / `is_wall_clock_exhausted()` are pure
  predicates the guard hook calls.

The state does NOT persist across turns. A new `build_agent` produces
a new `ChainState`; the per-chat chain budget resets when the user
sends the next message.

## Cancel-signal registry

A module-level `dict[str, float]` in `tool_hooks.py` mapping
`chat_id → time.monotonic()` timestamp.

```python
_CANCEL_SIGNAL_TTL_SEC = 300  # 5 min — longer than any chain wall-clock budget
_CANCEL_SIGNALS: dict[str, float] = {}
```

**Setting.** `chats.py`'s `_stream_bedrock_reply` calls
`set_cancel_signal(chat_id)` from its
`except (asyncio.CancelledError, GeneratorExit)` block when the SSE
client disconnects. The signal is intentionally NOT cleared in a
`finally:` block — it must persist past the dying generator to be
observable by the next `BeforeToolCallEvent`.

**Three clearing mechanisms (defense in depth):**

1. **Entry-time clear** — the next turn's `_stream_bedrock_reply` for
   this chat calls `clear_cancel_signal` at the top of the function,
   wiping any stale signal left over from a previous disconnect.
2. **Guard one-shot consume** — `ToolCallGuardHook` clears the signal
   immediately after observing it and assigning
   `event.cancel_tool = "cancelled"`.
3. **TTL prune** — `_prune_expired_cancel_signals` drops any entry
   older than `_CANCEL_SIGNAL_TTL_SEC` on every public op (`set`,
   `clear`, `is_cancel_requested`). Bounds the registry against the
   disconnect-without-followup leak in a warm Lambda — a chat that
   disconnects mid-chain and never receives another turn would
   otherwise leave its signal in the registry forever. One O(n) walk
   per call; n stays small in practice.

Cold start clears all signals (no persistence needed — a request
that hasn't reached its first tool call by Lambda restart is already
dead).

## Strands 1.41.0 cited paths

The chassis builds on Strands' native machinery. Cited paths follow,
load-bearing for any future code reader trying to understand why a
particular shape was chosen:

- `strands/types/tools.py` — `ToolUse` / `ToolResult` TypedDicts; the
  vocabulary the chassis reads in `translate_event`.
- `strands/models/bedrock.py` — Converse `toolConfig` build,
  tool-block translation, `stop_reason: "tool_use"`.
- `strands/event_loop/event_loop.py` — `_handle_tool_execution` is
  the loop that fires our `BeforeToolCallEvent` and
  `AfterToolCallEvent`.
- `strands/hooks/events.py` — `BeforeModelCallEvent`,
  `BeforeToolCallEvent`, `AfterToolCallEvent`. Key field:
  `BeforeToolCallEvent.cancel_tool: bool | str` is writable —
  `_can_write` returns True for `["cancel_tool", "selected_tool",
  "tool_use"]`.
- `strands/types/_events.py` — `ToolUseStreamEvent`,
  `ToolResultEvent`, `ToolStreamEvent`, `ToolCancelEvent`,
  `ToolInterruptEvent`. Top-level dicts, NOT nested under the
  `event` envelope. This is what `translate_event` dispatches on.
- `strands/tools/executors/_executor.py` — wraps the guard's
  `cancel_tool` string into `ToolResult.content` as
  `{"content": [{"text": cancel_message}]}`. This is why
  `translate_event` concatenates `text` blocks to surface the
  reason as `error_type`.
- `strands/tools/mcp/mcp_client.py` — `MCPClient` is itself a
  `ToolProvider`, so a flat `tools=[...]` list is polymorphic on the
  MCP axis. The MCP spike (#184 / spec at
  `docs/superpowers/specs/2026-06-06-mcp-server-registry-spike.md`)
  confirms this — no special-case wiring needed when MCP integration
  lands.

## SSE protocol spec

Four new event types. All four flow through `_sse(...)` and end up as
`data: <json>\n\n` on the wire. Payload shapes:

- **`tool_started`** — `{type, tool_name, tool_use_id, args_preview}`.
  `args_preview` truncated to 200 chars (`_ARGS_PREVIEW_MAX`). Sourced
  from Strands' `ToolUseStreamEvent`, which fires once per input token
  while the model emits the call's JSON arguments;
  `_stream_bedrock_reply` tracks `emitted_tool_starts: set[str]` to
  dedupe so the SPA only appends one step row per call.
- **`tool_progress`** — `{type, tool_use_id, status_text}`. Sourced
  from `ToolStreamEvent` whose `data` is a string. Non-string yields
  are skipped.
- **`tool_finished`** — `{type, tool_use_id, summary}`. **`summary` is
  always the literal `"completed"`** in v1. The chassis NEVER leaks
  raw tool output over SSE. Tool-specific structured summaries
  ("Found 3 results" / "Ran 5 lines") arrive in #182 / #183 via an
  explicit `ToolResult` summary field. The actual tool output
  reaches the user via the model's text reply (Strands' event_loop
  feeds the result back into the model). The SSE step row is
  telemetry, not the delivery channel. `_SUMMARY_MAX` (500 chars)
  caps the field at the emitter as defense in depth.
- **`tool_error`** — `{type, tool_use_id, error_type,
  partial_result_count}`. `error_type` is extracted by
  `translate_event` from `ToolResult.content` text blocks — so the
  guard's `cancel_tool="chain_cap"` reaches the SPA as
  `error_type="chain_cap"`. Empty content falls back to
  `"tool_failed"`. Sourced from `ToolResultEvent` with
  `status="error"`, plus `ToolCancelEvent` (`error_type` from
  `cancel.message`, default `"cancelled"`) and `ToolInterruptEvent`
  (`error_type="interrupted"`). `partial_result_count` is populated
  by `_stream_bedrock_reply`'s `completed_tool_calls` counter (not
  by `translate_event`, which can't see chain-level state) so the
  SPA can render "I have 3 of 5 results" instead of a silent abort.
  `error_type` truncated to 200 chars at the emitter.

**Skip semantics.** Events with missing `tool_use_id` return
`("skip", None)` from `translate_event` — uncorrelatable events
don't get emitted because the SPA keys step rows on `tool_use_id`
and can't attach an orphan payload anywhere.

## EMF cardinality discipline

Counter names: `ToolCallSuccesses`, `ToolCallFailures` (namespace
`Channel`). Same shape as the existing `MemoryWriteSuccesses` /
`MemoryWriteFailures` siblings.

- NO per-actor dimension — would scale with user count → unbounded
  cardinality.
- NO per-tool dimension — would scale with tool count, with explicit
  blowup risk once MCP integration adds dynamic tools.
- Per-tool failure breakdowns belong in structured logs (CloudWatch
  Logs Insights can query without dimension blowup).
- Codified by `test_record_tool_call_outcome_signature_locks_out_dimensions`
  in `tests/unit/test_metrics.py` — asserts the signature has exactly
  one parameter (`success: bool`). Same shape as
  `record_memory_write_outcome`'s sibling test (see the `## AgentCore
  Memory` section in CLAUDE.md).

## Model-visibility addendum rationale

The 2026-06-03 design conversation pinned the principle: "walls are
worse than dashboards." A model that knows it has 8 calls max and
has used 3 plans differently than one that hits a wall at call 9.
The addendum is the dashboard; the guard is the wall. Both ship
together because they solve different things — the model can
economize against a visible budget, and the guard ensures a runaway
chain is bounded if it economizes badly anyway.

The addendum format under v1:

```
\n\n<tool-use-budget>
tool calls used: 3 of 8
</tool-use-budget>
```

Sentinel-bounded so re-injection inside the same chain replaces
rather than compounds. Policy P1: the `RemainingBudget` line is
OMITTED under the v1 stub; the #131 swap-in PR adds it.

## Policy callouts

### Three-PR split (strategy P4)

Epic #181 shipped as PR-1 (backend chassis, [#209]), PR-2 (SSE
protocol, [#219]), PR-3 (SPA step list + smoke test + this doc +
CHANGELOG, this PR (#220)). Each independently revertible:

- PR-1 ships hooks + ChainState + `current_time` tool + EMF; no
  end-to-end stream yet, exercised by unit tests.
- PR-2 ships the four SSE event types + dispatcher in
  `chats.py` + cancel-signal-on-disconnect; SPA still ignores them.
- PR-3 ships SPA handlers + collapsible step list + the e2e smoke
  test + this design doc + CHANGELOG.

### `current_time` is the chassis exit test, not a product feature (strategy P2)

- Default `STARTER_CLOCK_TOOL_ENABLED=1` in jc/dev, `0` in prod
  (CDK env var, `is_prod`-gated).
- Reviewer enforcement: any future PR flipping the prod flag must
  justify it as a product decision, not implementation convenience.
- The phenomenology demo on 2026-06-07 ran on dev with this tool
  registered.

### `ToolResultBlock` seed (strategy P3)

PR-3 ships `ui/src/app/ToolResultBlock.jsx` as a discriminated-union
renderer with ONE default text-summary branch. #182 will add
`kind="web-search-citations"`. #183 will add `kind="code-output"`.
Reviewer enforcement on #182: a one-branch component whose extension
shape is obvious from the JSX — not a layer of abstraction that
future-#183 has to fight.

## ADR-0009 cross-reference

The chassis's addendum mechanism is the named consumer of
`RemainingBudget` per [ADR-0009]: the chassis hooks "surface
remaining budget to the model via a system-prompt addendum so the
model can economize its tool-result compressibility decisions." The
v1 stub omits the budget line; the #131 swap-in PR adds it without
changing the existing "tool calls used" line. ADR-0009 names the
role `ModelVisibilityAddendumHook` fills — that naming is why the
hook lives on `BeforeModelCallEvent` rather than (say) a direct edit
at message-build time.

## CloudWatch dashboards gap

Strategy spec policy P6 calls for dashboards on chain-length
distribution (p50/p95/p99 of `tool_calls_used`), chain wall-clock
distribution, and per-tool success/failure ratios (derivable from
the counters plus tool name as a log-extracted metric, NOT as an EMF
dimension). The counters land in PR-1; the dashboard CDK construct
is its own task (~50–100 LOC).

**Follow-up:** file `chore(infra): tool-use CloudWatch dashboards
(epic #128 P6)` after PR-3 merges. `size:s`, `priority:p2`,
`area:infra` + `area:observability`.

Strategy spec also names the trigger for revisiting AgentCore Runtime
migration per [ADR-0004]: p95 chain length above 6 (of 8) after #182
+ #183 are live.

## Tests + coverage

100% Python coverage on chassis modules (CI gate). Test surface:
`test_metrics.py` (EMF + cardinality lockout), `test_memory.py`
(memory-invariant hardening + `write_meta_event`), `test_tool_hooks.py`
(all three hooks + ChainState + cancel-signal registry incl. TTL prune),
`test_tools_clock.py` (registration + flag gating), `test_chat_agent.py`
(`build_agent` wires `tools=[...]` + attaches `ChainState`),
`test_chats_api.py` (dispatcher wiring, cancel-signal on disconnect,
`tool_started` dedup), `test_strands_sse.py` (`translate_event`
dispatch + four emitters + skip semantics), `test_channel_stack.py`
(CDK `STARTER_CLOCK_TOOL_ENABLED` per-env assertion), and
`tests/e2e/test_tool_use_smoke.py` (the chassis exit test, gated on
`STARTER_CLOCK_TOOL_ENABLED=1`).

## Cross-references

- Strategy spec: `docs/superpowers/specs/2026-06-06-epic-128-tool-use-sequencing.md`
- [ADR-0009] unified context budget; [ADR-0004] AgentCore Runtime
  feasibility (revisited under strategy R6)
- MCP spike: `docs/superpowers/specs/2026-06-06-mcp-server-registry-spike.md`
- Strands/AgentCore Memory pattern reference:
  `docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md`
- Epic [#128] + [decisions comment][#128-decisions]; sub-issues [#181]
  (chassis), [#182] (Exa), [#183] (code-exec), [#184] (MCP spike)
- CLAUDE.md updates landed via PR #185 (tool payloads never persist,
  Strands-native invariants)
- Chassis PRs: [#209] (PR-1 backend), [#219] (PR-2 SSE), this PR (PR-3)

[#128]: https://github.com/warlordofmars/channel/issues/128
[#131]: https://github.com/warlordofmars/channel/issues/131
[#181]: https://github.com/warlordofmars/channel/issues/181
[#182]: https://github.com/warlordofmars/channel/issues/182
[#183]: https://github.com/warlordofmars/channel/issues/183
[#184]: https://github.com/warlordofmars/channel/issues/184
[#209]: https://github.com/warlordofmars/channel/pull/209
[#219]: https://github.com/warlordofmars/channel/pull/219
[#128-decisions]: https://github.com/warlordofmars/channel/issues/128#issuecomment-4617554628
[ADR-0004]: ../../adr/0004-agentcore-runtime-feasibility.md
[ADR-0009]: ../../adr/0009-unified-context-budget.md
