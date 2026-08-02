---
name: adr-write
description: Conventions for writing a project ADR — file naming, the Date/Status header line shape, the Context → Decision → Consequences structure (plus when to add Findings or Alternatives considered), the index-update protocol, and when to file an ADR vs a design-review issue.
status: full
triggers:
  paths:
    - "docs/adr/**.md"
  areas: []
---

# adr-write

Architecture Decision Records in this project follow a fixed
shape established across ADR-0001 through ADR-0006. The shape is
deliberately small: file-naming convention, three-line header
block, three required sections (with two optional ones), and a
single-row update to the index in `docs/adr/README.md`. Each
convention is mechanically checkable against the existing six
ADRs; the canonical examples are cited inline by file and line
range.

The conventions exist because ADRs are read months after they
land — usually by an agent or contributor trying to figure out
why a constraint exists. Predictable structure means the reader
can skim straight to the section they need (Decision for "what's
the rule", Context for "why", Consequences for "what does this
unblock or block"). Drift in the header shape or section order
breaks that skim path.

## 1. File naming and numbering

Path: `docs/adr/NNNN-kebab-title.md`.

- **`NNNN`** — sequential, zero-padded to four digits. Pick the
  next free number by listing `docs/adr/`. Never reuse a
  number, even if a previous ADR is `Deprecated` or
  `Superseded`. The deprecated/superseded record stays in place
  with its original number.
- **`kebab-title`** — lowercase, hyphenated, descriptive of the
  decision (not the verb). Existing examples:
  `0001-bedrock-converse-api.md`,
  `0002-streaming-lambda-web-adapter.md`,
  `0003-inline-agent-and-session-memory.md`,
  `0005-orchestrator-agent.md`,
  `0006-skills-system.md`.
- **Long titles get abbreviated.** Existing slugs sit in the
  18–36 character range (e.g. `0006-skills-system` at 18,
  `0003-inline-agent-and-session-memory` at 36); aim for a slug
  that reads cleanly when listed alongside the others.

Long-form ADRs may ship a companion summary file using the
`NNNN-<title>-summary.md` form — `0004-agentcore-runtime-feasibility-summary.md`
is the only current example, distilling a long spike report
(~270 lines) into a short summary (~75 lines) for a
downstream issue. The summary uses a free-form section
structure, not the canonical template, because it is a
derived artifact pointed at a specific consumer (#48 in
that case). Don't reach for this shape unless the source
ADR is itself unusually long *and* a downstream consumer
needs the distilled form.

## 2. Header block — three lines, exact shape

The first three lines of every ADR are fixed:

```markdown
# ADR-NNNN: Title
Date: YYYY-MM-DD  
Status: Accepted | Deprecated | Superseded by ADR-XXXX
```

Two non-obvious details, both load-bearing:

- **The `Date:` line ends with two trailing spaces** — the
  markdown hard-line-break shape, so the rendered output keeps
  `Date` and `Status` on adjacent lines without a blank between
  them. All six existing ADRs carry the trailing whitespace; an
  editor that strips trailing whitespace on save will collapse
  the two lines together when rendered. Verify with
  `head -3 docs/adr/0001-bedrock-converse-api.md | od -c` if
  unsure.
- **`# ADR-NNNN: Title`** — the document title is one H1.
  Subsequent sections are H2 (`##`). No H1 inside the body.

The status field is one of three exact strings:

- **`Accepted`** — current and in force. New ADRs almost always
  ship as `Accepted`; the design conversation that produces
  the ADR is the acceptance.
- **`Deprecated`** — no longer accurate but kept for history.
  Use when the decision has been reversed or the surface it
  governs has been removed, but no replacement ADR exists.
- **`Superseded by ADR-XXXX`** — replaced by a newer ADR.
  Readers following the older ADR should pivot to the newer
  one. The `XXXX` is the four-digit number (matching §1) of
  the replacement.

When superseding an ADR, update both files in the same PR: the
old ADR's status flips to `Superseded by ADR-XXXX`, the new ADR
opens with a `## Context` section that names what it replaces.

## 3. Required sections — Context, Decision, Consequences

Every ADR carries these three H2 sections in this order. The
canonical short example is the entire body of
`docs/adr/0001-bedrock-converse-api.md:5-48`:

```markdown
## Context
What situation or problem prompted this decision?

## Decision
What did we decide to do?

## Consequences
What are the trade-offs, follow-on work, or constraints this
decision creates?
```

That template lives at `docs/adr/README.md:8-22` and is the
schema contract; do not invent a fourth required section.

### 3.1 Context

What problem prompted the decision. Cite the surface that
forced the choice — current code state, external constraint
(IAM rule, service quota, vendor API shape), or the issue
that opened the question. ADR-0001 §Context lists three
goals (model-agnostic, AWS-native, non-streaming first) plus
the two API options on Bedrock; the reader can immediately
see why a choice between them was unavoidable.

ADR-0003 §Context (`docs/adr/0003-inline-agent-and-session-memory.md:5-34`)
demonstrates a sub-heading inside Context (`### Session
memory`) when the problem has two distinct surfaces. Use
`### `-level sub-headings sparingly — most ADRs don't need
them; reach for one only when the Context section would
otherwise mix two unrelated concerns under a single heading.

### 3.2 Decision

What was decided, in a form a future reader can apply.
Numbered lists are preferred over prose for multi-part
decisions — see ADR-0002 §Decision (`docs/adr/0002-streaming-lambda-web-adapter.md:41-66`)
where five numbered items each carry a sub-bullet block of
implementation specifics. For a single-axis decision, prose
is fine — see ADR-0001 §Decision
(`docs/adr/0001-bedrock-converse-api.md:22-36`).

When the Decision needs sub-headings (rare but legitimate),
use `### `-level for sub-sections. ADR-0006 §Decision
(`docs/adr/0006-skills-system.md:37-214`) is the strongest
example — eight `### ` sub-sections (`Skill file layout`,
`Frontmatter schema`, `Discovery trigger`, etc.) under one
Decision heading. Reach for this shape when a decision has
genuinely multiple axes that each deserve their own block;
don't sub-head a short decision artificially.

### 3.3 Consequences

Load-bearing. Don't skip it.

The Consequences section is what makes an ADR useful months
later — it's the section a reader checks to figure out
"what does this decision still constrain me to do today?"
Three patterns recur:

- **Trade-offs accepted.** ADR-0002's "cold-start overhead
  increases slightly" (`docs/adr/0002-streaming-lambda-web-adapter.md:73-76`)
  names a real cost the decision incurs, with the
  mitigation (`AWS_LWA_READINESS_CHECK_TIMEOUT`).
- **Follow-on work.** ADR-0005 §Consequences
  (`docs/adr/0005-orchestrator-agent.md:127-192`) and
  ADR-0006 §Consequences
  (`docs/adr/0006-skills-system.md:246-301`) both close
  with `Future work — <topic>` bullets pointing at follow-up
  issues. The "Future work" prefix is the established
  convention; use it when the consequence is "we'll need
  to do X later".
- **IAM / CDK / migration implications.** ADR-0003
  (`docs/adr/0003-inline-agent-and-session-memory.md:69-72`)
  documents the `bedrock:InvokeInlineAgent` IAM addition
  and the migration path to pre-configured agents. ADR-0002
  documents the layer ARN pinning. Wherever the decision
  forces a per-environment migration, name it explicitly so
  the next deploy isn't a surprise.

The strongest Consequences sections cite the specific files
or constructs the decision now constrains — see ADR-0004
§Consequences (`docs/adr/0004-agentcore-runtime-feasibility.md:226-271`)
which ties each consequence to a downstream issue (#48,
#37) or a CLAUDE.md update.

Length: bias short, but never sacrifice the Consequences
section to keep the ADR under a target line count. ADR-0001
fits in 48 lines because the decision is single-axis;
ADR-0006 is 301 lines because the surface area is genuinely
larger. Match the length to the decision's scope, not the
other way around.

## 4. Optional sections — Findings, Alternatives considered

Two H2 sections appear in some but not all ADRs. Use them
only when the decision warrants them.

### 4.1 Findings (between Context and Decision)

For ADRs that distill a research spike or inventory phase,
a `## Findings` section names what the spike learned before
the Decision section commits to a specific path. The single
canonical example is ADR-0005
(`docs/adr/0005-orchestrator-agent.md:41-62`) — a "what's
already in the repo" inventory before proposing the new
agent. Findings there documents the existing observability
that makes the live-query approach viable; splitting it
out keeps the Decision section focused on what the team
committed to rather than how the team got there.

ADR-0004 is the variant where the spike body is large
enough to warrant section-level structure but doesn't
graduate to a top-level Findings: it carries an `### Findings`
sub-heading inside `## Context`
(`docs/adr/0004-agentcore-runtime-feasibility.md:5-173`),
keeping the spike narrative under the Context umbrella.
Use the H3-inside-Context shape when the findings *are*
the context (the reader needs them to understand the
problem); reach for the H2 standalone Findings shape when
the findings are a distinct phase between framing the
problem and committing to the decision (ADR-0005's
inventory).

If the ADR is a straightforward decision without a spike
or inventory phase, skip both shapes — `## Context`
handles "what's the situation" on its own.

### 4.2 Alternatives considered (between Decision and Consequences)

The `## Alternatives considered` section names the paths
*not* taken and why. Use it when the decision is genuinely
multi-option and a future reader would otherwise re-derive
the rejected paths.

ADR-0005 §Alternatives considered
(`docs/adr/0005-orchestrator-agent.md:103-125`) and
ADR-0006 §Alternatives considered
(`docs/adr/0006-skills-system.md:216-244`) both use the
same shape: bolded option label `**(a) Description.**`
followed by a one-paragraph "rejected because..." or
"chosen because...". Three to four options is the typical
count.

Inline alternatives (a paragraph in Context naming "the two
main API options" — `docs/adr/0001-bedrock-converse-api.md:19-20`)
are fine for two-option decisions where one is obviously
correct. Reach for the dedicated section when there are
three or more genuine candidates and rejecting them needs
prose.

## 5. Update the index in `docs/adr/README.md`

Every new ADR appends a row to the index table at
`docs/adr/README.md:24-33`, in the same PR that adds the ADR
file. The current shape:

```markdown
| ADR | Title | Status |
|---|---|---|
| [0001](0001-bedrock-converse-api.md) | Bedrock Converse API as the agent LLM interface | Accepted |
...
| [0006](0006-skills-system.md) | Skills system for agent-loaded reference material | Accepted |
```

Conventions:

- **Number is a relative-link** to the ADR file (just the
  filename, not a path — the README and the ADRs share a
  directory).
- **Title** mirrors the ADR's H1 minus the `ADR-NNNN: `
  prefix.
- **Status** mirrors the ADR's `Status:` field.

When superseding an ADR, update the older row's Status cell
to `Superseded by ADR-XXXX` in the same PR — don't leave
the index lying about the older record's currency.

## 6. ADR vs design-review issue — when each applies

The `design-review` agent processes issues labelled
`status:design-needed` by posting a decisions comment and
flipping the label based on the outcome — `status:ready`
when fully specified, `status:blocked` when an in-repo
dependency is named, or `status:needs-info` when waiting
on off-platform info (see `.claude/agents/design-review.md`
§"Phase 2 — label flip"). Some of those decisions land as
ADRs; most don't. The rule is mechanical:

| Surface | Right home | Why |
| --- | --- | --- |
| Architectural constraint that shapes future code (IAM scope, single-table key prefix, streaming wire format, agent-system contract) | **ADR** | The constraint outlives the issue that produced it; readers months later need to find it. |
| Implementation decision contained to a single feature (which library to use, which UI tab to put a control on, which env var name) | **Issue body / decisions comment** | The decision is consumed by the PR that closes the issue and rarely re-litigated. |
| Decision that *hasn't* been made yet | **`status:design-needed` issue** | Needs the design pass before implementation. Promote to ADR if the outcome is architecturally durable. |

Some issues produce both: the design-review issue's
decisions comment becomes the seed for the ADR's Context +
Decision sections, and the design-review issue closes when
the ADR lands. ADR-0005 captures this case explicitly
(`docs/adr/0005-orchestrator-agent.md:152-158` —
"`status:design-needed` on issue #59 is satisfied by this
ADR").

When in doubt: if removing the decision would force a
future agent to re-derive the same constraint, the decision
is architectural — file the ADR. If the decision only
matters until the next PR ships, the issue body is enough.

The same boundary appears from the other side in
CLAUDE.md §"Product decisions" — durable architectural
choices that constrain future designs live in CLAUDE.md
when they affect *every* invocation, in an ADR when they
affect a specific surface. ADRs and CLAUDE.md product
decisions don't overlap; ADRs cite the surface they
constrain (IAM, single-table keys, agent contracts),
CLAUDE.md product decisions cite the cross-cutting rule
(workspaces are tenancy root, billing deferred, etc.).

## 7. Length — bias short, scale with surface

Existing ADR lengths (counted by `wc -l`, i.e. newline
count — some editors and viewers display one more for the
trailing-newline cursor position):

| ADR | Lines | Why |
| --- | --- | --- |
| 0001 | 48 | Single-axis decision (which Bedrock API). |
| 0002 | 82 | Five-part decision plus IAM and CloudFront notes. |
| 0003 | 76 | Two-axis decision (inline-agent + session memory). |
| 0004 | 271 | Spike report — six investigation areas with citations. |
| 0005 | 192 | New agent definition plus alternatives considered. |
| 0006 | 301 | Multi-axis system contract — frontmatter, discovery, stubs. |

Heuristics:

- **Default to short.** ADR-0001's 48 lines is sufficient
  for most decisions and a useful baseline. A first-draft
  ADR longer than 100 lines should prompt a "is this really
  a single decision?" check — sometimes it should be split
  into two ADRs (e.g. ADR-0002 streaming + ADR-0003 inline
  agent are two separate records, not one combined).
- **Spike outcomes are long because the citations are
  load-bearing.** ADR-0004's length is justified by the
  AWS-doc citations and the boto3 service-model
  inspections — a future reader following the rationale
  needs the same evidence the spike used. Don't trim those
  citations.
- **System contracts are long because the contract surface
  is the ADR.** ADR-0006 is the schema contract for every
  future skill — every clause earns its place. Don't trim
  to hit a length target.

The opposite slip — padding a short decision to feel
"thorough" — is the more common mistake. ADR-0001 doesn't
include a Findings or Alternatives considered section
because the decision didn't warrant them; resist the urge
to add empty section scaffolding.

## 8. Voice and citations

Across the existing six ADRs, the voice is:

- **Plural first-person present tense for decisions** —
  "Use the Bedrock Converse API" not "We will use the
  Bedrock Converse API"; the Decision section is
  prescriptive.
- **Past tense for Context** when describing how the
  situation arose — "AgentCore Starter needed a standard
  interface..." (ADR-0001 §Context — quoted verbatim; that ADR
  predates the fork rename, so it names the template rather than
  Channel. New ADRs say "Channel").
- **Code blocks are language-tagged** — `python`, `text`,
  `markdown`, `yaml`. Existing ADRs that contain code
  fences (ADR-0004 uses `text`, ADR-0006 uses `yaml` and
  `markdown`) all tag the language; new ADRs should match.
- **Citations to other ADRs use the `ADR-NNNN` form**
  (e.g. "see ADR-0002") with the file path in parentheses
  on first reference if the ADR is not the immediately
  preceding one. ADR-0006 §Consequences shows the path
  citation shape (`docs/adr/0005-orchestrator-agent.md`).
- **Citations to issues use the `#NNN` form** — bare in
  most contexts (e.g. "tracked in #29"); the longer
  "issue #NNN" form is also fine when the surrounding
  prose reads better with it (ADR-0004 §Context line 7,
  ADR-0005 §Consequences line 152). Either is valid; pick
  whichever reads cleaner in the sentence.
- **Citations to code use `path:line` or `path:line-line`**
  consistent with the rest of the project — see ADR-0006's
  references to skill frontmatter rules.

Sub-headings inside `## Context` or `## Decision` use H3
(`### `). Lists use `-` for bullets, `1.` / `2.` for
numbered items. Tables are fine for prefix taxonomies,
quota lists, or alternative comparisons — ADR-0004's
"Integration options" table
(`docs/adr/0004-agentcore-runtime-feasibility.md`, the
Option / Description / Cost table inside `## Decision`)
is the canonical example.

## See also

- `docs/adr/README.md` — index of existing ADRs and the
  canonical three-section template.
- `docs/adr/0001-bedrock-converse-api.md` — shortest ADR
  in the set; canonical shape for a single-axis decision.
- `docs/adr/0006-skills-system.md` — longest contract-style
  ADR; canonical shape for a multi-axis system decision
  with `Alternatives considered`.
- `docs/adr/0005-orchestrator-agent.md` — canonical shape
  for a decision with `## Findings` between Context and
  Decision.
- `.claude/agents/design-review.md` — when a
  `status:design-needed` issue's decisions comment becomes
  the seed for an ADR.
- CLAUDE.md §"Product decisions" — the cross-cutting
  product rules that complement (don't overlap) ADRs.
