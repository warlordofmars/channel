# Electron sub-project B — signing, distribution, auto-update — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the unsigned-build path from sub-project A into a signed, notarised, auto-updating release pipeline for macOS, with seams for Windows/Linux signing to drop in later. Six sub-issues, each its own PR.

**Architecture:** macOS-only auto-update via `electron-updater` polling a generic feed at `https://channel{,-dev}.warlordofmars.net/updates/{channel}/latest-mac.yml`. Two rolling channels (`latest` from `main`, `dev` from `development`) mapped 1:1 to existing CDK envs. Same bucket + new `/updates/*` CloudFront behavior. Apple Developer ID secrets in GitHub Actions repo secrets.

**Tech Stack:** Electron 42 + electron-builder 26 + electron-updater (new) + @electron/notarize (new), AWS CDK Python, vitest, GitHub Actions on macos-14 runners.

**Spec:** `docs/superpowers/specs/2026-05-30-electron-signing-distribution-design.md`

---

## File structure overview

**To create:**

| Path | Responsibility |
|---|---|
| `docs/dev/apple-signing-bootstrap.md` | Standalone runbook for Apple Developer enrolment + cert generation + secret setup. |
| `desktop/scripts/sign_local.sh` | Locally sign + notarise a built `.app` against Keychain cert + App Store Connect API key. Used to validate credentials before trusting CI. |
| `desktop/main/updater.js` | `electron-updater` wrapper. Platform-gated to darwin. Maps channel → feed URL, emits IPC update status, exposes `relaunchToUpdate`. |
| `desktop/scripts/notarize.js` | `afterSign` hook for electron-builder. Submits signed `.app` to Apple notary, polls, staples. |
| `desktop/test/updater.test.js` | Unit tests covering platform gate, channel→URL mapping, IPC event surface, error path. |
| `docs/adr/0009-electron-release-pipeline.md` | Records the five sub-B decisions (macOS-only-now, channels, GH secrets, same-distribution, GH-Releases-for-dmg). |

**To modify:**

| Path | Change |
|---|---|
| `desktop/resources/entitlements.mac.plist` | Replace placeholder with hardened-runtime entitlements (jit, allow-unsigned-executable-memory, network.client). |
| `desktop/preload/index.js` | Extend contextBridge with `onUpdateStatus(cb)` and `relaunchToUpdate()`. |
| `desktop/test/preload.test.js` | Cover the new bridge methods. |
| `desktop/electron-builder.yml` | Flip `hardenedRuntime: true`, `gatekeeperAssess: true`. Add `mac.entitlements` path, `afterSign` hook, `publish` block. Override `mac.artifactName` per target. |
| `desktop/package.json` | Add `electron-updater` + `@electron/notarize` deps. |
| `desktop/main/index.js` | Wire `updater.init({ channel, webContents })` after window creation. Add channel detection helper. |
| `desktop/main/ipc.js` | Add `desktop:relaunch-to-update` route. |
| `desktop/test/ipc.test.js` | Cover the new route. |
| `infra/stacks/channel_stack.py` | Add `/updates/*` CloudFront behavior, bucket lifecycle rule for `updates/*/Channel-*-mac.zip*`, OIDC role IAM policy delta, `CfnOutput` for feed URL. |
| `.github/workflows/ci.yml` | Add `publish-desktop-mac` job (macos-14, on push to `main` or `development`, `needs: [desktop]`). |
| `ui/src/app/Sidebar.jsx` | Replace tombstone comment with the "Relaunch to update v…" pill (3 states). |
| `ui/src/app/Sidebar.test.jsx` | Cover the 3 pill states (no-electron / electron-no-update / electron-update-ready). |
| `ui/src/marketing/pages/Download.jsx` | Swap `releases/download/dev/*` URLs (from PR #30) for `releases/latest/download/*`; remove the "Dev preview" banner; collapse Apple Silicon/Intel dual-button to single universal `.dmg`; tweak the auto-update paragraph. |
| `ui/src/marketing/pages/Download.test.jsx` | Assert the 5 link destinations. |
| `docs/adr/README.md` (or index) | Link the new ADR. |

---

## Per-PR convention

Each sub-issue lands as its own PR off `origin/development`, named `feat/electron-subB-<short>-#<issue>`. Each sub-issue's section ends with a **Local verify** task (manual, never skipped) and an **Open PR** task. All PRs include `Closes #<sub-issue-number>` and `Part of #33`. PRs #1–#5 are `agent-safe`; PR #4 is not (touches `.github/workflows/`).

---

# Sub-issue #1 — Apple Developer ID bootstrap

**Branch:** `feat/electron-subB-bootstrap-#<n>`

**Goal:** Stand up the Apple cert + GH Actions secrets and prove the local sign+notarise loop works *before* any CI signing code lands.

### Task 1.1 — Runbook doc

**Files:**
- Create: `docs/dev/apple-signing-bootstrap.md`

- [ ] **Step 1: Create the runbook**

```markdown
<!-- Copyright (c) 2026 John Carter. All rights reserved. -->
# Apple signing bootstrap

One-time setup (and renewal procedure) for code-signing the Channel
desktop app on macOS. Cert lasts 5 years; the App Store Connect API
key should be rotated annually.

## 1. Enrol in the Apple Developer Program

- Visit https://developer.apple.com/programs/enroll/
- Sign in with the Apple ID you want to associate with Channel.
- Choose **Individual / Sole Proprietor**, not Company. The legal name
  on the account is the name that appears on the signed builds'
  certificate ("Developer ID Application: <Your Name> (TEAMID)").
- $99 USD/year. Identity verification can take 24–48h.

## 2. Generate the Developer ID Application certificate

- Open Xcode → Settings → Accounts.
- Add your Apple ID if not present.
- Select the team → **Manage Certificates…** → `+` → **Developer ID
  Application**.
- Xcode generates the cert in the portal and stores the private key
  in your login Keychain.

## 3. Export the cert + key as a .p12

- Open Keychain Access → login keychain → My Certificates.
- Right-click "Developer ID Application: …" → **Export…**
- Save as `~/secrets/channel-developer-id.p12` and set a strong
  password (this is `APPLE_CERT_PASSWORD`).

## 4. Create an App Store Connect API key

- https://appstoreconnect.apple.com/access/integrations/api
- `+` → **Generate API Key**.
- Name: `channel-notarytool`. Access: **Developer** (sufficient for
  notarisation, no broader scope).
- Download the `.p8` once — it is never downloadable again.
- Note the **Key ID** (10-char) and **Issuer ID** (UUID).

## 5. Set GitHub Actions repo secrets

```bash
base64 -i ~/secrets/channel-developer-id.p12 | pbcopy
gh secret set APPLE_CERT_P12_BASE64        # paste, then Enter, Ctrl-D
gh secret set APPLE_CERT_PASSWORD          # the password from step 3
base64 -i ~/secrets/AuthKey_XXXXXXXXXX.p8 | pbcopy
gh secret set APPLE_API_KEY_P8_BASE64
gh secret set APPLE_API_KEY_ID             # 10-char string
gh secret set APPLE_API_ISSUER_ID          # UUID
```

## 6. Verify locally

```bash
cd desktop && npm run build:current
./scripts/sign_local.sh release/mac-arm64/Channel.app
```

The script signs against the Keychain cert, submits to notarytool,
staples the ticket, and runs `spctl -a -vv` to confirm Gatekeeper
accepts the result. Expected final line: `source=Notarized Developer ID`.

## Renewal

- **Cert** — Apple emails 6 weeks before expiry. Re-export the new
  `.p12`, `gh secret set APPLE_CERT_P12_BASE64` with the new value.
- **API key** — `+` a new one, set the secrets, then revoke the old
  key. Do not revoke before the new key is in CI.
```

- [ ] **Step 2: Commit**

```bash
git add docs/dev/apple-signing-bootstrap.md
git commit -m "docs(dev): apple signing bootstrap runbook"
```

### Task 1.2 — sign_local.sh

**Files:**
- Create: `desktop/scripts/sign_local.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Copyright (c) 2026 John Carter. All rights reserved.
#
# Locally sign + notarise a built Channel.app against the Developer ID
# cert in the user's Keychain and the App Store Connect API key in
# ~/secrets/. Idempotent; safe to re-run.
#
# Usage: ./desktop/scripts/sign_local.sh <path-to-Channel.app>

set -euo pipefail

APP_PATH="${1:?path to Channel.app required}"
ENTITLEMENTS="$(dirname "$0")/../resources/entitlements.mac.plist"
IDENTITY="${APPLE_SIGNING_IDENTITY:-Developer ID Application}"
API_KEY_PATH="${APPLE_API_KEY_PATH:-$HOME/secrets/AuthKey.p8}"
API_KEY_ID="${APPLE_API_KEY_ID:?APPLE_API_KEY_ID env var required}"
API_ISSUER_ID="${APPLE_API_ISSUER_ID:?APPLE_API_ISSUER_ID env var required}"

if [[ ! -d "$APP_PATH" ]]; then
  echo "sign_local: $APP_PATH does not exist or is not a .app bundle" >&2
  exit 1
fi

echo "==> Signing $APP_PATH against '$IDENTITY'"
codesign --force --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" \
  --sign "$IDENTITY" \
  --deep "$APP_PATH"

echo "==> Zipping for notarytool submission"
ZIP_PATH="${APP_PATH%.app}.zip"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

echo "==> Submitting to Apple notary"
xcrun notarytool submit "$ZIP_PATH" \
  --key "$API_KEY_PATH" \
  --key-id "$API_KEY_ID" \
  --issuer "$API_ISSUER_ID" \
  --wait

echo "==> Stapling ticket"
xcrun stapler staple "$APP_PATH"

echo "==> Verifying with spctl"
spctl -a -vv "$APP_PATH"

echo "==> Done. Cleaning up zip."
rm -f "$ZIP_PATH"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x desktop/scripts/sign_local.sh
```

- [ ] **Step 3: Smoke-validate the script structure with shellcheck**

```bash
brew list shellcheck >/dev/null 2>&1 || brew install shellcheck
shellcheck desktop/scripts/sign_local.sh
```

Expected: no errors. (Warnings about the `:?` parameter expansion are normal and may be silenced with a `# shellcheck disable=SC2059` if shellcheck complains; this script should pass clean.)

- [ ] **Step 4: Commit**

```bash
git add desktop/scripts/sign_local.sh
git commit -m "feat(desktop): sign_local.sh — local sign + notarise + verify"
```

### Task 1.3 — entitlements.mac.plist

**Files:**
- Modify: `desktop/resources/entitlements.mac.plist`

- [ ] **Step 1: Replace the placeholder with the real plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- Copyright (c) 2026 John Carter. All rights reserved. -->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <!-- Electron + V8 need a JIT page allocator -->
  <key>com.apple.security.cs.allow-jit</key>
  <true/>
  <!-- V8 emits executable bytecode that is not signed by Apple -->
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
  <true/>
  <!-- Allow outbound TCP/HTTP to the FastAPI backend -->
  <key>com.apple.security.network.client</key>
  <true/>
</dict>
</plist>
```

- [ ] **Step 2: Validate plist syntax**

```bash
plutil -lint desktop/resources/entitlements.mac.plist
```

Expected: `desktop/resources/entitlements.mac.plist: OK`

- [ ] **Step 3: Commit**

```bash
git add desktop/resources/entitlements.mac.plist
git commit -m "feat(desktop): populate mac entitlements for hardened runtime"
```

### Task 1.4 — Apple Developer enrolment (MANUAL, USER ACTION)

This task is **performed by the human, not the agent**. It cannot be automated; Apple's enrolment portal requires manual identity verification.

- [ ] **Step 1: Follow `docs/dev/apple-signing-bootstrap.md` §1–§5**

The agent's role here is to *prompt the user* and wait. Expected duration: 24–48h for Apple to approve the enrolment.

- [ ] **Step 2: Confirm the five GH Actions secrets exist**

```bash
gh secret list | grep -E '^APPLE_(CERT|API)_'
```

Expected output (exact order may differ):

```
APPLE_API_ISSUER_ID    Updated YYYY-MM-DD
APPLE_API_KEY_ID       Updated YYYY-MM-DD
APPLE_API_KEY_P8_BASE64 Updated YYYY-MM-DD
APPLE_CERT_P12_BASE64  Updated YYYY-MM-DD
APPLE_CERT_PASSWORD    Updated YYYY-MM-DD
```

If any are missing, return to `docs/dev/apple-signing-bootstrap.md` §5.

- [ ] **Step 3: Confirm the local secret files exist**

```bash
ls -l ~/secrets/channel-developer-id.p12 ~/secrets/AuthKey_*.p8
```

Both must exist for Task 1.5 to work.

### Task 1.5 — Local verify (sign_local.sh end-to-end)

- [ ] **Step 1: Build a local unsigned app**

```bash
cd desktop && npm run build:current && cd ..
```

Expected: `desktop/release/mac-arm64/Channel.app/` exists.

- [ ] **Step 2: Run sign_local.sh**

```bash
APPLE_API_KEY_PATH=$HOME/secrets/AuthKey_<your-key-id>.p8 \
APPLE_API_KEY_ID=<your-key-id> \
APPLE_API_ISSUER_ID=<your-issuer-id> \
./desktop/scripts/sign_local.sh desktop/release/mac-arm64/Channel.app
```

Expected final line:

```
desktop/release/mac-arm64/Channel.app: accepted
source=Notarized Developer ID
```

If `spctl` reports `rejected`: re-check that step 2 of the runbook generated the right type of cert (Developer ID Application, not Mac Development).

- [ ] **Step 3: Open + use the app**

Double-click `desktop/release/mac-arm64/Channel.app`. Gatekeeper must not show a warning dialog. Sign in, exchange a message, quit. Confirm normal operation.

### Task 1.6 — Open PR for sub-issue #1

- [ ] **Step 1: Push the bootstrap branch**

```bash
git push -u origin feat/electron-subB-bootstrap-#<n>:feat/electron-subB-bootstrap-#<n>
```

- [ ] **Step 2: Create the PR**

```bash
gh pr create --base development --title "feat(desktop): apple signing bootstrap (#<n>)" --body "$(cat <<'EOF'
## Summary
- Adds `docs/dev/apple-signing-bootstrap.md` — runbook for Apple enrolment + cert + secrets + renewal.
- Adds `desktop/scripts/sign_local.sh` — locally sign + notarise + verify a built `.app`.
- Populates `desktop/resources/entitlements.mac.plist` with hardened-runtime entries.

Closes #<n>
Part of #33

## Test plan
- [x] `plutil -lint desktop/resources/entitlements.mac.plist`
- [x] `shellcheck desktop/scripts/sign_local.sh`
- [x] Apple Developer enrolment complete; 5 GH Actions secrets set
- [x] `sign_local.sh` end-to-end: `spctl` reports "source=Notarized Developer ID"
- [x] Signed `.app` opens without Gatekeeper warning

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
gh pr merge --auto --squash --delete-branch
```

---

# Sub-issue #2 — Updater module + preload bridge

**Branch:** `feat/electron-subB-updater-module-#<n>`. Depends on #1 merged.

**Goal:** Wire `electron-updater` into `desktop/main/` with a platform gate, IPC push events, and a renderer-side bridge. Module is dormant in CI until the publish job lands (sub-issue #4), but the tests exercise the full surface in isolation.

### Task 2.1 — Add electron-updater dependency

**Files:**
- Modify: `desktop/package.json`, `desktop/package-lock.json`

- [ ] **Step 1: Install**

```bash
cd desktop && npm install --save electron-updater@^6.6.0 && cd ..
```

- [ ] **Step 2: Confirm it's in devDependencies/dependencies**

```bash
grep -A1 'electron-updater' desktop/package.json
```

Expected: a line like `"electron-updater": "^6.6.0",` in `dependencies` (not `devDependencies` — the renderer's main bundle imports it at runtime).

- [ ] **Step 3: Commit**

```bash
git add desktop/package.json desktop/package-lock.json
git commit -m "feat(desktop): add electron-updater dependency"
```

### Task 2.2 — Updater module skeleton + platform gate (TDD)

**Files:**
- Create: `desktop/main/updater.js`
- Create: `desktop/test/updater.test.js`

- [ ] **Step 1: Write the failing test for the platform gate**

```js
// desktop/test/updater.test.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mockElectron } from "./_helpers.js";

vi.mock("electron", () => mockElectron());
vi.mock("electron-updater", () => ({
  autoUpdater: {
    setFeedURL: vi.fn(),
    on: vi.fn(),
    checkForUpdates: vi.fn(),
    quitAndInstall: vi.fn(),
  },
}));

const { init } = await import("../main/updater.js");

let originalPlatform;
beforeEach(() => {
  vi.clearAllMocks();
  originalPlatform = process.platform;
});
afterEach(() => {
  Object.defineProperty(process, "platform", { value: originalPlatform });
});

function setPlatform(p) {
  Object.defineProperty(process, "platform", { value: p });
}

describe("updater.init platform gate", () => {
  it("does nothing on win32", async () => {
    setPlatform("win32");
    const { autoUpdater } = await import("electron-updater");
    init({ channel: "latest", webContents: { send: vi.fn() } });
    expect(autoUpdater.setFeedURL).not.toHaveBeenCalled();
    expect(autoUpdater.checkForUpdates).not.toHaveBeenCalled();
  });

  it("does nothing on linux", async () => {
    setPlatform("linux");
    const { autoUpdater } = await import("electron-updater");
    init({ channel: "latest", webContents: { send: vi.fn() } });
    expect(autoUpdater.setFeedURL).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the test and confirm it fails**

```bash
cd desktop && npx vitest run test/updater.test.js
```

Expected: `Cannot find module '../main/updater.js'`.

- [ ] **Step 3: Write the minimal updater.js**

```js
// desktop/main/updater.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { autoUpdater } from "electron-updater";

export function init({ channel, webContents }) {
  if (process.platform !== "darwin") return;
  // Channel + IPC wiring added in subsequent tasks.
  autoUpdater.setFeedURL({ provider: "generic", url: `https://placeholder/${channel}` });
  autoUpdater.checkForUpdates();
}
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
cd desktop && npx vitest run test/updater.test.js
```

Expected: 2 passing.

- [ ] **Step 5: Commit**

```bash
git add desktop/main/updater.js desktop/test/updater.test.js
git commit -m "feat(desktop): updater module — platform gate"
```

### Task 2.3 — Channel → feed URL mapping (TDD)

**Files:**
- Modify: `desktop/main/updater.js`, `desktop/test/updater.test.js`

- [ ] **Step 1: Add failing tests for the URL mapping**

Append to `desktop/test/updater.test.js`:

```js
describe("updater.init feed URL mapping", () => {
  beforeEach(() => setPlatform("darwin"));

  it("uses the prod URL for the latest channel", async () => {
    const { autoUpdater } = await import("electron-updater");
    init({ channel: "latest", webContents: { send: vi.fn() } });
    expect(autoUpdater.setFeedURL).toHaveBeenCalledWith({
      provider: "generic",
      url: "https://channel.warlordofmars.net/updates/latest",
    });
  });

  it("uses the dev URL for the dev channel", async () => {
    const { autoUpdater } = await import("electron-updater");
    init({ channel: "dev", webContents: { send: vi.fn() } });
    expect(autoUpdater.setFeedURL).toHaveBeenCalledWith({
      provider: "generic",
      url: "https://channel-dev.warlordofmars.net/updates/dev",
    });
  });

  it("throws on unknown channels", () => {
    expect(() =>
      init({ channel: "beta", webContents: { send: vi.fn() } }),
    ).toThrow(/unknown channel: beta/);
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd desktop && npx vitest run test/updater.test.js
```

Expected: the URL-mapping tests fail (current implementation uses `https://placeholder/...`).

- [ ] **Step 3: Replace the placeholder URL with a real mapping**

```js
// desktop/main/updater.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { autoUpdater } from "electron-updater";

const FEED_URL_BY_CHANNEL = {
  latest: "https://channel.warlordofmars.net/updates/latest",
  dev: "https://channel-dev.warlordofmars.net/updates/dev",
};

export function init({ channel, webContents }) {
  if (process.platform !== "darwin") return;
  const url = FEED_URL_BY_CHANNEL[channel];
  if (!url) throw new Error(`unknown channel: ${channel}`);
  autoUpdater.setFeedURL({ provider: "generic", url });
  autoUpdater.checkForUpdates();
}
```

- [ ] **Step 4: Run the tests**

```bash
cd desktop && npx vitest run test/updater.test.js
```

Expected: 5 passing.

- [ ] **Step 5: Commit**

```bash
git add desktop/main/updater.js desktop/test/updater.test.js
git commit -m "feat(desktop): updater — channel→feed URL mapping"
```

### Task 2.4 — IPC push events (TDD)

**Files:**
- Modify: `desktop/main/updater.js`, `desktop/test/updater.test.js`

- [ ] **Step 1: Add failing tests for the IPC surface**

Append to `desktop/test/updater.test.js`:

```js
describe("updater.init IPC push events", () => {
  beforeEach(() => setPlatform("darwin"));

  it("subscribes to autoUpdater events with handlers that forward to webContents", async () => {
    const { autoUpdater } = await import("electron-updater");
    const send = vi.fn();
    init({ channel: "latest", webContents: { send } });

    const eventNames = autoUpdater.on.mock.calls.map(([name]) => name);
    expect(eventNames).toEqual(
      expect.arrayContaining(["checking-for-update", "update-available", "update-downloaded", "error"]),
    );

    // Fire each registered handler and assert the IPC payload shape.
    const handlerByEvent = Object.fromEntries(autoUpdater.on.mock.calls);
    handlerByEvent["checking-for-update"]();
    expect(send).toHaveBeenLastCalledWith("desktop:update-status", { state: "checking" });

    handlerByEvent["update-available"]({ version: "0.2.1" });
    expect(send).toHaveBeenLastCalledWith("desktop:update-status", { state: "available", version: "0.2.1" });

    handlerByEvent["update-downloaded"]({ version: "0.2.1" });
    expect(send).toHaveBeenLastCalledWith("desktop:update-status", { state: "downloaded", version: "0.2.1" });

    handlerByEvent["error"](new Error("notary down"));
    expect(send).toHaveBeenLastCalledWith("desktop:update-status", { state: "error", message: "notary down" });
  });

  it("exposes relaunchToUpdate which calls autoUpdater.quitAndInstall", async () => {
    const { autoUpdater } = await import("electron-updater");
    const { relaunchToUpdate } = await import("../main/updater.js");
    relaunchToUpdate();
    expect(autoUpdater.quitAndInstall).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd desktop && npx vitest run test/updater.test.js
```

Expected: the IPC-event tests fail (no `.on(...)` calls yet, no `relaunchToUpdate` export).

- [ ] **Step 3: Extend updater.js**

```js
// desktop/main/updater.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { autoUpdater } from "electron-updater";

const FEED_URL_BY_CHANNEL = {
  latest: "https://channel.warlordofmars.net/updates/latest",
  dev: "https://channel-dev.warlordofmars.net/updates/dev",
};

const FOUR_HOURS_MS = 4 * 60 * 60 * 1000;

export function init({ channel, webContents }) {
  if (process.platform !== "darwin") return;
  const url = FEED_URL_BY_CHANNEL[channel];
  if (!url) throw new Error(`unknown channel: ${channel}`);

  autoUpdater.setFeedURL({ provider: "generic", url });

  const send = (state, extra) => webContents.send("desktop:update-status", { state, ...extra });

  autoUpdater.on("checking-for-update", () => send("checking"));
  autoUpdater.on("update-available", (info) => send("available", { version: info.version }));
  autoUpdater.on("update-downloaded", (info) => send("downloaded", { version: info.version }));
  autoUpdater.on("error", (err) => send("error", { message: err.message }));

  autoUpdater.checkForUpdates();
  setInterval(() => autoUpdater.checkForUpdates(), FOUR_HOURS_MS);
}

export function relaunchToUpdate() {
  autoUpdater.quitAndInstall();
}
```

- [ ] **Step 4: Add a test for the 4-hour interval**

Append:

```js
describe("updater.init periodic check", () => {
  beforeEach(() => {
    setPlatform("darwin");
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it("schedules a checkForUpdates every 4 hours after initial call", async () => {
    const { autoUpdater } = await import("electron-updater");
    init({ channel: "latest", webContents: { send: vi.fn() } });
    expect(autoUpdater.checkForUpdates).toHaveBeenCalledTimes(1); // initial
    vi.advanceTimersByTime(4 * 60 * 60 * 1000);
    expect(autoUpdater.checkForUpdates).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(4 * 60 * 60 * 1000);
    expect(autoUpdater.checkForUpdates).toHaveBeenCalledTimes(3);
  });
});
```

- [ ] **Step 5: Run all updater tests with coverage**

```bash
cd desktop && npx vitest run test/updater.test.js --coverage
```

Expected: all 8 tests pass; coverage for `main/updater.js` reports 100% lines/branches/functions.

- [ ] **Step 6: Commit**

```bash
git add desktop/main/updater.js desktop/test/updater.test.js
git commit -m "feat(desktop): updater — IPC push events + 4h poll"
```

### Task 2.5 — Preload bridge methods (TDD)

**Files:**
- Modify: `desktop/preload/index.js`, `desktop/test/preload.test.js`

- [ ] **Step 1: Add failing tests for the new bridge methods**

Append to `desktop/test/preload.test.js`:

```js
describe("preload — update bridge", () => {
  it("exposes onUpdateStatus, registering an ipcRenderer.on listener", async () => {
    const { contextBridge, ipcRenderer } = await import("electron");
    // The preload module ran on import; capture the exposed surface.
    const exposed = contextBridge.exposeInMainWorld.mock.calls.find(
      ([name]) => name === "channelDesktop",
    )[1];

    const cb = vi.fn();
    exposed.onUpdateStatus(cb);
    expect(ipcRenderer.on).toHaveBeenCalledWith("desktop:update-status", expect.any(Function));

    // Invoke the registered listener and assert the callback receives the payload.
    const [, registeredListener] = ipcRenderer.on.mock.calls.find(
      ([ch]) => ch === "desktop:update-status",
    );
    registeredListener({}, { state: "downloaded", version: "0.2.1" });
    expect(cb).toHaveBeenCalledWith({ state: "downloaded", version: "0.2.1" });
  });

  it("exposes relaunchToUpdate, invoking the ipcRenderer", async () => {
    const { contextBridge, ipcRenderer } = await import("electron");
    const exposed = contextBridge.exposeInMainWorld.mock.calls.find(
      ([name]) => name === "channelDesktop",
    )[1];
    await exposed.relaunchToUpdate();
    expect(ipcRenderer.invoke).toHaveBeenCalledWith("desktop:relaunch-to-update");
  });
});
```

The existing `_helpers.js` mock for `ipcRenderer` only declares `invoke`. Update it to also include `on`:

```js
// desktop/test/_helpers.js — modify the ipcRenderer line
ipcRenderer: { invoke: vi.fn(), on: vi.fn() },
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd desktop && npx vitest run test/preload.test.js
```

Expected: the two new tests fail (`onUpdateStatus is not a function`, `ipcRenderer.invoke not called for desktop:relaunch-to-update`).

- [ ] **Step 3: Extend preload/index.js**

```js
// desktop/preload/index.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("channelDesktop", {
  isDesktop: true,
  login: () => ipcRenderer.invoke("desktop:login"),
  logout: () => ipcRenderer.invoke("desktop:logout"),
  getVersion: () => ipcRenderer.invoke("desktop:version"),
  onUpdateStatus: (cb) => {
    ipcRenderer.on("desktop:update-status", (_event, payload) => cb(payload));
  },
  relaunchToUpdate: () => ipcRenderer.invoke("desktop:relaunch-to-update"),
});
```

- [ ] **Step 4: Run preload tests**

```bash
cd desktop && npx vitest run test/preload.test.js
```

Expected: all preload tests pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/preload/index.js desktop/test/preload.test.js desktop/test/_helpers.js
git commit -m "feat(desktop): preload bridge — onUpdateStatus + relaunchToUpdate"
```

### Task 2.6 — IPC route for relaunch-to-update (TDD)

**Files:**
- Modify: `desktop/main/ipc.js`, `desktop/test/ipc.test.js`

- [ ] **Step 1: Add failing test**

Append to `desktop/test/ipc.test.js`:

```js
it("registers desktop:relaunch-to-update and forwards to the relaunchToUpdate handler", async () => {
  const { ipcMain } = await import("electron");
  const relaunchToUpdate = vi.fn();
  registerIpc({
    mainWindowId: 7,
    handlers: { login: vi.fn(), logout: vi.fn(), getVersion: vi.fn(), relaunchToUpdate },
  });
  const channels = ipcMain.handle.mock.calls.map(([ch]) => ch);
  expect(channels).toContain("desktop:relaunch-to-update");
  const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:relaunch-to-update")[1];
  await handler(makeEvent(7));
  expect(relaunchToUpdate).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd desktop && npx vitest run test/ipc.test.js
```

Expected: the new test fails (channel not registered).

- [ ] **Step 3: Extend ipc.js**

```js
// desktop/main/ipc.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { ipcMain } from "electron";

const CHANNELS = [
  "desktop:login",
  "desktop:logout",
  "desktop:version",
  "desktop:relaunch-to-update",
];

export function registerIpc({ mainWindowId, handlers }) {
  const route = {
    "desktop:login": handlers.login,
    "desktop:logout": handlers.logout,
    "desktop:version": handlers.getVersion,
    "desktop:relaunch-to-update": handlers.relaunchToUpdate,
  };
  for (const channel of CHANNELS) {
    ipcMain.handle(channel, async (event, ...args) => {
      if (event.sender.id !== mainWindowId) {
        throw new Error("SENDER_FORBIDDEN");
      }
      return route[channel](...args);
    });
  }
}
```

- [ ] **Step 4: Run ipc tests**

```bash
cd desktop && npx vitest run test/ipc.test.js
```

Expected: all pass, including the new one.

- [ ] **Step 5: Commit**

```bash
git add desktop/main/ipc.js desktop/test/ipc.test.js
git commit -m "feat(desktop): ipc — desktop:relaunch-to-update route"
```

### Task 2.7 — Wire updater.init in desktop/main/index.js

**Files:**
- Modify: `desktop/main/index.js`

`desktop/main/index.js` is excluded from coverage (`exclude: ["main/index.js"]` in vitest.config.js), so this is a wiring change without a unit test.

- [ ] **Step 1: Edit index.js to import + initialise the updater**

```js
// desktop/main/index.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { app } from "electron";
import { resolve } from "node:path";
import { registerAppScheme, registerAppHandler } from "./protocol.js";
import { createMainWindow } from "./window.js";
import { registerIpc } from "./ipc.js";
import { login } from "./auth.js";
import { init as initUpdater, relaunchToUpdate } from "./updater.js";

const RENDERER_ROOT = resolve(__dirname, "../../dist-renderer");
const PRELOAD = resolve(__dirname, "../preload/index.js");
const IS_SMOKE = process.argv.includes("--smoke");
const AUTH_BASE = process.env.CHANNEL_API_BASE ?? "https://channel.warlordofmars.net";

let isQuitting = false;
app.on("before-quit", () => { isQuitting = true; });

registerAppScheme();

app.whenReady().then(() => {
  registerAppHandler(RENDERER_ROOT);

  const win = createMainWindow({ preloadPath: PRELOAD, isQuitting: () => isQuitting });

  registerIpc({
    mainWindowId: win.webContents.id,
    handlers: {
      login: () => login({ authBaseUrl: AUTH_BASE }),
      logout: () => {},
      getVersion: () => app.getVersion(),
      relaunchToUpdate,
    },
  });

  // Channel detection: `0.2.0-dev.142` → dev; `0.2.0` → latest.
  // electron-builder bakes the version into package.json at build time.
  // app.isPackaged is false for `electron .` (inv desktop-dev), true for built apps.
  // Without this gate, dev would check the prod feed and try to install 0.2.0
  // over an `electron .` session — disruptive and meaningless.
  if (app.isPackaged) {
    const channel = app.getVersion().includes("-dev") ? "dev" : "latest";
    initUpdater({ channel, webContents: win.webContents });
  }

  if (IS_SMOKE) {
    win.webContents.on("did-finish-load", () => {
      setTimeout(() => app.quit(), 250);
    });
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
```

- [ ] **Step 2: Run the full desktop test suite + coverage gate**

```bash
cd desktop && npm run test:coverage
```

Expected: all tests pass; 100% coverage on `main/updater.js` and `preload/index.js`.

- [ ] **Step 3: Run linter**

```bash
cd desktop && npm run lint
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add desktop/main/index.js
git commit -m "feat(desktop): wire updater.init in main entry"
```

### Task 2.8 — Local verify (inv desktop-dev smoke)

- [ ] **Step 1: Boot the stack with the desktop app**

```bash
uv run inv desktop-dev
```

Wait until Vite + FastAPI + Electron are all up.

- [ ] **Step 2: Open devtools in the Electron window** (Cmd-Opt-I)

In the devtools console, run:

```js
window.channelDesktop
```

Expected: object with `isDesktop`, `login`, `logout`, `getVersion`, `onUpdateStatus`, `relaunchToUpdate`. All five methods present.

- [ ] **Step 3: Subscribe to update status**

```js
window.channelDesktop.onUpdateStatus(p => console.log("[update]", p));
```

Expected: no output. `inv desktop-dev` runs Electron via `electron .`, so `app.isPackaged` is `false` and `main/index.js` skips `initUpdater()` entirely. The bridge is exposed but no events fire because no listener was registered in the main process. This proves the bridge surface works without the updater making unwanted network requests against the prod feed.

- [ ] **Step 4: Ctrl-C to tear down**

### Task 2.9 — Open PR for sub-issue #2

- [ ] **Step 1: Push**

```bash
git push -u origin feat/electron-subB-updater-module-#<n>:feat/electron-subB-updater-module-#<n>
```

- [ ] **Step 2: Create PR**

```bash
gh pr create --base development --title "feat(desktop): updater module + preload bridge (#<n>)" --body "$(cat <<'EOF'
## Summary
- Adds `desktop/main/updater.js` — `electron-updater` wrapper, platform-gated to darwin, channel→URL mapping, IPC push events, 4-hour poll.
- Extends `desktop/preload/index.js` with `onUpdateStatus` + `relaunchToUpdate`.
- Adds `desktop:relaunch-to-update` IPC route in `desktop/main/ipc.js`.
- 100% coverage maintained on `desktop/`.

Closes #<n>
Part of #33

## Test plan
- [x] `cd desktop && npm run test:coverage` — all pass, 100%
- [x] `cd desktop && npm run lint` — clean
- [x] `uv run inv desktop-dev` smoke: `window.channelDesktop` exposes all 5 methods; onUpdateStatus subscribes without error

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
gh pr merge --auto --squash --delete-branch
```

---

# Sub-issue #3 — CDK additions for /updates/ hosting

**Branch:** `feat/electron-subB-cdk-updates-#<n>`. Can run in parallel with #2.

**Goal:** Add the `/updates/*` CloudFront behavior, the bucket lifecycle rule for old auto-update zips, and the IAM delta on the OIDC role so CI can publish to S3 + invalidate CloudFront.

### Task 3.1 — Add /updates/* behavior + lifecycle rule

**Files:**
- Modify: `infra/stacks/channel_stack.py`

- [ ] **Step 1: Open `infra/stacks/channel_stack.py` and locate the `distribution = cloudfront.Distribution(...)` block** (around line 708 per the spec exploration).

- [ ] **Step 2: Add an `updates_behavior` BehaviorOptions definition just before the `Distribution` block**

Add immediately after `docs_behavior` is defined:

```python
# Cache policy: short TTL for the manifest, long immutable TTL for binaries.
# The manifest filename (latest-mac.yml) and the binary filename (Channel-<version>-mac.zip)
# differ, so a single behavior can cover both with the manifest TTL controlled by
# Cache-Control headers on the manifest object itself (set by the CI publish step).
updates_cache_policy = cloudfront.CachePolicy(
    self,
    "UpdatesCachePolicy",
    cache_policy_name=f"channel-updates-{env_name}",
    default_ttl=Duration.seconds(60),
    min_ttl=Duration.seconds(0),
    max_ttl=Duration.days(365),
    cookie_behavior=cloudfront.CacheCookieBehavior.none(),
    query_string_behavior=cloudfront.CacheQueryStringBehavior.none(),
    header_behavior=cloudfront.CacheHeaderBehavior.none(),
    enable_accept_encoding_gzip=False,
    enable_accept_encoding_brotli=False,
)

updates_behavior = cloudfront.BehaviorOptions(
    origin=ui_s3_origin,
    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
    cache_policy=updates_cache_policy,
    response_headers_policy=security_headers_policy,
    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
)
```

- [ ] **Step 3: Add `/updates/*` to `additional_behaviors`**

```python
additional_behaviors={
    "/api/*": api_behavior,
    "/auth/*": api_behavior,
    "/health": cloudfront.BehaviorOptions(...),
    "/docs*": docs_behavior,
    "/updates/*": updates_behavior,  # NEW
},
```

- [ ] **Step 4: Add the lifecycle rule on `ui_bucket`**

Locate the `ui_bucket = s3.Bucket(...)` definition (search for `ui_bucket =`). Add a `lifecycle_rules=` parameter (or append to existing). Example:

```python
ui_bucket = s3.Bucket(
    self,
    "UiBucket",
    # ... existing args ...
    lifecycle_rules=[
        s3.LifecycleRule(
            id="ExpireOldAutoUpdateBundles",
            enabled=True,
            prefix="updates/",
            # Expire only versioned auto-update zips + blockmaps.
            # The stable Channel-mac.dmg and latest-mac.yml do not match
            # this filter and are never expired.
            expiration=Duration.days(30),
            tag_filters={"channel-asset-type": "versioned-zip"},
        ),
    ],
)
```

Note: S3 lifecycle policies cannot filter by filename pattern directly — they filter by prefix or tag. The CI publish step (Task 4.4) sets the tag `channel-asset-type=versioned-zip` on each `.zip` and `.blockmap` upload, leaving `Channel-mac.dmg` and `latest-mac.yml` untagged. This is the cleanest way to express "keep the stable names, expire the versioned ones" within S3's filter limitations.

- [ ] **Step 5: Run `cdk synth` to validate**

```bash
uv run inv synth
```

Expected: synth completes successfully. No diagnostic errors.

- [ ] **Step 6: Commit**

```bash
git add infra/stacks/channel_stack.py
git commit -m "feat(infra): cloudfront /updates/* behavior + bucket lifecycle"
```

### Task 3.2 — IAM delta on the OIDC role

**Files:**
- Modify: `infra/stacks/channel_stack.py`

- [ ] **Step 1: Locate the OIDC role definition**

```bash
grep -n "OpenIdConnectProvider\|github_oidc\|federated_role\|deploy_role" infra/stacks/channel_stack.py
```

Identify the role attached to the GitHub OIDC provider that `deploy-dev` and `deploy-prod` jobs assume.

- [ ] **Step 2: Add the publish policy statements**

Append to the role's inline policy (or attach a new managed policy named `ChannelPublishUpdates`):

```python
# Allow the GH Actions OIDC role to publish auto-update artefacts to
# s3://<bucket>/updates/* and invalidate the corresponding CloudFront paths.
deploy_role.add_to_policy(iam.PolicyStatement(
    sid="PublishUpdatesS3",
    effect=iam.Effect.ALLOW,
    actions=[
        "s3:PutObject",
        "s3:PutObjectTagging",
        "s3:DeleteObject",
        "s3:ListBucket",
    ],
    resources=[
        ui_bucket.bucket_arn,
        f"{ui_bucket.bucket_arn}/updates/*",
    ],
))

deploy_role.add_to_policy(iam.PolicyStatement(
    sid="InvalidateUpdatesCloudFront",
    effect=iam.Effect.ALLOW,
    actions=["cloudfront:CreateInvalidation"],
    resources=[
        f"arn:aws:cloudfront::{self.account}:distribution/{distribution.distribution_id}",
    ],
))
```

If the role variable is named differently (e.g. `github_actions_role`), substitute it.

- [ ] **Step 3: Add a CfnOutput for the publish target**

After the distribution is defined:

```python
CfnOutput(
    self,
    "UpdatesFeedUrl",
    value=f"https://{custom_domain}/updates",
    description="Base URL for the desktop auto-update manifest tree (channel suffix appended at build time)",
)

CfnOutput(
    self,
    "UpdatesBucketName",
    value=ui_bucket.bucket_name,
    description="S3 bucket name for the `aws s3 sync` step in publish-desktop-mac",
)

CfnOutput(
    self,
    "UpdatesDistributionId",
    value=distribution.distribution_id,
    description="CloudFront distribution ID for the cache invalidation step",
)
```

- [ ] **Step 4: Run synth**

```bash
uv run inv synth
```

Expected: synth completes successfully.

- [ ] **Step 5: Commit**

```bash
git add infra/stacks/channel_stack.py
git commit -m "feat(infra): IAM + outputs for desktop update publish"
```

### Task 3.3 — Local verify (synth + diff)

- [ ] **Step 1: Synth and inspect**

```bash
uv run inv synth
```

- [ ] **Step 2: Confirm the /updates/* behavior appears**

```bash
grep -A2 '/updates/' infra/cdk.out/ChannelStack-dev.template.json | head -20
```

Expected: a behavior entry with `PathPattern: "/updates/*"` and a TargetOriginId pointing at the UI bucket origin.

- [ ] **Step 3: Confirm the lifecycle rule**

```bash
grep -A5 'ExpireOldAutoUpdateBundles' infra/cdk.out/ChannelStack-dev.template.json
```

Expected: the rule definition with `Days: 30` and a tag filter matching `channel-asset-type=versioned-zip`.

- [ ] **Step 4: Confirm the IAM statements**

```bash
grep -B1 -A8 'PublishUpdatesS3\|InvalidateUpdatesCloudFront' infra/cdk.out/ChannelStack-dev.template.json
```

Expected: two policy statements present, with correct resources.

- [ ] **Step 5: Confirm the outputs**

```bash
grep -A2 'UpdatesFeedUrl\|UpdatesBucketName\|UpdatesDistributionId' infra/cdk.out/ChannelStack-dev.template.json
```

Expected: three CfnOutput entries.

If you have a personal AWS dev env, optionally deploy:

```bash
uv run inv deploy --env jc
```

Expected: deploy succeeds; no `cdk-nag` violations introduced.

### Task 3.4 — Open PR for sub-issue #3

- [ ] **Step 1: Push**

```bash
git push -u origin feat/electron-subB-cdk-updates-#<n>:feat/electron-subB-cdk-updates-#<n>
```

- [ ] **Step 2: Create PR**

```bash
gh pr create --base development --title "feat(infra): /updates/* CloudFront behavior + IAM (#<n>)" --body "$(cat <<'EOF'
## Summary
- Adds `/updates/*` behavior on the existing CloudFront distribution with a dedicated cache policy (60s manifest TTL via origin Cache-Control, 1y for content-addressed binaries).
- Adds a bucket lifecycle rule that expires `updates/*` objects tagged `channel-asset-type=versioned-zip` after 30 days. The stable `Channel-mac.dmg` and `latest-mac.yml` are not tagged and therefore never expire.
- Extends the GitHub OIDC role with `s3:PutObject` on `updates/*` and `cloudfront:CreateInvalidation` on the distribution.
- New CfnOutputs: `UpdatesFeedUrl`, `UpdatesBucketName`, `UpdatesDistributionId` — consumed by `publish-desktop-mac` CI job in sub-issue #4.

Closes #<n>
Part of #33

## Test plan
- [x] `uv run inv synth` — clean
- [x] `/updates/*` behavior present in generated template
- [x] Lifecycle rule present with 30-day expiration + tag filter
- [x] IAM statements present
- [x] CfnOutputs present
- [ ] (optional) `uv run inv deploy --env jc` against personal AWS env

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
gh pr merge --auto --squash --delete-branch
```

---

# Sub-issue #4 — Publish CI job + notarize hook + electron-builder.yml

**Branch:** `feat/electron-subB-publish-ci-#<n>`. Depends on #1 AND #3 merged.

**Goal:** Add the `publish-desktop-mac` GitHub Actions job that signs, notarises, syncs to S3, invalidates CloudFront, and (on main only) uploads to the GitHub release. Wires the notarize.js hook + electron-builder.yml flips. **NOT `agent-safe`** — touches `.github/workflows/`.

### Task 4.1 — Add @electron/notarize dependency

**Files:**
- Modify: `desktop/package.json`, `desktop/package-lock.json`

- [ ] **Step 1: Install**

```bash
cd desktop && npm install --save-dev @electron/notarize@^2.5.0 && cd ..
```

- [ ] **Step 2: Verify**

```bash
grep '@electron/notarize' desktop/package.json
```

Expected: in `devDependencies` (build-time only).

- [ ] **Step 3: Commit**

```bash
git add desktop/package.json desktop/package-lock.json
git commit -m "feat(desktop): add @electron/notarize devDependency"
```

### Task 4.2 — notarize.js afterSign hook (TDD)

**Files:**
- Create: `desktop/scripts/notarize.js`
- Create: `desktop/test/notarize.test.js`

- [ ] **Step 1: Write the failing test**

```js
// desktop/test/notarize.test.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("@electron/notarize", () => ({ notarize: vi.fn(() => Promise.resolve()) }));

const { default: notarizeHook } = await import("../scripts/notarize.js");

const ORIG_ENV = { ...process.env };

beforeEach(() => {
  vi.clearAllMocks();
  process.env.APPLE_API_KEY_P8_BASE64 = "ZmFrZQ=="; // "fake"
  process.env.APPLE_API_KEY_ID = "ABCDEFGHIJ";
  process.env.APPLE_API_ISSUER_ID = "00000000-0000-0000-0000-000000000000";
});
afterEach(() => {
  process.env = { ...ORIG_ENV };
});

describe("notarize afterSign hook", () => {
  it("skips when electronPlatformName is not darwin", async () => {
    const { notarize } = await import("@electron/notarize");
    await notarizeHook({
      electronPlatformName: "win32",
      appOutDir: "/tmp",
      packager: { appInfo: { productFilename: "Channel" } },
    });
    expect(notarize).not.toHaveBeenCalled();
  });

  it("skips when APPLE_API_KEY_P8_BASE64 is unset (e.g. local builds)", async () => {
    delete process.env.APPLE_API_KEY_P8_BASE64;
    const { notarize } = await import("@electron/notarize");
    await notarizeHook({
      electronPlatformName: "darwin",
      appOutDir: "/tmp",
      packager: { appInfo: { productFilename: "Channel" } },
    });
    expect(notarize).not.toHaveBeenCalled();
  });

  it("calls notarize with the decoded API key path when env is set", async () => {
    const { notarize } = await import("@electron/notarize");
    await notarizeHook({
      electronPlatformName: "darwin",
      appOutDir: "/tmp/build",
      packager: { appInfo: { productFilename: "Channel" } },
    });
    expect(notarize).toHaveBeenCalledWith(expect.objectContaining({
      appPath: "/tmp/build/Channel.app",
      appleApiKey: expect.stringMatching(/AuthKey_.*\.p8$/),
      appleApiKeyId: "ABCDEFGHIJ",
      appleApiIssuer: "00000000-0000-0000-0000-000000000000",
    }));
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd desktop && npx vitest run test/notarize.test.js
```

Expected: `Cannot find module '../scripts/notarize.js'`.

- [ ] **Step 3: Write notarize.js**

```js
// desktop/scripts/notarize.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { notarize } from "@electron/notarize";
import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Buffer } from "node:buffer";

export default async function notarizeHook(context) {
  const { electronPlatformName, appOutDir, packager } = context;
  if (electronPlatformName !== "darwin") return;
  if (!process.env.APPLE_API_KEY_P8_BASE64) return; // local builds: skip silently

  const appName = packager.appInfo.productFilename;
  const appPath = join(appOutDir, `${appName}.app`);

  const keyPath = join(tmpdir(), `AuthKey_${process.env.APPLE_API_KEY_ID}.p8`);
  writeFileSync(
    keyPath,
    Buffer.from(process.env.APPLE_API_KEY_P8_BASE64, "base64"),
    { mode: 0o600 },
  );

  await notarize({
    appPath,
    appleApiKey: keyPath,
    appleApiKeyId: process.env.APPLE_API_KEY_ID,
    appleApiIssuer: process.env.APPLE_API_ISSUER_ID,
  });
}
```

- [ ] **Step 4: Update vitest.config.js to include scripts in coverage**

```js
// desktop/vitest.config.js
// Copyright (c) 2026 John Carter. All rights reserved.
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    globals: true,
    passWithNoTests: true,
    include: ["test/**/*.test.js"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["main/**/*.js", "preload/**/*.js", "scripts/notarize.js"],
      exclude: ["main/index.js"],
      thresholds: {
        lines: 100,
        functions: 100,
        branches: 100,
        statements: 100,
      },
    },
  },
});
```

- [ ] **Step 5: Run tests + coverage**

```bash
cd desktop && npm run test:coverage
```

Expected: all pass; 100% coverage on `scripts/notarize.js`.

- [ ] **Step 6: Commit**

```bash
git add desktop/scripts/notarize.js desktop/test/notarize.test.js desktop/vitest.config.js
git commit -m "feat(desktop): notarize afterSign hook + tests"
```

### Task 4.3 — Update electron-builder.yml + desktop matrix for universal arch

**Files:**
- Modify: `desktop/electron-builder.yml`

- [ ] **Step 1: Replace the file with the signing-enabled version**

```yaml
appId: net.warlordofmars.channel.desktop
productName: Channel
copyright: Copyright (c) 2026 John Carter
directories:
  buildResources: resources
  output: release
files:
  - dist-main/**/*
  - dist-renderer/**/*
  - package.json
asar: true
afterSign: scripts/notarize.js
mac:
  category: public.app-category.productivity
  hardenedRuntime: true
  gatekeeperAssess: true
  entitlements: resources/entitlements.mac.plist
  entitlementsInherit: resources/entitlements.mac.plist
  # Universal2 binaries: one artefact that runs on both Apple Silicon and Intel.
  # Doubles binary size (~280MB) but means a single stable URL for Channel-mac.dmg
  # and a single latest-mac.yml manifest. Audience is small enough that bandwidth
  # is not a constraint; URL simplicity wins.
  target:
    - target: dmg
      arch: [universal]
    - target: zip
      arch: [universal]
  artifactName: "Channel-${os}.${ext}"  # default; overridden per-target below
  dmg:
    artifactName: "Channel-${os}.${ext}"   # → Channel-mac.dmg (stable)
  zip:
    artifactName: "Channel-${version}-${os}.${ext}"   # → Channel-0.2.0-mac.zip (versioned)
win:
  target:
    - target: nsis
      arch: [x64]
    - target: portable
      arch: [x64]
linux:
  target:
    - AppImage
    - deb
    - rpm
  category: Office
publish:
  - provider: generic
    url: "${env.UPDATES_FEED_URL}"
```

`${env.UPDATES_FEED_URL}` is templated by electron-builder at build time from the `UPDATES_FEED_URL` environment variable. The CI job (Task 4.4) sets this to `https://channel.warlordofmars.net/updates/latest` or `https://channel-dev.warlordofmars.net/updates/dev` per branch.

- [ ] **Step 2: Validate YAML**

```bash
node -e "console.log(require('yaml').parse(require('fs').readFileSync('desktop/electron-builder.yml','utf8')))" | head -20
```

(If `yaml` is not installed locally, `cd desktop && npx -y yaml < electron-builder.yml` or just rely on the CI parse.)

Expected: clean parse.

- [ ] **Step 3: Update the existing `desktop` matrix's macOS binary path**

The universal-arch change renames the build output from `mac-arm64/` to `mac-universal/`. The existing smoke-test matrix in `.github/workflows/ci.yml` references the old path. Locate (around line 475):

```yaml
          - os: macos-14
            target: --mac
            binary: desktop/release/mac-arm64/Channel.app/Contents/MacOS/Channel
```

Change to:

```yaml
          - os: macos-14
            target: --mac
            binary: desktop/release/mac-universal/Channel.app/Contents/MacOS/Channel
```

- [ ] **Step 4: Validate the YAML edits**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

Expected: silent success.

- [ ] **Step 5: Commit**

```bash
git add desktop/electron-builder.yml .github/workflows/ci.yml
git commit -m "feat(desktop): electron-builder — universal arch + hardened runtime + publish"
```

### Task 4.4 — publish-desktop-mac CI job

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Locate the existing `desktop` matrix job** (around line 467 per spec exploration).

- [ ] **Step 2: Add the new job immediately after the `desktop` job, before `notify-pipeline-failure`**

```yaml
# ── Publish signed mac auto-update artefacts (push to main|development only) ──

  publish-desktop-mac:
    name: Publish desktop (macOS)
    needs: [desktop]
    if: github.event_name == 'push' && (github.ref == 'refs/heads/main' || github.ref == 'refs/heads/development')
    runs-on: macos-14
    permissions:
      id-token: write
      contents: write   # for `gh release upload` on the main branch path
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v4
        with:
          fetch-depth: 0

      - uses: actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e # v6.4.0
        with:
          node-version: "20"

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@e3dd6a429d7300a6a4c196c26e071d42e0343502 # v4
        with:
          role-to-assume: ${{ vars.AWS_OIDC_ROLE_ARN }}
          aws-region: us-east-1

      - name: Resolve channel + env
        id: env
        run: |
          if [[ "${{ github.ref }}" == "refs/heads/main" ]]; then
            echo "channel=latest" >> "$GITHUB_OUTPUT"
            echo "cdk_env=prod"   >> "$GITHUB_OUTPUT"
            echo "domain=channel.warlordofmars.net" >> "$GITHUB_OUTPUT"
          else
            echo "channel=dev"    >> "$GITHUB_OUTPUT"
            echo "cdk_env=dev"    >> "$GITHUB_OUTPUT"
            echo "domain=channel-dev.warlordofmars.net" >> "$GITHUB_OUTPUT"
          fi

      - name: Read CDK outputs
        id: cdk
        run: |
          STACK="ChannelStack$([[ "${{ steps.env.outputs.cdk_env }}" == "dev" ]] && echo "-dev")"
          BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='UpdatesBucketName'].OutputValue" --output text)
          DIST=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='UpdatesDistributionId'].OutputValue" --output text)
          echo "bucket=$BUCKET" >> "$GITHUB_OUTPUT"
          echo "distribution=$DIST" >> "$GITHUB_OUTPUT"

      - name: Resolve version
        id: version
        working-directory: desktop
        run: |
          BASE=$(node -p "require('./package.json').version")
          if [[ "${{ steps.env.outputs.channel }}" == "dev" ]]; then
            VERSION="${BASE}-dev.${{ github.run_number }}"
          else
            VERSION="${BASE}"
          fi
          npm version --no-git-tag-version "$VERSION"
          echo "version=$VERSION" >> "$GITHUB_OUTPUT"

      - name: Import Apple signing cert
        env:
          APPLE_CERT_P12_BASE64: ${{ secrets.APPLE_CERT_P12_BASE64 }}
          APPLE_CERT_PASSWORD: ${{ secrets.APPLE_CERT_PASSWORD }}
        run: |
          KEYCHAIN=channel-signing.keychain-db
          KEYCHAIN_PASSWORD=$(openssl rand -hex 16)
          security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
          security set-keychain-settings -lut 3600 "$KEYCHAIN"
          security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
          security list-keychains -d user -s "$KEYCHAIN" $(security list-keychains -d user | tr -d '"')
          echo "$APPLE_CERT_P12_BASE64" | base64 -d > /tmp/cert.p12
          security import /tmp/cert.p12 -k "$KEYCHAIN" -P "$APPLE_CERT_PASSWORD" -T /usr/bin/codesign
          security set-key-partition-list -S apple-tool:,apple: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
          rm -f /tmp/cert.p12

      - name: Install desktop deps
        working-directory: desktop
        run: npm ci

      - name: Build + sign + notarise
        working-directory: desktop
        env:
          UPDATES_FEED_URL: https://${{ steps.env.outputs.domain }}/updates/${{ steps.env.outputs.channel }}
          APPLE_API_KEY_P8_BASE64: ${{ secrets.APPLE_API_KEY_P8_BASE64 }}
          APPLE_API_KEY_ID: ${{ secrets.APPLE_API_KEY_ID }}
          APPLE_API_ISSUER_ID: ${{ secrets.APPLE_API_ISSUER_ID }}
        run: |
          npm run build:main
          npm run build:renderer
          npx electron-builder --mac --publish=never

      - name: Sync versioned auto-update bundles to S3 (tagged for lifecycle)
        run: |
          aws s3 sync desktop/release/ s3://${{ steps.cdk.outputs.bucket }}/updates/${{ steps.env.outputs.channel }}/ \
            --exclude '*' \
            --include '*.zip' --include '*.blockmap' \
            --tagging "channel-asset-type=versioned-zip"

      - name: Sync stable artefacts (dmg + manifest) to S3 (untagged)
        run: |
          # The dmg uses the stable artifactName "Channel-${os}.${ext}".
          aws s3 cp desktop/release/Channel-mac.dmg \
            s3://${{ steps.cdk.outputs.bucket }}/updates/${{ steps.env.outputs.channel }}/Channel-mac.dmg \
            --cache-control "public, max-age=300"
          aws s3 cp desktop/release/latest-mac.yml \
            s3://${{ steps.cdk.outputs.bucket }}/updates/${{ steps.env.outputs.channel }}/latest-mac.yml \
            --cache-control "public, max-age=60"

      - name: Invalidate CloudFront for the manifest + stable dmg
        run: |
          aws cloudfront create-invalidation \
            --distribution-id ${{ steps.cdk.outputs.distribution }} \
            --paths "/updates/${{ steps.env.outputs.channel }}/latest-mac.yml" \
                    "/updates/${{ steps.env.outputs.channel }}/Channel-mac.dmg"

      - name: Upload signed assets to GitHub release (main only)
        if: github.ref == 'refs/heads/main'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          VERSION="${{ steps.version.outputs.version }}"
          gh release upload "v$VERSION" \
            desktop/release/Channel-mac.dmg \
            desktop/release/*.exe \
            desktop/release/*.deb \
            desktop/release/*.rpm \
            desktop/release/*.AppImage \
            --clobber
```

Note on the `gh release upload` step: it expects the release `v$VERSION` to already exist. The existing `release` job creates it earlier in the pipeline; if `release` depends on `publish-desktop-mac` instead (which it currently does NOT — `release: needs: [..., desktop, ...]`), we'd have a circular wait. Verify the existing graph:

```bash
grep -B1 -A2 "^  release:\|^  publish-desktop-mac:" .github/workflows/ci.yml
```

Then add `publish-desktop-mac` to `release`'s `needs:` *only if* it's safe — actually, the simpler approach is: `release` runs in parallel with `publish-desktop-mac`. The `gh release upload` step in `publish-desktop-mac` waits for the release to exist by polling:

Replace the upload step with:

```yaml
      - name: Upload signed assets to GitHub release (main only)
        if: github.ref == 'refs/heads/main'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          VERSION="${{ steps.version.outputs.version }}"
          # Wait up to 5 min for the release to be created by the `release` job.
          for i in {1..30}; do
            if gh release view "v$VERSION" >/dev/null 2>&1; then break; fi
            echo "Waiting for release v$VERSION to exist... ($i/30)"
            sleep 10
          done
          gh release upload "v$VERSION" \
            desktop/release/Channel-mac.dmg \
            desktop/release/*.exe \
            desktop/release/*.deb \
            desktop/release/*.rpm \
            desktop/release/*.AppImage \
            --clobber
```

- [ ] **Step 3: Add `publish-desktop-mac` to `notify-pipeline-failure`'s `needs:`**

```yaml
  notify-pipeline-failure:
    name: Notify Slack — CI Failure
    needs: [lint-typecheck, audit, unit-tests, integration-tests, coverage, frontend, desktop, infra-synth, publish-desktop-mac]
```

- [ ] **Step 4: Validate the workflow YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

Expected: silent success (no parse error).

Optionally use `actionlint` if installed:

```bash
brew list actionlint >/dev/null 2>&1 || brew install actionlint
actionlint .github/workflows/ci.yml
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: publish-desktop-mac job — sign, notarise, sync, invalidate, upload"
```

### Task 4.5 — Local verify (throwaway branch off development)

This task validates the CI pipeline end-to-end without disturbing the bootstrap or earlier sub-issues' state.

- [ ] **Step 1: Create a throwaway branch off the publish-CI branch**

```bash
git checkout -b temp/test-publish-flow
```

- [ ] **Step 2: Force a CI run by touching an inert file**

```bash
echo "# trigger" >> .ci-trigger
git add .ci-trigger
git commit -m "chore: trigger CI for publish smoke"
git push -u origin temp/test-publish-flow:temp/test-publish-flow
```

Note: this branch is local-only for testing. The push only triggers the `desktop` matrix (not `publish-desktop-mac`, which only fires on push to `main`/`development`). To trigger `publish-desktop-mac`, you would need to merge the publish-CI PR to `development` first. **Therefore: defer this verify until after merging.**

- [ ] **Step 3: After PR #4 merges to `development`, watch CI**

```bash
gh run watch
```

Wait for `publish-desktop-mac` to complete. Expected duration: 8–15 min.

- [ ] **Step 4: Verify the manifest was published**

```bash
curl -s https://channel-dev.warlordofmars.net/updates/dev/latest-mac.yml | head -10
```

Expected: YAML output with `version: 0.2.0-dev.<run_number>`, `files:`, etc.

- [ ] **Step 5: Verify a running dev desktop app picks up the update**

If you have a previous dev build installed (or install one fresh from `https://channel-dev.warlordofmars.net/updates/dev/Channel-mac.dmg`):

1. Open the app, sign in.
2. Open devtools (Cmd-Opt-I in the renderer).
3. In console: `window.channelDesktop.onUpdateStatus(p => console.log('[update]', p))`
4. Trigger a check: send IPC manually via devtools, or just wait — the startup check fires within ~30s.
5. Expected: log entries with `{ state: "checking" }` then `{ state: "available", version: "0.2.0-dev.<n>" }` then `{ state: "downloaded", version: "0.2.0-dev.<n>" }`.

- [ ] **Step 6: Clean up the throwaway branch**

```bash
git push origin --delete temp/test-publish-flow
git checkout feat/electron-subB-publish-ci-#<n>
git branch -D temp/test-publish-flow
rm -f .ci-trigger
```

### Task 4.6 — Open PR for sub-issue #4

- [ ] **Step 1: Push**

```bash
git push -u origin feat/electron-subB-publish-ci-#<n>:feat/electron-subB-publish-ci-#<n>
```

- [ ] **Step 2: Create PR**

```bash
gh pr create --base development --title "ci: publish-desktop-mac — sign + notarise + S3 + GH release (#<n>)" --body "$(cat <<'EOF'
## Summary
- Adds `desktop/scripts/notarize.js` afterSign hook (calls `@electron/notarize` with the App Store Connect API key).
- Updates `desktop/electron-builder.yml`: `hardenedRuntime: true`, `gatekeeperAssess: true`, entitlements path, afterSign hook, `publish` block templated by `UPDATES_FEED_URL`, per-target `artifactName` (stable `.dmg`, versioned `.zip`).
- Adds the `publish-desktop-mac` GitHub Actions job: imports cert, builds + signs + notarises, S3 syncs the versioned zips (tagged for lifecycle expiry) plus the stable `.dmg` and manifest (untagged), invalidates CloudFront, and on `main` uploads to the GitHub release.
- Adds `@electron/notarize` devDependency and includes `scripts/notarize.js` in the desktop coverage gate.

**Not agent-safe** — touches `.github/workflows/ci.yml`. Requires human merge after CI passes.

Closes #<n>
Part of #33

## Test plan
- [x] `cd desktop && npm run test:coverage` — clean, 100% incl. notarize.js
- [x] `actionlint .github/workflows/ci.yml` — clean
- [ ] After merge to `development`: watch the `publish-desktop-mac` job, confirm S3 manifest publishes, confirm a running dev desktop app picks up the update via the IPC events.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
# Do NOT --auto merge — this PR is not agent-safe.
```

---

# Sub-issue #5 — UI restorations (Sidebar pill + Download.jsx URLs)

**Branch:** `feat/electron-subB-ui-restorations-#<n>`. Depends on #2 merged (consumes the preload bridge).

**Goal:** Restore the "Relaunch to update v…" pill in the Sidebar (3 states gated on `window.channelDesktop?.onUpdateStatus`) and update `Download.jsx` to point at the `releases/latest/download/*` signed-release URLs (replacing the `releases/download/dev/*` dev-tag URLs PR #30 added).

### Task 5.1 — Sidebar pill (TDD)

**Files:**
- Modify: `ui/src/app/Sidebar.jsx`, `ui/src/app/Sidebar.test.jsx`

- [ ] **Step 1: Write failing tests for the 3 pill states**

Append to `ui/src/app/Sidebar.test.jsx`:

```jsx
describe("Sidebar — update pill", () => {
  afterEach(() => { delete window.channelDesktop; });

  it("does not render the pill when window.channelDesktop is absent (web SPA)", () => {
    renderSidebar();
    expect(screen.queryByRole("button", { name: /relaunch to update/i })).toBeNull();
  });

  it("does not render the pill when no update event has fired yet (desktop, current)", () => {
    window.channelDesktop = {
      isDesktop: true,
      onUpdateStatus: vi.fn(),                      // never invokes its cb
      relaunchToUpdate: vi.fn(),
    };
    renderSidebar();
    expect(screen.queryByRole("button", { name: /relaunch to update/i })).toBeNull();
  });

  it("renders the pill with version + click handler after update:downloaded fires", () => {
    let registeredCb;
    const relaunchToUpdate = vi.fn();
    window.channelDesktop = {
      isDesktop: true,
      onUpdateStatus: (cb) => { registeredCb = cb; },
      relaunchToUpdate,
    };
    renderSidebar();
    expect(registeredCb).toBeTypeOf("function");

    // Simulate the main process pushing an event.
    act(() => registeredCb({ state: "downloaded", version: "0.2.1" }));

    const pill = screen.getByRole("button", { name: /relaunch to update v0\.2\.1/i });
    expect(pill).toBeTruthy();
    fireEvent.click(pill);
    expect(relaunchToUpdate).toHaveBeenCalled();
  });

  it("ignores non-downloaded states (checking/available/error)", () => {
    let registeredCb;
    window.channelDesktop = {
      isDesktop: true,
      onUpdateStatus: (cb) => { registeredCb = cb; },
      relaunchToUpdate: vi.fn(),
    };
    renderSidebar();
    act(() => registeredCb({ state: "checking" }));
    act(() => registeredCb({ state: "available", version: "0.2.1" }));
    act(() => registeredCb({ state: "error", message: "boom" }));
    expect(screen.queryByRole("button", { name: /relaunch to update/i })).toBeNull();
  });
});
```

Add the missing `act` import at the top of the test file:

```jsx
import { act } from "react";
```

- [ ] **Step 2: Run to confirm failure**

```bash
npx vitest run ui/src/app/Sidebar.test.jsx
```

Expected: the four new tests fail.

- [ ] **Step 3: Modify Sidebar.jsx to render the pill**

Open `ui/src/app/Sidebar.jsx`. Locate the tombstone comment (around line 14–15) and the structure where it lived. Add a `useEffect` hook subscribing to `onUpdateStatus`, store the downloaded version in component state, and render a pill button conditionally.

Add to imports:

```jsx
import { useEffect, useState } from "react";
```

(If already imported, skip.)

Add inside the component body:

```jsx
const [pendingUpdateVersion, setPendingUpdateVersion] = useState(null);

useEffect(() => {
  const desktop = window.channelDesktop;
  if (!desktop?.onUpdateStatus) return;
  desktop.onUpdateStatus(handleUpdateStatus);
  // No cleanup — desktop ipcRenderer.on listeners persist for the app lifetime
  // and the Sidebar mounts once per signed-in session.
}, []);

function handleUpdateStatus(payload) {
  if (payload?.state === "downloaded" && payload.version) {
    setPendingUpdateVersion(payload.version);
  }
}

function handleRelaunch() {
  window.channelDesktop?.relaunchToUpdate?.();
}
```

Render the pill in the appropriate location (replacing the tombstone comment region):

```jsx
{pendingUpdateVersion && (
  <button
    type="button"
    className="update-pill"
    onClick={handleRelaunch}
  >
    Relaunch to update v{pendingUpdateVersion}
  </button>
)}
```

Add the `.update-pill` style to `ui/src/styles/app.css` (or wherever the sidebar chrome styles live):

```css
.update-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  margin: 0 12px 12px;
  border-radius: 999px;
  background: var(--accent);
  color: var(--accent-ink);
  font-size: 12px;
  font-weight: 600;
  border: none;
  cursor: pointer;
  animation: update-pill-pulse 700ms ease-out 1;
}

@keyframes update-pill-pulse {
  0%   { transform: scale(0.92); opacity: 0; }
  60%  { transform: scale(1.04); opacity: 1; }
  100% { transform: scale(1); }
}
```

Note: extract `handleUpdateStatus` and `handleRelaunch` as named functions inside the component (not inline arrow functions) per the project's vitest v8 anonymous-function coverage gotcha.

- [ ] **Step 4: Run Sidebar tests**

```bash
npx vitest run ui/src/app/Sidebar.test.jsx
```

Expected: all pass.

- [ ] **Step 5: Run full frontend tests + coverage**

```bash
uv run inv test-frontend
```

Expected: pass, coverage maintained at 100%.

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/Sidebar.jsx ui/src/app/Sidebar.test.jsx ui/src/styles/app.css
git commit -m "feat(ui): restore Relaunch-to-update pill (3 states)"
```

### Task 5.2 — Download.jsx URLs (TDD)

**Files:**
- Modify: `ui/src/marketing/pages/Download.jsx`, `ui/src/marketing/pages/Download.test.jsx`, `ui/src/styles/site.css`

**Starting state (per PR #30, already on `development`):** `Download.jsx` defines `RELEASE_BASE = "https://github.com/warlordofmars/channel/releases/download/dev"` and `RELEASES_PAGE = "https://github.com/warlordofmars/channel/releases/tag/dev"`, has a `<div className="dev-preview-banner" data-testid="dev-preview-banner">…</div>`, and renders six download links: Apple Silicon `.dmg` (primary) + Intel `.dmg` (secondary `dl-alt`); Windows `.exe`; Linux `.AppImage` (primary) + `.deb` + `.rpm` (secondary `dl-alt`). Auto-update paragraph reads "Channel updates itself automatically — you'll see a 'Relaunch to update' prompt…"

**Target state:** Single `RELEASE_BASE = "https://github.com/warlordofmars/channel/releases/latest/download"`. No dev-preview banner (signed builds, no scary OS warnings). Single universal `Channel-mac.dmg` for macOS (drop the Intel-vs-Apple-Silicon split; universal binary handles both). Windows + Linux structure preserved. Auto-update paragraph rewritten to acknowledge macOS-only auto-update.

- [ ] **Step 1: Read the current Download.test.jsx + Download.jsx**

```bash
cat ui/src/marketing/pages/Download.test.jsx
cat ui/src/marketing/pages/Download.jsx
```

This is essential — PR #30 may have introduced assertions about the dev banner or the Intel-secondary link that need to be removed or rewritten.

- [ ] **Step 2: Update Download.test.jsx — assert new state and remove dev-state assertions**

Remove or rewrite any test that asserts:
- `data-testid="dev-preview-banner"` is rendered
- The Apple Silicon vs Intel split exists
- Links contain `releases/download/dev/`

Add assertions for the new state:

```jsx
describe("Download — signed-release URLs", () => {
  const releasesBase = "https://github.com/warlordofmars/channel/releases/latest/download";

  it.each([
    ["Download .dmg",      `${releasesBase}/Channel-mac.dmg`],
    ["Download .exe",      `${releasesBase}/Channel-Setup.exe`],
    ["Download AppImage",  `${releasesBase}/Channel-linux.AppImage`],
  ])("primary button %s links to %s", (label, expectedHref) => {
    render(<MemoryRouter><Download /></MemoryRouter>);
    const link = screen.getByRole("link", { name: new RegExp(label, "i") });
    expect(link.getAttribute("href")).toBe(expectedHref);
  });

  it.each([
    [".deb", `${releasesBase}/Channel-linux.deb`],
    [".rpm", `${releasesBase}/Channel-linux.rpm`],
  ])("secondary Linux link %s points at %s", (label, expectedHref) => {
    render(<MemoryRouter><Download /></MemoryRouter>);
    const link = screen.getByRole("link", { name: new RegExp(`^\\${label}$`, "i") });
    expect(link.getAttribute("href")).toBe(expectedHref);
  });

  it("does not render the dev-preview banner", () => {
    render(<MemoryRouter><Download /></MemoryRouter>);
    expect(screen.queryByTestId("dev-preview-banner")).toBeNull();
  });

  it("does not render an Intel-specific Mac download link (universal binary)", () => {
    render(<MemoryRouter><Download /></MemoryRouter>);
    expect(screen.queryByText(/intel mac/i)).toBeNull();
  });

  it("notes the macOS-only auto-update story", () => {
    render(<MemoryRouter><Download /></MemoryRouter>);
    expect(screen.getByText(/macOS auto-updates in the background/i)).toBeTruthy();
  });

  it("no link points at releases/download/dev/", () => {
    render(<MemoryRouter><Download /></MemoryRouter>);
    for (const link of screen.getAllByRole("link")) {
      expect(link.getAttribute("href") ?? "").not.toContain("releases/download/dev/");
    }
  });
});
```

- [ ] **Step 3: Run to confirm failure**

```bash
npx vitest run ui/src/marketing/pages/Download.test.jsx
```

Expected: the new assertions fail (current Download.jsx still points at the dev tag, banner present, Intel link present, old auto-update text).

- [ ] **Step 4: Update Download.jsx**

Edit the constants at the top:

```jsx
const RELEASE_BASE = "https://github.com/warlordofmars/channel/releases/latest/download";
// (RELEASES_PAGE constant removed — no longer needed)
```

Delete the dev-preview banner section entirely:

```jsx
// REMOVE:
//   <section className="section wrap" style={{ paddingTop: "24px" }}>
//     <div className="dev-preview-banner" data-testid="dev-preview-banner">
//       …
//     </div>
//   </section>
```

Adjust the first `.dl-grid` section's `paddingTop` back to its natural value (the banner section is gone, so the grid can sit closer to the page head):

```jsx
<section className="section wrap" style={{ paddingTop: "44px" }}>
  <div className="dl-grid">
```

Collapse the macOS card to a single universal dmg button (drop the `.dl-alt` Intel link):

```jsx
<div className="dl-card feat">
  <div className="dlic">
    {/* unchanged Apple SVG */}
  </div>
  <h3>macOS</h3>
  <div className="dlv">Universal · macOS 14+</div>
  <a className="btn btn-primary" href={`${RELEASE_BASE}/Channel-mac.dmg`} style={{ width: "100%" }}>
    Download .dmg
  </a>
</div>
```

Update the Windows button URL:

```jsx
<a className="btn btn-ghost" href={`${RELEASE_BASE}/Channel-Setup.exe`} style={{ width: "100%" }}>
  Download .exe
</a>
```

Update the Linux button URLs (preserve PR #30's primary + dl-alt structure, just swap the URLs):

```jsx
<a className="btn btn-ghost" href={`${RELEASE_BASE}/Channel-linux.AppImage`} style={{ width: "100%" }}>
  Download AppImage
</a>
<div className="dl-alt">
  <a href={`${RELEASE_BASE}/Channel-linux.deb`}>.deb</a>
  {" · "}
  <a href={`${RELEASE_BASE}/Channel-linux.rpm`}>.rpm</a>
</div>
```

Replace the auto-update paragraph in the System requirements section (currently reads "Channel updates itself automatically — you'll see a 'Relaunch to update' prompt in the sidebar when a new build is ready."):

```jsx
<p>
  macOS auto-updates in the background — you'll see a "Relaunch to
  update" prompt in the sidebar when a new build is ready. Windows and
  Linux: re-download from this page when a new build lands.
</p>
```

- [ ] **Step 5: Remove the `.dev-preview-banner` CSS rule from `ui/src/styles/site.css`**

PR #30 added a `.dev-preview-banner` rule. Search:

```bash
grep -n "dev-preview-banner" ui/src/styles/site.css
```

Delete the rule. No new CSS needed for this task — the existing `.dl-alt` styling handles the Linux secondary links.

- [ ] **Step 6: Run Download tests**

```bash
npx vitest run ui/src/marketing/pages/Download.test.jsx
```

Expected: all pass.

- [ ] **Step 6: Run full frontend suite**

```bash
uv run inv test-frontend
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add ui/src/marketing/pages/Download.jsx ui/src/marketing/pages/Download.test.jsx ui/src/styles/site.css
git commit -m "feat(ui): point Download.jsx at signed-release URLs + drop dev banner"
```

### Task 5.3 — Local verify (inv dev + mocked desktop)

- [ ] **Step 1: Boot the dev stack**

```bash
uv run inv dev
```

Wait for Vite + FastAPI.

- [ ] **Step 2: Sign in at the local URL** (typically `http://localhost:5173/auth/login?test_email=jc@example.com`).

- [ ] **Step 3: Open devtools, mock `window.channelDesktop`**

```js
let cb;
window.channelDesktop = {
  isDesktop: true,
  onUpdateStatus: (fn) => { cb = fn; },
  relaunchToUpdate: () => console.log("relaunch"),
};
location.reload();
```

After reload + sign-in, in the devtools console:

```js
cb({ state: "downloaded", version: "0.2.1" });
```

Expected: the "Relaunch to update v0.2.1" pill appears in the sidebar with a brief pulse animation.

- [ ] **Step 4: Click the pill**

Expected: devtools console logs `relaunch`.

- [ ] **Step 5: Verify the Download page**

Navigate to `/download` (in another tab or by signing out and visiting the marketing site).

Expected:
- macOS button shows `Download .dmg` and links to the GitHub release URL.
- Windows button shows `Download .exe`.
- Linux card shows three buttons (.deb, .rpm, .AppImage).
- The text below the grid reads "macOS auto-updates in the background. Windows and Linux: reinstall from this page when a new build lands."

- [ ] **Step 6: Tear down**

Ctrl-C the `inv dev` stack.

### Task 5.4 — Open PR for sub-issue #5

- [ ] **Step 1: Push**

```bash
git push -u origin feat/electron-subB-ui-restorations-#<n>:feat/electron-subB-ui-restorations-#<n>
```

- [ ] **Step 2: Create PR**

```bash
gh pr create --base development --title "feat(ui): restore update pill + real download URLs (#<n>)" --body "$(cat <<'EOF'
## Summary
- Restores the "Relaunch to update v…" pill in `Sidebar.jsx`, gated on `window.channelDesktop?.onUpdateStatus`. Three states (no-electron / no-event / downloaded).
- Swaps `Download.jsx`'s `releases/download/dev/*` URLs (from PR #30) for `releases/latest/download/*` signed-release URLs.
- Removes the "Dev preview" banner — signed builds no longer warrant the OS-warning advisory.
- Collapses the Apple Silicon / Intel dual-button on macOS to a single universal `.dmg`.
- Rewrites the auto-update paragraph to acknowledge the macOS-only-now reality.

Closes #<n>
Part of #33

## Test plan
- [x] `uv run inv test-frontend` — clean, 100% coverage
- [x] `uv run inv dev`: mocked `window.channelDesktop`, pill appears + click fires relaunch
- [x] `/download` page: all 5 links resolve to GitHub release URLs

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
gh pr merge --auto --squash --delete-branch
```

---

# Sub-issue #6 — ADR

**Branch:** `feat/electron-subB-adr-#<n>`. Depends on #4 merged (writes about real decisions).

**Goal:** Capture the five sub-B decisions in an ADR so future maintainers don't re-derive them.

### Task 6.1 — Write the ADR

**Files:**
- Create: `docs/adr/0009-electron-release-pipeline.md`
- Modify: `docs/adr/README.md` (or the ADR index, whatever exists)

- [ ] **Step 1: Confirm the next ADR number**

```bash
ls docs/adr/ | sort | tail -5
```

Pick the next sequential number (likely `0009`; adjust if `0009` is already taken).

- [ ] **Step 2: Write the ADR**

```markdown
<!-- Copyright (c) 2026 John Carter. All rights reserved. -->
# ADR 0009 — Electron desktop release pipeline + macOS signing

**Date:** 2026-MM-DD (date of publish-desktop-mac PR merge)
**Status:** Accepted

## Context

Sub-project A (#23) shipped an unsigned Electron wrapper that bundles
the React SPA and authenticates via a loopback OAuth flow. Sub-project B
(#33) needed to take that unsigned build path and turn it into a real,
auto-updating release pipeline. Five design choices stuck out as
needing durable rationale.

The audience driving these choices is "the author plus a handful of
vouched testers" — a small known group. A different audience (public
download from the marketing site, or B2B/enterprise) would change every
one of the decisions below.

## Decision

### 1. macOS-only signing in this round

We sign and notarise macOS. Windows and Linux ship unsigned in this
round. Users on Win/Linux see a SmartScreen warning / unsigned-AppImage
prompt on first install; they reinstall manually for updates.

**Why:** Gatekeeper blocks an unsigned `.app` from opening at all, even
for technical testers, so macOS signing earns its keep on day one. The
Win/Linux warnings are clickable-through. Windows EV certs (~$300/yr)
and Azure Trusted Signing setup are deferred until the audience grows.

### 2. Two channels: `latest` and `dev`, mapped 1:1 to the CDK env split

`main` → `latest` channel, served from
`https://channel.warlordofmars.net/updates/latest/`.
`development` → `dev` channel, served from
`https://channel-dev.warlordofmars.net/updates/dev/`.
The channel is baked into each desktop build at build time via the
`UPDATES_FEED_URL` env var consumed by `electron-builder.yml`.

**Why:** The CDK env split (`ChannelStack` / `ChannelStack-dev`) already
has separate CloudFront distributions and S3 buckets. Mapping channels
1:1 to envs requires zero cross-env coupling and matches the natural
deployment cadence (push to `development` ≠ push to `main`). A
three-channel topology (`stable` / `beta` / `dev`) was rejected as
over-engineered for the current audience.

### 3. GitHub Actions repo secrets for the signing secrets

The five Apple credentials (`APPLE_CERT_P12_BASE64`,
`APPLE_CERT_PASSWORD`, `APPLE_API_KEY_P8_BASE64`, `APPLE_API_KEY_ID`,
`APPLE_API_ISSUER_ID`) live as repo secrets in GitHub Actions.

**Why:** They are build-time only — Lambda never sees them. AWS SSM
Parameter Store and Secrets Manager are appropriate for runtime secrets
(`/channel/{env}/jwt-secret`), not build-time CI inputs. Putting build
secrets in SSM would force the runner to assume an AWS role just to
fetch them, adding IAM complexity for no audit-trail benefit (GH already
audits secret access in the workflow log).

### 4. Same bucket + new `/updates/` prefix + new CloudFront behavior

Auto-update artefacts (`latest-mac.yml`, `Channel-<ver>-mac.zip`,
`Channel-mac.dmg`) live in the existing UI bucket under prefix
`updates/<channel>/`, fronted by a new `/updates/*` behavior on the
existing distribution. No separate distribution, no separate bucket,
no separate subdomain.

**Why:** Smallest infra delta; reuses the existing OAC + WAF + cert.
Blast radius is contained via path-scoped behaviors (a bad cache
config on `/updates/*` cannot affect `/api/*`, `/docs*`, or `/`). The
alternative (separate `updates.channel.warlordofmars.net` distribution)
would double CloudFront cost for no security benefit.

### 5. GitHub Releases for first-install downloads; S3 for auto-update only

The public `Download.jsx` page links to
`https://github.com/warlordofmars/channel/releases/latest/download/<file>`
for all platforms. The S3 `/updates/<channel>/` tree holds the
auto-update manifest + `.zip` + `.blockmap` plus a stable
`Channel-mac.dmg` for dev-channel testers (private link).

**Why:** Avoids dual-source-of-truth for the same `.dmg`. GitHub
Releases are indexed by changelog, versioned URLs, and already serve
as the public release surface. S3 stays lean and exists only for the
auto-update flow. Dev testers (private link) get the dmg from S3
because there is no public dev-channel GH release.

## Consequences

- Apple Developer Program enrolment ($99/yr) is now a project
  prerequisite. Renewal procedure in
  `docs/dev/apple-signing-bootstrap.md`.
- The `publish-desktop-mac` CI job adds ~8–15 min to every push to
  `main` and `development`. Notarisation latency dominates.
- A notary outage breaks the publish job but does not gate
  `deploy-dev` / `deploy-prod` (they run in parallel). Testers stay on
  the previous version until the next successful publish.
- Windows and Linux signing remain a planned follow-on. When that
  lands, the matrix can expand without re-architecting the channel
  topology, the bucket layout, or the CDK construct.
- The `Channel-mac.dmg` URL is stable across releases (no version in
  the filename), which means downloaders cannot easily rollback to a
  prior version via URL surgery. This is acceptable for the current
  audience — they have shell access and can fetch a specific
  `Channel-<version>-mac.zip` from S3 if needed.
```

- [ ] **Step 3: Update the ADR index**

```bash
cat docs/adr/README.md 2>/dev/null || ls docs/adr/
```

If `README.md` exists with an index, add the new entry. If no index exists, skip.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0009-electron-release-pipeline.md docs/adr/README.md
git commit -m "docs(adr): 0009 — electron release pipeline + macOS signing"
```

### Task 6.2 — Open PR for sub-issue #6

- [ ] **Step 1: Push**

```bash
git push -u origin feat/electron-subB-adr-#<n>:feat/electron-subB-adr-#<n>
```

- [ ] **Step 2: Create PR**

```bash
gh pr create --base development --title "docs(adr): 0009 — electron release pipeline + macOS signing (#<n>)" --body "$(cat <<'EOF'
## Summary
ADR 0009 captures the five sub-project B decisions (macOS-only signing, two channels mapped 1:1 to CDK envs, GH Actions secrets, same-distribution `/updates/*` prefix, GH Releases for first-install / S3 for auto-update).

Written after the publish CI job landed so it documents lived decisions, not aspirations.

Closes #<n>
Part of #33

## Test plan
- [x] ADR follows the project's ADR conventions (Context / Decision / Consequences)
- [x] Linked from the ADR index (if applicable)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
gh pr merge --auto --squash --delete-branch
```

---

## Plan completion gate

After all six sub-issue PRs are merged:

- [ ] Close issue #33 with a comment summarising the merged PRs and confirming the macOS auto-update path is live.
- [ ] File a follow-on issue for Windows signing (deferred per audience scope).
- [ ] File a follow-on issue for Linux package signing.
- [ ] Update the `CHANGELOG.md` `[Unreleased]` section with the user-visible bits (auto-update on macOS; real download URLs on the public site).
