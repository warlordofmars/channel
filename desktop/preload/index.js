// Copyright (c) 2026 John Carter. All rights reserved.
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("channelDesktop", {
  isDesktop: true,
  login: () => ipcRenderer.invoke("desktop:login"),
  logout: () => ipcRenderer.invoke("desktop:logout"),
  getVersion: () => ipcRenderer.invoke("desktop:version"),
});
