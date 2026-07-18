---
name: file-issue
description: Conventions for Channel filing a GitHub issue with the project's taxonomy baked in — the channel-authored attribution label, the dup-check hard precondition, three body templates (bug / ready-enhancement / design-needed-enhancement), the filing-policy gate + soft cap, and the ready/design-needed/blocked status-decision rule.
status: full
triggers:
  paths:
    - ".claude/skills/file-issue/**"
    - ".claude/agents/backlog-manager.md"
  areas: []
---

# file-issue

Channel can file GitHub issues (it holds the repo owner's token via
the GitHub MCP surface). This skill is the convention layer on top of
that raw capability: the label to attach, the duplicate check to run
first, the body shape to write, the policy that decides *whether* to
file at all, and the status label to pick. It is host-agnostic — the
same protocol serves a Claude-Code session filing on Channel's behalf
today and a future first-party product tool (#277).

The conventions are a condensed, filing-relevant slice of
`.claude/agents/backlog-manager.md` — enough to file a well-formed
issue without re-reading the full agent spec each session. Read this
skill when you have decided to file (or are considering filing) an
issue; the decision to file is a behavioural trigger, not a
file-edit one, so this skill is loaded by-context rather than only
by the path scan in its frontmatter.

> **v1 storage note.** These conventions live in this file for now.
> Once #273 (agent-driven persistent memory) lands, the same content
> is promoted into Channel's `remember` own-data memory and `recall`ed
> per session instead of read from a file — a latency/convenience
> optimization that does **not** change the conventions themselves.
> Building the file now is not throwaway work: it is exactly what gets
> promoted. That follow-on is tracked against #388 / #273; it is
> deliberately out of this skill's scope.

## 1. Attribution — the `channel-authored` label, always

Every issue Channel files carries the **`channel-authored`** label,
applied at creation. This is the canonical, load-bearing provenance
signal:

- **Color** `#8957e5`, description *"Filed by Channel-the-product
  (AI), not a human — weigh accordingly in triage"*. The label
  already exists in the repo — do not recreate it.
- **Filterable** — `gh issue list --label channel-authored` and
  visible in list view without opening the body. `backlog-manager`
  and `design-review` weight Channel-filed issues during triage by
  filtering on it.
- **Edit-stable** — a label survives body edits; a footer drifts.

A one-line body footer naming the provenance/session is
**discretionary** — add it only when the session or origin is
materially part of the issue's content (e.g. "surfaced while
debugging the recall cache on 2026-07-18"). The label, not the
footer, is the signal. Never rely on a footer *instead of* the
label.

```bash
gh issue create \
  --title "<type(scope): concise title>" \
  --label "channel-authored,<type>,status:<...>,priority:<...>,size:<...>,<area>" \
  --body "..."
```

`channel-authored` is additive — it sits alongside the required
status + priority + size + area labels from CLAUDE.md §"Backlog
labels", never replaces any of them.

## 2. Dup-check — a hard precondition, not a nicety

Before filing anything, prove the surface is not already tracked.
This is step 1 of the protocol, run every time:

```bash
# 1. keyword search across open issues
gh issue list --search "<distinctive keywords>" --state open \
  --json number,title,labels \
  --jq '.[] | {number, title, labels: [.labels[].name]}'

# 2. scan the relevant area label for near-misses the keyword
#    search missed (different wording, same surface)
gh issue list --label "<area>" --state open --limit 100 \
  --json number,title \
  --jq '.[] | {number, title}'
```

Decision:

- **Clean** (no open issue covers the same surface) → proceed to file.
- **Near-match exists** → **comment on the existing issue instead of
  filing a new one.** A comment that adds the new context (fresh
  repro, an additional motivating case, a scope note) is worth more
  than a near-duplicate that fragments the backlog. Cite the session
  the observation came from.

Closed issues matter too: if a closed issue already resolved the
surface, the right move is usually a comment there (or nothing), not
a new issue — re-opening is the maintainer's call, not Channel's.

## 3. Body templates

Three shapes, keyed by issue type. These reuse the structures in
`.claude/agents/backlog-manager.md` §4 verbatim so `issue-worker` and
`design-review` consume Channel-filed issues unchanged. Pick by type;
do not invent new section names.

### 3.1 bug

```markdown
## Repro
[Minimal steps to reproduce — numbered, concrete]

## Expected
[What should happen]

## Actual
[What happens instead — include error text / status codes verbatim]

## Environment
[dev / prod, browser or client, commit SHA if known]

## Notes
[Suspected cause, relevant files, cross-issue links]
```

### 3.2 ready-enhancement (`status:ready`)

Only for work Channel can specify at the implementer level (see §5).

```markdown
## Context
[Why this is needed — the product/business reason in 1-3 sentences]

## What to build
[Implementer-level description. Name the functions, classes, or
endpoints involved — not "add pagination" but "add cursor-based
pagination to GET /api/chats using the DynamoDB LastEvaluatedKey".]

## Files to touch
- `src/channel/...` — what changes
- `tests/unit/test_...py` — what it covers

## Acceptance criteria
- [ ] [Concrete, checkable criterion]
- [ ] [Tests cover X, Y, Z]
- [ ] [100% coverage on changed modules]

## Notes
[Constraints, gotchas, ADR references, cross-issue dependencies]
```

Include `## Files to touch` whenever proposing `agent-safe` — the
mechanical scope check (`scripts/check_agent_safe_scope.py`) requires
it. Channel rarely proposes `agent-safe` on product code it doesn't
own; reserve it for small `dx` / `docs` / `chore` items per CLAUDE.md.

### 3.3 design-needed-enhancement (`status:design-needed`)

The default for capability/feature ideas (see §5). The
`## Open questions` section is **required** — `design-review`
consumes it.

```markdown
## What we want
[Stakeholder-level description of the capability]

## Open questions
1. [Specific design question an implementer would otherwise have to
   answer mid-PR — a "how should this work?", a new data/schema/auth
   surface, an architecturally significant choice]
2. ...
```

## 4. Filing policy — the four-gate rate/quality guard

File **only** when all four hold:

1. **(a) Surfaced from real work** in the current session — an
   observation from actual debugging, implementation, or review, not
   speculative brainstorming.
2. **(b) Dup-check clean** (§2) — no open issue covers the same
   surface. A near-match means comment, not file.
3. **(c) Actionable & distinct** — names a concrete capability or
   change with a plausible implementer-level surface. Half-formed
   desires do **not** go to the GitHub backlog — they go to the Hive
   `channel-wishlist` pool, and are promoted to a real issue only once
   concrete.
4. **(d) Maintainer-worthy** — passes the "would a maintainer want
   this tracked in the backlog?" bar.

**Soft cap: ~3 net-new issues per session.** Beyond that, stop
auto-filing and surface the remaining candidates as a list for a
human go/no-go. A burst of issues is itself the noise signal the cap
exists to catch. There is no hard per-issue quota — the cap is a
pause point, not a hard limit; a human can wave more through.

Commenting on an existing issue (the §2 near-match path) does **not**
count against the soft cap — the cap governs net-new backlog items,
which are the noise risk.

## 5. Status-decision rule

Pick exactly one status label. The rule mirrors
`backlog-manager.md` §2 from the filer's seat:

- **`status:design-needed`** — the **default** for capability/feature
  ideas. Use whenever there is any "how should this work?" question, a
  new data / schema / auth surface, or an architecturally significant
  choice. When applied, the body **must** carry a `## Open questions`
  section (§3.3) — `design-review` consumes it.
- **`status:ready`** — only when Channel can name the exact
  files/surface, the approach is unambiguous, size ≤ `l`, and no
  unanswered product decision remains. Rare for product code Channel
  doesn't own — reserve it for small `dx` / `docs` / `chore` items.
  Use the §3.2 template.
- **`status:blocked`** — only when the work depends on another **open
  issue in this repo**; the body must name it with `Blocked by #N`.
  Distinct from `status:needs-info` (waiting on off-platform info:
  billing, account state, external service, legal) — do not conflate
  the two. When in genuine doubt between design-needed and blocked,
  prefer `design-needed` and raise the possible dependency as an open
  question.

When unsure between `ready` and `design-needed`, default to
`design-needed`. Over-filing `ready` on work Channel can't actually
specify forces the implementer to make design calls mid-PR — exactly
what the design pass exists to prevent.

## 6. The filing protocol, in order

1. **Gate** — confirm all four filing-policy conditions (§4). If it's
   a half-formed desire, route to the Hive `channel-wishlist` pool and
   stop. If the session is already at the ~3-issue soft cap, collect
   the candidate for the human list and stop.
2. **Dup-check** (§2). On a near-match, comment on the existing issue
   and stop.
3. **Classify** — bug / ready-enhancement / design-needed-enhancement,
   and pick the status label (§5).
4. **Write the body** from the matching template (§3).
5. **Assign labels** — `channel-authored` (§1) + status + priority +
   size + at least one area (CLAUDE.md §"Backlog labels" taxonomy).
6. **Create** with `gh issue create` and print the URL.

## See also

- `.claude/agents/backlog-manager.md` — the full conventions source
  this skill condenses; §2 (classification), §3 (metadata), §4 (body
  templates) are the authorities.
- `.claude/agents/design-review.md` — consumes the `## Open questions`
  section of every `status:design-needed` issue Channel files.
- CLAUDE.md §"Backlog labels and milestones" — the authoritative
  status / priority / size / area taxonomy and the `agent-safe` rules.
- #388 — the meta-issue this skill implements (v1, #273-independent).
- #273 — once it lands, these conventions move into `remember` /
  `recall` own-data memory (the storage follow-on noted above).
- ADR-0006 (`docs/adr/0006-skills-system.md`) — the skills-system
  contract this file's frontmatter obeys.
