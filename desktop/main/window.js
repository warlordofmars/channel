// Copyright (c) 2026 John Carter. All rights reserved.
import { BrowserWindow } from "electron";

// Landscape default sized to fit a 13" MacBook (1440x900 effective)
// without overflowing the dock. 1280 wide leaves ~160px of horizontal
// margin and gives the conversation column real breathing room next to
// the 264px sidebar; 900 tall leaves ~70px for the menu bar + dock on
// a small screen. The minimums let users shrink down for split-screen
// work; the defaults exist to shape first-launch impression.
const DEFAULT_DIMENSIONS = { width: 1280, height: 900, minWidth: 720, minHeight: 600 };

// macOS-only: hide the title bar chrome but keep the traffic lights, so
// the sidebar background extends to the top edge and the window feels
// native (matches the visual style of Claude Desktop / Linear / Notion).
// On Windows/Linux titleBarStyle: "hidden" would also hide min/max/close
// without a replacement, so we leave those platforms on the default chrome.
//
// Traffic-light position stays at the default top-left. The sidebar's
// header row is lifted up (CSS, in app.css) so its icons share a row
// with the traffic lights, with platform-aware left padding pushing
// them past the lights. Coordinated change — see the
// `.is-electron-mac .sb-top` rule in ui/src/styles/app.css.
function macosFrameOpts(platform) {
  if (platform !== "darwin") return {};
  return {
    titleBarStyle: "hidden",
    trafficLightPosition: { x: 14, y: 14 },
  };
}

export function createMainWindow({ preloadPath, isQuitting = () => false, platform = process.platform }) {
  const win = new BrowserWindow({
    ...DEFAULT_DIMENSIONS,
    ...macosFrameOpts(platform),
    // Start hidden; show only after ready-to-show fires (below) so the
    // user never sees a white/blank frame before the renderer paints
    // its first content. Works regardless of theme.
    show: false,
    autoHideMenuBar: false,
    webPreferences: {
      preload: preloadPath,
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });

  win.once("ready-to-show", () => {
    win.show();
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
