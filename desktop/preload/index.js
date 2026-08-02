// Copyright (c) 2026 John Carter. All rights reserved.
import { contextBridge, ipcRenderer } from "electron";

// Kept in sync with APP_ORIGIN_FLAG in desktop/main/window.js, which sets
// it via webPreferences.additionalArguments. A sandboxed preload can't
// read process.env, but additionalArguments does land in process.argv —
// so this is how the main process tells the preload which single origin
// is allowed to hold the bridge.
const APP_ORIGIN_FLAG = "--channel-app-origin=";

/**
 * The app origin the main process configured, or null when the flag is
 * absent. Null means "fail closed" — never "allow anything".
 */
export function expectedOriginFrom(argv) {
  const flag = (argv ?? []).find((arg) => arg.startsWith(APP_ORIGIN_FLAG));
  return flag === undefined ? null : flag.slice(APP_ORIGIN_FLAG.length);
}

/** The IPC surface handed to the renderer. Five functions, no more. */
export function createBridge() {
  return {
    isDesktop: true,
    // Exposed so the renderer can adapt platform-specific UI (e.g. the
    // sidebar header padding that clears macOS traffic lights). Read-only.
    platform: process.platform,
    login: () => ipcRenderer.invoke("desktop:login"),
    logout: () => ipcRenderer.invoke("desktop:logout"),
    getVersion: () => ipcRenderer.invoke("desktop:version"),
    onUpdateStatus: (cb) => {
      ipcRenderer.on("desktop:update-status", (_event, payload) => cb(payload));
    },
    relaunchToUpdate: () => ipcRenderer.invoke("desktop:relaunch-to-update"),
  };
}

/**
 * Expose the bridge only on the app's own origin.
 *
 * Defence in depth behind the main process's navigation guards (#472): if
 * a guard is ever bypassed, or a future code path loads a foreign page in
 * this window, that page must still not receive `relaunchToUpdate()` and
 * friends. Fails closed — an unknown expected origin, an origin mismatch,
 * or a document with no origin at all (opaque `"null"`) all yield no
 * bridge.
 *
 * Returns whether the bridge was installed, so the behaviour is directly
 * assertable in tests.
 */
export function installBridge({ argv, documentOrigin, expose }) {
  const expected = expectedOriginFrom(argv);
  if (expected === null || documentOrigin !== expected) return false;
  expose("channelDesktop", createBridge());
  return true;
}

// `location.origin` is the renderer-side counterpart of the main
// process's `${protocol}//${host}` serialisation: identical for http(s),
// and `app://-` for the packaged app because protocol.js registers the
// `app:` scheme as standard (so it gets a real tuple origin rather than
// an opaque one).
installBridge({
  argv: process.argv,
  documentOrigin: globalThis.location?.origin ?? null,
  expose: (name, api) => contextBridge.exposeInMainWorld(name, api),
});
