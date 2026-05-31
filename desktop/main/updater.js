// Copyright (c) 2026 John Carter. All rights reserved.
import { autoUpdater } from "electron-updater";

const FEED_URL_BY_CHANNEL = {
  latest: "https://channel.warlordofmars.net/updates/latest",
  dev: "https://channel-dev.warlordofmars.net/updates/dev",
};

export function init({ channel, webContents }) {
  if (process.platform !== "darwin") return;
  const url = FEED_URL_BY_CHANNEL[channel];
  if (!url) throw new Error(`unknown channel: ${channel}`);
  autoUpdater.setFeedURL({ provider: "generic", url });
  autoUpdater.checkForUpdates();
}
