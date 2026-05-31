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
