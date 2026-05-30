// Copyright (c) 2026 John Carter. All rights reserved.
import { app } from "electron";
import { resolve } from "node:path";
import { registerAppProtocol } from "./protocol.js";
import { createMainWindow } from "./window.js";
import { registerIpc } from "./ipc.js";
import { login } from "./auth.js";

// esbuild CJS bundle: __dirname is the bundle's directory at runtime.
const RENDERER_ROOT = resolve(__dirname, "../dist-renderer");
const PRELOAD = resolve(__dirname, "../preload/index.js");
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
