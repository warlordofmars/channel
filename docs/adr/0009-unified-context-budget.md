# ADR-0009: Unified context budget across history, recall, attachments, and tool results
Date: 2026-06-03  
Status: Accepted

## Context

Channel's prompt context is filled from four pools that today have no
shared budget model:

- **Conversational history** — recent message turns loaded by
  `src/channel/api/chats.py` and replayed into the Strands `Agent`.
- **Recall context** — prior-chat content injected by
  `src/channel/agents/recall.py` via `BeforeInvocationEvent`, capped
  today by `_RECALL_MAX_SESSIONS = 5` and similar constants but not
  by a token budget.
- **Attachments** (#109, finalized 2026-06-03) — user-uploaded PDFs,
  images, spreadsheets forwarded as Strands multipart `ContentBlock`s
  with `s3Location` sources. Bedrock fetches the bytes; the bytes
  count against the model's input window even though they don't pass
  through Lambda.
- **Tool use** (#128, in design) — tool definitions in `toolConfig`,
  `ToolUse` / `ToolResult` blocks injected per step in a chained call.

Each pool is governed by a different file, different conventions, and
different "how big can this get" assumptions. With no shared budget
they will compete:

- A 50-page PDF attached at turn N can crowd out recall context from
  earlier sessions.
- A multi-step web-search chain can blow chat history out by injecting
  raw tool results.
- Recall context tuned independently can starve attachment payloads.

The cross-cutting observation arrived in Channel-the-product's
2026-06-03 design input on #128 (comment 4617260888): "Tool use,
attachments, memory, long-chat handling — they're all faces of the
same problem: what occupies my attention, and how do I allocate
limited context wisely?" The reply also proposed an explicit
allocation order, which this ADR adopts.

Issue #131 (long-chat handling: trim / rolling summary / selective
retrieval) is already in design and would have invented a budget
mechanism anyway. This ADR is the umbrella over what that mechanism
must support so #131, #128, and the recall hook don't each invent it
differently.

## Decision

Treat the model's prompt input window as a **single budget** allocated
across four pools in priority order:

1. **Conversational history** — the thread of continuity. Highest
   priority; never summarized below a minimum floor (defined by #131).
2. **Recall context** — cross-session relevance. Fixed slice; the
   recall hook yields what fits and truncates the rest.
3. **Attachments** — the largest, most compressible pool. If a single
   attachment would exceed its slice, it is summarized or chunked
   rather than crowding out 1 or 2.
4. **Tool results** — ephemeral by nature; older steps may be
   summarized so the chain can continue.

The budget primitive is owned by #131 (long-chat handling).
`RemainingBudget` becomes a first-class concept consumed by:

- The recall hook (yields a sized slice).
- The chats send-path attachment-build logic (decides whether to
  summarize before forwarding).
- The #128 chassis tool hooks (`BeforeToolCallEvent` /
  `AfterToolCallEvent`) which surface remaining budget to the model
  via a system-prompt addendum so the model can economize its
  tool-result compressibility decisions (Channel's "model picks raw
  vs. summary per-step" point — comment 4617260888).

Each pool's emitter is responsible for **compressibility** — the
budget primitive tells the emitter "you have N tokens left in your
slice"; the emitter decides how to fit.

## Alternatives considered

**(a) Per-pool independent budgets.** Each pool gets a hard cap
unrelated to others. Simpler to implement; rejected because the caps
either waste headroom (when one pool is empty) or fight each other at
the model boundary (when summed they exceed the input window).

**(b) Unbounded with explicit overflow.** Let context saturate; drop
oldest content first. Rejected because the dropped content is
unpredictable — a recall context can be evicted by a single large
attachment with no model visibility into the trade. Also makes the
"show the plan" UI surface (#128 chassis) impossible to reason about.

**(c) Unified budget with explicit allocation order.** Chosen.
Predictable, visible to the model, names a single owner (#131) of the
budget primitive.

## Consequences

- **#131 (long-chat handling)** owns `RemainingBudget` and the
  history-pool summarization mechanism. The design pass for #131 must
  define this primitive's API (likely a method on a `BudgetState`
  dataclass passed through hook events) and the history-minimum-floor
  constant.
- **#128 (tool-use chassis)** consumes `RemainingBudget` in
  `BeforeToolCallEvent`. The chassis surfaces remaining budget to the
  model via a system-prompt addendum injected by a
  `BeforeModelCallEvent` hook. This ADR makes the "model sees its
  remaining budget" decision durable so #128's design pass doesn't
  re-litigate it.
- **#109 attachments (already filed)** may need a follow-up sub-issue
  to add summarize/chunk logic for single attachments that exceed
  their allocation slice. v1 sub-issues #173–#179 ship with a hard
  5-files-per-turn / 20MB-per-file cap and no per-attachment
  summarization; the gap is documented as future work pending the
  budget primitive landing.
- **Recall hook (`src/channel/agents/recall.py`)** gains a
  "yielded slice" concept tied to `RemainingBudget`. The current
  `_RECALL_MAX_SESSIONS = 5` and `_RECALL_EVENTS_PER_SESSION = 2`
  constants survive as hard caps but become subordinate to the slice
  budget once #131 lands.
- **UI / `ui/src/app/views/Customize.jsx`** — a future sub-issue
  surfaces the budget caps to the user (#128's issue body already
  notes this). Server prefs (#113) is the plumbing layer; the budget
  primitive in #131 is the source of truth.
- **Memory write hook (`src/channel/agents/memory.py`)** — tool-result
  payloads and attachment payloads do NOT persist to AgentCore
  Memory; conclusions and META facts (e.g. "John attached spec-v2.pdf
  and asked about section 4") may. This ADR is the architectural
  reason: persisting raw payloads inflates the recall pool's
  effective size at retrieval time, undermining its budget slice. The
  detailed write policy is codified by #128's chassis design pass.

Future work — once #131, #128, and the recall hook implement this
ADR, revisit and tighten the constants (history floor, recall slice,
attachment allocation fraction) based on real chain measurements.
