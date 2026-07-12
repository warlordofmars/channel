# Channel

An AI agent chat backend on AWS.
Built with FastAPI (Python), DynamoDB, AWS CDK, and a React management UI.

## Stack

- FastAPI (Python) — management REST API
- React (Vite) — management UI SPA
- DynamoDB — persistent storage (single table design)
- AWS Lambda + Function URL — hosting
- AWS CDK (Python) — IaC
- IAM roles — Lambda <-> DynamoDB auth
- Google OAuth — identity provider for management UI login
- uv — dependency management (pyproject.toml + uv.lock)

## Structure

```text
channel/
├── src/
│   └── channel/
│       ├── storage.py         # DynamoDB read/write logic
│       ├── models.py          # Data models
│       ├── logging_config.py  # Structured JSON logging setup
│       ├── metrics.py         # CloudWatch EMF metrics helpers
│       ├── auth/
│       │   ├── tokens.py      # Management JWT issuance + validation
│       │   ├── google.py      # Google OAuth integration
│       │   └── mgmt_auth.py   # Management API authentication
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── chat_agent.py   # Strands Agent factory + build_titler_agent (7c/7d)
│       │   ├── memory.py       # AgentCoreMemoryHook + get_or_create_memory (Phase 7c)
│       │   ├── recall.py       # AgentCoreRecallHook + cache (Phase 7d → 8a)
│       │   └── strands_sse.py  # Strands event → SSE byte translator
│       └── api/
│           ├── main.py        # FastAPI app + routes
│           ├── _auth.py       # Shared mgmt-JWT dependency for /api/* routes
│           ├── _debug.py      # /api/_debug/* — dev-only, gated by STARTER_ENABLE_DEBUG_ENDPOINTS
│           ├── chats.py       # Chat CRUD + SSE streaming + regenerate
│           ├── models.py      # GET /api/models — server allowlist
│           └── csp.py         # CSP violation reporting endpoint
├── ui/
│   ├── index.html             # Vite entry HTML
│   ├── src/
│   │   ├── App.jsx            # Marketing + app route shell
│   │   ├── main.jsx           # React DOM entry; imports styles/channel.css
│   │   ├── api.js             # API client (fetch wrappers)
│   │   ├── analytics.js       # GA4 trackPageView + trackEvent helpers
│   │   ├── styles/
│   │   │   ├── channel.css    # Design tokens (OKLCH themes, radii, shadows, fonts)
│   │   │   ├── site.css       # Marketing site layout (nav, footer, hero, tiers)
│   │   │   └── app.css        # Chat-app layout (sidebar, composer, popovers, greet)
│   │   ├── lib/
│   │   │   ├── auth.js        # parseToken, isTokenValid, TOKEN_KEY
│   │   │   └── utils.js       # cn (class-name join via clsx)
│   │   ├── hooks/
│   │   │   ├── useChannelPrefs.js   # theme/accent/density/shape/font/model/effort + siteTheme
│   │   │   ├── useChatList.js       # Sidebar Recents + optimistic create/rename/archive
│   │   │   ├── useChatStream.js     # SSE chat stream: history load, send, abort, status/error
│   │   │   ├── ChatsContext.jsx     # ChatsProvider + useChats() — single useChatList instance app-wide
│   │   │   └── useRelativeTime.js
│   │   ├── components/
│   │   │   ├── AuthGate.jsx       # Redirects /app/* visits to /app/login when no JWT
│   │   │   ├── ChannelMark.jsx    # Brand mark SVG (rounded square + two bars)
│   │   │   ├── ErrorBoundary.jsx  # Token-styled error fallback
│   │   │   ├── Icon.jsx           # 24×24 stroke icon set
│   │   │   └── Modal.jsx          # Shared modal primitive (Esc + backdrop dismissal)
│   │   ├── marketing/             # Marketing site routes (Phase 6b)
│   │   │   ├── Nav.jsx, Footer.jsx, SiteLayout.jsx, ThemeToggle.jsx, ImageSlot.jsx
│   │   │   └── pages/             # Home, Product, Models, Pricing, Download, About, Blog, Careers, Privacy, NotFound
│   │   └── app/                   # Chat app routes (Phase 6c onward)
│   │       ├── data.js                                       # MODELS, EFFORTS, QUICK_ACTIONS, PROJECTS, ARTIFACTS, PROJECT_DOCS
│   │       ├── Login.jsx, GoogleG.jsx                        # Centered Google sign-in
│   │       ├── Shell.jsx, Sidebar.jsx, AccountPopover.jsx    # Layout chrome
│   │       ├── Composer.jsx, ModelPicker.jsx, AttachMenu.jsx # Composer + its popovers
│   │       ├── ChatHome.jsx                                  # Empty-state greeting
│   │       ├── ChatRowMenu.jsx                               # Per-row sidebar popover (rename/delete/placeholders)
│   │       ├── Conversation.jsx                              # Streamed-turns view + inline artifact card
│   │       ├── DeleteChatModal.jsx                           # Delete confirm dialog
│   │       ├── RenameChatModal.jsx                           # Rename chat dialog
│   │       ├── renderMarkdown.jsx                            # Tiny markdown helper (paragraphs/bold/OL/cursor)
│   │       └── views/                                        # /app/projects, /app/projects/:id, /app/artifacts, /app/customize
│   │           ├── Projects.jsx                              # Grid of project cards + 'New project' tile
│   │           ├── ProjectDetail.jsx                         # Project header + Composer + docs + chats list
│   │           ├── Artifacts.jsx                             # List of artifact rows + bookmarkable panel
│   │           ├── ArtifactPanel.jsx                         # Slide-in viewer with 5 renderers (Code/Chart/Data/Interactive/Document)
│   │           ├── Customize.jsx                             # 5 visual prefs (theme/accent/density/model/effort) + 3 behavior toggles
│   │           └── artifactHelpers.js                        # colorFor / inkFor / artIcon utilities
│   └── package.json
├── desktop/
│   ├── package.json
│   ├── main/                  # main-process modules
│   ├── preload/               # sandboxed contextBridge
│   ├── electron-builder.yml
│   └── test/
├── docs-site/                 # VitePress documentation site
│   ├── .vitepress/
│   │   ├── config.mjs         # base: "/docs/", nav, sidebar
│   │   └── theme/
│   │       ├── index.js       # Custom Layout (nav-bar-content-after slot)
│   │       └── style.css      # Dark navy navbar, brand colours
│   └── getting-started/       # Introduction and quick-start
├── infra/
│   ├── app.py                 # CDK app entry point
│   └── stacks/
│       └── channel_stack.py   # Lambda + DynamoDB + CloudFront + IAM
├── tests/
│   ├── unit/                  # Pure logic, no AWS deps
│   ├── integration/           # Tests against DynamoDB Local
│   └── e2e/                   # Playwright tests against deployed env
│       ├── __init__.py
│       └── conftest.py        # Shared fixtures (e.g. live_admin_token)
├── scripts/
│   └── check_copyright.py     # Copyright header linter
├── .github/
│   └── workflows/
│       ├── ci.yml             # CI on PRs + deploy on push to dev/main
│       ├── deploy-dev.yml     # Manual dev deploy (workflow_dispatch)
│       └── security.yml       # Scheduled security scans
├── tasks.py                   # Invoke task definitions (lint, test, deploy)
├── pyproject.toml
└── README.md
```

## Auth

Google OAuth is the identity provider for management UI login
(`/auth/login`). On successful Google sign-in, the API mints a
management JWT (`typ=mgmt`, `role=admin|user`, 8h TTL) signed with
HS256 using a secret resolved from SSM
(`/channel/{env}/jwt-secret`). All `/api/*` endpoints
require a valid Bearer mgmt JWT. JWT validation enforces `iss`,
`typ=mgmt`, and `exp`. The token is stored client-side in
`localStorage` under the `starter_mgmt_token` key.

## DynamoDB single table design

- Activity log items: `PK=LOG#{date}#{hour}`, `SK={timestamp}#{event_id}`
  (hour-sharded to avoid hot partitions)
- Audit log items: `PK=AUDIT#{date}#{hour}`, `SK={timestamp}#{event_id}`
  (immutable compliance trail, TTL via `STARTER_AUDIT_RETENTION_DAYS`,
  default 365 days)
- User items: `PK=USER#{user_id}`, `SK=META`
- Mgmt state items: `PK=MGMT_STATE#{state}`, `SK=META`
  (TTL enabled, used for the Google OAuth state parameter)
- JWT revocation denylist items: `PK=DENY#{jti}`, `SK=META`
  (point-read keyed by the mgmt JWT's `jti`; written on `/auth/logout`,
  checked in `require_mgmt_user` on every authenticated request so a
  revoked session is rejected; `ttl` = the denied token's own `exp` so
  the row self-prunes exactly when the token would have expired anyway,
  keeping the denylist bounded by the live-token set — #240)
- Chat-index items: `PK=USER#{user_id}`, `SK=CHAT#{created_at}#{chat_id}`
  (one row per chat; sortable so the Recents query is a single
  `Query(ScanIndexForward=False)`; also projects onto `ChatByIdIndex`)
- Chat message items: `PK=CHAT#{chat_id}`, `SK=MSG#{created_at}#{msg_id}`
  (one row per turn; UUID suffix prevents cross-Lambda-instance
  collisions at the same microsecond)
- Idempotency items: `PK=IDEMP#{user_id}`, `SK={key}`
  (TTL = 1 hour after reserve; used for the streaming POST replay
  short-circuit)
- MCP server items: `PK=USER#{user_id}`, `SK=MCPSERVER#{server_id}`
  (one row per registered MCP server; persists DCR client_id +
  tool_prefix + globally_enabled flag; no GSI projection)
- MCP token items: `PK=USER#{user_id}`, `SK=MCPTOKEN#{server_id}`
  (sibling row to MCPSERVER; access/refresh tokens KMS-encrypted at
  application layer; TTL set to `expires_at + 30 days` as a hard
  upper bound for the orphan-row case where DELETE lost a race)
- Chat MCP override items: `PK=CHAT#{chat_id}`, `SK=MCPSERVERS#META`
  (one optional row per chat; `mode=inherit` means "follow user's
  globally_enabled flags as of now", `mode=explicit` means
  "use exactly this list, ignoring future changes to globally_enabled")
- GSIs:
  - `UserEmailIndex` — `PK=EMAIL#{email}` (for user lookups by email)
  - `ChatByIdIndex` — `PK=CHAT_ID#{chat_id}`, `SK=META`
    (sparse; only chat-index rows project onto it; used for direct
    chat-id → chat lookups without knowing `created_at`)

## AgentCore Memory

Phase 7c onward, every chat turn is persisted to a Bedrock AgentCore
Memory resource via a Strands `AfterInvocationEvent` hook
(`src/channel/agents/memory.py`). Phase 8a adds the read side: a
`BeforeInvocationEvent` recall hook (`src/channel/agents/recall.py`)
injects prior-chat context into the system prompt via synchronous
`ListSessions` + `ListEvents` reads (no strategy required), plus
auto-titling of fresh chats via a Haiku one-shot Agent (Phase 7d).

- **One Memory resource per environment** — named `channel_{env}`
  (underscores, not hyphens — AgentCore's name validator rejects
  hyphens). Discovered or created lazily on Lambda cold-start; the
  module-level cache means subsequent requests in the same instance
  pay zero overhead.
- **`STARTER_AGENTCORE_MEMORY_NAME`** overrides the default name —
  useful for pointing a personal dev environment at a pre-existing
  Memory resource.
- **`actorId = sanitize(jwt.sub)`** — one actor per Channel user.
  Disallowed characters (anything outside `[a-zA-Z0-9_/-]`) are
  replaced with `_` so email-form JWT subs (containing `@` and `.`)
  satisfy AgentCore's regex. Per-workspace partitioning (per the
  "workspaces are the tenancy root" product decision) will eventually
  become `actorId = f"{workspace_id}/{user_id}"`.
- **`sessionId = chat_id`** — one AgentCore session per Channel chat.
  Chat ids are UUIDs so no sanitization needed.
- **Failure mode**: log + EMF counter (`MemoryWriteFailures`) +
  swallow. Memory writes must not break chats. Loss-on-Lambda-freeze
  is acceptable (the recall hook treats missing sessions as "no memory").
- **`MemoryWriteSuccesses` / `MemoryWriteFailures`** are CloudWatch
  counters under namespace `Channel`, with NO per-actor or per-session
  dimensions (cardinality blowup risk — codified by the
  `_signature_locks_out_dimensions` test on
  `record_memory_write_outcome`).
- **Chat deletion wipes session events** — `DELETE /api/chats/{chat_id}`
  removes the chat from DynamoDB AND best-effort deletes the
  AgentCore Memory events for the chat's session (`ListEvents` +
  `DeleteEvent`). Failure is logged + counted
  (`ChatDeleteMemoryWipeFailures`) but does not fail the user-visible
  delete — DDB is the source of truth for chat existence.

Dev-only `GET /api/_debug/memory/events?chat_id=...&limit=...` and
`DELETE /api/_debug/memory/events?chat_id=...&event_id=...`
(`src/channel/api/_debug.py`) surface writes for Playwright e2e
verification. Mounted only when `STARTER_ENABLE_DEBUG_ENDPOINTS=1`;
prod never sets the flag (CDK assertion test in
`tests/unit/test_channel_stack.py` guards this).

### Recall (Phase 8a; pivoted from 7d)

- **`AgentCoreRecallHook`** (`src/channel/agents/recall.py`)
  subscribes to `BeforeInvocationEvent`. Per turn, queries the
  actor's prior chats via `ListSessions` + per-session
  `ListEvents`, formats the aggregate as a Markdown addendum
  grouped by session with a date header, mutates
  `event.messages[0]`.
- **Caps** — `_RECALL_MAX_SESSIONS = 5` most-recent prior
  sessions; `_RECALL_EVENTS_PER_SESSION = 2` most-recent events
  per session; `_RECALL_EVENT_TEXT_TRUNCATE = 120` chars per
  quoted turn. Worst-case prompt overhead ~2.4 KB.
- **Current chat excluded** — PR #73 already feeds the current
  chat's DDB history into Strands; the recall hook drops that
  `sessionId` from the ListSessions result to avoid double-feeding.
- **5-turn cache** — keyed by `(actor_id, chat_id)`, module-level.
  Cold-start invalidates.
- **Kill-switch** — `STARTER_RECALL_ENABLED=0` short-circuits.
- **History**: Phase 7d implemented this via `RetrieveMemoryRecords`
  + `SemanticMemoryStrategy`. The strategy's async ingestion lag
  (hours in real use) made recall empty for too long, so 8a pivoted
  to raw-event reads. The strategy is no longer attached to new
  memories.

### Auto-titling (Phase 7d)

- After the first assistant `done` event lands (and
  `chat.message_count == 0` at function entry), `_stream_bedrock_reply`
  invokes a small Haiku Strands `Agent` (from `build_titler_agent()`)
  with NO memory hooks and `max_tokens=20`.
- Persists the title via `storage.patch_chat`, then emits
  `sse_title_suggested(chat_id, title)` SSE frame before stream close.
  The SPA's `useChatStream` forwards via `onTitleSuggested` to
  `ChatsContext.renameChatLocal`.
- **Kill-switch** — `STARTER_AUTO_TITLE_ENABLED=0` skips the titler
  block. Default `"1"`.
- **Model override** — `STARTER_TITLER_MODEL` (default `claude-haiku-4-5`).

## Management UI

- React SPA (Vite), runs on port 5173 in dev
- Marketing routes at `/`, app routes at `/app/*`
- Communicates with FastAPI management API on port 8001
- Auth: Google OAuth via `/auth/login`; token stored in localStorage as `starter_mgmt_token`
- Web SPA AND Electron desktop app ship from the same `ui/` SPA source. The
  desktop wrapper lives in `desktop/` (Electron main + preload) and bundles
  the SPA build. See `## Desktop app` below.

## Desktop app

Electron wrapper around the SPA. Built from `desktop/` with its own
`package.json` (separate from `ui/` so the renderer's bundle can't import
`electron` at build time).

### Layout

- `desktop/main/` — Node main-process modules (protocol handler, window
  factory, IPC registry, OAuth loopback)
- `desktop/preload/` — sandboxed bridge exposing `window.channelDesktop`
- `desktop/test/` — vitest with `environment: "node"`; 100% coverage gate
- `desktop/dist-main/`, `desktop/dist-renderer/`, `desktop/release/` —
  build outputs, all gitignored

### Dev workflow

```bash
uv run inv desktop-dev
```

Spawns DynamoDB Local + FastAPI + Vite + Electron in parallel; Electron
points at `http://localhost:5173` and uses `http://localhost:8001` as the
OAuth base. Ctrl-C tears everything down.

### Build

```bash
uv run inv desktop-build --platform current             # local build
uv run inv desktop-build --platform mac|win|linux       # explicit
uv run inv desktop-build --api-base https://dev.example # override API
```

Outputs unsigned artifacts under `desktop/release/`. Signing and
auto-update arrive in sub-project B.

### OAuth flow (Electron-specific)

The SPA's redirect-to-localStorage OAuth flow doesn't work from an
Electron renderer (Google rejects embedded BrowserWindows). The desktop
app uses external-browser + loopback instead:

1. Renderer calls `window.channelDesktop.login()` via the preload IPC bridge.
2. Main process picks a kernel-assigned port on 127.0.0.1, starts a tiny
   HTTP server with one route (`GET /callback`), and opens the user's
   default browser at:
   ```
   https://<api-host>/auth/login?desktop_callback=http://127.0.0.1:<port>/callback&state=<S>
   ```
3. FastAPI's `/auth/login` validates `desktop_callback` is a loopback URL
   and stores it (along with the caller-supplied `state`) in the
   `MGMT_STATE` DynamoDB record.
4. Google → `/auth/callback` exchanges the code, mints the mgmt JWT, and
   redirects to the loopback URL with `?token=&state=`.
5. The main process verifies state (timing-safe), hands the JWT to the
   renderer via IPC, closes the loopback server, and serves the browser
   a "you can close this window" HTML page.

In `inv desktop-dev`, the dev FastAPI runs with `STARTER_BYPASS_GOOGLE_AUTH=1`,
but the desktop renderer does not send the `?test_email=` shortcut, so the
dev flow currently still goes through Google. A follow-up will wire a
desktop-specific bypass path that mints a synthetic JWT and short-circuits
the loopback for faster dev iteration.

### Why these decisions

The design and rationale live in
`docs/superpowers/specs/2026-05-30-electron-shell-oauth-design.md`.
Don't re-derive sub-project boundaries (A/B/C/D) here — cite the spec.

## Docs site

- VitePress with `base: "/docs/"` — served at `<domain>/docs/`
- CloudFront Function rewrites clean URLs (no extension → `.html`)
- Nav links injected via `nav-bar-content-after` layout slot as plain `<a>`
  elements (not Vue Router links) so Vue Router never intercepts
  marketing-site clicks
- Deployed to S3 prefix `docs/` alongside the React SPA in the same bucket
- `DeployUi` CDK construct uses `prune=False` — never delete docs assets
- `DeployDocs` depends on `DeployUi` so docs always win on final write order

## Testing

- pytest for all Python tests (unit, integration, e2e)
- DynamoDB Local (Docker) for integration tests
- Playwright for UI e2e tests
- Unit tests: no AWS deps, fully mocked
- Integration tests: run against DynamoDB Local
- E2e tests: run against deployed AWS dev environment
- **100% coverage required** — both Python (pytest-cov) and JS (vitest v8);
  CI fails below 100%
- Every new UI component needs a co-located `*.test.jsx` file

### E2e test conventions

- Use a unique tag per test run (e.g. `e2e-{timestamp}`) when creating test
  data, then filter by that tag to assert — avoids pagination issues from
  accumulated test data
- When selecting one element among many sharing a class, use Playwright
  `has_text=` (e.g. `page.locator(".docs-nav-link", has_text="Docs")`)
  to avoid strict-mode violations

## CI/CD (GitHub Actions)

`ci.yml` runs on every PR and push to `development` or `main`:

- Lint (ruff) + type check (mypy) + copyright headers
- Unit tests + integration tests (DynamoDB Local) + combined coverage report
- Frontend tests (vitest) + build; coverage uploaded to Codecov
- Docs site build
- Infra synth + Trivy IaC scan (CloudFormation SARIF → GitHub Security tab)
- Trivy dependency audit (SARIF → GitHub Security tab)
- SonarCloud scan
- On push to `development`: deploy to dev + smoke-test the dev API
  (`/health` curl; the two Phase 7c/7d e2e suites in `tests/e2e/`
  are not yet wired into CI — see backlog)
- On push to `main`: release + deploy to prod + back-merge to development

Other workflows:

- `deploy-dev.yml` — manual dev deploy via `workflow_dispatch`
- `security.yml` — scheduled security scans
- `backup-test.yml` — scheduled DynamoDB PITR restore validation

Deploy order: React SPA → docs site (docs depend on SPA deployment completing
first).

## Conventions

- Use uv for all dependency management — never pip or requirements.txt
- Management API on port 8001, UI on port 5173
- All infra in CDK (Python) under `infra/`
- All config via environment variables
- Never hardcode credentials or secrets
- AWS credentials in GitHub Actions via OIDC (no long-lived access keys)
- Pin third-party GitHub Actions to full commit SHAs, not mutable version tags
  (e.g. `uses: actions/checkout@<sha> # v4`); use
  `gh api repos/{owner}/{repo}/git/ref/tags/{tag}` to resolve SHAs

## Product decisions

Durable architectural choices that constrain future designs. Don't
re-derive these during design review — cite them.

- **Workspaces are the tenancy root** — any multi-tenancy feature
  consumes the workspace model. Don't invent a second tenancy axis
  (per-user, per-client-group, etc.) without explicit design review.
- **Billing deferred** — ship features free. Do not design tier
  abstractions, per-seat accounting, or billing gates until billing is
  an active constraint. Keep the concept out of data models for as long
  as possible.
- **Client-side LLM preferred** — features needing an LLM (extraction,
  classification, synthesis) use MCP Sampling. Don't add
  Bedrock / OpenAI dependencies when the MCP client can provide the model.
- **Shared-infra features ship full scope** — when two capabilities share
  ~80% of the infrastructure, ship them together in one release.
  Splitting a shared-infra pair doubles release cost for marginal benefit.
- **Agents swap tokens to switch context** — don't design tool APIs that
  take a `workspace_id` / `namespace` param on every call. Scope comes
  from the token claim; agents register a new DCR client per context and
  swap tokens to switch.
- **Chat scope is enforced via the chat-index ownership check** — every
  read/write of a chat goes through `_load_owned_chat(chat_id, jwt_sub)`
  in `src/channel/api/chats.py` which compares the chat row's `user_id`
  to the JWT `sub` claim. Mismatches return 404 (not 403) so chat
  existence isn't leaked. This replaces the pre-Strands
  `f"{jwt_sub}:{session_id}"` Bedrock sessionId namespacing — Strands'
  `BedrockModel` doesn't expose Bedrock's session machinery, so the
  cross-user guard happens at the API layer instead.
- **Tool payloads never persist to AgentCore Memory.** Conclusions and
  tool-use META facts may. The `[meta]`-prefixed synthetic ASSISTANT
  message is the canonical META-fact shape, written via the existing
  `CreateEvent` path from the `AfterToolCallEvent` hook. Codified by
  ADR-0009 (recall pool budget) and the existing drop in
  `src/channel/agents/memory.py:240` (`_payload_from_messages` strips
  `toolUse` / `toolResult` content blocks before serializing).
- **The code-exec sandbox has zero network egress.** The
  `CodeExecLambda` runs in a dedicated PRIVATE_ISOLATED VPC (no IGW,
  no NAT, no VPC endpoints) with a zero-egress security group; IAM
  remains the data-plane boundary. Don't add NAT, VPC endpoints, or
  egress rules to the sandbox without a design review — the isolation
  closes the prompt-injection → code-exec → exfiltration trifecta.
  Threat model: `docs/security/threat-model-code-exec.md` (#249).
- **Tool integrations use Strands' native `BedrockModel` + `MCPClient` +
  tool hooks.** Don't build a parallel tool-calling shim. Verified in
  Strands 1.41.0: `strands/types/tools.py` (`ToolUse` / `ToolResult`),
  `strands/models/bedrock.py` (Converse `toolConfig` build + tool-block
  translation + `stop_reason: "tool_use"`),
  `strands/event_loop/event_loop.py` (`_handle_tool_execution`),
  `strands/hooks/events.py` (`BeforeToolCallEvent` / `AfterToolCallEvent`),
  `strands/types/_events.py` (`ToolUseStreamEvent` / `ToolResultEvent` /
  `ToolStreamEvent` / `ToolCancelEvent` / `ToolInterruptEvent`),
  `strands/tools/mcp/mcp_client.py` (`MCPClient` is itself a
  `ToolProvider`).

## UI conventions

- **CSS-vars only** — token system in `ui/src/styles/channel.css` (OKLCH light + dark
  themes, radii, shadows, fonts). No Tailwind. Use `var(--canvas)`, `var(--ink)`,
  `var(--raised)`, `var(--accent)`, `var(--border)`, etc. Never hardcode colours.
- **Icons** — `Icon.jsx` provides the project's 24×24 stroke icon set; never use
  emojis as UI elements
- **Marketing image placeholders** — every marketing image is a `<ImageSlot
  name="<descriptive>" />` rendering a placeholder block tagged
  `data-image-slot="<name>"`. A future content pass swaps in real `<img>`
  elements by querying that attribute.
- **Marketing internal links** — `<Link>` from React Router (never plain
  `<a>` for in-app navigation). In-page anchors (`#features`, `#models`)
  stay as `<a>`.
- **jsdom colour normalisation** — in vitest, jsdom converts hex to
  `rgb(r, g, b)`; assert `"rgb(232, 160, 32)"` not `"#e8a020"`
- **Anonymous inline functions** — vitest v8 counts uncovered anonymous
  functions; extract or name handlers that must be tested (e.g. event
  listeners in `useEffect`)
- **`vi.useFakeTimers()`** — activate **before** `render(...)` when the
  timer is scheduled in the component's mount `useEffect` (the common
  case); activating
  after mount leaves the timer pinned to the real clock. Activate
  *after* the initial render only when the test needs real-clock
  progress for some setup phase (e.g. `waitFor` / `findBy*` polling on
  the real clock, or a mount-time helper that requires real-clock
  progress) before switching to fake timers for the timer-driven
  assertion. vitest 1.x's default `toFake` set excludes microtasks, so
  promise resolution is unaffected. Always pair with `vi.useRealTimers()`
  in a matching cleanup. See `.claude/skills/react-component/SKILL.md`
  §5.2 for the full pattern.
- **Chat-app popovers** — ModelPicker, AttachMenu, AccountPopover all follow
  the same `<div className="backdrop" />` + `<div className="pop" />` pattern.
  The backdrop captures outside-clicks to close the popover; the caller
  controls `open` state.
- **Modal dialogs** — all destructive confirms and edit-in-place dialogs
  use `Modal.jsx` (`ui/src/components/Modal.jsx`) for backdrop + Esc
  dismissal. Don't reinvent the modal shell.
- **User identity from JWT** — chat-app components that need the user's
  email or display name read the mgmt JWT from `localStorage[TOKEN_KEY]`
  via `parseToken` from `lib/auth.js`. Display name = email's local-part
  unless we later add a `name` claim.

## Copyright headers

All source files must carry a copyright header. Current year is 2026.

New Python files:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
```

New JS/JSX files:

```js
// Copyright (c) 2026 John Carter. All rights reserved.
```

When editing a file in a new year, append that year to the existing line
(e.g. editing in 2027 → `# Copyright (c) 2026, 2027 John Carter.
All rights reserved.`).

## PR workflow

### Opening a PR

1. Always branch off `origin/development`, never off another feature branch:

   ```bash
   git fetch origin
   git checkout -b fix/my-fix origin/development
   ```

2. Before `gh pr create`, rebase and verify clean history. The full
   procedure — including the W4 shadow-branch fast-forward, the
   `PRE_REBASE_SHA` capture for subsequent pushes, and the explicit-
   refspec push forms — lives in [`.claude/agents/issue-worker.md`
   §"Push discipline"](.claude/agents/issue-worker.md) and §6 ("Create PR")
   of that same file. Read those sections; do not improvise. The
   minimum-shape sketch:

   ```bash
   git fetch origin
   # then: W4 shadow-branch fast-forward, immediately followed by W7
   # ancestry check (W4 and W7 are a coupled pair — see
   # issue-worker.md §"Push discipline" for the mechanical procedure)
   git rebase origin/development
   git log --oneline origin/development..HEAD  # must show ONLY your commits
   ```

3. Every PR body must include `Closes #NNN` linking to the GitHub issue.

4. Validate locally before pushing (same gate as CI):

   ```bash
   uv run inv pre-push        # lint + typecheck + unit tests + frontend tests
   uv run inv deploy --env jc # deploy to personal AWS env
   uv run inv e2e --env jc    # e2e tests against that env
   ```

5. Push using the canonical procedure in
   [`.claude/agents/issue-worker.md` §6](.claude/agents/issue-worker.md) —
   first-push and subsequent-push paths each have a separate explicit-
   refspec form that the W1–W7 rules require. Do not use bare `git push`
   or `git push --force-with-lease` without an explicit refspec.

6. After pushing, watch CI (`gh run watch`) and fix any failures immediately.

All git push operations in this workflow — feature/fix branches in the
"Opening a PR" flow above, the `release/*` branches in the "Releasing
to production" flow below, and every push in the `issue-worker`
autonomous cycle — are bound by the W1–W7 push-discipline rules in
[`.claude/agents/issue-worker.md` §"Push discipline"](.claude/agents/issue-worker.md).
W1 has two scopes: the **issue-worker scope** allows
`feat/` / `fix/` / `chore/` only (the agent never creates release
branches); the **wider project scope** (when humans run W1–W7 manually
during the release flow below) extends the allowlist to include
`release/`. Both scopes share the same rule shape — explicit refspecs,
no wholesale pushes, no bare `git push`. ADR-0008 captures the rationale
and the 2026-04-26 incident timeline. The rules themselves are the
authoritative source — read them there.

### Merge strategy

`gh pr create` doesn't support a merge-strategy flag — but immediately
enabling auto-merge after creating the PR pre-configures the strategy so
it fires automatically once CI passes:

```bash
# feature/fix → development  (squash)
gh pr create --base development ...
gh pr merge --auto --squash --delete-branch

# development → main  (merge commit)
gh pr create --base main ...
gh pr merge --auto --merge
```

| PR direction | Strategy | Why |
| --- | --- | --- |
| feature/fix → `development` | **Squash** | One clean commit per feature |
| `development` → `main` | **Merge commit** | Preserves squashed history |
| `main` → `development` (back-merge) | **Merge commit** | Handled by CI automatically |

### Releasing to production

1. **Create a release branch off `development`:**

   ```bash
   git fetch origin
   git checkout -b release/vX.Y.Z origin/development
   ```

2. **Pick the version number from the drained milestone, not from
   Release Drafter.** The milestone title (`vX.Y`) is the commitment;
   Release Drafter's draft often auto-labels the next patch (e.g.
   `v0.22.1`) because it bumps from the last published tag regardless
   of scope. If the milestone says `v0.23`, the release is `v0.23.0`.

3. **Update `CHANGELOG.md`** — move items from `[Unreleased]` into a
   new `## vX.Y.Z — YYYY-MM-DD` section **curated into
   `Added / Changed / Fixed / Meta` subsections** matching the prior
   releases in the file. The **draft release auto-maintained by
   Release Drafter** (see the releases page in this repo) is the
   what-landed source of truth — don't re-derive from PR history —
   but the Drafter body is a flat bulleted list of PR titles; do
   **not** paste it verbatim. Group related PRs, write 1–2
   descriptive sentences per bullet explaining *what changed and
   why*, and cite the PRs in parentheses. Do **not** add a
   `**Full Changelog:** https://github.com/…/compare/…` link at the
   bottom — CHANGELOG.md is a local artefact, not a GitHub release
   note; the prior sections don't carry compare links either. Compare
   the prior versioned section (`v0.22.0`) for the exact tone and
   subsection structure. Commit the change:

   ```bash
   git add CHANGELOG.md
   git commit -m "chore: prepare release vX.Y.Z"
   git push -u origin release/vX.Y.Z:release/vX.Y.Z   # explicit refspec (W3/W6)
   ```

4. **Open a PR** from `release/vX.Y.Z` → `main`:

   ```bash
   gh pr create --base main --title "release: vX.Y.Z" \
     --body "Release vX.Y.Z. See CHANGELOG for details."
   ```

5. **Merge with `--merge`** (not squash) once CI passes:

   ```bash
   gh pr merge NNN --merge --delete-branch
   ```

6. **CI takes over** — on merge to `main`, the pipeline automatically:
   - Creates the GitHub release + tag
   - Deploys to prod
   - Back-merges `main` → `development`

   **Never run `gh release create` manually** — the pipeline owns this.

## Running the full stack locally

```bash
# 1. Start all services (DynamoDB Local, API, Vite dev server)
#    Add --seed to also seed demo data automatically once the API is ready
uv run inv dev [--seed]
```

`inv dev` sets automatically:

- `CORS_ORIGINS` — `localhost:5173` through `localhost:5179` (handles port
  collisions if 5173 is already taken by another project)
- `STARTER_BYPASS_GOOGLE_AUTH=1` — enables the `?test_email=` auth shortcut
  (only activates when that query param is present; normal browser flows
  are unaffected)

```bash
# 2. Seed DynamoDB with demo data (also creates the table) — if not using --seed
uv run inv seed
```

Must be re-run after every `inv dev` restart (DynamoDB Local is ephemeral).

### Running UI e2e tests locally

> **Note:** `tests/e2e/` currently contains the Phase 7c memory-write
> suite (`test_memory_writes.py`) and the Phase 7d recall + auto-titling
> suite (`test_memory_recall_and_titling.py`). The chats CRUD, auth, CSP,
> regenerate, and idempotency suites have not yet been authored — those
> flows are exercised only by unit + integration tests today.

```bash
# Auto-detects the Vite port — no env vars to set manually
uv run inv e2e-local

# Run a specific test file
uv run inv e2e-local --tests tests/e2e/<file>.py

# Repeat N times to check for flakiness
uv run inv e2e-local --n 5
```

`inv e2e-local` probes ports 5173–5179 for the Channel Vite dev server (via
`/auth/login?test_email=probe`) and passes the detected URL as `STARTER_UI_URL`.

Key local e2e gotchas:

- The Vite proxy handles `/auth`, `/api`, `/oauth`, `/mcp` — tests must use
  the Vite URL (not the API URL directly) so the auth bypass sets
  `localStorage` at the correct origin.
- If Vite lands on a port other than 5173, `CORS_ORIGINS` must include that
  port. `inv dev` covers 5173–5179; if you're outside that range, pass
  `CORS_ORIGINS=http://localhost:<port>` when starting the stack.
- `inv seed` (or `inv dev --seed`) must succeed before running e2e tests —
  if auth bypass returns 500, the table is likely missing.
- Any `test_docs_e2e.py` suite (when restored) is excluded automatically —
  those tests require a deployed VitePress build; run them against the
  deployed stack with `inv e2e`.

### When to run local e2e tests

Not required on every PR — `uv run inv pre-push` (unit + frontend tests) is
the standard gate. Run `inv e2e-local` before opening a PR when the change
touches any of the following:

**Always required:**

- Fixing a failing e2e test — the fix must pass locally before the PR opens
- Auth flows (`auth/`, `AuthGate.jsx`, OAuth endpoints)
- Management API endpoints (`api/`) that the UI tests exercise

**Use judgement (run the relevant `--tests` file at minimum):**

- UI component changes that affect user-visible flows
- Vite proxy config or API base URL changes

**Not needed:**

- Pure unit test fixes, documentation, infra/CDK changes, style/CSS tweaks,
  or any change fully covered by unit + frontend tests

## Pre-PR checklist (required before every push)

Run `uv run inv pre-push` — this runs the same gate as CI:

1. `inv lint-backend` — ruff lint + format check
2. `inv typecheck` — mypy
3. `inv test-unit` — pytest unit tests
4. `inv test-frontend` — vitest

This is enforced automatically if you install the git hook:
`uv run inv install-hooks`

If infra files changed, also run: `uv run inv synth`

---

## Agent workflows

Three agents handle the structured issue workflows. They live in `.claude/agents/` and load automatically.

- **`orchestrator`** — sequences in-flight work across specialist agents: reads live repo state, resolves directives (`status`, `work next <filter>`, `delegate <#N> to <agent>`, `check #N`, `brief #N`, `epic #N`), delegates, and halts on unresolved blockers or tracked template-bootstrap gaps. Design rationale: `docs/adr/0005-orchestrator-agent.md`.
- **`issue-worker`** — autonomous issue cycle: pick → implement → PR → CI → Copilot review → post-merge pipeline watch. Invoke by asking Claude to work through issues, or with `@"issue-worker (agent)"`.
- **`design-review`** — processes `status:design-needed` issues interactively: triage, decisions comment, label flip, sub-issue breakdown for epics.

---

## Autonomous issue workflow

Full protocol lives in `.claude/agents/issue-worker.md`. Summary of invariants that apply even outside the agent:

- Never push directly to `development` or `main`
- Never merge a PR manually — auto-merge handles this
- Never run `gh release create` — CI owns releases
- Never hardcode credentials, secrets, or AWS account IDs
- Never use `pip` or `requirements.txt` — always use `uv`
- Never skip `inv pre-push` before creating a PR
- Never pin GitHub Actions to mutable version tags — use full commit SHAs

## Backlog labels and milestones

Every open implementation issue must carry status + priority + size + area labels.
This section defines the taxonomy.

### Status (one, required)

- `status:ready` — fully scoped, no blockers, queue-eligible
- `status:blocked` — depends on another **open issue in this repo**;
  body must name the blocker with `Blocked by #N`. Not queue-eligible.
- `status:needs-info` — waiting on **off-platform info** (billing,
  account state, external service verification, legal review). Distinct
  from `blocked` — the resolution isn't in this repo. Not queue-eligible.
- `status:design-needed` — not yet reviewed; needs a design pass per
  §Design-review workflow. Not queue-eligible.

### Priority (one, required)

- `priority:p0` — compliance, security, or outage-adjacent; ship this week
- `priority:p1` — ship this quarter
- `priority:p2` — ship eventually; useful but not urgent
- `priority:p3` — someday-maybe

### Size (one, required)

- `size:xs` — less than 1 hour
- `size:s` — half a day
- `size:m` — 1–2 days
- `size:l` — 3–5 days
- `size:xl` — a week or more; must be broken down before the agent picks
  it up

### Area (one or more, required)

`ui`, `ux`, `a11y`, `api`, `mcp`, `auth`, `infra`, `ci`, `dx`, `sdk`,
`security`, `compliance`, `docs`, `design`, `performance`, `observability`,
`marketing`, `seo`, `growth`, `ops`, `reliability`.

### Special labels

- `epic` — tracking issue with sub-issue checklist; never queue-eligible
- `bug` / `enhancement` / `chore` — issue type
- `agent-safe` — PR from this issue can be merged **autonomously by
  the agent** after the §7.5 Copilot review + CI pass. Apply when the
  work is low-risk enough that an LLM reviewer's feedback is
  sufficient without a human final look: `priority:p2` / `p3`,
  `size:xs` / `s` / `m`, and not touching `infra/stacks/channel_stack.py`,
  `.github/workflows/`, or any auth / token-issuance path. Without
  this label, the agent still runs Copilot review (everyone benefits
  from a second opinion) but then stops for human merge.

  Issues carrying `agent-safe` MUST also include a `## Files to touch`
  (or `### Files to touch`) section in the body listing the files the
  PR is allowed to touch. The mechanical scope-boundary check
  (`scripts/check_agent_safe_scope.py`, run by both `code-reviewer`
  and the `agent-safe-scope.yml` CI workflow) blocks any PR whose diff
  strays outside that section. See issue #77 for the design rationale
  and edge-case handling.

  Co-located test files are implicitly accepted alongside their source
  (issue #105) — issue authors don't need to enumerate both. Two rule
  shapes:
  - **JS/TS** — listing `<dir>/<name>.{js,jsx,ts,tsx}` implicitly accepts
    `<dir>/<name>.test.<ext>` and `<dir>/__snapshots__/<name>.test.<ext>.snap`
    in the **same directory only**.
  - **Python** — listing any non-test `<basename>.py` (under
    `src/channel/**`, `scripts/**`, or anywhere) implicitly accepts
    `tests/unit/test_<basename>.py` and any nested
    `tests/unit/**/test_<basename>.py` (matches the repo's pytest
    convention of a fixed test root regardless of source location).
    Entries whose basename already starts with `test_` are skipped —
    forward-direction only.

  Glob entries in `## Files to touch` (e.g. `src/channel/api/*.py`) do
  not generate implicit test derivations; the implicit allowlist is
  keyed off concrete source paths so a wildcard entry can't widen
  scope to `tests/unit/test_*.py` (effectively all unit tests).

  The implicit allowlist is **forward-direction only**: listing the
  source implicitly accepts the test, but listing the test does NOT
  implicitly accept the source. Production code changes always require
  explicit listing — this preserves the property that the issue body
  documents the real production-code surface of change. Test-only
  changes (e.g. "add unit tests for X") are still legitimate; list the
  test file directly in `## Files to touch` and the existing parser
  accepts it without any implicit derivation.

### Issue creation rules

When filing a new issue:

1. Use the GitHub issue template (defaults to `status:ready`)
2. Add a `priority:*` label and a `size:*` label before leaving the page
3. Add at least one area label
4. If the issue is part of an existing epic, add `Part of #NNN` to the
   body so the epic's checklist stays linked
5. If the issue depends on another, add `Blocked by #NNN` to the body and
   apply `status:blocked`

The `label-check.yml` workflow enforces status + priority + size at PR
merge time for any PR that contains `Closes #NNN`.

### Milestones

Keep **three** active milestones at any time — no more:

1. **Current release** (e.g. `v0.20`) — what ships next
2. **Themed hardening bucket** (e.g. `MVP-hardening`) — ship-blocking work
   that's too large for the current release
3. **`Backlog`** — accepted but unscheduled p2/p3 work

Epics are **not** milestoned — they span multiple releases.

Do not create future release milestones in advance; they become stockpiles
and degrade the "what's next" signal. When the current release closes,
create the next one and promote items from the hardening bucket.

### Triage cadence (human, not agent)

- **Weekly** — glance at issues created in the last 7 days; fix any
  missing priority / size / area labels
- **Monthly** — review the hardening bucket and promote shippable items
  into the current release
- **Quarterly** — review `priority:p3` and `status:design-needed` issues;
  promote, rescope, or close. Don't let them rot