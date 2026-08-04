# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Memory read model + `GET /api/memory/records` (#475, epic #129). The
  first honest answer to "what does Channel remember about me": the new
  endpoint enumerates the caller's AgentCore Memory grouped by chat,
  labels every record's provenance (`conversation` / `remembered` /
  `meta`, derived from the `[remember]` / `[meta]` text prefixes and
  applied only to ASSISTANT turns so a user message can't masquerade),
  and — critically — flags which records are actually reaching the model
  via `used_in_recall`, alongside a `recall_window` block read straight
  from `recall.py`'s cap constants. Showing stored records alone implies
  Channel uses far more than it does; showing only the recalled slice
  implies it has forgotten far more than it has (#227). The #245 rolling
  head summaries ride along read-only, because they are injected into
  their chat's system prompt every single turn. Enumeration lives in a
  new shared `agents/memory_records.py` so the later forget / edit /
  export surfaces inherit one definition of a record; `_debug` is
  untouched. **Security:** the actor-id derivation was not injective
  when this landed (#474 — `jc+work@x.com` and `jc_work@x.com` derived
  the same `actorId`), so scoping by actor alone could expose a
  colliding user's memory; every session is therefore additionally
  verified against its chat row's owner using the raw JWT sub, and
  anything unverified is dropped and counted in
  `withheld_record_count`. #474 itself is fixed below, but the gate
  stays: a read boundary must not depend on a derivation's properties,
  and the count is now a standing canary that should read 0. Record
  text is returned in full and is **data**: render it as plain text,
  never as Markdown (#465).
- Image generation via the `generate_image` tool (#279, epic #321).
  Channel can now draw: a single narrow `generate_image(prompt,
  aspect_ratio)` Strands tool calls Stability AI Stable Image Core
  (`stability.stable-image-core-v1:1`) through Bedrock `InvokeModel`,
  and the generated PNG persists as an `origin=generated` / `kind=image`
  asset through the #321 pipeline — surfaced via the `asset_created` SSE
  frame + inline card, never as base64 over the wire. Content
  moderation is Bedrock's built-in Stability RAI filter (a blocked
  prompt surfaces over SSE as a `tool_error` with
  `error_type="content_filtered"`); there is no cost gating (billing
  deferred), only the `CHANNEL_IMAGE_GEN_ENABLED` kill-switch and the
  `ImageGenInvocations` / `ImageGenFailures` EMF counters. The API
  Lambda's IAM role gains `bedrock:InvokeModel` on the Stability
  image-generation foundation model; `CHANNEL_IMAGE_GEN_MODEL` can flip
  to Stable Image Ultra or SD3.5 Large with no code change (all three
  share one request/response contract).
- Asset producers + `asset_created` SSE (#326, epic #321). Chats now
  persist their assets: verified uploads project into `origin=upload`
  ASSET rows at message-send (same S3 object, no byte copy), code-exec
  output images persist to S3 under `assets/chat/{chat_id}/{asset_id}`
  as `origin=tool_output`, and — behind the
  `CHANNEL_ASSET_EXTRACTION_ENABLED` kill-switch — fenced code blocks
  of 15+ lines (mermaid excluded) in the settled assistant text become
  `kind=code` assets with `source.fence_index` for a deterministic
  fence → card swap. New `asset_created` / `asset_updated` SSE frames
  carry the inline-card descriptor, emitted only after the row
  persists; message text is never mutated, and asset content never
  reaches AgentCore Memory (pinned by a regression test).
- MCP-server registry + per-chat selection. Users can register external
  Model Context Protocol servers from `/app/customize`; each chat
  inherits the globally-enabled set or opts into an explicit list via a
  new composer popover. Channel owns OAuth 2.1 + DCR + refresh while
  Strands' `MCPClient` consumes a pre-authenticated transport thunk —
  MCP tools flow through the existing `BeforeToolCallEvent` /
  `AfterToolCallEvent` chassis hooks identically to native tools.
  Tokens are KMS-encrypted at the application layer (dedicated CMK with
  annual rotation; passthrough sentinel for local dev) and refreshed
  lazily on every turn. A defensive SSRF guard validates the
  user-supplied URL (reject userinfo, require https except an explicit
  localhost carve-out, resolve hostname and block any
  loopback/link-local/private/reserved address). (#207, #184 spike,
  epic #128 part D)
- Code execution via Lambda sandbox: `code_exec` tool registered on the
  chassis behind `CHANNEL_CODE_EXEC_ENABLED` (default on in deployed envs;
  kill switch only). A separate `CodeExecLambda` CDK construct runs user
  Python in a subprocess on a least-privilege IAM role (no DDB / S3 /
  Bedrock / Secrets / SSM — pure compute), with SnapStart on Python 3.13,
  reserved-concurrency 5, no VPC, 270s subprocess cap, 20 KB stdout / 5 KB
  stderr length caps, `/tmp` wiped at entry, and post-exec `/tmp/*.png|jpg`
  harvest (≤3 × 1 MB base64-encoded) with symlink-escape rejection.
  Sci-stack pre-installed (numpy, pandas, matplotlib, requests, httpx,
  python-dateutil — scipy excluded from v1 because the full stack
  exceeded Lambda's 250 MB unzipped limit; follow-up will move scipy
  to a Lambda layer). The chassis SSE protocol grew an optional
  `kind="code-output"` + structured `payload` field on `tool_finished`
  events, and the SPA's `ToolResultBlock` gained the matching code-output
  branch (collapsible stdout pane >5 lines, `<details>`-wrapped stderr,
  inline images, meta line with exit/duration/truncation/timeout flags).
  Closes epic #128 part C (#183).
- Web search via Exa: `web_search` tool registered on the chassis behind
  `CHANNEL_WEB_SEARCH_ENABLED` (default on in all envs; kill switch only,
  not progressive rollout). The model writes replies with inline markdown
  links (`[claim](url)`) as the citation channel — no SSE protocol change,
  no new SPA renderer. Rate limit deferred for v1; the chassis chain-cap
  (8 tools/chain) is the de-facto budget. (#182, epic #128)
- Tool-use chassis: Strands `tools=[...]` wiring on `build_agent()`,
  three new hook handlers (`ModelVisibilityAddendumHook` for the
  in-prompt addendum, `ToolCallGuardHook` for chain-cap / wall-clock /
  mid-chain cancel enforcement, `ToolCallTelemetryHook` for EMF +
  AgentCore Memory META-fact writes), four new SSE event types
  (`sse_tool_started` / `sse_tool_progress` / `sse_tool_finished` /
  `sse_tool_error`), a collapsible step list in the Conversation view
  (default collapsed, expandable, distinct chain-cap affordance), the
  `ToolResultBlock` seed component, and the `current_time` smoke-test
  tool behind `CHANNEL_CLOCK_TOOL_ENABLED` (default on in jc/dev, off
  in prod per strategy spec policy P2). EMF counters
  `ToolCallSuccesses` / `ToolCallFailures` land alongside (#181, epic
  #128).
- Server-side user preferences. `/app/customize` now persists across
  devices via a new `PK=USER#{u}, SK=PREFS` DDB row exposed at
  `GET / PUT /api/me/prefs`. The `useChannelPrefs` hook hydrates from
  the server once on first mount when a mgmt JWT is present, and
  debounce-writes each change back (200ms per-key, last value wins).
  localStorage stays the canonical source of truth so the UI never
  blocks on network. Part of #113.
- Send on Enter toggle in `/app/customize` is now live. When on
  (default), Enter sends and Shift+Enter inserts a newline. When off,
  Enter inserts a newline and Cmd/Ctrl+Enter sends — matches Slack /
  GitHub / Discord convention. Part of #113.
- Follow-up suggestion chips. When the Suggest follow-ups pref is on
  (default), each assistant reply is followed by a chip row carrying
  2-3 generated prompts; clicking a chip drops its text into the
  composer for editing (no auto-send). Backend emits a new
  `follow_ups_suggested` SSE event powered by a Haiku one-shot
  Strands Agent that runs after the auto-title block, behind the
  `CHANNEL_FOLLOWUPS_ENABLED` (default on) and `CHANNEL_FOLLOWUPS_MODEL`
  (default `claude-haiku-4-5`) env knobs. Generation failures log +
  emit a `FollowupGenFailures` CloudWatch counter and are swallowed —
  no visible regression to the chat reply. Part of #113.
- Conversation view now shows a header strip with the chat title and
  a menu trigger. Clicking the title opens the same Pin / Rename /
  Change project / Remove from project / Delete menu that the
  sidebar row's `⋮` button does. A placeholder share icon sits on
  the right for future wiring.
- Chat rename + delete from the sidebar. Hovering a chat in the
  Recents list reveals a `⋮` menu button; clicking it opens a popover
  with Rename (wired), Delete (wired, permanent), and three disabled
  placeholders (Pin, Change project, Remove from project). Delete
  wipes the chat from both DynamoDB AND AgentCore Memory so the
  agent stops recalling the deleted conversation in future chats.
  Introduces a reusable `Modal.jsx` primitive and a `--danger` CSS
  token (no destructive colour existed yet).
- Phase 7d — agent now recalls relevant context from the user's
  PRIOR chats via Bedrock AgentCore Memory, and auto-titles new
  chats in 3-6 words after the first reply. Recall fires per turn
  with a 5-turn cache; injection is a Markdown addendum on the
  system prompt. Auto-title uses a Haiku one-shot Strands Agent
  emitted inline as a new `title_suggested` SSE event. Both
  features fail-soft and ship behind `CHANNEL_RECALL_ENABLED` and
  `CHANNEL_AUTO_TITLE_ENABLED` kill-switches. NOTE: AgentCore's
  `SemanticMemoryStrategy` is asynchronous — newly-written events
  take minutes to appear as queryable recall records.
- Phase 7c — agent now persists every chat turn to Bedrock AgentCore
  Memory via a Strands `AfterInvocationEvent` hook. Writes are
  fire-and-forget (never block the SSE response), failures swallowed
  with EMF + structured-log surfacing, and recall stays disabled —
  that's Phase 7d. Dev environments gain a debug endpoint
  (`GET /api/_debug/memory/events`) for inspecting writes, gated by
  `CHANNEL_ENABLE_DEBUG_ENDPOINTS=1` with a CDK assertion test that
  blocks the flag from leaking to prod.
- Real Bedrock streaming via Strands Agents — replaces the canned reply
  that 7a shipped. Three models served: Sonnet 4.6, Haiku 4.5, Opus 4.6.
- `GET /api/models` — server-side allowlist drives the ModelPicker.
- `POST /api/chats/{id}/regenerate` — drop the last assistant turn and
  re-stream from the same user message. New "Regenerate" button on the
  last assistant turn.
- `scripts/reset_dev_table.py` — drop+recreate the local DDB table with
  the current schema (incl. `ChatByIdIndex` GSI). DDB Local is in-memory
  and `inv dev --seed` doesn't create the table yet; this is the
  stopgap. Run with `uv run python scripts/reset_dev_table.py`.

### Changed

- Desktop app default window size is now **1280 × 900** (landscape)
  instead of 880 × 800 (nearly square). The previous default looked
  cramped next to the sidebar; the new size gives the conversation
  column real breathing room and matches the visual convention of
  Claude Desktop / Linear / Notion. Min size is unchanged at
  720 × 600 — users can still shrink for split-screen work.
- The active chat row in the sidebar Recents list now has a subtle
  selection highlight (matches Claude Desktop's selected-row
  treatment). Reuses the existing `--sel` and `.recent.active` CSS
  rule that had been styled but not wired — the active chat was
  visually indistinguishable from inactive rows before.
- macOS app icon regenerated as a full-bleed brand mark (orange
  squircle with two white pause bars). The previous icon rendered
  the brand mark inside a separate white squircle, producing a
  "rounded square in a rounded square" look that stood out next to
  other apps in Finder. The new icon matches the macOS app-icon
  convention used by Calendar, Books, App Store, etc.
- Phase 8a — memory recall now uses synchronous `ListSessions` +
  `ListEvents` instead of `RetrieveMemoryRecords`. AgentCore's
  `SemanticMemoryStrategy` had a multi-hour ingestion lag in real
  use, leaving recall empty long after events were written. The
  new path retrieves raw prior-chat events directly (cap: 5
  sessions × 2 events each) and works immediately. New Memory
  resources are no longer created with a strategy.
- Lambda timeout raised from 30s to 5 min for streaming chats.
- Bedrock IAM extended to allowlist Opus 4.6 and to grant invocation on
  the US cross-region inference profiles (required for on-demand
  throughput on Sonnet/Haiku/Opus).
- Assistant turn header renders the friendly model label (e.g. "Claude
  Sonnet 4.6") instead of the raw Bedrock ARN — fixes a 7a regression.
- ModelPicker now sources valid model IDs from `GET /api/models` with
  the static MODELS array as fallback when the API is unavailable.

### Removed

- `src/channel/agents/inline_agent.py` and `src/channel/agents/bedrock.py`
  — superseded by Strands.
- Stale `(FastAPI + Mangum)` comment in the CDK stack docstring (the
  project has been on AWSLWA since pre-7a).

### Fixed

- **Security:** the AgentCore `actorId` derivation is now injective, so
  two distinct users can no longer share a memory partition (#474). The
  old mapping replaced every disallowed character with `_`, which
  collapsed everyday address shapes onto one id — `jc+work@x.com` and
  `jc_work@x.com` both became `jc_work_x_com`, as did `a.b@x.com` and
  `a@b.x.com`. Since one AgentCore Memory resource serves an entire
  environment and `actorId` is its only partition, a collision meant
  one person's chat content could surface in another's system prompt
  via the recall hook. `derive_actor_id` now appends a 128-bit SHA-256
  suffix to a (still readable) label prefix, and every call site —
  memory hook, recall hook, memory tools, chat-delete session wipe,
  debug endpoints — derives through that one function. Memories written
  under the old ids are deliberately orphaned rather than dual-read: a
  fallback read of the lossy partition would be the very disclosure
  being fixed. See CLAUDE.md §AgentCore Memory for the full migration
  reasoning.
- Auto-titler now salvages partial output when the Haiku titler trips
  `MaxTokensReachedException`. Previously the whole title was
  discarded and the chat stayed on "New chat" in the sidebar. The
  salvage path strips colon-prefixed preamble (e.g. "Here's a title:
  ...") and caps the result at 6 words. Closes #90.
- `useChatStream` no longer aborts the in-flight stream during React
  StrictMode dev double-effect cleanup. The `useEffect(() => () =>
  abort(), [chatId])` pattern fired during the initial mount's cleanup
  and killed the first POST before the body could be sent. Replaced
  with a ref-based prev-chatId check that only aborts on a real
  chatId change.
- SPA was POSTing the full picker object as `model` (e.g.
  `{"id":"claude-sonnet-4-6","name":"Claude Sonnet 4.6", ...}`) but
  the backend's `SendMessageRequest.model` is a short id string.
  `ChatHome.handleSend` and `Conversation.followUp` now extract
  `model.id` before forwarding.
- `inv dev` no longer overrides `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
  to fake values. The override was a convenience for DDB Local (which
  accepts any non-empty credentials), but it also masked the developer's
  real AWS credentials from Bedrock. Now ambient credentials flow
  through; DDB Local still works.
- `GET /api/memory/records` and `GET /api/memory/export` no longer 500 for
  a signed-in user who has never had a chat turn (#527). An AgentCore
  actor partition is created lazily by the first memory *write*, so those
  users have none, and every read against them raised
  `ResourceNotFoundException`. Both endpoints now answer with an empty
  result — no partition means no records — via a shared
  `memory_records._agentcore_read` seam so `/records` and `/export` can't
  drift apart on it. Only that one AgentCore error code is absorbed; any
  other failure (throttling, access denied, a service fault) still
  surfaces as a 5xx, since an export claiming "you have no memories"
  during an AgentCore outage would be a data-integrity claim the endpoint
  has no basis to make.
- Swapped Opus 4.7 for Opus 4.6 across the model allowlist, IAM grants,
  and SPA defaults. Opus 4.7 requires contacting AWS Sales for account
  enablement, which is not a realistic prerequisite for a dev iteration
  loop. Opus 4.6 is the latest model available via the standard model-
  access self-serve UI in the Bedrock console.

### Meta

- Cleared the last pockets of `agentcore-starter` template residue from
  the developer-facing surfaces the earlier sweeps missed (#453,
  following #262/#263/#264). `ui/README.md` carried a local-API command
  that could not run — `STARTER_JWT_SECRET=dev-secret uv run uvicorn
  starter.api.main:app` names an env var renamed in #259 *and* a Python
  module that has been `channel.api.main` since the fork — so anyone
  following the README hit an import error; the corrected command is
  now verified to boot and serve `/health`. The same file's project
  tree, feature list, script table and CloudFront path list were all
  describing a SPA that no longer exists, and have been rewritten
  against the live source. Five `.claude/skills/` and eight
  `.claude/agents/` files were repointed the same way: `STARTER_*`
  env-var names became their real `CHANNEL_*` counterparts (checked
  against the definition site, not mechanically prefixed — the drift in
  #259 was not a uniform rename, e.g. `STARTER_JWT_ISSUER` is really
  `CHANNEL_ISSUER`), and dead `src/starter/**` paths were repointed at
  modules that exist. Two agent checks were not merely misnamed but
  contradicted current architecture — `code-reviewer`'s "session
  namespace (inline agent)" check and `security-auditor`'s matching
  section both demanded the pre-Strands `f"{user_id}:{session_id}"`
  Bedrock sessionId prefix, which the chat-index ownership check
  replaced — so both now describe `_load_owned_chat` and its 404-not-403
  rule. Deliberately left alone: `onboarding.md` (its job is onboarding
  the *template*, so its `agentcore-starter` references are correct),
  the `Starter*` CloudFormation logical IDs in
  `infra/stacks/channel_stack.py` (renaming forces a table / CloudFront
  policy / dashboard replace), the still-live `starter_mgmt_token`
  localStorage key (#295), and the frozen `docs/adr/`, `docs/retros/`
  and `docs/superpowers/` trees.
- Brought the automated review instructions back in line with the UI
  conventions they are supposed to enforce (#449). PR #447 removed
  `lucide-react`, but `.claude/agents/code-reviewer.md` still issued a
  `FAIL` against any icon not sourced from that deleted package and
  carried a whole "shadcn/ui primitives" check telling authors to add
  primitives to a `ui/src/components/ui/` directory that has never
  existed — since `code-reviewer` runs as a merge gate on every PR,
  the next UI change would have been blocked on impossible
  instructions. Check 5 now states the real rule (icons come from the
  hand-rolled `ui/src/components/Icon.jsx` stroke set; emoji-as-UI
  stays a `FAIL`; a new icon-package import is also a `FAIL`), and
  check 6 is rewritten from "use shadcn" to the actual convention —
  hand-rolled primitives, no component library, raw HTML with semantic
  class names is correct — while still flagging a re-rolled modal over
  `Modal.jsx` and any new Tailwind/component-library dependency. The
  same correction lands in `.github/copilot-instructions.md`, the
  vestigial `ui/components.json` (shadcn CLI config pointing at a
  Tailwind config and `src/index.css` that don't exist) is deleted, and
  the `react-component` skill drops its now-false "the dependency still
  lingers in `ui/package.json`" parenthetical. Reviewing the rewrite
  surfaced a second, quieter defect in the same file: `gh pr diff` takes
  a PR number and nothing else, so every check written as
  `gh pr diff <PR> -- '*.jsx'` was exiting with `accepts at most 1
  arg(s)` and piping empty stdout into its grep — checks 4, 5 and 7 had
  been silently passing everything. All of them now scope with `grep`,
  anchored so that a convention merely *discussed* in Markdown or
  CHANGELOG prose can't be mistaken for a violation of it, and
  §Invocation states both rules once: `gh pr diff` can't be
  path-scoped, and a grep hit is a candidate rather than a finding
  until the reviewer has confirmed which file it came from.
- Swept `CLAUDE.md` for the stale `STARTER_*` environment-variable
  prefix left behind by the #259 hard-cut rename: all 17 occurrences
  across 13 distinct variables (the debug-endpoint gate, audit
  retention, the image-generation trio, asset extraction, the
  AgentCore Memory name override, the recall / memory-tools /
  auto-title kill-switch family, the titler model override, the
  Google-auth bypass, and the e2e UI URL) now read `CHANNEL_*`, each
  name verified against the live readers in `src/`, `infra/`, and
  `tasks.py` rather than mechanically re-prefixed. This is the
  env-var half of #264 only — the `starter_mgmt_token` localStorage
  key is deliberately left untouched because it is still the key the
  SPA actually writes; that rename rides with #295, and a second
  documentation pass follows it. The same pass also corrects the
  Electron OAuth section, whose closing paragraph still promised a
  desktop-specific dev bypass as future work — it has since shipped, so
  the text now documents the real behaviour: `inv desktop-dev` sets both
  `CHANNEL_BYPASS_GOOGLE_AUTH` and `CHANNEL_DESKTOP_DEV_EMAIL`, and
  `/auth/login` mints a synthetic JWT straight back to the loopback
  callback when both are present, while deployed dev (which sets only the
  former) still goes through Google (#264, part of #258).
- Renamed every production environment variable from the legacy
  `STARTER_*` prefix (a leftover from the `agentcore-starter` fork) to
  `CHANNEL_*` across the CDK Lambda env block
  (`infra/stacks/channel_stack.py`), every `src/channel/` reader,
  `tasks.py`, the CI integration/smoke-test env, and the test suite —
  a single atomic hard-cut (CDK writes the env block and the readers
  ship in the same Lambda zip, so there is no window where old code
  reads a new name or vice versa). Pure mechanical rename, zero
  behaviour change; the SSM parameter *paths* held as `*_PARAM` values
  are untouched (#259, part of #258). Documentation of the new names in
  the READMEs and `CLAUDE.md` follows in #263 and #264.
- Public docs-site operations pages now cite the correct module paths:
  the stale `src/starter/*` references in
  `docs-site/operations/security.md` and
  `docs-site/operations/endpoints.md` (a leftover from the
  starter → channel rename) were corrected to `src/channel/*`, and the
  stale `infra/stacks/starter_stack.py` references in the same two
  files were corrected to `infra/stacks/channel_stack.py`, so readers
  following the docs to inspect the source no longer land on 404s
  (#262, part of #258).
- In-repo docs and module docstrings swept of residual `starter`
  naming. `CONTRIBUTING.md` was actively wrong — its local-dev snippet
  told contributors to export `STARTER_BYPASS_GOOGLE_AUTH=1`, a
  variable #259 removed, so the `?test_email=` bypass silently failed
  for anyone following the guide; it now sets
  `CHANNEL_BYPASS_GOOGLE_AUTH=1` and runs `channel.api.main:app`.
  `infra/README.md` now documents the live `CHANNEL_TABLE_NAME` /
  `CHANNEL_ISSUER` / `CHANNEL_JWT_SECRET_PARAM` names and the real
  `stacks/channel_stack.py` / `cp -r src/channel` bundling step;
  `src/channel/README.md`'s module index was renamed off `src/starter/`
  and refreshed to match the package as it stands; and the
  `starter.auth.*` docstring cross-references plus the
  "Starter management API" OpenAPI description were corrected.
  Each replacement was verified against live code rather than
  mechanically re-prefixed, since the variable set drifted during
  #259 (#263, part of #258). Deliberately left alone: the
  `starter_mgmt_token` localStorage key (still the real key — that
  rename is open as #295), the `Starter*` CDK construct logical IDs in
  `infra/stacks/channel_stack.py` (renaming them would force a
  replace of the DynamoDB table, CloudFront policy, and dashboard),
  and frozen historical records under `docs/adr/`, `docs/retros/`,
  `docs/superpowers/`, and past CHANGELOG sections.
- Web search design doc: `docs/superpowers/specs/2026-06-07-182-web-search-design.md`.
- `strands-agents-tools` pinned in `pyproject.toml` for the native Exa wrapper.
- Memory invariant lifted into the `_payload_from_messages` docstring +
  hardened via a recursive structural walker in tests: tool payloads
  (`toolUse` / `toolResult` blocks) never persist to AgentCore Memory
  (#181, epic decision 7).
- Strategy spec for epic #128 sequencing:
  `docs/superpowers/specs/2026-06-06-epic-128-tool-use-sequencing.md`.
- Chassis design doc:
  `docs/superpowers/specs/2026-06-06-tool-use-chassis-design.md`.
- MCP-server registry spike:
  `docs/superpowers/specs/2026-06-06-mcp-server-registry-spike.md`
  (resolved `Strands MCPClient vs fastmcp.Client` — `transport_callable`
  is the seam; Channel owns OAuth + DCR + refresh, Strands gets a
  pre-authenticated transport thunk).
