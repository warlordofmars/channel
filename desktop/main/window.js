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

  // Desktop only mounts /app/* routes — the marketing site has no place
  // inside a packaged desktop app. AuthGate redirects unauthenticated
  // /app visits to /app/login, so we land in the right place either way.
  const baseUrl = process.env.VITE_DEV_SERVER_URL ?? "app://-";
  win.loadURL(`${baseUrl}/app`);

  return win;
}
