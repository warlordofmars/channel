// Copyright (c) 2026 John Carter. All rights reserved.
import { autoUpdater } from "electron-updater";

export function init({ channel, webContents }) {
  if (process.platform !== "darwin") return;
  // Channel + IPC wiring added in subsequent tasks.
  autoUpdater.setFeedURL({ provider: "generic", url: `https://placeholder/${channel}` });
  autoUpdater.checkForUpdates();
}
