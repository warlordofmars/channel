# Channel desktop app — shell & OAuth (sub-project A)

**Date:** 2026-05-30
**Status:** Draft — pending implementation plan
**Worktree:** `.claude/worktrees/feat+electron-app` on branch `feat/electron-app`

## Why now

Marketing already promises an Electron app: `ui/src/marketing/pages/Download.jsx`
advertises macOS `.dmg`, Windows `.exe`, and Linux `.deb/.rpm/AppImage` with
"global hotkeys, offline drafts, and a window that's always a keystroke away,"
and `Careers.jsx` posts a "Desktop Engineer (Electron)" role. The MVP spec
(`docs/superpowers/specs/2026-05-29-channel-mvp-design.md` §"Web frame only")
and `CLAUDE.md:129` both explicitly deferred desktop packaging — so it's time
to fulfil the promise.

## Scope decomposition

"Ship to satisfy the Download page" is multi-spec. This spec covers
**sub-project A only**. B/C/D are listed for context but get their own specs.

| # | Sub-project | Scope summary | Blocks |
|---|---|---|---|
| **A** | **Shell + OAuth rework** *(this spec)* | Electron main/renderer split, bundled SPA, Electron-native Google OAuth via external browser + loopback, cross-platform CI matrix producing unsigned artifacts | everything below |
| B | Signing, distribution, auto-update | Apple Developer ID + notarisation, Windows code-signing, hosted update manifest on S3+CloudFront, `electron-updater`, real Download-page URLs, restore "Relaunch to update" pill | public release |
| C | Native niceties | Application menu, dock badges, global hotkey, tray icon, theme-follows-OS, single-instance lock | nothing |
| D | Offline drafts | Local persistence of unsent composer text + sync-on-reconnect | nothing |

**Rationale for bundling B:** code-signing infrastructure, the update manifest
hosting, and the auto-updater client all share the same release pipeline.
Splitting them doubles release cost for marginal value (CLAUDE.md product
decision: *shared-infra features ship full scope*).

## Goal of sub-project A

Land a runnable, packaged Electron app for macOS / Windows / Linux that:

1. Boots into the existing chat SPA (`/app/*` routes only — marketing pages
   are excluded by design).
2. Signs the user in via Google OAuth without relying on the SPA's
   browser-redirect-to-localStorage pattern (which doesn't work in an
   Electron renderer).
3. Builds in CI on all three OSes with full test coverage.
4. Produces **unsigned** installer artifacts. Signing, distribution, and
   auto-update are sub-project B.

## Architecture

Two processes:

**Main process** (`desktop/main/`, Node runtime) owns:
- Window lifecycle (single `BrowserWindow`, hides on close — quit only via
  menu/dock so unread-count badges and global-hotkey wakeups still work in
  sub-project C without re-launch cost).
- `app://` custom protocol handler serving the bundled SPA bundle from the
  asar. Deep-link paths fall through to `index.html` so React Router's
  `BrowserRouter` works without forking the SPA to `HashRouter`.
- OAuth loopback HTTP server started on demand at a random port in
  `[49152, 65535]`, listens at `/callback`, captures the JWT, IPC-emits it
  to the renderer, then shuts down.
- IPC handlers exposed via a preload script using `contextBridge`.

**Renderer** is the existing SPA bundle, untouched architecturally. It detects
desktop mode via `window.channelDesktop` (only set when the preload runs) and
uses the desktop login bridge instead of the SPA's redirect-flow login.

**Security posture:** `nodeIntegration: false`, `contextIsolation: true`,
`sandbox: true` on the renderer. The renderer has no Node access; every
privileged operation crosses IPC.

**Network:**
- Renderer fetches the API at `https://channel.warlordofmars.net` (build-time
  env var `VITE_API_BASE`). `ui/src/api.js` already reads `VITE_API_BASE` —
  no code change.
- FastAPI's CORS allowlist gains the `app://` renderer origin. Fallback if
  Chromium's opaque-origin handling rejects: a permissive CORS rule scoped
  to `/api/*` paths.

## Project layout

```
channel/
├── desktop/                              # NEW
│   ├── package.json                      # electron, electron-builder, devDeps
│   ├── vitest.config.js                  # environment: "node", v8 coverage 100%
│   ├── electron-builder.yml              # mac/win/linux targets, asar config
│   ├── main/
│   │   ├── index.js                      # app.whenReady → createWindow + protocol
│   │   ├── window.js                     # BrowserWindow factory + lifecycle
│   │   ├── protocol.js                   # app:// handler (deep-path → index.html)
│   │   ├── auth.js                       # loopback server + IPC handler
│   │   └── ipc.js                        # registers all IPC channels
│   ├── preload/
│   │   └── index.js                      # contextBridge → window.channelDesktop
│   ├── resources/
│   │   ├── icon.icns / icon.ico / icon.png   # from existing ChannelMark assets
│   │   └── entitlements.mac.plist        # placeholder — populated in sub-project B
│   └── test/
│       ├── auth.test.js, protocol.test.js, ipc.test.js
│       └── fixtures/
│
├── ui/                                   # SPA — minimal additive changes
│   └── src/app/Login.jsx                 # branches on window.channelDesktop
│
├── src/channel/auth/                     # FastAPI — small additive changes
│   ├── google.py                         # accept + validate desktop_callback
│   └── (route registration)              # /auth/desktop-complete static HTML
│
├── tasks.py                              # add: desktop-dev, desktop-build, desktop-test
├── .github/workflows/
│   └── desktop-build.yml                 # NEW — macos-14 / windows-latest / ubuntu-latest matrix
└── CLAUDE.md                             # new "## Desktop app" section
```

**Why a separate `package.json` under `desktop/`:** Electron's main-process
Node deps (electron, electron-builder, electron-updater later) have no
business in the renderer's dep tree. Two package roots mean `electron` is
unresolvable from the renderer's import graph at build time, so an
accidental `import 'electron'` in SPA source can't slip through review
silently — Vite errors out. The security posture above
(`contextIsolation: true`, `sandbox: true`) is what makes it unreachable
at runtime; the package split is the build-time guard.

**Single-source, twin-build SPA contract:** The SPA has one source tree
with no Electron-specific code-paths. It builds twice from that source
with different `VITE_API_BASE` env vars — once for CloudFront deploy
(`VITE_API_BASE=""`, relative URLs) and once for the Electron bundle
(`VITE_API_BASE="https://channel.warlordofmars.net"`, absolute URLs
because `app://` has no API origin to be relative to). The `app://`
protocol handler in the main process serves the bundle and falls
through deep paths to `index.html`.

## OAuth flow

End-to-end sequence (production):

```
1. User clicks "Sign in with Google" in renderer (Login.jsx, desktop branch)
2. Renderer → preload IPC: window.channelDesktop.login()
3. Main process:
   a. Picks free port P in [49152, 65535]
   b. Generates random state token S (32 bytes, base64url)
   c. Starts http.Server on 127.0.0.1:P, only route: GET /callback
   d. shell.openExternal(
        "https://channel.warlordofmars.net/auth/login"
        + "?desktop_callback=http://127.0.0.1:" + P + "/callback"
        + "&state=" + S
      )
   e. 60-second timeout armed
4. User's default browser opens → FastAPI /auth/login:
   a. Validates desktop_callback is a loopback URL (127.0.0.1 or localhost
      only, http only, /callback path only — no open-redirect surface)
   b. Stores (state S, desktop_callback, Google CSRF nonce) in DynamoDB
      MGMT_STATE record (PK=MGMT_STATE#{S}, TTL 10min)
   c. Redirects to Google with redirect_uri = deployed /auth/callback
      (Google never sees the loopback URL)
5. User authenticates with Google in their normal browser
6. Google → FastAPI /auth/callback:
   a. Validates Google's state nonce against the MGMT_STATE record
   b. Exchanges code → Google id_token → mints mgmt JWT (existing logic)
   c. State record has desktop_callback set → redirects to
      "http://127.0.0.1:P/callback?token=<jwt>&state=<S>"
      instead of the SPA host
   d. Deletes the MGMT_STATE record
7. Browser hits the loopback URL → main process /callback handler:
   a. Validates state S matches what it generated (timing-safe compare)
   b. Responds 200 with a small static HTML "Channel desktop is signed in.
      You can close this window." page
   c. Resolves the pending IPC promise with the JWT
   d. Shuts down the loopback server
8. Renderer receives the JWT, writes it to localStorage under the existing
   key (starter_mgmt_token), navigates to /app
```

**Dev-mode parity:** identical flow, but the `/auth/login` URL is
`http://localhost:8001/auth/login` instead of the deployed host. The
FastAPI's `STARTER_BYPASS_GOOGLE_AUTH=1` `?test_email=` shortcut still
works — `inv desktop-dev` sets it — so dev login is one click.

### Failure-mode handling

| Failure | Handling |
|---|---|
| User closes the browser tab without auth'ing | 60s loopback timeout → IPC promise rejects → renderer surfaces "Login timed out, try again" |
| User cancels at Google's consent screen | Google redirects to FastAPI with `error=access_denied` → FastAPI redirects loopback `?error=access_denied` → main rejects IPC with `USER_CANCELLED` |
| Port collision (loopback can't bind) | Retry on a new random port up to 5×, then surface "Couldn't start local auth server" |
| State mismatch on loopback callback | 400 to the browser, IPC promise rejects with `STATE_MISMATCH` |
| User clicks "Sign in" twice | Second call returns the existing in-flight promise — no second server, no second browser window |
| App quits mid-flow | `before-quit` shuts down the loopback server cleanly |

### Why this is safe

- The `desktop_callback` is loopback-only → an attacker who tricks a user
  into clicking a poisoned `/auth/login?desktop_callback=evil.com` link
  can't redirect to a remote host because the validator rejects it.
- The state token is per-flow random + DynamoDB-stored → an attacker can't
  predict it; the main-process loopback validates it with a timing-safe
  comparison.
- The JWT only ever transits `127.0.0.1` (process-local) — never over a
  network the attacker controls.

### Server-side changes (additive, non-breaking)

- `src/channel/auth/google.py` — `/auth/login` accepts optional
  `desktop_callback` query param. Validator: scheme `http`, host in
  `{127.0.0.1, localhost}`, path `/callback`, port required. Stored in
  the existing MGMT_STATE item.
- `/auth/callback` — if state record carries `desktop_callback`, redirect
  there with `?token=&state=`; otherwise existing SPA-redirect behaviour.
- Unit tests: 8 negative cases for `desktop_callback` validation (external
  host, `https`, wrong path, missing port, `0.0.0.0`, IPv6 loopback,
  query-string injection, fragment injection) — each returns 400.
- Integration test: full loopback path against DynamoDB Local, using a
  stub Google OAuth response.

## Build, dev, and CI

### Dev workflow (`inv desktop-dev`)

Spawns four things in one terminal, tearing them all down on Ctrl-C:

1. DynamoDB Local (Docker, port 8000) — reuses existing `inv dev` helper.
2. FastAPI on `:8001` with `STARTER_BYPASS_GOOGLE_AUTH=1` and `CORS_ORIGINS`
   including `app://-` so the same CORS allowlist works whether Electron
   loads from `localhost:5173` or `app://`.
3. Vite on `:5173`.
4. Electron, launched with `--remote-debugging-port=9222`, pointing the
   renderer at `http://localhost:5173`. DevTools opens automatically; in
   prod builds DevTools is disabled.

### Production build (`inv desktop-build`)

```
inv desktop-build [--platform mac|win|linux|current] [--api-base URL]
```

1. `vite build` in `ui/` with `VITE_API_BASE=https://channel.warlordofmars.net`
   (overridable via `--api-base`).
2. `rsync ui/dist/ desktop/dist-renderer/`.
3. `cd desktop && npm run build` — esbuild bundles the main + preload
   scripts so the preload script stays minimal and auditable.
4. `electron-builder --<platform>` produces unsigned artifacts in
   `desktop/release/`.

Outputs per platform (all unsigned in A; users will see "unidentified
developer" warnings — deliberate; sub-project B fixes it):

- **macOS:** `Channel-<version>-mac-arm64.dmg`, `Channel-<version>-mac-x64.dmg`,
  plus matching `.zip` variants ready for future auto-update.
- **Windows:** `Channel Setup <version>.exe` (NSIS) + portable `.exe`.
- **Linux:** `Channel-<version>.AppImage`, `channel_<version>_amd64.deb`,
  `channel-<version>.x86_64.rpm`.

### CI — new workflow `.github/workflows/desktop-build.yml`

Triggers: PRs touching `desktop/`, `ui/`, or `src/channel/auth/google.py`;
pushes to `development`. Matrix:

| Runner | Builds |
|---|---|
| `macos-14` (Apple Silicon) | mac-arm64, mac-x64 (universal via lipo) |
| `windows-latest` | win-x64 |
| `ubuntu-latest` | linux AppImage + deb + rpm |

Each job runs `inv desktop-test` (100% coverage gate), then
`inv desktop-build --platform <its-own>`, then uploads artifacts via
`actions/upload-artifact` for 30-day retention. **No publishing** —
releases are sub-project B. GitHub Actions third-party actions are
SHA-pinned per project convention.

### Inv wiring

- `inv pre-push` adds a `desktop-test` step so the desktop unit tests
  must pass before any push.
- `inv lint` extends to lint `desktop/` (ESLint with the SPA's config
  minus jsx rules — main process is plain Node).
- `inv typecheck` is unaffected — `desktop/` is JS, not TS; mypy doesn't
  care.

### Ignored paths

`.gitignore` gains:
- `desktop/node_modules/`
- `desktop/dist-renderer/`
- `desktop/release/`

## Testing strategy

Three layers, each with a different runtime.

### Layer 1 — Main-process unit tests (`desktop/test/`, vitest `environment: "node"`)

| Module under test | What we assert |
|---|---|
| `main/protocol.js` | `app://-/index.html` → reads `dist-renderer/index.html`; `app://-/assets/foo.js` → reads the asset; deep path `app://-/app/login` → falls through to `index.html` (so React Router takes over); `app://-/../etc/passwd` → 403 (path-traversal guard); unknown scheme → ignored |
| `main/auth.js` | Free-port picker stays in `[49152, 65535]`; state token is 32 bytes base64url; loopback `/callback?token=&state=` resolves the IPC promise; mismatched state rejects with `STATE_MISMATCH`; `error=access_denied` query rejects with `USER_CANCELLED`; 60s timeout rejects with `TIMEOUT`; double-call returns the existing promise; `before-quit` calls `server.close()` |
| `main/ipc.js` | Each `ipcMain.handle(...)` channel is registered exactly once; unknown channels reject; handlers run only on the main window's `webContents` (sender-id check) |
| `preload/index.js` | `contextBridge.exposeInMainWorld` is called with `"channelDesktop"` and an object whose keys are `{isDesktop: true, login, logout, getVersion}`; no other globals leaked |

`electron` itself is mocked at the module boundary —
`vi.mock('electron', ...)` returns stub `app`, `BrowserWindow`, `ipcMain`,
`shell`, `protocol`. No real Electron runtime in unit tests; that keeps
the suite fast and deterministic in CI.

### Layer 2 — Server unit + integration tests

- `tests/unit/test_auth_google.py` — 8 negative cases for
  `desktop_callback` validation (each returns 400); 1 positive case
  (`http://127.0.0.1:54321/callback` accepted and persisted with the
  `desktop_callback` field on the MGMT_STATE record).
- `tests/integration/` — full `/auth/callback` flow against DynamoDB
  Local: state record with `desktop_callback` causes redirect to that
  loopback URL with `?token=&state=`; a web record (no `desktop_callback`)
  preserves existing SPA-redirect behaviour.

### Layer 3 — Renderer (`ui/`, existing vitest)

`Login.test.jsx` gains two cases:
- `window.channelDesktop` absent → existing SPA redirect button rendered.
- `window.channelDesktop` present → "Sign in with Google" button calls
  `window.channelDesktop.login()` and stores the returned JWT in
  `localStorage[starter_mgmt_token]` on success. The desktop-mode branch
  is mocked via `vi.stubGlobal('channelDesktop', ...)`.

### Smoke step in CI

Each platform's build job runs `electron --version && node desktop/test/smoke.js`,
where `smoke.js` is a ~20-line script that launches the packaged app
headlessly (`xvfb-run` on Linux), waits for the main window's
`did-finish-load` event, and exits 0. Catches the obvious "binary is
corrupt / preload throws on boot" regressions without a full E2E harness.

### Coverage exclusions

`desktop/main/index.js` is the equivalent of `ui/src/main.jsx` — an entry
point that wires modules together, covered by the smoke step rather than
unit tests. Added to `vitest.config.js`'s `coverage.exclude`. Every other
file in `desktop/` is at 100%.

### Deliberately NOT in sub-project A

- **Electron-launching E2E** (Playwright's `_electron.launch()`). The
  CLAUDE.md note says the per-suite e2e files are pending reauthor —
  adding Electron E2E now would land in an empty harness. Defer.
- **Code-signing tests** — sub-project B.
- **Auto-updater tests** — sub-project B.

## Out of scope (deferred / never)

**Sub-project B:**
- Apple Developer ID code-signing & notarisation (entitlements file is a
  placeholder in A).
- Windows code-signing (EV or OV cert, SmartScreen reputation).
- Linux package signing (deb-sig, rpm-sign).
- `electron-updater` integration in `main/` + a hosted update manifest
  on S3+CloudFront.
- The "Relaunch to update v…" pill restored in `Sidebar.jsx`.
- Real download URLs replacing the `href="#"` placeholders in
  `ui/src/marketing/pages/Download.jsx`.
- The GitHub Action for *publishing* a release.
- ADR documenting the release pipeline.

**Sub-project C:**
- Application menu beyond Electron defaults.
- macOS dock badge for unread count.
- Global hotkey to summon the window.
- System tray icon + quick-toggle menu.
- Native theme follow (`useChannelPrefs` "system" → `nativeTheme` events).
- Single-instance lock + window-restore-on-relaunch.

**Sub-project D:**
- Local persistence of unsent composer text per conversation.
- Sync-on-reconnect logic.
- `Composer.jsx` UX for the offline state.

**Permanently out of scope (not even queued):**
- Electron app showing the marketing site (`/`, `/product`, etc.). The
  desktop app only mounts `/app/*` routes. Marketing belongs in a
  browser.
- A separate Google OAuth client of type "Desktop app". The loopback
  flow keeps a single Web app client because Google never sees the
  loopback URL — only FastAPI does.
- Keytar / OS keychain for the JWT in sub-project A. Renderer continues
  to use `localStorage[starter_mgmt_token]` (same boundary as the SPA).
  A keychain migration would be a separate hardening pass.

## Risks for sub-project A

- **Apple Silicon CI runners** (`macos-14`) are paid minutes — multi-OS
  matrix on every PR touching `desktop/`. Mitigated by path filters so
  PRs touching only `src/channel/*` don't trigger the desktop matrix.
- **Vite `base: './'` assumption** — current SPA build works with
  absolute paths because CloudFront serves from the root. The `app://`
  protocol handler rewrites URLs internally, so this is fine, but worth
  verifying with a manual smoke before we commit to the asar layout.
- **CORS preflight on `app://`** — Chromium treats `app://` as opaque
  origin in some preflight cases. If FastAPI's `CORSMiddleware` doesn't
  match `app://-` cleanly, fallback is a permissive CORS rule scoped to
  `/api/*` paths only.

## Open questions for the implementation plan

These are intentionally not resolved in this spec — they belong in the
implementation plan once we know how the work decomposes into PRs:

- Whether `inv desktop-dev` reuses the existing `inv dev` orchestration
  (concurrent-process supervisor) or stands up its own. Likely reuse.
- Asar layout: do we include `dist-renderer/` inside the asar (smaller,
  signed-as-one) or as an unpacked resource (faster icon/asset reads).
  Default to inside-asar; revisit only if measurable startup pain.
- Default window dimensions on first launch, saved across launches via
  `electron-window-state` (a tiny dep) — minor enough to bundle in A or
  defer to C. Default to bundle in A so users get a remembered window.

## Next step

Invoke the writing-plans skill to produce an implementation plan from
this spec.
