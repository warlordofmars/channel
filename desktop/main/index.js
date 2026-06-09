// Copyright (c) 2026 John Carter. All rights reserved.
import { app } from "electron";
import { resolve } from "node:path";
import { registerAppScheme, registerAppHandler } from "./protocol.js";
import { createMainWindow } from "./window.js";
import { registerIpc } from "./ipc.js";
import { login } from "./auth.js";
import { init as initUpdater, relaunchToUpdate } from "./updater.js";

// esbuild CJS bundle: __dirname at runtime is <app>/dist-main/main/.
// The preload sits at <app>/dist-main/preload/index.js (sibling directory).
// The SPA bundle sits at <app>/dist-renderer/ (two levels up from main/).
const RENDERER_ROOT = resolve(__dirname, "../../dist-renderer");
const PRELOAD = resolve(__dirname, "../preload/index.js");
const IS_SMOKE = process.argv.includes("--smoke");
const AUTH_BASE = process.env.CHANNEL_API_BASE ?? "https://channel.warlordofmars.net";

let isQuitting = false;
app.on("before-quit", () => { isQuitting = true; });

// Scheme registration must happen BEFORE app.whenReady().
registerAppScheme();

app.whenReady().then(() => {
  // Handler registration must happen AFTER app.whenReady() — it reads
  // the default session, which doesn't exist until then.
  registerAppHandler(RENDERER_ROOT);

  const win = createMainWindow({ preloadPath: PRELOAD, isQuitting: () => isQuitting });

  registerIpc({
    mainWindowId: win.webContents.id,
    handlers: {
      login: () => login({ authBaseUrl: AUTH_BASE }),
      logout: () => {},                          // renderer clears localStorage itself
      getVersion: () => app.getVersion(),
      // Electron's autoUpdater.quitAndInstall closes all windows before
      // calling app.quit() — but window.js's close handler hides (rather
      // than closes) until isQuitting flips, and isQuitting only flips on
      // before-quit, which fires AFTER the close-windows phase. Set it
      // here so the close-windows phase can complete and the quit-and-
      // install sequence actually exits the process.
      relaunchToUpdate: () => {
        isQuitting = true;
        relaunchToUpdate();
      },
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
