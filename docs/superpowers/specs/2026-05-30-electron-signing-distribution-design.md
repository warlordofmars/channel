# Electron sub-project B — code signing, distribution, and auto-update

**Status:** Approved — 2026-05-30
**Tracking issue:** #33
**Prior art:** `docs/superpowers/specs/2026-05-30-electron-shell-oauth-design.md` (sub-project A)

## Goal

Take the unsigned-build path established by sub-project A (#23, #29, #30) and turn it into a real, signed, auto-updating release pipeline for the Channel desktop app — scoped to what's worth standing up for the current audience (the author plus a handful of vouched testers), with seams that let Windows and Linux signing drop in later as a follow-on without re-architecting CI or CDK.

## Audience and signing scope

The audience this round is **the author plus a handful of vouched testers**. That choice constrains the signing scope:

- **macOS** — fully signed and notarised. Without notarisation Gatekeeper blocks testers from even opening the `.app`, so this earns its keep on day one.
- **Windows** — unsigned. SmartScreen will warn on first install; testers click through. A future sub-project introduces an EV or Azure Trusted Signing cert.
- **Linux** — unsigned. `.deb` / `.rpm` / AppImage download normally; testers reinstall when they want a newer build.

Auto-update is **macOS-only this round**. The `electron-updater` module is wired into `desktop/main/`, but its initialiser is gated on `process.platform === "darwin"`. Windows and Linux users reinstall manually from GitHub Release attachments.

## Channels

Two channels, both rolling, no human-in-the-loop publishing:

| Channel | Build trigger | Update manifest URL |
|---|---|---|
| `latest` | push to `main` → `ChannelStack` | `https://channel.warlordofmars.net/updates/latest/latest-mac.yml` |
| `dev` | push to `development` → `ChannelStack-dev` | `https://channel-dev.warlordofmars.net/updates/dev/latest-mac.yml` |

Each CDK env already owns its own CloudFront distribution; each gets its own `/updates/*` behavior. The update URL is baked into each desktop build at build time via `electron-builder.yml`'s `publish.url`, so a production build only ever checks the prod URL and a dev build only ever checks the dev URL. No cross-env coupling, no runtime channel-switching UI.

## Apple Developer ID bootstrap (one-time, by hand)

The author does not yet hold an Apple Developer ID. The bootstrap sub-issue (see §Sub-issue breakdown) covers this end-to-end before any CI signing work begins. Runbook lives in `docs/dev/apple-signing-bootstrap.md` and is summarised here:

1. **Enrol in the Apple Developer Program** at developer.apple.com — personal account against the author's existing Apple ID, $99/yr, 24–48h to activate. Identity verification may ask for a photo ID.
2. **Generate the Developer ID Application certificate** — Xcode → Settings → Accounts → Manage Certificates → `+` → "Developer ID Application". Xcode stores the private key in the Keychain.
3. **Export the cert + key as a `.p12`** — Keychain Access → right-click the cert → Export → set a strong password. This is the `APPLE_CERT_P12` blob.
4. **Create an App Store Connect API key for `notarytool`** — appstoreconnect.apple.com → Users and Access → Integrations → App Store Connect API → `+`. Role: "Developer" (sufficient for notarisation, no broader access). Download the `.p8` once; it is never downloadable again.
5. **Set the five GitHub Actions repo secrets** via `gh secret set`:
   - `APPLE_CERT_P12_BASE64` — `base64 -i cert.p12 | pbcopy`
   - `APPLE_CERT_PASSWORD`
   - `APPLE_API_KEY_P8_BASE64`
   - `APPLE_API_KEY_ID` — 10-char string from the portal
   - `APPLE_API_ISSUER_ID` — UUID from the portal
6. **Verify locally before trusting CI** — `desktop/scripts/sign_local.sh` signs a built `.app` against the cert in the local Keychain, calls `xcrun notarytool submit` with the API key, and reports until success. Idempotent.

Lambda never sees any of these secrets. They are build-time only.

## Components

### Bootstrap sub-issue (closes before main implementation)

- `docs/dev/apple-signing-bootstrap.md` — the runbook in checklist form, written for the future case of cert renewal as much as initial setup.
- `desktop/scripts/sign_local.sh` — signs a built `.app` against the local Keychain cert, calls `xcrun notarytool submit`, polls status until done. Idempotent; safe to re-run.
- `desktop/resources/entitlements.mac.plist` — replaces the placeholder shipped in sub-project A. Required entries: `com.apple.security.cs.allow-jit` and `com.apple.security.cs.allow-unsigned-executable-memory` (Electron needs both for V8), `com.apple.security.network.client` (for FastAPI calls). Other entries added only if a smoke run shows they are needed.

### Main implementation

- **`desktop/main/updater.js`** — new module. `init(channel)` wires `electron-updater` against the channel-specific feed URL. No-ops if `process.platform !== "darwin"`. Emits IPC events `update:checking`, `update:available`, `update:downloaded`, `update:error`. Exposes `relaunchToUpdate()`. Polls on startup + every 4h.
- **`desktop/preload/index.js`** — contextBridge extended with `onUpdateStatus(cb)` and `relaunchToUpdate()`.
- **`desktop/scripts/notarize.js`** — `afterSign` hook for `electron-builder`. Calls `@electron/notarize` with the App Store Connect API key resolved from env vars; staples the ticket. CI-only.
- **`desktop/electron-builder.yml`** — flip `hardenedRuntime: true`, `gatekeeperAssess: true`. Point `mac.entitlements` at the plist. Register the `afterSign` hook. Set `publish.provider: generic` with the channel-templated URL. Override `mac.artifactName` per target so the `.dmg` is `Channel-${os}.${ext}` (stable) while `.zip` stays `Channel-${version}-${os}.${ext}` (versioned).
- **`desktop/test/updater.test.js`** — vitest, `environment: "node"`. Covers the platform gate (returns early on `win32` / `linux`), the IPC event surface, the channel→URL mapping. Mocks `electron-updater`. 100% coverage per the desktop/ project gate.
- **`infra/stacks/channel_stack.py`** — new `DeployUpdates` construct mirroring `DeployUi` / `DeployDocs`. Adds the CloudFront behavior for `/updates/*` (60s TTL for manifests, 1y immutable for binaries). Attaches the bucket lifecycle policy described in §Versioning (scoped to `updates/*/Channel-*-mac.zip*`, 30-day expiration). `prune=False`. Depends on `DeployUi` and `DeployDocs` to preserve write order. IAM: extends the GitHub OIDC role with `s3:PutObject` + `s3:DeleteObject` on `arn:aws:s3:::<bucket>/updates/*` and CloudFront invalidation on `/updates/*`.
- **`.github/workflows/ci.yml`** — new `publish-desktop-mac` job on `macos-14`. Fires only on `push` to `main` or `development`. Depends on the existing `desktop` matrix job (so the mac smoke test still gates this). Steps in §CI topology below. Added to `notify-pipeline-failure`'s `needs:` graph so a publish failure pages Slack.
- **`ui/src/app/Sidebar.jsx`** — restore the "Relaunch to update v…" pill. State derived from `window.channelDesktop?.onUpdateStatus`; rendered only when an `update:downloaded` event has fired with a version string. Hidden in the web SPA (no `window.channelDesktop`) and on Windows/Linux desktop (no event ever fires).
- **`ui/src/app/Sidebar.test.jsx`** — extends existing tests to cover the three pill states (no-electron / electron-no-update / electron-update-ready) using a mocked `window.channelDesktop`.
- **`ui/src/marketing/pages/Download.jsx`** — replace `href="#"` placeholders with `https://github.com/warlordofmars/channel/releases/latest/download/<file>` URLs for all five buttons (one macOS, one Windows, three Linux). Replace the "Channel updates itself automatically" paragraph with a smaller line acknowledging the asymmetry: *macOS auto-updates in the background; Windows and Linux: reinstall from this page when a new build lands.*
- **`ui/src/marketing/pages/Download.test.jsx`** — asserts the five link destinations resolve to GitHub Release URLs, not `href="#"`.
- **`docs/adr/NNNN-electron-release-pipeline.md`** — new ADR. Written after the publish CI job lands so it records decisions taken, not aspirations.

## Data flow

### Publish path (per push to `main` or `development`)

1. Existing `desktop` matrix job builds unsigned artefacts for all three platforms as today; runs the mac smoke test.
2. New `publish-desktop-mac` job (macos-14, `needs: [desktop]`, runs only on `push` to `main` or `development`):
   1. Resolve channel from `github.ref` → `latest` or `dev`.
   2. Resolve version from `package.json` → `0.2.0` for `main`, `0.2.0-dev.<github.run_number>` for `development`. Set via `npm version --no-git-tag-version` before build. No commit, no tag — version mutation is in-job only.
   3. Decode `APPLE_CERT_P12_BASE64` into a temporary keychain; import `APPLE_API_KEY_P8_BASE64` to `~/private_keys/AuthKey_<id>.p8`.
   4. Run `electron-builder --mac --publish=never` with `publish.url` overridden via env to the channel-specific URL.
   5. The `afterSign` hook submits the signed `.app` to Apple notary, polls until done, staples the ticket.
   6. `aws s3 sync desktop/release/ s3://<bucket>/updates/<channel>/ --exclude '*' --include '*.dmg' --include '*.zip' --include '*.blockmap' --include 'latest-mac.yml'` — manifest written last so a partial sync never advertises a binary that is not yet present.
3. Existing `release` job on `main` continues to create the GitHub Release with `--generate-notes`. The new publish job appends one final step (`gh release upload v$VERSION desktop/release/*.dmg desktop/release/*.exe desktop/release/*.deb desktop/release/*.rpm desktop/release/*.AppImage`) so the public-facing `Download.jsx` URLs resolve. This step runs only on the `main` push, not `development` — dev testers get their first install from the S3 `/updates/dev/` tree (see §Versioning), not from a GitHub release.

### Consume path (running desktop app, macOS)

1. App launches → `desktop/main/updater.js` calls `autoUpdater.checkForUpdates()` on startup, then every 4h.
2. `electron-updater` GETs `<feedUrl>/latest-mac.yml` → compares against `app.getVersion()`.
3. If newer: GETs the `.zip` (delta or full per `blockmap`), verifies the signature against the embedded public key in the running binary, stages the update.
4. Emits `update-downloaded` → updater module forwards IPC `update:downloaded` with `{ version }` to the renderer.
5. Renderer's Sidebar receives the event via `window.channelDesktop.onUpdateStatus` and shows the "Relaunch to update v…" pill.
6. User clicks the pill → `window.channelDesktop.relaunchToUpdate()` → main process calls `autoUpdater.quitAndInstall()`.

### Failure modes

- **Notary flake in CI** — `publish-desktop-mac` job fails. Upstream `desktop` matrix and `deploy-dev` / `deploy-prod` succeed (publish does not gate them). Slack notification fires via `notify-pipeline-failure`. The channel manifest stays on the previous version because the manifest is the last write — no half-published state.
- **Update download fails or signature mismatch** — `electron-updater` emits `error`. Updater module logs but does not surface to the renderer — no scary toast on transient network errors. The next 4h tick retries.
- **Offline at startup** — `checkForUpdates` swallows the network error; no event reaches the renderer; pill stays hidden.

## CI topology

New job in `.github/workflows/ci.yml`:

```
desktop (matrix: ubuntu/macos/windows)        ← unchanged; smoke tests
  └── publish-desktop-mac                      ← new
        - runs-on: macos-14
        - if: push && (ref == main || ref == development)
        - needs: [desktop]
        - steps:
            * decode-secrets (APPLE_CERT_P12_BASE64 → temp keychain,
                              APPLE_API_KEY_P8_BASE64 → ~/private_keys/)
            * version-bump   (npm version --no-git-tag-version <computed>)
            * build+sign     (electron-builder --mac --publish=never)
            * notarise       (afterSign hook → @electron/notarize)
            * publish        (aws s3 sync → updates/<channel>/)
            * invalidate     (CloudFront invalidation on /updates/<channel>/latest-mac.yml
                              and /updates/<channel>/Channel-mac.dmg)
            * gh-release     (only on main: gh release upload v$VERSION
                              desktop/release/*.{dmg,exe,deb,rpm,AppImage})
        - uses existing OIDC role (IAM policy extended to allow
          PutObject on s3://<bucket>/updates/* and CloudFront
          invalidation on /updates/*)
        - permissions: id-token: write, contents: write
                       (contents: write only needed for the main
                       branch path that runs `gh release upload`)
```

The job sits beside `deploy-dev` / `deploy-prod`, not gating them — a notary outage does not block backend deploys. `notify-pipeline-failure` includes the full needs-graph today; the new job is appended to its `needs:`.

## Versioning + manifest layout

**Version strings:**

- `main` builds → `package.json` version verbatim (e.g. `0.2.0`).
- `development` builds → `<base>-dev.<github.run_number>` (e.g. `0.2.0-dev.142`). Pre-release suffix is semver-legal, monotonic, no SHA lookup needed in the manifest.

**S3 layout** (prod and dev buckets each hold their own `updates/` tree):

```
updates/
  latest/
    latest-mac.yml                  # the manifest electron-updater fetches
    Channel-mac.dmg                 # stable un-versioned name (first-install)
    Channel-0.2.0-mac.zip           # auto-update binary (delta-friendly)
    Channel-0.2.0-mac.zip.blockmap  # block-level diff metadata
  dev/
    latest-mac.yml
    Channel-mac.dmg
    Channel-0.2.0-dev.142-mac.zip
    Channel-0.2.0-dev.142-mac.zip.blockmap
```

**Artefact naming.** `electron-builder.yml` sets `mac.artifactName` per target so the `.dmg` drops the version (`Channel-${os}.${ext}` → `Channel-mac.dmg`) while the auto-update `.zip` keeps it (`Channel-${version}-${os}.${ext}`). The dmg is overwritten on every publish; the zip is content-addressed.

**First-install URLs.**
- `latest` channel: `https://github.com/warlordofmars/channel/releases/latest/download/Channel-mac.dmg` (public — Download.jsx points here) and `https://channel.warlordofmars.net/updates/latest/Channel-mac.dmg` (mirror).
- `dev` channel: `https://channel-dev.warlordofmars.net/updates/dev/Channel-mac.dmg` (private link DM'd to testers).

**Retention.** The `DeployUpdates` construct attaches a lifecycle policy to the bucket scoped to the `updates/*/` prefix: it prunes objects matching `Channel-*-mac.zip*` (versioned auto-update bundles + blockmaps) older than 30 days. The stable-named `Channel-mac.dmg` and the `latest-mac.yml` manifest never match that pattern, so neither is pruned. Effective retention is roughly the last ~5 versions on `latest` and ~20 on `dev` — time-based not count-based, forgiving of release-cadence changes.

**CloudFront behaviors for `/updates/*`:**

- `*.yml` → TTL 60s, `Cache-Control: public, max-age=60`. Short enough that a new release reaches clients within minutes; long enough that the 4h poll does not pound the origin.
- `*.zip`, `*.blockmap` → TTL 1y, `Cache-Control: public, max-age=31536000, immutable`. Filenames carry the version so cache keys never collide.

## UI restorations

### `ui/src/app/Sidebar.jsx`

Three pill states:

- No `window.channelDesktop` (web SPA) → pill never renders.
- `window.channelDesktop` present but no `update:downloaded` event yet (desktop, current version) → pill never renders.
- `update:downloaded` fired with `{ version }` → pill renders with text "Relaunch to update v0.2.0" and click handler calling `window.channelDesktop.relaunchToUpdate()`.

The pill sits where the `// dropped` tombstone comment currently lives (between sign-in chrome and Recents). Styled with the same shape as existing sidebar chips: `var(--accent)` background, `var(--accent-ink)` foreground, 12px font, 8px×12px padding. One-cycle pulse animation on first appear, then static.

### `ui/src/marketing/pages/Download.jsx`

- macOS button → `https://github.com/warlordofmars/channel/releases/latest/download/Channel-mac.dmg`
- Windows button → `https://github.com/warlordofmars/channel/releases/latest/download/Channel-Setup.exe`
- Linux: three sub-buttons (`.deb`, `.rpm`, AppImage) each pointing at the corresponding `releases/latest/download/<file>`.
- Replace the "Channel updates itself automatically" paragraph with a smaller line: *macOS auto-updates in the background. Windows and Linux: reinstall from this page when a new build lands.*

Using `releases/latest/download/` (not a version-pinned URL) means the Download page auto-resolves to the newest tag without code changes per release.

## Testing strategy

- `desktop/test/updater.test.js` — unit; mocks `electron-updater`; covers platform gate, IPC event surface, channel→URL mapping. 100% coverage gate.
- `ui/src/app/Sidebar.test.jsx` — covers the three pill states using a mocked `window.channelDesktop`.
- `ui/src/marketing/pages/Download.test.jsx` — asserts the five link destinations.
- `infra/tests/` — CDK snapshot test for the new `DeployUpdates` construct and `/updates/*` behavior, matching the existing snapshot pattern.

**Manual local verification (per project convention — local-feature-test before PR):**

- **Bootstrap sub-issue:** run `desktop/scripts/sign_local.sh` end-to-end against a built `.app`; confirm `spctl -a -vv Channel.app` reports "accepted" with "source=Notarized Developer ID".
- **Updater module:** `uv run inv desktop-dev`; open devtools; confirm `window.channelDesktop` exposes `onUpdateStatus` and `relaunchToUpdate`; confirm updater does not initialise on Windows/Linux builds (no IPC events fire).
- **CDK construct:** `uv run inv synth`; inspect the generated template for the `/updates/*` behavior and IAM policy entries.
- **Publish CI job:** push a no-op commit to a throwaway branch off `development`, watch the publish job; confirm `s3://<dev-bucket>/updates/dev/latest-mac.yml` updates; install the previous dev build, force a `checkForUpdates()` call via devtools, confirm the new version downloads.
- **UI restorations:** `uv run inv dev`; mock `window.channelDesktop` in devtools (`window.channelDesktop = { onUpdateStatus: cb => cb({ event: 'update:downloaded', version: '0.2.1' }), relaunchToUpdate: () => console.log('relaunch') }`); confirm pill renders and click handler fires. Visit `/download`; confirm all five download buttons have real URLs.

## ADR scope

`docs/adr/NNNN-electron-release-pipeline.md` records five decisions, with rationale tied back to this spec:

1. macOS-only signing this round; Windows/Linux deferred — driven by audience scope (author + handful of testers).
2. Two channels (`latest` + `dev`) mapped 1:1 to the existing CDK env split.
3. GitHub Actions secrets over SSM Parameter Store / Secrets Manager — secrets are build-time only, never seen by Lambda.
4. Same bucket + new `/updates/` prefix + new CloudFront behavior — vs separate distribution. Trade-off: shared cache config and access logs, but smallest infra delta and lowest blast radius via path-scoped behavior.
5. GitHub Releases as the first-install download surface; S3 as the auto-update manifest surface. Avoids dual-source-of-truth for the `.dmg`.

The ADR is written *after* the publish CI job has landed and a real release has been cut, so it documents lived behaviour rather than projected intent.

## Sub-issue breakdown

The tracking issue (#33) becomes a checklist of six sub-issues. Each gets `Part of #33` in its body, an explicit `## Files to touch` section, and per-issue verification steps. Sub-issues #1–#5 fit `agent-safe`; #4 does not because it touches `.github/workflows/`.

| # | Sub-issue | Size | Blocked by | Files to touch (summary) | Local verify |
|---|---|---|---|---|---|
| 1 | Apple Developer ID bootstrap | s | — | `docs/dev/apple-signing-bootstrap.md`, `desktop/scripts/sign_local.sh`, `desktop/resources/entitlements.mac.plist` | `sign_local.sh` end-to-end; `spctl -a -vv Channel.app` passes |
| 2 | `updater.js` module + preload bridge | s | 1 | `desktop/main/updater.js`, `desktop/preload/index.js`, `desktop/test/updater.test.js` | `inv desktop-dev`; devtools verify IPC surface and platform gate |
| 3 | `DeployUpdates` CDK construct | s | — (parallel with 2) | `infra/stacks/channel_stack.py`, infra snapshot | `inv synth`; inspect template |
| 4 | `publish-desktop-mac` CI job + notarize hook + electron-builder.yml flips | m | 1, 3 | `.github/workflows/ci.yml`, `desktop/scripts/notarize.js`, `desktop/electron-builder.yml` | Throwaway branch off `development`; watch CI; confirm S3 manifest update |
| 5 | UI restorations (Sidebar pill + Download.jsx URLs) | s | 2 | `ui/src/app/Sidebar.jsx`, `ui/src/app/Sidebar.test.jsx`, `ui/src/marketing/pages/Download.jsx`, `ui/src/marketing/pages/Download.test.jsx` | `inv dev`; mock `window.channelDesktop`; verify all behaviour |
| 6 | ADR | xs | 4 merged | `docs/adr/NNNN-electron-release-pipeline.md` | n/a (docs-only) |

#1 is the prerequisite — the author cannot validate any other sub-issue end-to-end until the cert exists and `sign_local.sh` passes. #2 and #3 can run in parallel after #1. #4 depends on both #2 and #3. #5 depends on #2 (it consumes the IPC bridge). #6 lands last so it documents actual decisions.

## Out of scope

Carried forward from sub-project A's deferral list and reaffirmed here:

- **Windows code signing** — EV cert vs Azure Trusted Signing. Becomes its own sub-issue once a Windows-signing decision is made.
- **Linux package signing** — `.deb` via `dpkg-sig`, `.rpm` via `rpm --addsign`, AppImage zsync metadata for delta updates.
- **Auto-update on Windows / Linux AppImage** — `electron-updater` supports these unsigned in principle but the asymmetry with macOS would be a footgun for testers. Re-evaluate when Windows signing lands.
- **Three-channel topology** (`stable` / `beta` / `dev`) — not justified by the current audience. Re-evaluate when the audience expands.
- **Keychain / OS credential store for the JWT in the renderer** — out of scope per sub-project A's deferral; unchanged here.
- **Per-customer / per-tenant update channels** — billing is deferred per CLAUDE.md product decision; tier-keyed channels do not exist as a concept yet.

## Risks

- **Apple notary latency** — submissions are typically 1–15 min but Apple has had multi-hour outages. Mitigation: publish job is downstream of `desktop` and does not gate `deploy-dev` / `deploy-prod`. A failed publish leaves the previous manifest in place; testers stay on the previous version until the next push.
- **GH Actions secret rotation** — Apple's Developer ID Application cert expires after 5 years; the App Store Connect API key has no enforced rotation but should be rotated annually. Renewal procedure documented in `docs/dev/apple-signing-bootstrap.md`.
- **CloudFront cache poisoning** — a corrupted manifest published with a 60s TTL could break auto-update for up to 60s of clients. Mitigation: publish job ends with a CloudFront invalidation on `/updates/<channel>/latest-mac.yml` so the new manifest is live immediately; the binary cache is content-addressed via versioned filename and not invalidated.
- **`electron-updater` API changes** — upstream library has historically broken API across majors. Mitigation: pin to a minor version in `desktop/package.json` and verify via `desktop/test/updater.test.js` on every dependency bump.
