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
│           ├── _debug.py      # /api/_debug/* — dev-only, gated by CHANNEL_ENABLE_DEBUG_ENDPOINTS
│           ├── admin.py       # Admin REST — user list/detail (#235) + CloudWatch metrics (#236); every route require_admin-gated
│           ├── assets.py      # Asset REST surface — per-chat list/get/content/delete + browse
│           ├── attachments.py # Attachments API (#175) — presigned S3 upload + finalize
│           ├── chats.py       # Chat CRUD + SSE streaming + regenerate
│           ├── mcp.py         # MCP-server registry REST (list/register/rename/delete/reauth) + per-chat override + /auth/mcp/callback
│           ├── models.py      # GET /api/models — server allowlist
│           ├── prefs.py       # User preferences API — GET/PUT /api/me/prefs (single PREFS row)
│           ├── sessions.py    # Sessions API — GET/DELETE /api/me/sessions[/{device_id}] over refresh rows
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
│   │   │   ├── auth.js        # session storage: load/save/clearSession, readToken, TOKEN_KEY
│   │   │   └── utils.js       # cn (class-name join via clsx)
│   │   ├── hooks/
│   │   │   ├── useAssetContent.js   # Auth-fetched asset payload → blob object-URL or text; shared render hook
│   │   │   ├── useChannelPrefs.js   # theme/accent/density/shape/font/model/effort + siteTheme
│   │   │   ├── useChatList.js       # Sidebar Recents + optimistic create/rename/archive
│   │   │   ├── useChatStream.js     # SSE chat stream: history load, send, abort, status/error
│   │   │   └── ChatsContext.jsx     # ChatsProvider + useChats() — single useChatList instance app-wide
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
management **access** JWT (`typ=mgmt`, `role=admin|user`, **1-hour
TTL**, revocable via the `DENY#{jti}` denylist — #240) signed with
HS256 using a secret resolved from SSM
(`/channel/{env}/jwt-secret`). All `/api/*` endpoints
require a valid Bearer mgmt JWT. The session is stored client-side in
`localStorage` under the `channel_mgmt_token` key. The value is normally
a JSON `{access_token, expires_at}` envelope, but is a **bare JWT**
between a web login and the first SPA save — the login-completion page
writes the token directly rather than restating the envelope's shape in
a Python string template. Readers accept both and derive the deadline
from the `exp` claim for the bare form. See §"Silent refresh (SPA)".

`decode_mgmt_jwt` (`src/channel/auth/tokens.py`) is the single
validation point: it enforces the signature, `iss`, `exp`, and
`typ=mgmt`, then rejects any `jti` on the `DENY#{jti}` denylist via one
strongly-consistent point read. `require_mgmt_user`
(`src/channel/api/_auth.py`) just maps the resulting `JWTError` to HTTP
401 — revocation lives in the decode path (#291) so a future consumer
cannot forget it. A token minted before #240 carries no `jti` and skips
the read; a denylist *read failure* propagates as a 500 rather than
degrading to "not revoked".

**Two token types, different lifetimes.** The 1h access JWT is the
bearer credential on every `/api/*` request. The long-lived credential
is the opaque refresh token (`REFRESH#{sha256(token)}` rows, #290),
which is server-side, hard-rotated on every use, and revocable.
`MGMT_JWT_TTL_SECONDS` was 8h originally, 30 days under #240 (a stop-gap
while no refresh flow existed), and is 1h again as of #291.

**`/auth/callback` mints the session's first refresh token (#292)** —
bound to a server-side opaque `device_id` generated per sign-in (epic
#241 Q2; never client-supplied). The web flow sets the `channel_refresh`
cookie on the login-completion page; the desktop loopback redirect
carries `{token, refresh_token}` in its query string. Minting is
**fail-soft**: a storage failure logs loudly and degrades the session to
access-token-only rather than turning a DynamoDB blip into a total login
outage. The `?test_email=` and desktop-dev bypass shortcuts on
`/auth/login` deliberately mint **no** refresh token — they run on every
deployed non-prod stack, and the decision was to keep a bypass
credential's blast radius at 1 hour rather than extend it to a 30-day
family. Bypass logins therefore re-auth hourly.

**Invariant — every login-completing response must SET or CLEAR the
refresh cookie, never leave it untouched.** Route new login paths
through `_finish_login` in `mgmt_auth.py`; nothing mechanical catches a
path that skips it. `POST /auth/refresh` is unauthenticated by design,
so identity comes entirely from `row.user_id` of whichever cookie is
presented — the cookie *is* the session. A login that leaves a previous
account's cookie in the jar therefore hands the new user the old user's
session on the next refresh. That was live in review: sign in as A
through Google, then hit `/auth/login?test_email=b@example.com` on any
stack with the bypass enabled — localStorage holds B's access token
while the cookie still holds A's 30-day family. The redirect *to* Google
is deliberately exempt: it is not a login completion, and clearing there
would let an abandoned sign-in sign the user out of the session they
already have.

**`POST /auth/logout` revokes both credentials.** It denies the access
token's `jti` (#240) *and* calls `revoke_refresh_token` on the presented
refresh token, which kills the whole device family — not just the one
row, so a successor minted by a concurrent rotation dies too. The token
reaches it by cookie (web) or `{"refresh_token": ...}` body (desktop),
body winning. The cookie is also cleared, but that is cosmetic next to
the server-side revoke: clearing retires the browser's copy, not one
already exfiltrated. All three writes are best-effort — a failure is
logged, never stranding the visible logout.

### `POST /auth/refresh` (#291)

Exchanges a refresh token for a fresh 1h access JWT, rotating the
refresh token in the same round trip (`src/channel/auth/refresh.py`).
**Unauthenticated by design** — the refresh token is the credential, and
the access JWT it renews has usually already expired.

- **Transport is pluggable.** Web SPA: the token rides the
  `channel_refresh` cookie (`HttpOnly` + `Secure` + `SameSite=Strict`,
  path-scoped to `/auth`, `Max-Age` pinned to the family's
  `absolute_expires_at`) and the rotated successor goes back in a
  `Set-Cookie` — never in the response body. Desktop/mobile: the token
  arrives as `{"refresh_token": ...}` and the successor comes back in
  the JSON body. A body token wins over a stray cookie.
  **Never re-spell the cookie's attributes** — `set_refresh_cookie` /
  `clear_refresh_cookie` in `src/channel/auth/refresh.py` are the single
  definition, shared by the mint (`mgmt_auth.py`), the rotation, and the
  logout clear. `Path` + `Name` together are a cookie's identity, so a
  mismatched `Path` silently *adds* a second cookie instead of replacing
  the first. The path is `/auth`, not `/auth/refresh`, because
  `/auth/logout` has to receive the cookie to revoke the family (#292);
  it still keeps the cookie off every `/api/*` request, which is the
  exposure the scoping exists to limit.
- **CSRF**: a **non-empty** `X-Channel-Refresh` header is required; its
  content is never inspected, so send any non-empty string. (Empty is
  treated as absent — proxies and client stacks routinely strip empty
  headers, so accepting one would put the gate at the mercy of
  intermediaries.) A cross-origin forgery cannot set a custom
  header without a preflight this API won't grant. Missing header → 403,
  checked *before* the token is consumed so a forged request can't burn
  a rotation.
- **Every failure is an indistinguishable 401** (`Invalid or expired
  refresh token`); the not-found / revoked / reused / idle-expired /
  absolute-expired taxonomy stays in the logs, since exposing it would
  be an oracle. On the cookie transport a rejection also clears the dead
  cookie, so a browser can't replay it and re-arm the reuse cascade.
- **No refresh token at all → 401**, which is the epic's migration path
  rather than an error: a session predating the refresh flow keeps using
  its existing access token until it expires.
- Response: `{access_token, token_type: "bearer", expires_in}` plus
  `refresh_token` on the body transport only.

### Silent refresh (SPA) (#295)

`ui/src/api.js` renews the access token before it expires, so #291's
1-hour TTL is invisible to the user. Three rules are load-bearing:

- **One seam.** Every authenticated wrapper builds its headers through
  `authHeader()`, which is `async` and renews first. Adding a new
  endpoint wrapper inherits refresh automatically; a raw `fetch` with a
  hand-rolled `Authorization` header does not. **`logout()` is the one
  deliberate exception** — it reads the token synchronously, because
  `Sidebar.signOut` navigates away in the same tick and an awaited
  refresh would let the navigation win, silently losing the family
  revoke and the `jti` denylist write. Rotating a family an instant
  before revoking it is waste besides.
- **Single-flight is correctness, not optimisation.** #290 hard-rotates
  on every use and reads a re-presented token as an OAuth 2.1 reuse
  breach that revokes the whole device family. A page load fires several
  API calls at once, so concurrent renewals would present the
  just-revoked predecessor and sign the user out on the exact path meant
  to keep them in. All callers share one in-flight promise.
- **A refused refresh is not automatically a sign-out.** `/auth/refresh`
  answers 401 both for a dead token and for a client with no refresh
  credential at all (desktop until #297, bypass logins, pre-#292
  sessions) — the latter is the epic's migration path. The still-valid
  access token is used, with a 30s cooldown before retrying. The session
  ends (`endSession()` — clear storage, route to `/app/login`) only when
  the access token is unusable **and** the refresh was refused with a
  **401**. Anything else — offline, DNS, 5xx, a malformed body, and
  notably the **429** #294 is about to add to this endpoint — says
  nothing about the credential, so local state survives and a later
  attempt can recover; reading any 4xx as a verdict would turn
  throttling into a mass logout. This protection covers the
  non-streaming wrappers: `useChatStream`'s send path still ends the
  session on a 401 from the messages endpoint itself, which is the API's
  own verdict on the token rather than a failed renewal.

Storage lives in `ui/src/lib/auth.js`: a JSON `{access_token,
expires_at}` envelope under `channel_mgmt_token`, renewed when
`expires_at` is within 5 minutes. **A refresh token never enters
localStorage** — web uses the HttpOnly cookie, desktop will use the OS
keychain (#297). `saveSession` refuses (throws) anything that is not
structurally a JWT rather than writing an unvalidated `/auth/refresh`
response body into browser storage; both callers already treat a throw
as an ordinary failure. Reads accept the pre-rename `starter_mgmt_token` key
and a bare-JWT value (the shape `/auth/callback`'s login page writes),
deriving the deadline from the `exp` claim; every write goes to the new
key and deletes the legacy one. That read order is only safe while
nothing writes the legacy key, so `MGMT_TOKEN_STORAGE_KEY` in
`mgmt_auth.py` and `TOKEN_KEY` in `lib/auth.js` must move together.
Dropping the legacy fallback is a follow-up, one release out.

**Two known gaps, both deliberate — but they differ in kind.**

1. Renewal fires only from `authHeader()`, i.e. on an API call — so
   `AuthGate` still bounces a *cold load* carrying an expired token to
   `/app/login` without trying the cookie. A session left open is kept
   alive indefinitely; one reopened after the access token died still
   re-authenticates. This one **is** status-quo-neutral — a no-op
   against a world with no refresh at all.
2. Single-flight is per-document: two tabs share a cookie jar but not
   the in-flight promise, so a simultaneous multi-tab renewal can trip
   #290's reuse detection and revoke the whole family — signing the user
   out *everywhere*. `consume_refresh_token`'s own docstring names
   "single-flight in the SPA" as the mitigation for exactly this, so the
   server relies on a guarantee the SPA currently provides only per-tab.
   Unlike gap 1 this is **not** status-quo-neutral: it introduces a
   failure mode that cannot occur today. The race is narrow — both tabs
   must cross the skew window within one round trip — which is why it
   ships, but `navigator.locks.request` should close it soon.

Server-side companion gap: `/auth/logout` is `Depends(require_mgmt_user)`
and `decode_mgmt_jwt` enforces `exp`, so signing out of a tab left idle
past the 1h mark 401s before the family revoke runs and leaves the
refresh family live. Closing it means letting the refresh cookie alone
authorise a logout. No issue filed for this yet — it needs one.

### `/api/me/sessions` (#293)

`GET` lists the caller's live sessions — one entry per `device_id`,
newest first, projected from the live refresh rows
(`list_live_refresh_tokens`). A *session is a device*, not a refresh
row: hard rotation burns through a chain of rows per device, so the
route collapses live rows by `device_id` and shows the newest. `DELETE
/api/me/sessions/{device_id}` ends one device
(`revoke_device_refresh_tokens`); `DELETE /api/me/sessions` ends every
device **and denylists the caller's own access token**, so "sign out
everywhere" takes effect immediately on the device that pressed it
rather than at that token's `exp` — the same pairing `/auth/logout`
performs at single-device scope (#292). The per-device route can't do
the equivalent for its target: the mgmt JWT still carries no `device_id`
claim, so there is no way to map a device to the `jti` it holds. That
would be a mint-path change and remains unbuilt.

Neither `DELETE` clears the `channel_refresh` cookie. It doesn't need
to: the server-side revoke is the authoritative one, and a browser
presenting the now-dead cookie to `/auth/refresh` gets a 401 that clears
it (see above). Clearing here would be cosmetic, and the cookie's
`Path=/auth` doesn't reach `/api/*` on the request side anyway.

Ownership is the JWT `sub` claim and a mismatch is **404, not 403**
(`_load_owned_session` mirrors `_load_owned_chat`): lookups run inside
the caller's own `REFRESH_USER#{sub}` index partition, so another user's
`device_id` is indistinguishable from one that never existed. Both
revoke routes write an audit event (`auth.session_revoke` /
`auth.session_revoke_all`), like `/auth/logout`. The `DELETE`s return
204 with no revoked-row count — a count would imply a post-condition the
eventually-consistent index cannot promise (see the refresh-token entry
under §DynamoDB single table design).

Still to land in epic #241: rate limiting + EMF counters (#294) and
desktop `safeStorage` persistence (#297). The SPA's silent-refresh
wrapper landed in #295 — see §"Silent refresh (SPA)" below. Until #297,
the desktop app still has no refresh credential to present and re-auths
at the 1h access-token expiry.

## DynamoDB single table design

- Activity log items: `PK=LOG#{date}#{hour}`, `SK={timestamp}#{event_id}`
  (hour-sharded to avoid hot partitions)
- Audit log items: `PK=AUDIT#{date}#{hour}`, `SK={timestamp}#{event_id}`
  (immutable compliance trail, TTL via `CHANNEL_AUDIT_RETENTION_DAYS`,
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
- Refresh-token items: `PK=REFRESH#{sha256(raw_token)}`, `SK=META`
  (one row per refresh token; **the raw token is never persisted** —
  the row keys off its SHA-256 hex digest, so a database disclosure
  yields no usable credential. Carries `user_id`, `device_id`,
  `issued_at`, `last_used_at`, plus two independent expiry columns:
  `absolute_expires_at` (30 days, fixed at login, carried forward
  unchanged by every rotation) and `idle_expires_at` (7 days, renewed
  on each rotation). Optional `display_name` (#292) carries Google's
  `name` claim — which reaches the server only at the OAuth callback —
  forward across rotations the same way `absolute_expires_at` does, so a
  refreshed access token stops degrading to the email's local-part;
  absent on pre-#292 rows, where readers fall back to that local-part.
  `ttl` = `absolute_expires_at` as integer Unix
  seconds so a whole token-family self-prunes together. Hard rotation:
  `consume_refresh_token` flips the presented row to `revoked=True` /
  `revoked_reason=rotated` via a **conditional `update_item`**
  (`attribute_exists(PK) AND #revoked = :live`) and mints a successor —
  the condition is what makes concurrent consumes serialize. Revoked
  ancestors are kept until TTL because they are what makes reuse
  detection possible: re-presenting a `rotated` row is the OAuth 2.1
  breach signal (RFC 9700 §4.14.2) and revokes the entire device
  token-family. Rows revoked for any other reason (`logout`,
  `user_revoked`, `reuse_detected`) are an already-dead family and do
  not re-arm the cascade. **Known window:** family revocation reads
  `RefreshByUserIndex`, and DynamoDB refuses `ConsistentRead` on a GSI,
  so a row minted inside the index-propagation window can survive a
  cascade — the reuse path sweeps twice to narrow this, which does not
  close it. Closing it needs a strongly-consistent per-device family
  marker checked on consume. **#293 did not add one** — the sessions
  API it introduced (`GET`/`DELETE /api/me/sessions`, backed by
  `list_live_refresh_tokens` / `revoke_device_refresh_tokens`) is a
  read-and-revoke surface over this same index and *inherits* the
  window: a just-created session can be missing from the list, and
  revoking it can 404 until the index catches up. A family marker is a
  change to the mint/consume path, not to that read surface, and is
  still unbuilt. Damage is bounded by the family's unchanged
  `absolute_expires_at` — #290, #293, epic #241)
- Chat-index items: `PK=USER#{user_id}`, `SK=CHAT#{created_at}#{chat_id}`
  (one row per chat; sortable so the Recents query is a single
  `Query(ScanIndexForward=False)`; also projects onto `ChatByIdIndex`)
- Chat message items: `PK=CHAT#{chat_id}`, `SK=MSG#{created_at}#{msg_id}`
  (one row per turn; UUID suffix prevents cross-Lambda-instance
  collisions at the same microsecond)
- Chat head-summary items: `PK=CHAT#{chat_id}`, `SK=SUMMARY`
  (at most one row per chat; the rolling gist of the turns that have
  scrolled out of the token-budgeted history window — #245. Carries
  `text` (capped at 4 000 chars ≈ 1k tokens), `covers_through` (the
  full `MSG#{created_at}#{msg_id}` sort key of the newest message
  folded in — the resume point for the next pass) and `updated_at`.
  No TTL: the summary is as durable as the chat. The SK carries no
  `MSG#` prefix, so every existing `begins_with("MSG#")` query skips
  it untouched; `storage.delete_chat` deletes it explicitly. Note it
  sorts *after* the `MSG#` rows — `M` < `S` — which is immaterial,
  only the prefix disjointness matters)
- Idempotency items: `PK=IDEMP#{user_id}`, `SK={key}`
  (TTL = 1 hour after reserve; used for the streaming POST replay
  short-circuit)
- MCP server items: `PK=USER#{user_id}`, `SK=MCPSERVER#{server_id}`
  (one row per registered MCP server; persists `auth_type`
  (`oauth_dcr` | `static_token`, #375), `tool_prefix` + `globally_enabled`
  flag; `client_id` is written only for `oauth_dcr` servers (the DCR-issued
  id) and omitted for `static_token` (PAT) servers; rows with no
  `auth_type` attribute read back as `oauth_dcr`; no GSI projection)
- MCP token items: `PK=USER#{user_id}`, `SK=MCPTOKEN#{server_id}`
  (sibling row to MCPSERVER; access/refresh tokens KMS-encrypted at
  application layer; TTL set to `expires_at + 30 days` as a hard
  upper bound for the orphan-row case where DELETE lost a race)
- Chat MCP override items: `PK=CHAT#{chat_id}`, `SK=MCPSERVERS#META`
  (one optional row per chat; `mode=inherit` means "follow user's
  globally_enabled flags as of now", `mode=explicit` means
  "use exactly this list, ignoring future changes to globally_enabled")
- Asset items: `PK=CHAT#{chat_id}`, `SK=ASSET#{created_at}#{asset_id}`
  (one row per chat asset — upload projection, generated artifact, or
  tool output; mirrors the `MSG#` convention so chat deletion cascades
  with one partition Query. Inline text ≤ 100 KB lives in the row's
  `content` attribute; larger text and all binary payloads live in the
  attachments bucket under `assets/chat/{chat_id}/{asset_id}`. Every
  row carries `owner_pk=ASSETOWNER#{owner}` /
  `owner_sk={created_at}#{asset_id}` projecting onto `AssetOwnerIndex`;
  `owner` is user_id today, `{workspace_id}/{user_id}` when workspaces
  land — single-attribute migration, never a second tenancy field)
- GSIs:
  - `KeyIndex` — `GSI1PK`/`GSI1SK` (provisioned but currently unused by
    `src/channel` — no query path in `storage.py`)
  - `TagIndex` — `GSI2PK`/`GSI2SK` (provisioned but currently unused by
    `src/channel` — no query path in `storage.py`)
  - `UserEmailIndex` — `PK=EMAIL#{email}` (for user lookups by email)
  - `ChatByIdIndex` — `PK=CHAT_ID#{chat_id}`, `SK=META`
    (sparse; only chat-index rows project onto it; used for direct
    chat-id → chat lookups without knowing `created_at`)
  - `AssetOwnerIndex` — `PK=ASSETOWNER#{owner}`,
    `SK={created_at}#{asset_id}` (sparse; only asset rows project onto
    it; powers the cross-chat asset browse view, newest first)
  - `RefreshByUserIndex` — `GSI5PK=REFRESH_USER#{user_id}`,
    `GSI5SK={issued_at}#{token_hash[:16]}` (sparse; only refresh rows
    project onto it; powers per-device family revoke, "sign out
    everywhere", and the #293 sessions list. GSI5 was the next free
    numbered slot — GSI3 is `ChatByIdIndex`, GSI4 is `UserEmailIndex`,
    and `AssetOwnerIndex` uses semantic `owner_pk`/`owner_sk` names
    rather than a numbered pair. Per-device narrowing is a
    `FilterExpression` on `device_id`, not a sharper key, so the sort
    key stays time-ordered for the sessions list)

## Asset producers (#326, epic #321)

Everything that creates ASSET rows lives in
`src/channel/agents/asset_producers.py`, driven from the
`chats.py` stream path. Three producers:

- **Upload projection** (deterministic) — at message-send, each
  verified attachment writes an `origin=upload` ASSET row pointing at
  the SAME S3 object as the ATTACHMENT row (metadata projection, no
  byte copy; `kind` derived from MIME). Fires on fresh sends only —
  regenerate never re-projects.
- **Code-exec images** (deterministic) — tool results matching the
  code-exec shape persist each `images[]` entry to S3 under
  `assets/chat/{chat_id}/{asset_id}` (`kind=image`,
  `origin=tool_output`, `source.tool_use_id`). Live base64-over-SSE
  rendering is unchanged; persistence is additive.
- **Generated images** (deterministic, #279) — the Stable Image Core
  `generate_image(prompt, aspect_ratio)` tool
  (`src/channel/agents/tools/generate_image.py`) invokes Bedrock
  `InvokeModel` on `stability.stable-image-core-v1:1` and stashes the
  base64 PNG on `agent.generated_image_sink` (out-of-band from SSE — the
  bytes NEVER ride the wire; the tool's `ToolResult` is a text-only
  confirmation). The post-stream slot reads that sink and persists each
  image via `persist_generated_image_assets` (`kind=image`,
  `origin=generated`, `source.tool_use_id`). Kill-switch:
  `CHANNEL_IMAGE_GEN_ENABLED` gates tool registration in
  `chats._build_tool_registry` (default-on in every deployed env; set
  `"0"` to remove the tool). Content moderation is Bedrock's built-in
  Stability RAI filter — a blocked generation returns a non-null first
  `finish_reasons` entry, which the tool maps to a `ToolResult` error
  whose reason token `content_filtered` (in `content[0].text`)
  `translate_event` extracts into the SSE `tool_error` `error_type`, so
  the SPA sees `error_type="content_filtered"`. No cost gating (billing
  deferred);
  the only observability is the `ImageGenInvocations` /
  `ImageGenFailures` EMF counters. `CHANNEL_IMAGE_GEN_MODEL` overrides
  the model id (Ultra / SD3.5 Large share the identical request
  contract). **Cross-region (the app's only one):** `generate_image`
  invokes Bedrock in **us-west-2** because the Stability text-to-image
  generators are not offered in us-east-1 (and have no us-east-1
  cross-region inference profile). Only this image call leaves
  us-east-1 — the returned PNG persists to the us-east-1 assets bucket
  via the unchanged pipeline, so data at rest stays in us-east-1.
  `CHANNEL_IMAGE_GEN_REGION` (default `us-west-2`) makes it
  configurable; `channel_stack.py` pins the three Stability
  foundation-model ARNs to us-west-2 to authorize the call.
- **Fenced-code extraction** (the ONLY heuristic) — post-stream, in
  the same slot as the auto-titler: fenced code blocks ≥ 15 body
  lines (mermaid excluded — #278 renders those inline) become
  `kind=code` assets with `source.fence_index` (0-based ordinal over
  ALL fences in the message) and `source.lang`. Message text persists
  UNCHANGED — the SPA swaps fence → card by ordinal. Kill-switch:
  `CHANNEL_ASSET_EXTRACTION_ENABLED` (default `"1"`; gates the
  heuristic only).

SSE vocabulary (`strands_sse.py`): `asset_created` / `asset_updated`
frames carry `{"type": ..., "asset": <card descriptor>}` where the
descriptor is `{asset_id, chat_id, msg_id, kind, title, mime,
size_bytes, origin, created_at, source}` — no payload bytes on the
wire; the SPA fetches content via the `/api/chats/{chat_id}/assets`
surface (#325). Frames are emitted only AFTER the row persists —
a rendered card is always durable. Generated-asset frames arrive
after `done` (post-stream slot); upload projections right after
`user_persisted`. Failures are fail-soft per asset:
`asset.persist_failed` log line + `AssetPersistFailures` EMF counter,
never a broken stream.

**Asset content never reaches AgentCore Memory** — code-exec images
ride `toolResult` blocks (stripped), uploads ride `document`/`image`
blocks (no `text` key — dropped), generated-image bytes travel
out-of-band on the agent sink (never in any message block), and
extraction never mutates the agent's message list. Pinned by
`tests/unit/test_memory.py::test_payload_from_messages_never_leaks_asset_content`.

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
- **`CHANNEL_AGENTCORE_MEMORY_NAME`** overrides the default name —
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
verification. Mounted only when `CHANNEL_ENABLE_DEBUG_ENDPOINTS=1`;
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
- **Kill-switch** — `CHANNEL_RECALL_ENABLED=0` short-circuits.
- **History**: Phase 7d implemented this via `RetrieveMemoryRecords`
  + `SemanticMemoryStrategy`. The strategy's async ingestion lag
  (hours in real use) made recall empty for too long, so 8a pivoted
  to raw-event reads. The strategy is no longer attached to new
  memories.

### Memory tools — `remember` / `recall` (#273)

Agent-driven persistent memory: two native Strands `@tool`s
(`src/channel/agents/tools/memory_tools.py`) that let the model
*choose* what to persist and when to search, complementing the
always-on `AgentCoreRecallHook`. Both are thin wrappers over the
already-wired AgentCore plumbing — **no parallel memory system, no new
infra**.

- **`remember(content, tags=None)`** → the same `CreateEvent` path as
  `AgentCoreMemoryHook`, writing a note-shaped `ASSISTANT` event
  tagged `[remember]` (parallels the `[meta]` convention). `tags` are
  encoded inline as `[tags: a, b]` (AgentCore's conversational payload
  has no native tag field), which also makes them keyword-searchable
  by `recall`.
- **`recall(query)`** → the same `ListSessions` + `ListEvents` reads as
  `AgentCoreRecallHook`, flattened and keyword/recency-ranked, returned
  as **tool-result text**. **Known v1 limitation:** the
  `SemanticMemoryStrategy` was retired in Phase 8a, so this is
  keyword/recency over the actor's own events, not vector search — the
  model does the final relevance judgment. A semantic index is a v2
  optimization.
- **Bound per-request via `build_memory_tools(memory_id, actor_id,
  session_id)`** — a factory closure, because the stateless
  module-level `@tool` pattern (`clock` / `web_search`) can't carry
  `memory_id` / `actor_id` / `session_id`. Appended to the tools list
  **inside `build_agent`** (not `chats._build_tool_registry`) where
  that context exists. Scoping reuses `actorId =
  _sanitize_actor_id(jwt.sub)` — follows, does not pre-empt, the future
  `{workspace_id}/{user_id}` scheme.
- **Kill-switch** — `CHANNEL_MEMORY_TOOLS_ENABLED` (default `"1"`, same
  memory-family convention as `CHANNEL_RECALL_ENABLED` /
  `CHANNEL_AUTO_TITLE_ENABLED`). `"0"` removes both tools.
- **Coexists with the recall hook** — Q2 keeps both; the hook's fate is
  deferred to #274 (+ #227). The tool's output lands in the
  *tool-result* register, the hook's in the *system prompt* — different
  registers, so overlap is bounded.
- **Trust posture (#273 / #299)** — own-data only (token-scoped, not
  the cross-owner Hive pool) delivered in the **data register**, never
  spliced into the system-prompt instruction register (that's the
  recall hook's seam, which #299/#95 flag). This is what keeps #273 v1
  off the confused-deputy surface #299 guards.
- **Metrics (#400)** — `remember` emits `record_memory_tool_write_outcome`
  (`MemoryToolWriteSuccesses` / `MemoryToolWriteFailures`); `recall` emits
  `record_memory_tool_recall_outcome` (`MemoryToolRecallSuccesses` /
  `MemoryToolRecallFailures`). These are **deliberately separate** from the
  hook counters (`MemoryWriteSuccesses` / `RecallSuccesses`) so the admin
  dashboard's hook-health signal stays isolated from agent-driven tool
  usage — separate counters rather than a `source=hook|tool` dimension,
  preserving the no-per-actor/per-session cardinality rule. Both are on the
  admin metrics allowlist (`src/channel/api/admin.py`) and surfaced by the
  dashboard's "Tool saves" / "Tool recall" tiles
  (`ui/src/app/admin/Dashboard.jsx`). v1 (#273) reused the hook counters;
  #400 split them. The chassis telemetry hook additionally records
  `[meta] used remember` / `[meta] used recall` events per invocation as
  normal.

### Auto-titling (Phase 7d)

- After the first assistant `done` event lands (and
  `chat.message_count == 0` at function entry), `_stream_bedrock_reply`
  invokes a small Haiku Strands `Agent` (from `build_titler_agent()`)
  with NO memory hooks and `max_tokens=20`.
- Persists the title via `storage.patch_chat`, then emits
  `sse_title_suggested(chat_id, title)` SSE frame before stream close.
  The SPA's `useChatStream` forwards via `onTitleSuggested` to
  `ChatsContext.renameChatLocal`.
- **Kill-switch** — `CHANNEL_AUTO_TITLE_ENABLED=0` skips the titler
  block. Default `"1"`.
- **Model override** — `CHANNEL_TITLER_MODEL` (default `claude-haiku-4-5`).

### Follow-up suggestions (#156, #469)

Chips of related prompts under the last assistant turn, generated by a
one-shot Agent in the same post-stream slot as the titler.

- **Two independent gates, both must allow it.**
  `CHANNEL_FOLLOWUPS_ENABLED` (default `"1"`) is the global
  kill-switch; the per-user `suggest_followups` pref (default `True`,
  in the `PREFS` row, surfaced on `/app/customize`) is the narrower
  second gate. `chats.py` reads the pref at stream start and skips
  **the model invocation itself** when either gate is off — the pref
  saves a Bedrock call per turn, it does not merely hide output.
  Reached by BOTH `POST /{chat_id}/messages` and
  `POST /{chat_id}/regenerate`, since both share
  `_stream_bedrock_reply`.
- **The SPA gates the render too** (`Conversation.jsx`), which is not
  redundant: `useChannelPrefs` PUTs pref changes to `/api/me/prefs`
  fire-and-forget with failures swallowed, and until #295 lands the SPA
  never refreshes its 1h access token — so a PUT on an expired token
  401s silently and the server's copy of the pref goes stale. The
  client gate makes the toggle take effect locally regardless.
- **Suggestions are never persisted** — they live only in the SSE
  `follow_ups_suggested` frame and the SPA's in-memory turn state, so
  they do not survive a reload and never reach AgentCore Memory.

## Long-chat context: token budget + rolling head summary (#245)

How much of the *current* chat the model actually sees. Distinct from
recall (which covers *other* chats and deliberately excludes this one)
and from `SummarizingConversationManager` (which compresses inside the
window this section decides).

- **The window is a token budget, not a turn count.**
  `_HISTORY_TOKEN_BUDGET = 64_000` in `src/channel/api/chats.py`
  replaced the old `_HISTORY_TURNS_LIMIT = 100`. Messages load
  newest-first until the budget is spent, with a hard row cap of
  `_HISTORY_MAX_MESSAGES = 500`. At least one message always survives.
  The per-message estimate is `len(text) // 4` — deliberately crude,
  because it only gates *loading*; `use_native_token_count=True` and
  the proactive-compression threshold work on accurate counts
  downstream. Don't add a CountTokens call at this layer.
  **Why:** the fixed count was measurably wrong — a live 312-message
  chat fed the model 100 messages and silently dropped 212 of them
  (#227), which is the "loses the thread mid-chat" symptom. ADR-0009
  makes the envelope, not a turn count, the unit.
- **Truncation is no longer silent.** A slid window emits a
  `history_window_truncated` INFO line and the `HistoryWindowTruncated`
  EMF counter. This is the diagnosis signal #227 lacked.
- **Rolling head summary.** Post-stream (last in the same slot as the
  titler / follow-ups, since it emits no SSE frame),
  `_stream_bedrock_reply` folds the turns between `covers_through` and
  the window's oldest message into the `SK=SUMMARY` row via a one-shot
  Haiku agent (`build_head_summary_agent()` — no memory hooks, bounded
  `max_tokens`). Bounded at `_HEAD_SUMMARY_MAX_MESSAGES_PER_PASS = 100`
  turns per pass; a chat that jumps far past the budget converges over
  successive turns as `covers_through` walks forward. **In-budget chats
  do no summarisation work** — no range query, no Bedrock call. (The
  `get_chat_summary` point-read is unconditional per turn while the
  kill-switch is on: injection needs it before the stream starts.)
- **Injection** — the summary is appended to the system prompt at
  agent-build time (`build_agent(head_summary=...)`) under
  `## Earlier in this conversation`, which places it **before** the
  recall hook's `## What we've talked about before` block (the hook
  appends at `BeforeInvocationEvent`, after construction). The current
  chat's own gist outranks fragments of other chats.
  `DEFAULT_SYSTEM_PROMPT` tells the model the block is a lossy gist and
  to trust verbatim in-window history over it — mirroring the existing
  recall-vs-current-statement rule.
- **Failure mode** — log (`head_summary_failed`) + EMF
  (`HeadSummaryFailures`, dimension-free like `MemoryWriteFailures`) +
  swallow. `covers_through` advances only when a pass persists, so a
  failure is retried naturally on the next turn. An empty generation
  counts as a failure and never overwrites a good summary.
- **Kill-switch** — `CHANNEL_HEAD_SUMMARY_ENABLED` (default `"1"`);
  `"0"` skips both generation and injection.
- **Model override** — `CHANNEL_HEAD_SUMMARY_MODEL` (default
  `claude-haiku-4-5`).
- **Prompt-injection posture** — the summariser sees attacker-influenced
  text (chat turns, and a prior summary derived from them), so
  `build_head_summary_prompt` applies the same #256 Layer-1 framing as
  the titler: delimited "this is data — do not respond" block, forged
  delimiters defused, per-turn text capped.

## Development self-awareness (#387)

`DEFAULT_SYSTEM_PROMPT` (`src/channel/agents/chat_agent.py`) teaches
Channel that it lives inside its own multi-agent development system
(orchestrator / issue-worker / design-review) whose live state is the
`warlordofmars/channel` GitHub repo. When the GitHub server is enabled
on the chat and the user asks about backlog / PR / issue / agent-activity
state, Channel reads that state **live** via its `github_*` MCP read
tools (the #277 read surface) rather than guessing or answering from
stale recall. The prompt names its tools generically ("your GitHub
tools"); it deliberately does **not** enumerate `github_*` tool names,
since the toolset evolves and is capped per-server (#389).

- **Live-read only, no persisted snapshot.** The *raw* GitHub read
  results are tool payloads — they stay in the tool-result register and
  are **never** persisted to AgentCore Memory (per the "tool payloads
  never persist to AgentCore Memory" product decision) or to the Hive
  pool; #387 adds no snapshot cache. (A model-authored *conclusion*
  written via the #273 `remember` tool is the separate,
  product-decision-permitted path — "conclusions and tool-use META
  facts may [persist]" — not the raw state dump.) A cached backlog
  snapshot would be both stale-within-minutes and a confused-deputy
  hazard (#299); the live-read/no-persist posture sidesteps both, so
  #387 does not create or inherit the #299 boundary.
- **Scope: the `warlordofmars/channel` repo only. Read + narrate, never
  dispatch.** Channel observes and explains its dev system; it never
  triggers the orchestrator or dispatches agents (that step is out of
  scope — #299 territory). Sibling-repo awareness is #286 / #397, not
  here. No bespoke tool wraps `github_*` — the composition is
  prompt-level (per "don't build a parallel tool-calling shim").
- **#393 ↔ #387 seam.** #393 stores *what each agent IS* (distilled
  agent-contract summaries in AgentCore own-data, surfaced by recall);
  #387 reads *what the system is DOING now* (live GitHub state) and
  narrates it through that identity knowledge. #387 ships independently
  of #393 (falling back to the `.claude/agents/` files) and gets richer
  when #393 lands.

## Observability (#111)

Every EMF counter lives in `src/channel/metrics.py` under namespace
`Channel`. **Go through a named `record_*` helper, never a raw
`emit_metric` call** — the helpers are where the no-dimensions rule is
enforced, pinned by `_signature_locks_out_dimensions` tests.

### Request SLIs

`api/main.py`'s `_log_requests` middleware both logs and meters every
request (one middleware, one clock — a second one would time the request
twice for no new signal). It emits `RequestCount`, `RequestLatencyMs`,
and `Request4xxCount` / `Request5xxCount` via `record_request_outcome`.

- **`_log_requests` is registered LAST on purpose**, which makes it the
  *outermost* user middleware (Starlette runs the most recently added
  wrapper first). From out there it observes every response the app
  produces — including `_verify_origin_secret`'s 403, which an inner
  layer never sees.
- **Unhandled exceptions are metered as a synthetic 500, then re-raised.**
  `ServerErrorMiddleware` — which turns an escaped exception into the 500
  the client receives — sits outside the *user* middleware stack
  entirely, so without the explicit `try/except` in `_log_requests` the
  most alarm-worthy failure class (a route raising) would produce neither
  a log line nor a `Request5xxCount` datapoint, and `ApiRequestErrorRate`
  would stay flat through a real outage. The catch is `except Exception`,
  not `BaseException`, so a client disconnect (`CancelledError`) is never
  miscounted as a 500.

- **`duration_ms` is time to response *start*, not last byte.** Starlette's
  `BaseHTTPMiddleware` returns from `call_next` once `http.response.start`
  arrives, so an SSE chat turn contributes its time-to-first-byte, not its
  multi-minute stream duration. That is what makes one service-wide p99
  latency alarm meaningful on a service whose slowest route streams by
  design.
- **`Route` is the only dimension the module permits**, and only because
  it is a build-time-bounded enum: the middleware resolves the matched
  FastAPI route's *template* (`/api/chats/{chat_id}`) from
  `request.scope["route"]`, never `request.url.path`, and collapses
  unmatched requests into the single `other` bucket. Every metric is ALSO
  emitted under the aggregate `{Environment}` dimension set — that is the
  one CDK alarms and `/api/admin/metrics/*` read, since CloudWatch matches
  a metric only on an exact dimension set.
- **`CHANNEL_REQUEST_ROUTE_DIMENSION_ENABLED`** (default `"1"`) kills the
  per-`Route` breakdown without touching the aggregate. Flip it to `"0"`
  if the custom-metric bill outgrows the drill-down — CloudWatch Logs
  Insights answers the same question from the structured request log lines
  for free (the `Channel/{env}/api-latency` saved query).
- **`client_id`** is set by `require_mgmt_user`, which publishes the
  caller's `fingerprint_id(jwt.sub)` digest (never the raw sub — it is an
  email) two ways: the ContextVar for log lines emitted *inside* the
  request, and `request.state` for the middleware's completion line.
  `BaseHTTPMiddleware` runs the app in its own task, so a ContextVar set
  inside the request does not propagate back out — hence both.

### Bedrock SLIs

`_stream_bedrock_reply` emits one `record_bedrock_turn` per turn:
`BedrockLatencyMs`, `BedrockTokensIn`, `BedrockTokensOut`, plus
`BedrockErrors` and `BedrockThrottles` on failure. Exactly one
`BedrockLatencyMs` datapoint per turn, so its **`SampleCount` statistic is
the turn count** — the CDK error-rate alarm uses it as the denominator
rather than paying for a separate counter. `error_code` is a branch
selector, not a dimension, so the `_STREAM_ERROR_MAP` code set can grow
without multiplying metrics; the per-code breakdown stays in the
`chat_stream_failed` log line. A client disconnect re-raises before this
point and is deliberately not counted as a Bedrock error.

### Alarms must reference metrics we emit

`tests/unit/test_channel_stack.py::test_every_channel_namespace_alarm_references_an_emitted_metric`
asserts every `Channel`-namespace alarm watches a name `metrics.py` (or
`csp.py`) actually publishes. Three alarms previously watched names
nothing ever emitted (`ToolErrors`, `StorageLatencyMs`,
`TokenValidationFailures`); with `treat_missing_data=NOT_BREACHING` they
sat permanently green, advertising coverage that did not exist. Adding an
alarm for a metric you have not wired up now fails at `inv pre-push`.

## Management UI

- React SPA (Vite), runs on port 5173 in dev
- Marketing routes at `/`, app routes at `/app/*`
- Communicates with FastAPI management API on port 8001
- Auth: Google OAuth via `/auth/login`; session stored in localStorage as `channel_mgmt_token`
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

In `inv desktop-dev`, the dev FastAPI runs with **both**
`CHANNEL_BYPASS_GOOGLE_AUTH=1` and `CHANNEL_DESKTOP_DEV_EMAIL`
(`dev@channel.local`). When both are set, `/auth/login` still validates
`desktop_callback` + `state` as in step 3, but then skips the `MGMT_STATE`
write and the whole of step 4: it mints a synthetic mgmt JWT for that
address and redirects straight back to the loopback with `?token=&state=`,
so local desktop iteration never touches Google or DynamoDB. Step 5 is
unchanged.

The dev-email gate is load-bearing. Every deployed non-prod stack sets
`CHANNEL_BYPASS_GOOGLE_AUTH=1` (for the `?test_email=` e2e shortcut) but
deliberately never sets `CHANNEL_DESKTOP_DEV_EMAIL`, so real desktop
sign-in on deployed dev routes through Google like any other browser flow.
Gating the short-circuit on the bypass flag alone would silently
auto-log-in every deployed-dev desktop user as the placeholder account.

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
- **Gotcha: the local pre-push gate does NOT enforce JS coverage.**
  `inv test-frontend` (and thus `inv pre-push`) runs `npm test`
  (`vitest run`, no coverage), so a per-file branch/line gap passes
  locally but fails CI, which runs `npm run test:coverage`
  (`vitest run --coverage`) with the 100% v8 threshold. Before pushing a
  UI change, run `cd ui && npm run test:coverage` to match CI.
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
  email or display name read the mgmt JWT via `readToken()` from
  `lib/auth.js` (never `localStorage.getItem` directly — the stored value
  is an envelope, and there is a legacy key to fall back to), then
  `parseToken`. Display name = the `display_name` claim, falling back to
  the email's local-part.

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
uv run inv dev
```

`inv dev` sets automatically:

- `CORS_ORIGINS` — `localhost:5173` through `localhost:5179` (handles port
  collisions if 5173 is already taken by another project)
- `CHANNEL_BYPASS_GOOGLE_AUTH=1` — enables the `?test_email=` auth shortcut
  (only activates when that query param is present; normal browser flows
  are unaffected)

```bash
# 2. Provision the local DynamoDB table (must re-run after every
#    `inv dev` restart — DynamoDB Local is in-memory/ephemeral)
uv run python scripts/reset_dev_table.py
```

The script drops and recreates the local `channel` table with all five
GSIs (schema lives in `channel._table_schema`, shared with the
integration test fixture). It provisions schema only — nothing seeds
demo data.

### Admin-role tokens in local dev

The `?test_email=` bypass mints an **admin**-role JWT only when the email
is listed in `ALLOWED_EMAILS` — a JSON array the login path reads via
`is_admin_email()` (`src/channel/auth/google.py`). Unlisted emails still
log in via the bypass, just downgraded to `role=user`. **Malformed JSON
fails closed to an empty allowlist**, so every `?test_email=` login drops
to `role=user` (the bypass runs only the role check, never the allowlist
*gate*); the real Google sign-in flow, which does run that gate, denies an
unlisted or malformed-config login outright (HTTP 403). `inv dev` spreads
the shell environment into the API process, so exporting before launch is
the whole recipe (when the env var is unset the allowlist falls back to
the `ALLOWED_EMAILS_PARAM` SSM param, default `/channel/allowed-emails`;
either source is cached in-process for ~60s):

```bash
export ALLOWED_EMAILS='["admin@channel.local"]'
uv run inv dev
# /auth/login?test_email=admin@channel.local → role=admin token
# any email NOT in the list → role=user token
```

### Running UI e2e tests locally

> **Note:** `tests/e2e/` covers memory writes (`test_memory_writes.py`),
> recall + auto-titling (`test_memory_recall_and_titling.py`), chat
> management (`test_chat_management.py`), attachments
> (`test_attachments.py`), assets (`test_assets.py`), and the tool
> smokes (`test_tool_use_smoke.py`, `test_code_exec.py`,
> `test_web_search_smoke.py`, `test_mcp_hive_smoke.py`). Auth, CSP,
> regenerate, and follow-up-chip suites have not yet been authored —
> those flows are exercised only by unit + integration tests today.
> Re-check this list against `ls tests/e2e/` before relying on it to
> justify skipping a local e2e run; it has drifted before.

```bash
# Auto-detects the Vite port — no env vars to set manually
uv run inv e2e-local

# Run a specific test file
uv run inv e2e-local --tests tests/e2e/<file>.py

# Repeat N times to check for flakiness
uv run inv e2e-local --n 5
```

`inv e2e-local` probes ports 5173–5179 for the Channel Vite dev server (via
`/auth/login?test_email=probe`) and passes the detected URL as `CHANNEL_UI_URL`.

Key local e2e gotchas:

- The Vite proxy handles `/auth`, `/api`, `/oauth`, `/mcp` — tests must use
  the Vite URL (not the API URL directly) so the auth bypass sets
  `localStorage` at the correct origin.
- If Vite lands on a port other than 5173, `CORS_ORIGINS` must include that
  port. `inv dev` covers 5173–5179; if you're outside that range, pass
  `CORS_ORIGINS=http://localhost:<port>` when starting the stack.
- `uv run python scripts/reset_dev_table.py` must succeed before running
  e2e tests — if auth bypass returns 500, the table is likely missing.
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
- **Weekly** — run `uv run inv check-blockers` (read-only) to catch
  `Blocked by #N` references that outlived their blocker: a
  `status:blocked` issue whose blockers all closed, an unlabelled issue
  naming an open blocker, a blocker closed `NOT_PLANNED` (re-point it,
  don't just clear it), or a reference to a non-existent issue. Add
  `--fix` to apply the two mechanical label flips. The
  `stale-blockers.yml` workflow runs the same sweep every Monday, but
  **report-only** — it never passes `--fix`
- **Monthly** — review the hardening bucket and promote shippable items
  into the current release
- **Quarterly** — review `priority:p3` and `status:design-needed` issues;
  promote, rescope, or close. Don't let them rot