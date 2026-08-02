// Copyright (c) 2026 John Carter. All rights reserved.
import { contextBridge, ipcRenderer } from "electron";

// Kept in sync with APP_ORIGIN_FLAG in desktop/main/window.js, which sets
// it via webPreferences.additionalArguments — that is how the main
// process tells the preload which single origin may hold the bridge.
//
// Why argv and not process.env: a sandboxed preload *can* read
// process.env (verified — it sees the main process's environment), but
// env is process-global while additionalArguments is per-window, and
// routing it through window.js keeps the origin computed in exactly one
// place, right next to the loadURL that establishes it. The two can't
// drift.
const APP_ORIGIN_FLAG = "--channel-app-origin=";

/**
 * The app origin the main process configured, or null when that can't be
 * determined unambiguously. Null means "fail closed" — never "allow
 * anything".
 *
 * Requires exactly one occurrence. Selecting by position would be
 * arbitrary: Chromium's own renderer switches surround ours in argv
 * (measured: our flag lands at index 23 of 25, with `--seatbelt-client`
 * after it), so neither the first nor the last match is authoritative.
 * A duplicated flag means something unexpected shaped the command line,
 * and the safe reading of an ambiguous security parameter is to refuse
 * it rather than pick a winner.
 */
export function expectedOriginFrom(argv) {
  const flags = (argv ?? []).filter((arg) => arg.startsWith(APP_ORIGIN_FLAG));
  if (flags.length !== 1) return null;
  return flags[0].slice(APP_ORIGIN_FLAG.length);
}

/**
 * The IPC surface handed to the renderer, and nothing beyond it: seven
 * keys — five callable methods (login, logout, getVersion,
 * onUpdateStatus, relaunchToUpdate) plus the read-only `isDesktop` and
 * `platform` values.
 */
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
