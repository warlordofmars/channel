// Copyright (c) 2026 John Carter. All rights reserved.
import { ipcMain } from "electron";

// Fixed allowlist — every channel the renderer can reach is named here
// and nowhere else. There is deliberately no "register whatever the
// caller passes" path, so gaining a capability is a reviewable diff to
// this list rather than a runtime decision. The three `token-*` channels
// (#297) are the renderer's only route to the OS keychain.
const CHANNELS = [
  "desktop:login",
  "desktop:logout",
  "desktop:version",
  "desktop:relaunch-to-update",
  "desktop:token-read",
  "desktop:token-write",
  "desktop:token-clear",
];

export function registerIpc({ mainWindowId, handlers }) {
  const route = {
    "desktop:login": handlers.login,
    "desktop:logout": handlers.logout,
    "desktop:version": handlers.getVersion,
    "desktop:relaunch-to-update": handlers.relaunchToUpdate,
    "desktop:token-read": handlers.tokenRead,
    "desktop:token-write": handlers.tokenWrite,
    "desktop:token-clear": handlers.tokenClear,
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
