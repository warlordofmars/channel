// Copyright (c) 2026 John Carter. All rights reserved.
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("channelDesktop", {
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
});
