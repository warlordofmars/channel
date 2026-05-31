// Copyright (c) 2026 John Carter. All rights reserved.
import { autoUpdater } from "electron-updater";

const FEED_URL_BY_CHANNEL = {
  latest: "https://channel.warlordofmars.net/updates/latest",
  dev: "https://channel-dev.warlordofmars.net/updates/dev",
};

const FOUR_HOURS_MS = 4 * 60 * 60 * 1000;

export function init({ channel, webContents }) {
  if (process.platform !== "darwin") return;
  const url = FEED_URL_BY_CHANNEL[channel];
  if (!url) throw new Error(`unknown channel: ${channel}`);

  autoUpdater.setFeedURL({ provider: "generic", url });

  const send = (state, extra) => webContents.send("desktop:update-status", { state, ...extra });

  autoUpdater.on("checking-for-update", () => send("checking"));
  autoUpdater.on("update-available", (info) => send("available", { version: info.version }));
  autoUpdater.on("update-downloaded", (info) => send("downloaded", { version: info.version }));
  autoUpdater.on("error", (err) => send("error", { message: err.message }));

  autoUpdater.checkForUpdates();
  setInterval(() => autoUpdater.checkForUpdates(), FOUR_HOURS_MS);
}

export function relaunchToUpdate() {
  autoUpdater.quitAndInstall();
}
