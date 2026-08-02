// Copyright (c) 2026 John Carter. All rights reserved.
import { BrowserWindow, shell } from "electron";

// Landscape default sized to fit a 13" MacBook (1440x900 effective)
// without overflowing the dock. 1280 wide leaves ~160px of horizontal
// margin and gives the conversation column real breathing room next to
// the 264px sidebar; 900 tall leaves ~70px for the menu bar + dock on
// a small screen. The minimums let users shrink down for split-screen
// work; the defaults exist to shape first-launch impression.
const DEFAULT_DIMENSIONS = { width: 1280, height: 900, minWidth: 720, minHeight: 600 };

// The packaged app serves the SPA from the custom `app:` scheme
// (registered standard + secure in protocol.js, so it gets a real tuple
// origin of `app://-`). `inv desktop-dev` points the window at Vite
// instead via VITE_DEV_SERVER_URL.
const PACKAGED_BASE_URL = "app://-";

// Only these schemes may ever reach shell.openExternal. Handing the OS
// a `file:`, `javascript:`, `smb:` or otherwise arbitrary-scheme URL
// would trade this fix for a worse bug: shell.openExternal delegates to
// the platform handler, which will happily launch a local executable or
// mount a remote share. Allowlist, never denylist.
const EXTERNAL_SCHEMES = new Set(["http:", "https:"]);

// Flag used to hand the preload the single origin that is allowed to
// hold the `window.channelDesktop` bridge. Sandboxed preloads cannot
// read process.env, so `webPreferences.additionalArguments` is the
// supported channel for this (it lands in the renderer's process.argv).
// Kept in sync with the same constant in desktop/preload/index.js.
const APP_ORIGIN_FLAG = "--channel-app-origin=";

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

/**
 * Serialise a URL's origin as `scheme://host[:port]`, or null if the URL
 * doesn't parse.
 *
 * Deliberately NOT `URL.origin`: in Node that returns the string "null"
 * for any non-special scheme, which includes our own `app:` scheme — so
 * comparing origins via `.origin` in the main process would collapse
 * `app://-` and every other custom-scheme URL into one bucket. Building
 * the tuple by hand keeps `app://-` distinct. For http/https this is
 * byte-identical to `URL.origin` (`host` already carries the port), which
 * is what lets the renderer-side guard compare against `location.origin`.
 */
export function originOf(url) {
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.host}`;
  } catch {
    return null;
  }
}

/**
 * True only when `url` parses AND belongs to the app's own origin.
 *
 * The `!== null` guard matters: if `appOrigin` is itself null (an
 * unparseable VITE_DEV_SERVER_URL), an unparseable target would otherwise
 * compare null === null and be waved through as same-origin.
 */
export function isAppOrigin(url, appOrigin) {
  const target = originOf(url);
  return target !== null && target === appOrigin;
}

// Named rather than inline so the swallowed rejection is legible in a
// stack trace and countable by the coverage gate.
function ignoreOpenFailure() {
  // shell.openExternal rejects when the OS has no handler for the URL.
  // There is nothing actionable to do about it, but an unhandled
  // rejection in the main process is noise we don't want.
}

/**
 * Hand a URL to the system browser, but only if its scheme is on the
 * allowlist. Returns whether the hand-off happened, so callers can tell
 * "opened externally" from "refused".
 */
export function openExternalIfSafe(url, openExternal) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (!EXTERNAL_SCHEMES.has(parsed.protocol)) return false;
  // Pass the re-serialised URL rather than the raw string so whatever the
  // parser normalised (embedded tabs/newlines, backslashes) is what the OS
  // actually receives.
  Promise.resolve(openExternal(parsed.toString())).catch(ignoreOpenFailure);
  return true;
}

/**
 * Keep the app window on the app's own origin, and route everything else
 * to the system browser.
 *
 * Without this, clicking any link — including a markdown link rendered
 * from model output or an MCP tool result — navigates the app's own
 * BrowserWindow. The window has no browser chrome, so the user is
 * stranded; worse, the preload's contextBridge would follow the
 * navigation and hand `window.channelDesktop` (login / logout /
 * relaunchToUpdate / …) to an arbitrary origin. See issue #472.
 */
export function installNavigationGuards({ webContents, appOrigin, openExternal }) {
  const guardNavigation = (event, url) => {
    if (isAppOrigin(url, appOrigin)) return;
    event.preventDefault();
    openExternalIfSafe(url, openExternal);
  };

  // will-navigate: renderer-initiated top-level navigations (link clicks,
  // location.assign). will-redirect: server-side redirects mid-navigation,
  // which will-navigate never sees. Neither fires for the main process's
  // own loadURL, so the initial load is unaffected.
  webContents.on("will-navigate", guardNavigation);
  webContents.on("will-redirect", guardNavigation);

  // target="_blank" / window.open. Always deny the Electron window; hand
  // http(s) targets to the system browser. Same-origin popups are denied
  // without an external hand-off (openExternalIfSafe refuses `app:`), which
  // is correct — the SPA only uses window.open for external OAuth starts.
  webContents.setWindowOpenHandler(({ url }) => {
    openExternalIfSafe(url, openExternal);
    return { action: "deny" };
  });
}

// Default hand-off. Named (not an inline arrow in the parameter list) so
// it reads in a stack trace and is reachable by the coverage gate.
function openInSystemBrowser(url) {
  return shell.openExternal(url);
}

export function createMainWindow({
  preloadPath,
  isQuitting = () => false,
  platform = process.platform,
  openExternal = openInSystemBrowser,
}) {
  // Desktop only mounts /app/* routes — the marketing site has no place
  // inside a packaged desktop app. AuthGate redirects unauthenticated
  // /app visits to /app/login, so we land in the right place either way.
  const baseUrl = process.env.VITE_DEV_SERVER_URL ?? PACKAGED_BASE_URL;
  const appOrigin = originOf(baseUrl);

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
      // A misconfigured base URL yields no flag at all, so the preload
      // fails closed (no bridge) rather than matching the literal string
      // "null" — which is what an opaque-origin document reports.
      additionalArguments: appOrigin === null ? [] : [`${APP_ORIGIN_FLAG}${appOrigin}`],
    },
  });

  installNavigationGuards({ webContents: win.webContents, appOrigin, openExternal });

  win.once("ready-to-show", () => {
    win.show();
  });

  win.on("close", (event) => {
    if (isQuitting()) return;
    event.preventDefault();
    win.hide();
  });

  win.loadURL(`${baseUrl}/app`);

  return win;
}
