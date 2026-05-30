# Channel desktop app — shell & OAuth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land sub-project A from `docs/superpowers/specs/2026-05-30-electron-shell-oauth-design.md` — a runnable, packaged Electron app for macOS / Windows / Linux that boots into the existing SPA, signs in via an Electron-native Google OAuth flow (external browser + loopback), and builds in CI on all three OSes with full coverage. Code signing, auto-update, and real distribution URLs are sub-project B and out of scope here.

**Architecture:** Two-process Electron app under a new `desktop/` package. Main process (Node) owns the `BrowserWindow`, an `app://` custom protocol handler that serves the bundled SPA from the asar (deep paths fall through to `index.html` so React Router's `BrowserRouter` keeps working unchanged), and an on-demand HTTP loopback server on `127.0.0.1` that captures the Google OAuth callback. Renderer is the existing SPA built once with `VITE_API_BASE=https://channel.warlordofmars.net`; it detects desktop mode via `window.channelDesktop` exposed by a sandboxed preload. FastAPI gains optional `desktop_callback` + caller-supplied `state` parameters on `/auth/login` and conditional loopback redirect in `/auth/callback`.

**Tech Stack:** Electron (latest stable), electron-builder, esbuild, vitest with `@vitest/coverage-v8` (100% gate, matches `ui/` config), Playwright for the platform smoke step in CI (already a project dep), FastAPI / httpx unchanged.

---

## Conventions to follow in every task

- **Copyright header** on every new `.js` file:
  ```js
  // Copyright (c) 2026 John Carter. All rights reserved.
  ```
  On every new `.py` file:
  ```python
  # Copyright (c) 2026 John Carter. All rights reserved.
  ```
- **Co-authorship trailer** on every commit:
  ```
  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  ```
- **`vi.mock('electron', …)` pattern** — `desktop/test/_helpers.js` (created in Task 1) exports a `mockElectron()` factory. Every desktop main-process test imports it. Repeating the mock pattern in every test file is OK; you may also use the helper.
- **No emojis** in source, docs, or commits unless the surrounding code already uses them. None of this codebase does.
- **One commit per task.** Each task ends with a `git add` + `git commit` step.
- **Never push from this plan** — the issue-worker / human owns the push and PR. Plan execution is local-commit-only.

---

## Task 1: Scaffold `desktop/` package + ignore paths

**Files:**
- Create: `desktop/package.json`
- Create: `desktop/vitest.config.js`
- Create: `desktop/electron-builder.yml`
- Create: `desktop/.eslintrc.json`
- Create: `desktop/test/_helpers.js`
- Create: `desktop/test/.gitkeep`
- Modify: `.gitignore` (root) — add desktop artifact paths

- [ ] **Step 1: Create `desktop/package.json`**

```json
{
  "name": "channel-desktop",
  "private": true,
  "version": "0.0.0",
  "description": "Channel desktop app (Electron wrapper around the SPA)",
  "main": "dist-main/index.js",
  "scripts": {
    "build:main": "esbuild main/index.js preload/index.js --bundle --platform=node --target=node20 --external:electron --outdir=dist-main --format=cjs",
    "build:renderer": "rsync -a --delete ../ui/dist/ dist-renderer/",
    "build": "npm run build:main && npm run build:renderer && electron-builder",
    "build:current": "npm run build:main && npm run build:renderer && electron-builder --dir",
    "dev:main": "esbuild main/index.js preload/index.js --bundle --platform=node --target=node20 --external:electron --outdir=dist-main --format=cjs --watch",
    "dev:electron": "electron .",
    "test": "vitest run",
    "test:coverage": "vitest run --coverage",
    "test:watch": "vitest",
    "lint": "eslint main preload test --ext .js"
  },
  "devDependencies": {
    "@vitest/coverage-v8": "^4.1.7",
    "electron": "latest",
    "electron-builder": "latest",
    "esbuild": "latest",
    "eslint": "^8.57.0",
    "vitest": "^4.1.5"
  }
}
```

- [ ] **Step 2: Create `desktop/vitest.config.js`**

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    globals: true,
    include: ["test/**/*.test.js"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["main/**/*.js", "preload/**/*.js"],
      // Entry point wires modules together — covered by the CI smoke
      // step, not unit tests. Mirrors ui/src/main.jsx exclusion.
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

- [ ] **Step 3: Create `desktop/electron-builder.yml`** — unsigned in A; signing entitlements are placeholders for sub-project B

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
mac:
  category: public.app-category.productivity
  target:
    - target: dmg
      arch: [arm64, x64]
    - target: zip
      arch: [arm64, x64]
  hardenedRuntime: false       # sub-project B turns this on with notarisation
  gatekeeperAssess: false
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
```

- [ ] **Step 4: Create `desktop/.eslintrc.json`**

```json
{
  "root": true,
  "env": { "node": true, "es2022": true },
  "extends": ["eslint:recommended"],
  "parserOptions": { "ecmaVersion": 2022, "sourceType": "module" },
  "rules": {
    "no-unused-vars": ["error", { "argsIgnorePattern": "^_" }]
  }
}
```

- [ ] **Step 5: Create `desktop/test/_helpers.js`** — shared electron mock factory

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { vi } from "vitest";

/**
 * Returns a stub object suitable for vi.mock("electron", () => mockElectron(...)).
 * Pass overrides keyed by submodule name to extend or replace individual mocks
 * in a specific test.
 */
export function mockElectron(overrides = {}) {
  return {
    app: {
      on: vi.fn(),
      whenReady: vi.fn(() => Promise.resolve()),
      quit: vi.fn(),
      getVersion: vi.fn(() => "0.0.0"),
      getPath: vi.fn(() => "/tmp"),
      ...overrides.app,
    },
    BrowserWindow: vi.fn().mockImplementation(() => ({
      loadURL: vi.fn(() => Promise.resolve()),
      on: vi.fn(),
      hide: vi.fn(),
      show: vi.fn(),
      webContents: { id: 1, on: vi.fn() },
      ...overrides.windowInstance,
    })),
    ipcMain: { handle: vi.fn(), removeHandler: vi.fn() },
    ipcRenderer: { invoke: vi.fn() },
    contextBridge: { exposeInMainWorld: vi.fn() },
    shell: { openExternal: vi.fn(() => Promise.resolve()) },
    protocol: {
      registerSchemesAsPrivileged: vi.fn(),
      handle: vi.fn(),
    },
    ...overrides,
  };
}
```

- [ ] **Step 6: Create empty `desktop/test/.gitkeep`** so the directory survives the first commit before tests land

```
(empty file)
```

- [ ] **Step 7: Update root `.gitignore`** — append:

```
# Electron desktop app
desktop/node_modules/
desktop/dist-main/
desktop/dist-renderer/
desktop/release/
```

- [ ] **Step 8: Install deps and verify**

Run:
```
cd desktop && npm install
```
Expected: exit 0; `desktop/node_modules/` populated; `desktop/package-lock.json` created.

Run:
```
cd desktop && npm test
```
Expected: exit 0; "No test files found" (zero tests).

- [ ] **Step 9: Commit**

```
git add desktop/ .gitignore
git commit -m "$(cat <<'EOF'
feat(desktop): scaffold desktop/ package skeleton

Empty Electron package — package.json with esbuild/electron-builder
toolchain, vitest config with 100% coverage gate on main + preload,
electron-builder.yml with mac/win/linux targets (unsigned), shared
test helper for mocking electron. No functionality yet; subsequent
tasks land main-process modules under this scaffold.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `app://` protocol handler (`main/protocol.js`)

**Files:**
- Create: `desktop/main/protocol.js`
- Create: `desktop/test/protocol.test.js`

**What this module does:** Registers `app://` as a privileged scheme on Electron startup, then handles `app://<host>/<path>` requests by reading from `dist-renderer/`. Unknown paths that look like SPA routes (no extension) fall through to `index.html` so React Router's `BrowserRouter` works. Path traversal is rejected.

- [ ] **Step 1: Write failing tests** — `desktop/test/protocol.test.js`

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { readFileSync } from "node:fs";
import { mockElectron } from "./_helpers.js";

vi.mock("electron", () => mockElectron());
vi.mock("node:fs", async () => {
  const actual = await vi.importActual("node:fs");
  return { ...actual, readFileSync: vi.fn() };
});

// Import after mocks
const { registerAppProtocol, handleAppRequest } = await import("../main/protocol.js");

const RENDERER_ROOT = "/fake/dist-renderer";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("registerAppProtocol", () => {
  it("registers app:// as a privileged scheme before app is ready", async () => {
    const { protocol } = await import("electron");
    registerAppProtocol(RENDERER_ROOT);
    expect(protocol.registerSchemesAsPrivileged).toHaveBeenCalledWith([
      { scheme: "app", privileges: { standard: true, secure: true, supportFetchAPI: true } },
    ]);
  });

  it("registers an app:// request handler", async () => {
    const { protocol } = await import("electron");
    registerAppProtocol(RENDERER_ROOT);
    expect(protocol.handle).toHaveBeenCalledWith("app", expect.any(Function));
  });
});

describe("handleAppRequest", () => {
  it("serves index.html for app://-/", async () => {
    readFileSync.mockReturnValue(Buffer.from("<html>root</html>"));
    const response = handleAppRequest(new Request("app://-/"), RENDERER_ROOT);
    expect(readFileSync).toHaveBeenCalledWith(`${RENDERER_ROOT}/index.html`);
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toMatch(/html/);
  });

  it("serves named assets verbatim", async () => {
    readFileSync.mockReturnValue(Buffer.from("console.log(1)"));
    const response = handleAppRequest(new Request("app://-/assets/app.js"), RENDERER_ROOT);
    expect(readFileSync).toHaveBeenCalledWith(`${RENDERER_ROOT}/assets/app.js`);
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toMatch(/javascript/);
  });

  it("falls through SPA deep paths to index.html", async () => {
    readFileSync.mockReturnValue(Buffer.from("<html>root</html>"));
    const response = handleAppRequest(new Request("app://-/app/login"), RENDERER_ROOT);
    expect(readFileSync).toHaveBeenCalledWith(`${RENDERER_ROOT}/index.html`);
    expect(response.status).toBe(200);
  });

  it("rejects path-traversal attempts with 403", async () => {
    const response = handleAppRequest(new Request("app://-/../etc/passwd"), RENDERER_ROOT);
    expect(response.status).toBe(403);
    expect(readFileSync).not.toHaveBeenCalled();
  });

  it("returns 404 when a real asset path doesn't exist", async () => {
    readFileSync.mockImplementation(() => { throw new Error("ENOENT"); });
    const response = handleAppRequest(new Request("app://-/assets/missing.png"), RENDERER_ROOT);
    expect(response.status).toBe(404);
  });

  it("falls through extension-less missing paths to index.html (SPA route)", async () => {
    // First call: index.html → ok. We use a single mock that always returns
    // the index payload; the assertion is that the request RESOLVED, not 404.
    readFileSync.mockReturnValue(Buffer.from("<html>root</html>"));
    const response = handleAppRequest(new Request("app://-/something/that/looks/like/a/route"), RENDERER_ROOT);
    expect(response.status).toBe(200);
  });
});
```

- [ ] **Step 2: Run tests, verify they fail**

```
cd desktop && npm test -- protocol.test.js
```
Expected: ALL FAIL with `Cannot find module '../main/protocol.js'`.

- [ ] **Step 3: Implement `desktop/main/protocol.js`**

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { protocol } from "electron";
import { readFileSync } from "node:fs";
import { extname, normalize, posix } from "node:path";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js":   "application/javascript; charset=utf-8",
  ".mjs":  "application/javascript; charset=utf-8",
  ".css":  "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg":  "image/svg+xml",
  ".png":  "image/png",
  ".jpg":  "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif":  "image/gif",
  ".ico":  "image/x-icon",
  ".woff2":"font/woff2",
};

function mimeFor(p) {
  return MIME[extname(p).toLowerCase()] ?? "application/octet-stream";
}

/**
 * Resolve an app:// URL to a path inside renderer root, returning null on
 * traversal attempts. The URL hostname is ignored (use "-" by convention).
 */
function resolveRendererPath(url, rendererRoot) {
  const u = new URL(url);
  const rawPath = decodeURIComponent(u.pathname); // e.g. "/assets/app.js"
  const normalized = posix.normalize(rawPath);
  if (normalized.startsWith("..") || normalized.includes("/../")) return null;
  return rendererRoot + normalized;
}

export function handleAppRequest(request, rendererRoot) {
  const resolved = resolveRendererPath(request.url, rendererRoot);
  if (resolved === null) return new Response("forbidden", { status: 403 });

  // Root request → index.html
  if (resolved === rendererRoot + "/") {
    const body = readFileSync(rendererRoot + "/index.html");
    return new Response(body, { status: 200, headers: { "content-type": "text/html; charset=utf-8" } });
  }

  // Asset request — try to serve verbatim
  if (extname(resolved)) {
    try {
      const body = readFileSync(resolved);
      return new Response(body, { status: 200, headers: { "content-type": mimeFor(resolved) } });
    } catch {
      return new Response("not found", { status: 404 });
    }
  }

  // Extension-less path = SPA route → fall through to index.html
  const body = readFileSync(rendererRoot + "/index.html");
  return new Response(body, { status: 200, headers: { "content-type": "text/html; charset=utf-8" } });
}

export function registerAppProtocol(rendererRoot) {
  protocol.registerSchemesAsPrivileged([
    { scheme: "app", privileges: { standard: true, secure: true, supportFetchAPI: true } },
  ]);
  protocol.handle("app", (request) => handleAppRequest(request, rendererRoot));
}
```

- [ ] **Step 4: Run tests, verify they pass**

```
cd desktop && npm test -- protocol.test.js
```
Expected: all 7 tests PASS.

- [ ] **Step 5: Run coverage, verify 100% on protocol.js**

```
cd desktop && npm run test:coverage -- protocol.test.js
```
Expected: `main/protocol.js` reports 100% lines / functions / branches / statements.

- [ ] **Step 6: Commit**

```
git add desktop/main/protocol.js desktop/test/protocol.test.js
git commit -m "$(cat <<'EOF'
feat(desktop): add app:// custom protocol handler

Serves bundled SPA assets from dist-renderer/, falls through deep paths
to index.html so BrowserRouter works, rejects path traversal with 403,
returns 404 for missing real assets. Privileged scheme registration
must run before app.whenReady; handler runs after.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: BrowserWindow factory (`main/window.js`)

**Files:**
- Create: `desktop/main/window.js`
- Create: `desktop/test/window.test.js`

**What this module does:** Returns a configured `BrowserWindow`. webPreferences enforce the sandboxed posture from the spec (`nodeIntegration: false`, `contextIsolation: true`, `sandbox: true`, preload path). Loads `process.env.VITE_DEV_SERVER_URL` if set, else `app://-/`. Wires `close` → `hide` so the app survives close-button on all platforms (sub-project A; explicit quit lives in the menu, defaults are fine for now).

- [ ] **Step 1: Write failing tests** — `desktop/test/window.test.js`

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockElectron } from "./_helpers.js";

const mocked = mockElectron();
vi.mock("electron", () => mocked);

const { createMainWindow } = await import("../main/window.js");

beforeEach(() => {
  vi.clearAllMocks();
  delete process.env.VITE_DEV_SERVER_URL;
});

describe("createMainWindow", () => {
  it("constructs a BrowserWindow with the secure web preferences", async () => {
    const { BrowserWindow } = await import("electron");
    createMainWindow({ preloadPath: "/preload.js" });
    expect(BrowserWindow).toHaveBeenCalledTimes(1);
    const opts = BrowserWindow.mock.calls[0][0];
    expect(opts.webPreferences).toEqual(
      expect.objectContaining({
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        preload: "/preload.js",
      }),
    );
  });

  it("loads app://-/ in production (no VITE_DEV_SERVER_URL)", async () => {
    const win = createMainWindow({ preloadPath: "/preload.js" });
    expect(win.loadURL).toHaveBeenCalledWith("app://-/");
  });

  it("loads VITE_DEV_SERVER_URL when set", async () => {
    process.env.VITE_DEV_SERVER_URL = "http://localhost:5173";
    const win = createMainWindow({ preloadPath: "/preload.js" });
    expect(win.loadURL).toHaveBeenCalledWith("http://localhost:5173");
  });

  it("intercepts close to hide the window instead of destroying it", async () => {
    const win = createMainWindow({ preloadPath: "/preload.js" });
    const closeListener = win.on.mock.calls.find(([event]) => event === "close")[1];
    const event = { preventDefault: vi.fn() };
    closeListener(event);
    expect(event.preventDefault).toHaveBeenCalled();
    expect(win.hide).toHaveBeenCalled();
  });

  it("allows close when app is quitting", async () => {
    const win = createMainWindow({ preloadPath: "/preload.js", isQuitting: () => true });
    const closeListener = win.on.mock.calls.find(([event]) => event === "close")[1];
    const event = { preventDefault: vi.fn() };
    closeListener(event);
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(win.hide).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests, verify they fail**

```
cd desktop && npm test -- window.test.js
```
Expected: all 5 fail with module-not-found.

- [ ] **Step 3: Implement `desktop/main/window.js`**

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { BrowserWindow } from "electron";

const DEFAULT_DIMENSIONS = { width: 1280, height: 800, minWidth: 720, minHeight: 480 };

export function createMainWindow({ preloadPath, isQuitting = () => false }) {
  const win = new BrowserWindow({
    ...DEFAULT_DIMENSIONS,
    show: true,
    autoHideMenuBar: false,
    webPreferences: {
      preload: preloadPath,
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });

  win.on("close", (event) => {
    if (isQuitting()) return;
    event.preventDefault();
    win.hide();
  });

  const url = process.env.VITE_DEV_SERVER_URL ?? "app://-/";
  win.loadURL(url);

  return win;
}
```

- [ ] **Step 4: Run tests and coverage**

```
cd desktop && npm run test:coverage -- window.test.js
```
Expected: all 5 PASS; 100% coverage on `main/window.js`.

- [ ] **Step 5: Commit**

```
git add desktop/main/window.js desktop/test/window.test.js
git commit -m "$(cat <<'EOF'
feat(desktop): add BrowserWindow factory with sandboxed web preferences

Single 1280x800 window, nodeIntegration: false, contextIsolation: true,
sandbox: true. Loads VITE_DEV_SERVER_URL when set, else app://-/.
close → hide unless isQuitting (sub-project C will wire menu Quit to
flip the flag); for sub-A the dock/menu native defaults handle quit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Preload bridge (`preload/index.js`)

**Files:**
- Create: `desktop/preload/index.js`
- Create: `desktop/test/preload.test.js`

**What this module does:** Runs in a sandboxed context with access to `contextBridge` and `ipcRenderer`. Exposes exactly one global, `window.channelDesktop`, with `{ isDesktop, login, logout, getVersion }`. The renderer uses `if (window.channelDesktop)` to branch on desktop mode.

- [ ] **Step 1: Write failing tests** — `desktop/test/preload.test.js`

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockElectron } from "./_helpers.js";

const electronMock = mockElectron();
vi.mock("electron", () => electronMock);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("preload script", () => {
  it("exposes channelDesktop on window via contextBridge", async () => {
    await import("../preload/index.js");
    const { contextBridge } = await import("electron");
    expect(contextBridge.exposeInMainWorld).toHaveBeenCalledTimes(1);
    const [name, api] = contextBridge.exposeInMainWorld.mock.calls[0];
    expect(name).toBe("channelDesktop");
    expect(api.isDesktop).toBe(true);
    expect(typeof api.login).toBe("function");
    expect(typeof api.logout).toBe("function");
    expect(typeof api.getVersion).toBe("function");
  });

  it("login() invokes ipcRenderer with 'desktop:login'", async () => {
    await import("../preload/index.js");
    const { contextBridge, ipcRenderer } = await import("electron");
    const [, api] = contextBridge.exposeInMainWorld.mock.calls[0];
    ipcRenderer.invoke.mockResolvedValue("a-jwt");
    const result = await api.login();
    expect(ipcRenderer.invoke).toHaveBeenCalledWith("desktop:login");
    expect(result).toBe("a-jwt");
  });

  it("logout() invokes ipcRenderer with 'desktop:logout'", async () => {
    await import("../preload/index.js");
    const { contextBridge, ipcRenderer } = await import("electron");
    const [, api] = contextBridge.exposeInMainWorld.mock.calls[0];
    await api.logout();
    expect(ipcRenderer.invoke).toHaveBeenCalledWith("desktop:logout");
  });

  it("getVersion() invokes ipcRenderer with 'desktop:version'", async () => {
    await import("../preload/index.js");
    const { contextBridge, ipcRenderer } = await import("electron");
    const [, api] = contextBridge.exposeInMainWorld.mock.calls[0];
    ipcRenderer.invoke.mockResolvedValue("1.2.3");
    const v = await api.getVersion();
    expect(ipcRenderer.invoke).toHaveBeenCalledWith("desktop:version");
    expect(v).toBe("1.2.3");
  });
});
```

- [ ] **Step 2: Run tests, verify they fail**

- [ ] **Step 3: Implement `desktop/preload/index.js`**

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("channelDesktop", {
  isDesktop: true,
  login: () => ipcRenderer.invoke("desktop:login"),
  logout: () => ipcRenderer.invoke("desktop:logout"),
  getVersion: () => ipcRenderer.invoke("desktop:version"),
});
```

- [ ] **Step 4: Run tests and coverage**

```
cd desktop && npm run test:coverage -- preload.test.js
```
Expected: 4 PASS; 100% on `preload/index.js`.

- [ ] **Step 5: Commit**

```
git add desktop/preload/index.js desktop/test/preload.test.js
git commit -m "$(cat <<'EOF'
feat(desktop): add preload script exposing window.channelDesktop

Sandboxed bridge: { isDesktop: true, login, logout, getVersion }.
Login/logout/getVersion are thin wrappers over ipcRenderer.invoke.
Renderer detects desktop mode via window.channelDesktop existence.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: IPC channel registry (`main/ipc.js`)

**Files:**
- Create: `desktop/main/ipc.js`
- Create: `desktop/test/ipc.test.js`

**What this module does:** Registers `ipcMain.handle()` for the three desktop channels. Each handler enforces a sender-id guard (only the main window can invoke). The handler implementations are passed in by `main/index.js` (so this module stays orchestration-only).

- [ ] **Step 1: Write failing tests** — `desktop/test/ipc.test.js`

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockElectron } from "./_helpers.js";

vi.mock("electron", () => mockElectron());

const { registerIpc } = await import("../main/ipc.js");

beforeEach(() => {
  vi.clearAllMocks();
});

function makeEvent(senderId) {
  return { sender: { id: senderId } };
}

describe("registerIpc", () => {
  it("registers all three channels on ipcMain", async () => {
    const { ipcMain } = await import("electron");
    registerIpc({
      mainWindowId: 7,
      handlers: { login: vi.fn(), logout: vi.fn(), getVersion: vi.fn() },
    });
    const channels = ipcMain.handle.mock.calls.map(([ch]) => ch);
    expect(channels).toEqual(["desktop:login", "desktop:logout", "desktop:version"]);
  });

  it("forwards login to the provided handler when sender matches", async () => {
    const { ipcMain } = await import("electron");
    const login = vi.fn().mockResolvedValue("the-jwt");
    registerIpc({ mainWindowId: 7, handlers: { login, logout: vi.fn(), getVersion: vi.fn() } });
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:login")[1];
    const result = await handler(makeEvent(7));
    expect(login).toHaveBeenCalled();
    expect(result).toBe("the-jwt");
  });

  it("rejects with SENDER_FORBIDDEN when sender does not match the main window", async () => {
    const { ipcMain } = await import("electron");
    registerIpc({
      mainWindowId: 7,
      handlers: { login: vi.fn(), logout: vi.fn(), getVersion: vi.fn() },
    });
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:login")[1];
    await expect(handler(makeEvent(99))).rejects.toThrow("SENDER_FORBIDDEN");
  });

  it("forwards logout to its handler", async () => {
    const { ipcMain } = await import("electron");
    const logout = vi.fn().mockResolvedValue(undefined);
    registerIpc({ mainWindowId: 7, handlers: { login: vi.fn(), logout, getVersion: vi.fn() } });
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:logout")[1];
    await handler(makeEvent(7));
    expect(logout).toHaveBeenCalled();
  });

  it("forwards getVersion to its handler", async () => {
    const { ipcMain } = await import("electron");
    const getVersion = vi.fn().mockReturnValue("9.9.9");
    registerIpc({ mainWindowId: 7, handlers: { login: vi.fn(), logout: vi.fn(), getVersion } });
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:version")[1];
    expect(await handler(makeEvent(7))).toBe("9.9.9");
  });
});
```

- [ ] **Step 2: Run tests, verify they fail**

- [ ] **Step 3: Implement `desktop/main/ipc.js`**

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { ipcMain } from "electron";

const CHANNELS = ["desktop:login", "desktop:logout", "desktop:version"];

export function registerIpc({ mainWindowId, handlers }) {
  const route = {
    "desktop:login": handlers.login,
    "desktop:logout": handlers.logout,
    "desktop:version": handlers.getVersion,
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

- [ ] **Step 4: Run tests and coverage**

```
cd desktop && npm run test:coverage -- ipc.test.js
```
Expected: 5 PASS; 100% on `main/ipc.js`.

- [ ] **Step 5: Commit**

```
git add desktop/main/ipc.js desktop/test/ipc.test.js
git commit -m "$(cat <<'EOF'
feat(desktop): add IPC channel registry with sender-id guard

Registers desktop:login / desktop:logout / desktop:version. Each
handler rejects invocations from any webContents other than the
main window (sender.id check), so a renegade BrowserView or
DevTools-injected frame can't reach privileged handlers.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: OAuth helpers — port picker + state token (`main/auth.js` part 1)

**Files:**
- Create: `desktop/main/auth.js` (initial — partial)
- Create: `desktop/test/auth.test.js` (initial — covers helpers only; Tasks 7-9 extend)

**What this task adds:** Pure-function helpers: `pickFreePort()` (random port in 49152-65535 with EADDRINUSE retry up to 5×) and `generateState()` (32 random bytes, base64url, no padding). Builds toward the full `login()` orchestration in Task 9.

- [ ] **Step 1: Write failing tests** — `desktop/test/auth.test.js`

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockElectron } from "./_helpers.js";

vi.mock("electron", () => mockElectron());

const { pickFreePort, generateState } = await import("../main/auth.js");

beforeEach(() => {
  vi.clearAllMocks();
});

describe("pickFreePort", () => {
  it("returns a port in the ephemeral range [49152, 65535]", async () => {
    const port = await pickFreePort();
    expect(port).toBeGreaterThanOrEqual(49152);
    expect(port).toBeLessThanOrEqual(65535);
  });

  it("retries on EADDRINUSE up to 5 times then throws PORT_UNAVAILABLE", async () => {
    // Fail by stubbing net.createServer to always emit EADDRINUSE
    vi.resetModules();
    vi.doMock("node:net", () => ({
      default: {
        createServer: () => ({
          unref: () => {},
          listen: function () {
            queueMicrotask(() => this._err({ code: "EADDRINUSE" }));
            return this;
          },
          once: function (event, cb) { if (event === "error") this._err = cb; return this; },
          close: () => {},
        }),
      },
    }));
    const { pickFreePort: picker } = await import("../main/auth.js");
    await expect(picker()).rejects.toThrow("PORT_UNAVAILABLE");
  });
});

describe("generateState", () => {
  it("returns 43-character base64url (32 bytes encoded, no padding)", () => {
    const s = generateState();
    expect(s).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("returns a different value each call", () => {
    expect(generateState()).not.toBe(generateState());
  });
});
```

- [ ] **Step 2: Run tests, verify they fail**

- [ ] **Step 3: Implement initial `desktop/main/auth.js`** — helpers only; the rest is added in Tasks 7-9.

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { randomBytes } from "node:crypto";
import net from "node:net";

const PORT_MIN = 49152;
const PORT_MAX = 65535;
const PORT_RETRIES = 5;

export function generateState() {
  return randomBytes(32).toString("base64url");
}

export function pickFreePort() {
  return new Promise((resolve, reject) => {
    let attempts = 0;

    const tryPort = () => {
      attempts += 1;
      const port = PORT_MIN + Math.floor(Math.random() * (PORT_MAX - PORT_MIN + 1));
      const server = net.createServer();
      server.unref();
      server.once("error", (err) => {
        if (err.code === "EADDRINUSE" && attempts < PORT_RETRIES) {
          tryPort();
        } else {
          reject(new Error("PORT_UNAVAILABLE"));
        }
      });
      server.listen(port, "127.0.0.1", () => {
        server.close(() => resolve(port));
      });
    };

    tryPort();
  });
}
```

- [ ] **Step 4: Run tests and coverage**

```
cd desktop && npm run test:coverage -- auth.test.js
```
Expected: 4 PASS; coverage on the implemented helpers is 100%. (Other auth.js functions don't exist yet — they'll be added in subsequent tasks; coverage on the file as a whole will close out by Task 9.)

- [ ] **Step 5: Commit**

```
git add desktop/main/auth.js desktop/test/auth.test.js
git commit -m "$(cat <<'EOF'
feat(desktop): add port picker + state token helpers for OAuth loopback

pickFreePort() selects a port in the IANA ephemeral range with retry on
EADDRINUSE (up to 5x), then PORT_UNAVAILABLE. generateState() returns
32 random bytes base64url-encoded. Both feed the loopback server added
in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: OAuth loopback server + success-path /callback (`main/auth.js` part 2)

**Files:**
- Modify: `desktop/main/auth.js`
- Modify: `desktop/test/auth.test.js`

**What this task adds:** `startLoopback({ state, onResult })` — starts an `http.Server` on `127.0.0.1:<port>` with a single route `GET /callback`. On `?token=<jwt>&state=<S>` with matching `S`, resolves `onResult({ ok: true, token })` and responds with a small static HTML "you can close this window" page. The success path only.

- [ ] **Step 1: Append failing tests to `desktop/test/auth.test.js`**

```js
import http from "node:http";

const { startLoopback } = await import("../main/auth.js");

async function getHtml(port, path) {
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port, path }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString() }));
    }).on("error", reject);
  });
}

describe("startLoopback — success path", () => {
  it("resolves onResult with the token when state matches", async () => {
    const state = generateState();
    let resolved;
    const onResult = vi.fn((v) => { resolved = v; });
    const { port, close } = await startLoopback({ state, onResult });
    const res = await getHtml(port, `/callback?token=THE_JWT&state=${state}`);
    expect(res.status).toBe(200);
    expect(res.body).toMatch(/close this window/i);
    expect(onResult).toHaveBeenCalledWith({ ok: true, token: "THE_JWT" });
    await close();
  });

  it("close() is idempotent", async () => {
    const { close } = await startLoopback({ state: "x", onResult: vi.fn() });
    await close();
    await close();   // no throw
  });
});
```

- [ ] **Step 2: Run tests, verify they fail** (these new ones do; previous ones still pass)

- [ ] **Step 3: Extend `desktop/main/auth.js`** — append exports:

```js
import http from "node:http";
import { timingSafeEqual } from "node:crypto";

const CLOSE_PAGE_HTML = `<!doctype html><meta charset="utf-8"><title>Signed in</title>
<style>body{font:14px system-ui;margin:40px;color:#222}</style>
<h2>You can close this window.</h2>
<p>Channel Desktop is signed in.</p>`;

function statesEqual(a, b) {
  const A = Buffer.from(a);
  const B = Buffer.from(b);
  if (A.length !== B.length) return false;
  return timingSafeEqual(A, B);
}

export function startLoopback({ state, onResult }) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (url.pathname !== "/callback") {
        res.writeHead(404); return res.end("not found");
      }
      const got = url.searchParams.get("state") ?? "";
      const tok = url.searchParams.get("token") ?? "";
      if (!statesEqual(got, state)) {
        res.writeHead(400); res.end("state mismatch");
        onResult({ ok: false, code: "STATE_MISMATCH" });
        return;
      }
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      res.end(CLOSE_PAGE_HTML);
      onResult({ ok: true, token: tok });
    });

    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      let closed = false;
      const close = () => new Promise((res) => {
        if (closed) return res();
        closed = true;
        server.close(() => res());
      });
      resolve({ port, close });
    });
    server.on("error", reject);
  });
}
```

Note: the `port` parameter is taken via `server.listen(0, ...)` — the kernel picks a free port. `pickFreePort()` is for cases where a *specific* port is required (registered with Google in sub-project B); for the dynamic loopback, kernel-assigned is fine. Update the spec's mental model accordingly — `pickFreePort` stays in the module for future use but isn't called by `startLoopback`. (This is a deliberate decision; revisit in Task 9 if it becomes inconsistent.)

Update Task 6's port picker test to remain green — it doesn't need touching since `pickFreePort` is still exported.

- [ ] **Step 4: Run tests, verify all pass**

```
cd desktop && npm test -- auth.test.js
```
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```
git add desktop/main/auth.js desktop/test/auth.test.js
git commit -m "$(cat <<'EOF'
feat(desktop): add loopback OAuth server with success-path /callback

startLoopback({state, onResult}) listens on 127.0.0.1:<kernel-assigned
port>, accepts GET /callback?token=&state=, validates state with a
timing-safe compare, resolves onResult({ok:true, token}) on match, and
responds with a 'you can close this window' HTML page. Error paths
are added in Task 8.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: OAuth loopback — error paths (`main/auth.js` part 3)

**Files:**
- Modify: `desktop/main/auth.js`
- Modify: `desktop/test/auth.test.js`

**What this task adds:** `?error=access_denied` → `onResult({ok:false, code:"USER_CANCELLED"})`. Missing params → `INVALID_CALLBACK`. State mismatch (already partly there) gets its own assertion. Unknown path → 404.

- [ ] **Step 1: Append failing tests**

```js
describe("startLoopback — error paths", () => {
  it("USER_CANCELLED on ?error=access_denied", async () => {
    const onResult = vi.fn();
    const { port, close } = await startLoopback({ state: "S", onResult });
    const res = await getHtml(port, `/callback?error=access_denied&state=S`);
    expect(res.status).toBe(400);
    expect(onResult).toHaveBeenCalledWith({ ok: false, code: "USER_CANCELLED" });
    await close();
  });

  it("STATE_MISMATCH when state differs", async () => {
    const onResult = vi.fn();
    const { port, close } = await startLoopback({ state: "right", onResult });
    const res = await getHtml(port, `/callback?token=t&state=wrong`);
    expect(res.status).toBe(400);
    expect(onResult).toHaveBeenCalledWith({ ok: false, code: "STATE_MISMATCH" });
    await close();
  });

  it("INVALID_CALLBACK when token+error both missing", async () => {
    const onResult = vi.fn();
    const { port, close } = await startLoopback({ state: "S", onResult });
    const res = await getHtml(port, `/callback?state=S`);
    expect(res.status).toBe(400);
    expect(onResult).toHaveBeenCalledWith({ ok: false, code: "INVALID_CALLBACK" });
    await close();
  });

  it("404 on unknown path", async () => {
    const { port, close } = await startLoopback({ state: "S", onResult: vi.fn() });
    const res = await getHtml(port, `/wat`);
    expect(res.status).toBe(404);
    await close();
  });
});
```

- [ ] **Step 2: Run tests, verify they fail**

- [ ] **Step 3: Extend the request handler in `desktop/main/auth.js`** — replace the existing handler body inside `startLoopback`:

```js
const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (url.pathname !== "/callback") {
    res.writeHead(404); return res.end("not found");
  }
  const err = url.searchParams.get("error");
  if (err === "access_denied") {
    res.writeHead(400); res.end("cancelled");
    return onResult({ ok: false, code: "USER_CANCELLED" });
  }
  const got = url.searchParams.get("state") ?? "";
  const tok = url.searchParams.get("token");
  if (!statesEqual(got, state)) {
    res.writeHead(400); res.end("state mismatch");
    return onResult({ ok: false, code: "STATE_MISMATCH" });
  }
  if (!tok) {
    res.writeHead(400); res.end("missing token");
    return onResult({ ok: false, code: "INVALID_CALLBACK" });
  }
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  res.end(CLOSE_PAGE_HTML);
  onResult({ ok: true, token: tok });
});
```

- [ ] **Step 4: Run tests, verify all pass**

```
cd desktop && npm test -- auth.test.js
```
Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```
git add desktop/main/auth.js desktop/test/auth.test.js
git commit -m "$(cat <<'EOF'
feat(desktop): add OAuth loopback error paths

USER_CANCELLED on ?error=access_denied (user clicked Cancel at
Google), STATE_MISMATCH on diverging state (replay / tamper),
INVALID_CALLBACK on missing token, 404 on any non-/callback path.
Each error returns 400 to the browser and resolves onResult with a
code the renderer can render a sensible message for.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: `login()` orchestration — timeout, single-flight, before-quit (`main/auth.js` part 4)

**Files:**
- Modify: `desktop/main/auth.js`
- Modify: `desktop/test/auth.test.js`

**What this task adds:** The top-level `login({ authBaseUrl, openExternal, app, timeoutMs })` that ties helpers + loopback + shell-open + 60s timeout + double-call coalescing + `before-quit` cleanup into one promise that resolves with a JWT or rejects with a code.

- [ ] **Step 1: Append failing tests**

```js
describe("login()", () => {
  it("opens external browser at /auth/login with state + desktop_callback", async () => {
    const openExternal = vi.fn().mockResolvedValue(undefined);
    const appOnce = vi.fn();
    let captureOnResult;
    const fakeStartLoopback = vi.fn(({ state, onResult }) => {
      captureOnResult = onResult;
      return Promise.resolve({ port: 51234, close: vi.fn().mockResolvedValue() });
    });

    const { loginWithDeps } = await import("../main/auth.js");
    const promise = loginWithDeps({
      authBaseUrl: "https://example.test",
      openExternal,
      onAppQuit: appOnce,
      startLoopback: fakeStartLoopback,
      timeoutMs: 1000,
    });
    // Resolve from the loopback side
    queueMicrotask(() => captureOnResult({ ok: true, token: "JWT123" }));
    expect(await promise).toBe("JWT123");

    expect(openExternal).toHaveBeenCalledTimes(1);
    const url = new URL(openExternal.mock.calls[0][0]);
    expect(url.origin).toBe("https://example.test");
    expect(url.pathname).toBe("/auth/login");
    expect(url.searchParams.get("desktop_callback")).toBe("http://127.0.0.1:51234/callback");
    expect(url.searchParams.get("state")).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("rejects with TIMEOUT after timeoutMs with no callback", async () => {
    const { loginWithDeps } = await import("../main/auth.js");
    await expect(loginWithDeps({
      authBaseUrl: "https://example.test",
      openExternal: vi.fn().mockResolvedValue(),
      onAppQuit: vi.fn(),
      startLoopback: () => Promise.resolve({ port: 1, close: vi.fn().mockResolvedValue() }),
      timeoutMs: 10,
    })).rejects.toThrow("TIMEOUT");
  });

  it("rejects with USER_CANCELLED when loopback signals access_denied", async () => {
    let captureOnResult;
    const { loginWithDeps } = await import("../main/auth.js");
    const promise = loginWithDeps({
      authBaseUrl: "https://example.test",
      openExternal: vi.fn().mockResolvedValue(),
      onAppQuit: vi.fn(),
      startLoopback: ({ onResult }) => { captureOnResult = onResult; return Promise.resolve({ port: 1, close: vi.fn().mockResolvedValue() }); },
      timeoutMs: 1000,
    });
    queueMicrotask(() => captureOnResult({ ok: false, code: "USER_CANCELLED" }));
    await expect(promise).rejects.toThrow("USER_CANCELLED");
  });

  it("coalesces concurrent login() calls into one in-flight promise", async () => {
    let captureOnResult;
    const startLoopback = vi.fn(({ onResult }) => {
      captureOnResult = onResult;
      return Promise.resolve({ port: 1, close: vi.fn().mockResolvedValue() });
    });
    const { loginWithDeps } = await import("../main/auth.js");
    const deps = {
      authBaseUrl: "https://example.test",
      openExternal: vi.fn().mockResolvedValue(),
      onAppQuit: vi.fn(),
      startLoopback,
      timeoutMs: 1000,
    };
    const p1 = loginWithDeps(deps);
    const p2 = loginWithDeps(deps);
    queueMicrotask(() => captureOnResult({ ok: true, token: "T" }));
    const [a, b] = await Promise.all([p1, p2]);
    expect(a).toBe("T");
    expect(b).toBe("T");
    expect(startLoopback).toHaveBeenCalledTimes(1);
  });

  it("closes the loopback on app before-quit", async () => {
    const close = vi.fn().mockResolvedValue();
    let beforeQuitListener;
    const { loginWithDeps } = await import("../main/auth.js");
    void loginWithDeps({
      authBaseUrl: "https://example.test",
      openExternal: vi.fn().mockResolvedValue(),
      onAppQuit: (cb) => { beforeQuitListener = cb; },
      startLoopback: () => Promise.resolve({ port: 1, close }),
      timeoutMs: 60000,
    });
    await Promise.resolve(); // let openExternal fire
    beforeQuitListener();
    await Promise.resolve();
    expect(close).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests, verify they fail**

- [ ] **Step 3: Extend `desktop/main/auth.js`** — append:

```js
let inFlight = null;

export function loginWithDeps({ authBaseUrl, openExternal, onAppQuit, startLoopback: startFn = startLoopback, timeoutMs = 60_000 }) {
  if (inFlight) return inFlight;

  const state = generateState();

  inFlight = new Promise((resolve, reject) => {
    let server;
    let timer;
    let settled = false;

    const settle = (fn, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (server) server.close();
      inFlight = null;
      fn(value);
    };

    startFn({
      state,
      onResult: (r) => {
        if (r.ok) settle(resolve, r.token);
        else settle(reject, new Error(r.code));
      },
    }).then((s) => {
      server = s;
      onAppQuit(() => { if (server) server.close(); });
      const url = new URL("/auth/login", authBaseUrl);
      url.searchParams.set("desktop_callback", `http://127.0.0.1:${server.port}/callback`);
      url.searchParams.set("state", state);
      openExternal(url.toString());
      timer = setTimeout(() => settle(reject, new Error("TIMEOUT")), timeoutMs);
    }).catch((e) => settle(reject, e));
  });

  return inFlight;
}

// Convenience export that wires the real electron deps.
export async function login({ authBaseUrl }) {
  const { shell, app } = await import("electron");
  return loginWithDeps({
    authBaseUrl,
    openExternal: (u) => shell.openExternal(u),
    onAppQuit: (cb) => app.on("before-quit", cb),
  });
}
```

- [ ] **Step 4: Run tests, verify all pass**

```
cd desktop && npm run test:coverage
```
Expected: every test in `desktop/test/auth.test.js` PASS; **100% coverage on `main/auth.js` and on all other modules under `main/` and `preload/`**.

- [ ] **Step 5: Commit**

```
git add desktop/main/auth.js desktop/test/auth.test.js
git commit -m "$(cat <<'EOF'
feat(desktop): wire OAuth login orchestration

loginWithDeps() ties helpers + loopback + shell.openExternal + 60s
timeout + single-flight coalescing + before-quit cleanup into one
promise. login() is the production wrapper that injects the real
electron deps; loginWithDeps() is the test seam.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Entry point + smoke harness (`main/index.js` + `test/smoke.js`)

**Files:**
- Create: `desktop/main/index.js` (excluded from coverage by the vitest config)
- Create: `desktop/test/smoke.js` (not a vitest test; standalone Node script)
- Modify: `desktop/package.json` — add `smoke` script

**What this task adds:** Module-wiring entry point + the post-build smoke harness referenced in the spec. Entry point also handles `--smoke` CLI flag by closing on `did-finish-load`, so the smoke script can spawn the packaged binary and exit-code-check it without a headless framework.

- [ ] **Step 1: Implement `desktop/main/index.js`**

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { app } from "electron";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { registerAppProtocol } from "./protocol.js";
import { createMainWindow } from "./window.js";
import { registerIpc } from "./ipc.js";
import { login } from "./auth.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const RENDERER_ROOT = resolve(__dirname, "../dist-renderer");
const PRELOAD = resolve(__dirname, "./preload.js"); // esbuild bundles preload/index.js → dist-main/preload.js
const IS_SMOKE = process.argv.includes("--smoke");
const AUTH_BASE = process.env.CHANNEL_API_BASE ?? "https://channel.warlordofmars.net";

let isQuitting = false;
app.on("before-quit", () => { isQuitting = true; });

registerAppProtocol(RENDERER_ROOT);

app.whenReady().then(() => {
  const win = createMainWindow({ preloadPath: PRELOAD, isQuitting: () => isQuitting });

  registerIpc({
    mainWindowId: win.webContents.id,
    handlers: {
      login: () => login({ authBaseUrl: AUTH_BASE }),
      logout: () => {},                          // renderer clears localStorage itself
      getVersion: () => app.getVersion(),
    },
  });

  if (IS_SMOKE) {
    win.webContents.on("did-finish-load", () => {
      // small delay so the app actually paints before we kill it
      setTimeout(() => app.quit(), 250);
    });
  }
});

app.on("window-all-closed", () => {
  // sub-project A: quit when window closes on non-macOS; on macOS
  // the dock keeps the process alive (Electron default).
  if (process.platform !== "darwin") app.quit();
});
```

- [ ] **Step 2: Create `desktop/test/smoke.js`** — spawns the packaged binary, waits for exit

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const bin = process.env.CHANNEL_DESKTOP_BIN;
if (!bin || !existsSync(bin)) {
  console.error(`smoke: CHANNEL_DESKTOP_BIN not set or missing: ${bin}`);
  process.exit(2);
}

const proc = spawn(bin, ["--smoke"], { stdio: ["ignore", "inherit", "inherit"] });
const timer = setTimeout(() => {
  console.error("smoke: timed out after 30s");
  proc.kill("SIGKILL");
  process.exit(3);
}, 30_000);

proc.on("exit", (code) => {
  clearTimeout(timer);
  console.log(`smoke: app exited with code ${code}`);
  process.exit(code === 0 ? 0 : 1);
});
```

- [ ] **Step 3: Add `smoke` script to `desktop/package.json`** — under `scripts`:

```json
"smoke": "node test/smoke.js"
```

- [ ] **Step 4: Build the desktop app locally and run the smoke harness**

Run:
```
cd ui && npm install && VITE_API_BASE=https://channel.warlordofmars.net npm run build
cd ../desktop && npm install && npm run build:current
```
This produces an unpacked Electron build under `desktop/release/<platform>-unpacked/`. Locate the executable path for your host platform (macOS: `desktop/release/mac-arm64/Channel.app/Contents/MacOS/Channel`; Linux: `desktop/release/linux-unpacked/channel`; Windows: `desktop/release/win-unpacked/Channel.exe`).

Run:
```
CHANNEL_DESKTOP_BIN=<path-from-above> npm run smoke
```
Expected: process logs `smoke: app exited with code 0` and exits 0.

- [ ] **Step 5: Commit**

```
git add desktop/main/index.js desktop/test/smoke.js desktop/package.json
git commit -m "$(cat <<'EOF'
feat(desktop): add main entry point + --smoke harness

main/index.js wires protocol/window/ipc/auth into the Electron app
lifecycle. CHANNEL_API_BASE overrides the OAuth base URL for dev.
--smoke flag closes the app after the first did-finish-load so a
post-build smoke step can exit-code-check the packaged binary
without a headless test framework.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Server-side — `desktop_callback` validation in `/auth/login`

**Files:**
- Modify: `src/channel/auth/mgmt_auth.py`
- Modify: `tests/unit/test_mgmt_auth.py` (assume the file exists; otherwise extend the closest unit test for `mgmt_auth`)

**What this task adds:** `/auth/login` accepts optional `desktop_callback` + caller-supplied `state` query parameters. When both present, the caller's `state` is reused (instead of generating one) as both the DynamoDB key and the Google CSRF nonce — so the loopback can verify the same `state` later. `desktop_callback` is validated for loopback-only origins. Validation failures return 400 with no state written.

- [ ] **Step 1: Write failing tests** — append to `tests/unit/test_mgmt_auth.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
import pytest
from fastapi.testclient import TestClient

from channel.api.main import app

client = TestClient(app)

DESKTOP_CALLBACK_OK = "http://127.0.0.1:54321/callback"
VALID_STATE = "A" * 43  # 43 base64url chars


@pytest.mark.parametrize("bad_callback", [
    "https://evil.example.com/callback",       # external host
    "https://127.0.0.1:54321/callback",        # https not allowed (cert pinning hassle for loopback)
    "http://127.0.0.1:54321/other",            # wrong path
    "http://127.0.0.1/callback",               # missing port
    "http://0.0.0.0:54321/callback",           # not loopback
    "http://[::1]:54321/callback",             # IPv6 loopback rejected for sub-A simplicity
    "http://127.0.0.1:54321/callback?foo=bar", # query injection
    "http://127.0.0.1:54321/callback#frag",    # fragment injection
])
def test_desktop_callback_rejects_bad_urls(bad_callback, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    resp = client.get("/auth/login", params={"desktop_callback": bad_callback, "state": VALID_STATE}, follow_redirects=False)
    assert resp.status_code == 400


def test_desktop_callback_rejects_short_state(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    resp = client.get("/auth/login", params={"desktop_callback": DESKTOP_CALLBACK_OK, "state": "too-short"}, follow_redirects=False)
    assert resp.status_code == 400


def test_desktop_callback_happy_path(monkeypatch):
    # Mock state_store.put_state so we don't need DynamoDB
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    captured = {}
    def fake_put(state, payload=None, ttl_seconds=None):
        captured["state"] = state; captured["payload"] = payload
    monkeypatch.setattr("channel.auth.state_store.put_state", fake_put)

    resp = client.get(
        "/auth/login",
        params={"desktop_callback": DESKTOP_CALLBACK_OK, "state": VALID_STATE},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert captured["state"] == VALID_STATE
    assert captured["payload"]["desktop_callback"] == DESKTOP_CALLBACK_OK
    assert "accounts.google.com" in resp.headers["location"]
    # Caller-supplied state is reused as Google's state parameter
    assert f"state={VALID_STATE}" in resp.headers["location"]
```

- [ ] **Step 2: Run, verify failure**

```
uv run pytest tests/unit/test_mgmt_auth.py -k "desktop_callback" -v
```
Expected: failures (the new params aren't implemented yet).

- [ ] **Step 3: Implement validation + state passthrough in `src/channel/auth/mgmt_auth.py`**

Add at module level:

```python
import re
from urllib.parse import urlparse

_DESKTOP_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}


def _validate_desktop_callback(callback: str) -> str | None:
    """Return the callback if it's a safe loopback /callback URL, else None."""
    try:
        u = urlparse(callback)
    except Exception:
        return None
    if u.scheme != "http": return None
    if u.hostname not in _LOOPBACK_HOSTS: return None
    if not u.port: return None
    if u.path != "/callback": return None
    if u.query or u.fragment: return None
    return callback
```

Modify `mgmt_login` to accept and route the new params:

```python
@router.get("/auth/login", include_in_schema=False)
async def mgmt_login(request: Request) -> RedirectResponse:
    test_email = request.query_params.get("test_email")
    if _BYPASS and test_email:
        user = _make_user(test_email, test_email.split("@")[0])
        token = issue_mgmt_jwt(user)
        return _html_redirect(token)  # type: ignore[return-value]

    desktop_callback = request.query_params.get("desktop_callback")
    caller_state = request.query_params.get("state")
    payload: dict[str, Any] = {}

    if desktop_callback is not None:
        validated = _validate_desktop_callback(desktop_callback)
        if validated is None or not caller_state or not _DESKTOP_STATE_RE.match(caller_state):
            raise HTTPException(status_code=400, detail="Invalid desktop_callback or state")
        state = caller_state
        payload["desktop_callback"] = validated
    else:
        state = secrets.token_urlsafe(32)

    state_store.put_state(state, payload=payload or None, ttl_seconds=_STATE_TTL_SECONDS)
    url = google_authorization_url(state, _mgmt_callback_uri())
    return RedirectResponse(url, status_code=302)
```

(Drop `_create_pending_state` if it now has no other callers, or leave it for backward-compatibility — verify with `grep`.)

- [ ] **Step 4: Run tests, verify pass**

```
uv run pytest tests/unit/test_mgmt_auth.py -k "desktop_callback" -v
```
Expected: all 10 PASS (8 negative, 1 short-state, 1 happy path).

- [ ] **Step 5: Run the full server unit suite to confirm no regressions**

```
uv run inv test-unit
```
Expected: all green.

- [ ] **Step 6: Commit**

```
git add src/channel/auth/mgmt_auth.py tests/unit/test_mgmt_auth.py
git commit -m "$(cat <<'EOF'
feat(auth): accept desktop_callback + caller state on /auth/login

When the Electron desktop app starts an OAuth flow it passes its own
random state and a loopback callback URL. The validator enforces
loopback-only (http://127.0.0.1|localhost:<port>/callback, no query or
fragment) so the param can't be turned into an open redirect. When
desktop_callback is set the caller's state is reused as both the
DynamoDB key and Google's CSRF nonce, so the loopback can verify the
same value on return.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Server-side — `/auth/callback` redirects to loopback when desktop_callback present

**Files:**
- Modify: `src/channel/auth/mgmt_auth.py`
- Modify: `tests/unit/test_mgmt_auth.py`
- Modify or create: `tests/integration/test_mgmt_auth_desktop.py`

**What this task adds:** `/auth/callback` reads the consumed state's payload. If it carries `desktop_callback`, redirects to that URL with `?token=<jwt>&state=<state>`. Web flow is unchanged. Integration test runs against DynamoDB Local with a stubbed Google token verifier.

- [ ] **Step 1: Write failing unit test** — append:

```python
def test_callback_redirects_to_desktop_callback_when_set(monkeypatch):
    """When the state record carries desktop_callback, redirect to the loopback URL."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    # State has a desktop_callback; pretend we already consumed it
    state = "B" * 43
    desktop_callback = "http://127.0.0.1:54321/callback"
    monkeypatch.setattr(
        "channel.auth.state_store.consume_state",
        lambda s: {"PK": f"MGMT_STATE#{s}", "SK": "META", "desktop_callback": desktop_callback},
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.exchange_google_code", _async_return("id_token"))
    monkeypatch.setattr(
        "channel.auth.mgmt_auth.verify_google_id_token",
        _async_return({"email": "user@example.com", "email_verified": True, "name": "User"}),
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.is_email_allowed", lambda e: True)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda e: False)

    resp = client.get("/auth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("http://127.0.0.1:54321/callback?")
    assert f"state={state}" in location
    assert "token=" in location


def _async_return(value):
    async def f(*a, **kw): return value
    return f
```

Add a complementary "no regression for web flow" assertion if not already present:

```python
def test_callback_still_returns_html_redirect_when_no_desktop_callback(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    state = "C" * 43
    monkeypatch.setattr(
        "channel.auth.state_store.consume_state",
        lambda s: {"PK": f"MGMT_STATE#{s}", "SK": "META"},  # no desktop_callback
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.exchange_google_code", _async_return("id_token"))
    monkeypatch.setattr(
        "channel.auth.mgmt_auth.verify_google_id_token",
        _async_return({"email": "user@example.com", "email_verified": True, "name": "User"}),
    )
    monkeypatch.setattr("channel.auth.mgmt_auth.is_email_allowed", lambda e: True)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda e: False)

    resp = client.get("/auth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert resp.status_code == 200
    assert b"localStorage.setItem" in resp.content
```

- [ ] **Step 2: Run, verify failure**

- [ ] **Step 3: Modify `mgmt_callback` in `src/channel/auth/mgmt_auth.py`**

Replace `_consume_pending_state` usage in `mgmt_callback` with a direct call to `state_store.consume_state` so the payload is available:

```python
@router.get("/auth/callback", include_in_schema=False, responses={400: {"description": "Invalid Google OAuth callback"}})
async def mgmt_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")

    record = state_store.consume_state(state)
    if record is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    try:
        id_token = await exchange_google_code(code, _mgmt_callback_uri())
        claims = await verify_google_id_token(id_token)
    except Exception as exc:
        logger.warning("Google token exchange failed: %s", exc)
        raise HTTPException(status_code=400, detail="Failed to verify Google identity") from exc

    if not claims.get("email_verified"):
        raise HTTPException(status_code=400, detail="Google email is not verified")

    email: str = claims["email"]
    if not is_email_allowed(email):
        logger.warning("Management login rejected — email not in allowlist: %s", email)
        raise HTTPException(status_code=403, detail="Email not authorised")

    display_name: str = claims.get("name", email.split("@")[0])
    user = _make_user(email, display_name)
    token = issue_mgmt_jwt(user)
    logger.info("Management login: %s (role=%s)", email, user["role"])

    desktop_callback = record.get("desktop_callback")
    if desktop_callback:
        from urllib.parse import urlencode
        qs = urlencode({"token": token, "state": state})
        return RedirectResponse(f"{desktop_callback}?{qs}", status_code=302)

    return _html_redirect(token)
```

- [ ] **Step 4: Add integration test** — `tests/integration/test_mgmt_auth_desktop.py`

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Integration test for the desktop OAuth callback redirect, against DynamoDB Local."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from channel.api.main import app
from channel.auth import state_store


@pytest.fixture
def state_record(monkeypatch):
    """Put a real state record into DynamoDB Local with desktop_callback set."""
    state = "D" * 43
    state_store.put_state(state, payload={"desktop_callback": "http://127.0.0.1:50000/callback"}, ttl_seconds=600)
    return state


def test_callback_redirects_to_loopback_against_real_dynamo(state_record, monkeypatch):
    """End-to-end: state record in DynamoDB Local + stubbed Google → loopback redirect."""
    async def fake_exchange(_code, _redirect): return "id_token"
    async def fake_verify(_id_token): return {
        "email": "user@example.com",
        "email_verified": True,
        "name": "User",
    }
    monkeypatch.setattr("channel.auth.mgmt_auth.exchange_google_code", fake_exchange)
    monkeypatch.setattr("channel.auth.mgmt_auth.verify_google_id_token", fake_verify)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_email_allowed", lambda e: True)
    monkeypatch.setattr("channel.auth.mgmt_auth.is_admin_email", lambda e: False)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")

    client = TestClient(app)
    resp = client.get("/auth/callback", params={"code": "c", "state": state_record}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("http://127.0.0.1:50000/callback?")
    assert f"state={state_record}" in resp.headers["location"]
```

- [ ] **Step 5: Run, verify all pass**

```
uv run inv test-unit && uv run inv test-integration
```
Expected: all green.

- [ ] **Step 6: Commit**

```
git add src/channel/auth/mgmt_auth.py tests/unit/test_mgmt_auth.py tests/integration/test_mgmt_auth_desktop.py
git commit -m "$(cat <<'EOF'
feat(auth): redirect to desktop loopback when state carries it

/auth/callback now consumes the full state record (not just a bool)
and, if it has a desktop_callback, redirects there with ?token=&state=
instead of returning the HTML-redirect page. Web flow unchanged.
Integration test against DynamoDB Local proves the round-trip.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Renderer — `Login.jsx` desktop-mode branch

**Files:**
- Modify: `ui/src/app/Login.jsx`
- Modify: `ui/src/app/Login.test.jsx`

**What this task adds:** The login page detects `window.channelDesktop` and renders a different button that calls `window.channelDesktop.login()` directly (instead of the SPA's redirect-flow). On success, stores the returned JWT in `localStorage` under the existing `starter_mgmt_token` key and navigates to `/app`. On failure, surfaces the error code.

- [ ] **Step 1: Read the existing `Login.jsx` to understand the surface**

Run:
```
cat ui/src/app/Login.jsx ui/src/app/Login.test.jsx
```

Note the existing structure (login button, `?test_email=` bypass handling, error surfaces). The desktop branch should mirror the existing visual treatment.

- [ ] **Step 2: Write failing tests** — append to `ui/src/app/Login.test.jsx`:

```js
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Login from "./Login.jsx";

function withDesktop(api) {
  beforeEach(() => { window.channelDesktop = api; });
  afterEach(() => { delete window.channelDesktop; });
}

describe("Login (desktop mode)", () => {
  withDesktop({
    isDesktop: true,
    login: vi.fn().mockResolvedValue("THE_JWT"),
  });

  it("renders the desktop-mode CTA when window.channelDesktop is present", () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    expect(screen.getByRole("button", { name: /sign in with google/i })).toBeInTheDocument();
  });

  it("calls window.channelDesktop.login() and stores the JWT on click", async () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /sign in with google/i }));
    expect(window.channelDesktop.login).toHaveBeenCalled();
    expect(localStorage.getItem("starter_mgmt_token")).toBe("THE_JWT");
  });
});

describe("Login (desktop mode — error)", () => {
  withDesktop({
    isDesktop: true,
    login: vi.fn().mockRejectedValue(new Error("USER_CANCELLED")),
  });

  it("surfaces an error message when login rejects", async () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /sign in with google/i }));
    expect(await screen.findByText(/cancelled/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run, verify failure**

```
cd ui && npm test -- Login.test.jsx
```
Expected: the new desktop-mode tests fail; pre-existing tests pass.

- [ ] **Step 4: Modify `ui/src/app/Login.jsx`**

Find the existing button-click handler. Wrap it with a desktop branch:

```jsx
const desktop = typeof window !== "undefined" ? window.channelDesktop : undefined;

async function handleDesktopLogin() {
  setError(null);
  try {
    const token = await desktop.login();
    localStorage.setItem("starter_mgmt_token", token);
    navigate("/app");
  } catch (err) {
    setError(err?.message === "USER_CANCELLED" ? "Login cancelled." : "Login failed.");
  }
}

// ... in the JSX, replace the existing primary button with:
{desktop?.isDesktop ? (
  <button type="button" onClick={handleDesktopLogin}>Sign in with Google</button>
) : (
  /* existing web flow button — unchanged */
)}
```

Keep the existing imports (`useState` / `useNavigate` / etc.); add any missing ones.

- [ ] **Step 5: Run tests, verify all pass**

```
cd ui && npm run test:coverage -- Login.test.jsx
```
Expected: all PASS; coverage on `Login.jsx` is 100%.

- [ ] **Step 6: Verify full UI suite still passes**

```
cd ui && npm test
```
Expected: green.

- [ ] **Step 7: Commit**

```
git add ui/src/app/Login.jsx ui/src/app/Login.test.jsx
git commit -m "$(cat <<'EOF'
feat(ui): branch Login.jsx on window.channelDesktop for desktop flow

When the renderer is hosted in the Electron app, the preload exposes
window.channelDesktop and the login page calls channelDesktop.login()
directly instead of redirecting to /auth/login. On success the JWT
is stored in localStorage under the existing key; on USER_CANCELLED
the page surfaces a friendly 'cancelled' message.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: `tasks.py` — `desktop-test`, `desktop-build`, `desktop-dev`, pre-push integration

**Files:**
- Modify: `tasks.py`

**What this task adds:** Four new invoke tasks and a `pre-push` extension. No new test coverage (`tasks.py` itself isn't unit-tested — invoke tasks are user-facing CLI shims).

- [ ] **Step 1: Read the existing `tasks.py` structure to follow conventions**

Run:
```
grep -n "^def \|^@task" tasks.py
```

Note the existing helpers and the `pre-push` task definition.

- [ ] **Step 2: Add the new tasks** — append before any `if __name__ == "__main__":` section, or in the natural alphabetical / logical grouping (mirror existing style):

```python
DESKTOP = ROOT / "desktop"


@task
def desktop_test(ctx, coverage=False):
    """Run desktop main-process unit tests (100% coverage gate when --coverage)."""
    cmd = "npm run test:coverage" if coverage else "npm test"
    ctx.run(f"cd {DESKTOP} && {cmd}", pty=True)


@task(help={"platform": "mac | win | linux | current", "api_base": "VITE_API_BASE for the SPA build"})
def desktop_build(ctx, platform="current", api_base="https://channel.warlordofmars.net"):
    """Build the Electron desktop app for the named platform."""
    ctx.run(f"cd {UI} && VITE_API_BASE={api_base} npm run build", pty=True)
    flag = "" if platform == "current" else f"--{platform}"
    ctx.run(f"cd {DESKTOP} && npm run build:main && npm run build:renderer && npx electron-builder {flag}", pty=True)


@task
def desktop_dev(ctx):
    """Start FastAPI + Vite + Electron pointing at localhost (Ctrl-C tears down all three)."""
    # Reuse the existing inv dev orchestration — it already supervises
    # DynamoDB Local + FastAPI + Vite — and add Electron in a sibling
    # process. The simplest reliable approach is to call inv dev in
    # one shell and electron in another; we wire a tiny wrapper here.
    env = os.environ.copy()
    env["VITE_DEV_SERVER_URL"] = f"http://localhost:{UI_PORT}"
    env["CHANNEL_API_BASE"] = f"http://localhost:{API_PORT}"

    api_proc = subprocess.Popen(["uv", "run", "inv", "dev"], cwd=ROOT)
    # Wait for Vite to come up on UI_PORT before launching Electron
    for _ in range(60):
        try:
            urllib.request.urlopen(env["VITE_DEV_SERVER_URL"], timeout=1)
            break
        except Exception:
            time.sleep(1)
    electron_proc = subprocess.Popen(["npm", "run", "dev:electron"], cwd=DESKTOP, env=env)

    try:
        electron_proc.wait()
    finally:
        api_proc.send_signal(signal.SIGINT)
        api_proc.wait()
```

Modify the existing `pre_push` task to include `desktop_test` in its
positional-decorator pre-deps. The current signature is at `tasks.py:276`:

```python
@task(lint_backend, typecheck, check_copyright, test_unit, test_frontend)
def pre_push(ctx):
```

Change it to:

```python
@task(lint_backend, typecheck, check_copyright, test_unit, test_frontend, desktop_test)
def pre_push(ctx):
```

(Invoke's `@task` decorator accepts pre-task callables as positional args;
keep the existing form rather than inventing a `pre=[...]` keyword.)

- [ ] **Step 3: Sanity-check the new tasks**

Run:
```
uv run inv --list | grep desktop
```
Expected: `desktop-test`, `desktop-build`, `desktop-dev` listed.

Run:
```
uv run inv desktop-test
```
Expected: desktop unit tests pass.

- [ ] **Step 4: Commit**

```
git add tasks.py
git commit -m "$(cat <<'EOF'
chore(tasks): add desktop-test / desktop-build / desktop-dev invoke tasks

inv pre-push now also runs the desktop main-process unit tests, so
the local gate matches CI. inv desktop-dev spawns the existing
dev stack (DynamoDB + FastAPI + Vite) and Electron in parallel; on
Ctrl-C it tears the whole thing down.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: CI — `.github/workflows/desktop-build.yml`

**Files:**
- Create: `.github/workflows/desktop-build.yml`

**What this task adds:** Cross-platform matrix build that runs on PRs touching `desktop/`, `ui/`, or `src/channel/auth/`, plus pushes to `development`. Each runner builds for its own platform, runs the unit tests + 100% coverage gate, executes the smoke harness against the packaged binary, and uploads artifacts (30-day retention) — no publishing.

- [ ] **Step 1: Resolve SHAs for third-party actions** — required by CLAUDE.md "Pin third-party GitHub Actions to full commit SHAs".

Run, capturing the SHA for each:

```
gh api repos/actions/checkout/git/ref/tags/v4 --jq .object.sha
gh api repos/actions/setup-node/git/ref/tags/v4 --jq .object.sha
gh api repos/actions/upload-artifact/git/ref/tags/v4 --jq .object.sha
gh api repos/astral-sh/setup-uv/git/ref/tags/v3 --jq .object.sha
```

Note the four SHAs; substitute them below.

- [ ] **Step 2: Create the workflow**

```yaml
# Copyright (c) 2026 John Carter. All rights reserved.
name: desktop-build

on:
  pull_request:
    paths:
      - "desktop/**"
      - "ui/**"
      - "src/channel/auth/**"
      - ".github/workflows/desktop-build.yml"
  push:
    branches: [development]

permissions:
  contents: read

jobs:
  build:
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: macos-14
            target: --mac
            binary: desktop/release/mac-arm64/Channel.app/Contents/MacOS/Channel
          - os: windows-latest
            target: --win
            binary: desktop/release/win-unpacked/Channel.exe
          - os: ubuntu-latest
            target: --linux
            binary: desktop/release/linux-unpacked/channel
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@<sha-from-step-1>
      - uses: actions/setup-node@<sha-from-step-1>
        with: { node-version: "20" }

      - name: Install ui deps + build SPA
        shell: bash
        run: |
          cd ui
          npm install
          VITE_API_BASE=https://channel.warlordofmars.net npm run build

      - name: Install desktop deps
        shell: bash
        run: |
          cd desktop
          npm install

      - name: Desktop unit tests + 100% coverage
        shell: bash
        run: |
          cd desktop
          npm run test:coverage

      - name: Build packaged app
        shell: bash
        run: |
          cd desktop
          npm run build:main
          npm run build:renderer
          npx electron-builder ${{ matrix.target }}

      - name: Smoke test (Linux uses xvfb)
        shell: bash
        run: |
          cd desktop
          if [[ "${{ matrix.os }}" == "ubuntu-latest" ]]; then
            sudo apt-get update && sudo apt-get install -y xvfb
            xvfb-run -a env CHANNEL_DESKTOP_BIN="$PWD/../${{ matrix.binary }}" npm run smoke
          else
            CHANNEL_DESKTOP_BIN="$PWD/../${{ matrix.binary }}" npm run smoke
          fi

      - uses: actions/upload-artifact@<sha-from-step-1>
        with:
          name: channel-desktop-${{ matrix.os }}
          path: desktop/release/
          retention-days: 30
```

- [ ] **Step 3: Validate workflow syntax**

Run:
```
gh workflow view desktop-build --yaml
```
(Until the file is on a pushed branch, you can validate locally with `yamllint` or just open in an editor.) If `actionlint` is installed:
```
actionlint .github/workflows/desktop-build.yml
```

- [ ] **Step 4: Commit**

```
git add .github/workflows/desktop-build.yml
git commit -m "$(cat <<'EOF'
ci: add desktop-build workflow for macOS / Windows / Linux

Builds + tests + smokes the Electron app on every PR touching desktop/,
ui/, or src/channel/auth/. Each runner produces unsigned artifacts for
its own platform and uploads them for 30-day retention. Publishing
(real releases, signing, auto-update) is sub-project B.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: `CLAUDE.md` — `## Desktop app` section

**Files:**
- Modify: `CLAUDE.md`

**What this task adds:** Documents the new `desktop/` layout, dev workflow, and OAuth flow for future contributors and agents. Updates the existing note that calls Electron "deferred".

- [ ] **Step 1: Locate the section that currently says Electron is deferred**

The line is `CLAUDE.md:129`:
```
- Window frame is web only at MVP — Electron desktop wrapper deferred per design handoff
```

Replace with:
```
- Web SPA AND Electron desktop app ship from the same `ui/` SPA source. The
  desktop wrapper lives in `desktop/` (Electron main + preload) and bundles
  the SPA build. See `## Desktop app` below.
```

- [ ] **Step 2: Add a new `## Desktop app` section** — insert after `## Management UI`, before `## Docs site`:

```markdown
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

`STARTER_BYPASS_GOOGLE_AUTH=1` works identically in dev — the bypass path
returns the JWT via the same loopback redirect when `desktop_callback` is
present, so `inv desktop-dev` users get a one-click login.

### Why these decisions

The design and rationale live in
`docs/superpowers/specs/2026-05-30-electron-shell-oauth-design.md`.
Don't re-derive sub-project boundaries (A/B/C/D) here — cite the spec.
```

- [ ] **Step 3: Update the structure tree** near the top of CLAUDE.md to include `desktop/`.

Find the `channel/` directory tree in CLAUDE.md and insert:

```text
├── desktop/
│   ├── package.json
│   ├── main/                  # main-process modules
│   ├── preload/               # sandboxed contextBridge
│   ├── electron-builder.yml
│   └── test/
```

…between `ui/` and `docs-site/`.

- [ ] **Step 4: Verify the file still lints/parses**

Run:
```
uv run inv lint
```
Expected: green. (CLAUDE.md isn't linted by ruff/mypy, but pre-push must still pass.)

- [ ] **Step 5: Commit**

```
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: document the new desktop/ Electron app in CLAUDE.md

Replaces the 'deferred' note with the live shape. New ## Desktop app
section covers layout, inv desktop-dev / desktop-build, and the
external-browser + loopback OAuth flow. Sub-project B/C/D rationale
stays in the spec to avoid duplication.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

After Task 16, run the full local CI gate end-to-end before opening a PR:

- [ ] **Run `inv pre-push`**

```
uv run inv pre-push
```
Expected: lint + typecheck + python unit + frontend + desktop unit all green.

- [ ] **Run `inv test-integration`** if you have Docker available

```
uv run inv test-integration
```
Expected: green (includes the new `tests/integration/test_mgmt_auth_desktop.py`).

- [ ] **Manually verify `inv desktop-dev`** boots the app

```
uv run inv desktop-dev
```
Expected: Electron window opens, loads the Vite SPA at `localhost:5173`, "Sign in with Google" button click opens the system browser, `?test_email=you@example.com` bypass completes login via loopback and lands in `/app`.

- [ ] **Manually verify `inv desktop-build`** produces an unsigned binary for your platform

```
uv run inv desktop-build --platform current
```
Expected: artifact at `desktop/release/<platform>/`; launching it shows the chat app pointed at `https://channel.warlordofmars.net`.

- [ ] **Open the PR** following CLAUDE.md §PR workflow

Base branch: `development`. PR body must include `Closes #<issue-number>` if an issue exists; otherwise reference the spec path in the description. Use the canonical push procedure in `.claude/agents/issue-worker.md` §"Push discipline" — never `git push` without an explicit refspec.

---

## Spec coverage checklist (self-review)

Every spec section maps to at least one task. Run this check before declaring the plan complete.

| Spec section | Task(s) |
|---|---|
| Architecture overview (main/renderer, `app://`, loopback, `nodeIntegration: false`) | 2, 3, 4, 5, 7-9, 10 |
| Project layout (`desktop/`, separate package.json, esbuild bundling) | 1 |
| Single-source twin-build SPA contract (`VITE_API_BASE`) | 1 (build script), 14, 15 |
| OAuth flow end-to-end sequence | 6-9 (client), 11-12 (server) |
| Failure modes table | 7-9 (each row mapped to a test) |
| Server-side `desktop_callback` validation (8 negative + 1 positive) | 11 |
| Server-side `/auth/callback` redirect to loopback | 12 |
| Layer 1 main-process unit tests | 2-9 |
| Layer 2 server unit + integration tests | 11, 12 |
| Layer 3 renderer Login.jsx tests | 13 |
| CI smoke step + `--smoke` harness | 10 (`--smoke` flag + smoke.js), 15 (xvfb wiring) |
| `inv desktop-test` / `desktop-build` / `desktop-dev` + pre-push integration | 14 |
| Cross-platform CI matrix | 15 |
| CLAUDE.md `## Desktop app` section | 16 |

No spec section is unaddressed.
