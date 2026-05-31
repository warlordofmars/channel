// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockElectron } from "./_helpers.js";

const electronMock = mockElectron();
vi.mock("electron", () => electronMock);

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
});

describe("preload script", () => {
  it("exposes channelDesktop on window via contextBridge", async () => {
    await import("../preload/index.js");
    const { contextBridge } = await import("electron");
    expect(contextBridge.exposeInMainWorld).toHaveBeenCalledTimes(1);
    const [name, api] = contextBridge.exposeInMainWorld.mock.calls[0];
    expect(name).toBe("channelDesktop");
    expect(api.isDesktop).toBe(true);
    expect(typeof api.login).toBe("function");
    expect(typeof api.logout).toBe("function");
    expect(typeof api.getVersion).toBe("function");
  });

  it("login() invokes ipcRenderer with 'desktop:login'", async () => {
    await import("../preload/index.js");
    const { contextBridge, ipcRenderer } = await import("electron");
    const [, api] = contextBridge.exposeInMainWorld.mock.calls[0];
    ipcRenderer.invoke.mockResolvedValue("a-jwt");
    const result = await api.login();
    expect(ipcRenderer.invoke).toHaveBeenCalledWith("desktop:login");
    expect(result).toBe("a-jwt");
  });

  it("logout() invokes ipcRenderer with 'desktop:logout'", async () => {
    await import("../preload/index.js");
    const { contextBridge, ipcRenderer } = await import("electron");
    const [, api] = contextBridge.exposeInMainWorld.mock.calls[0];
    await api.logout();
    expect(ipcRenderer.invoke).toHaveBeenCalledWith("desktop:logout");
  });

  it("getVersion() invokes ipcRenderer with 'desktop:version'", async () => {
    await import("../preload/index.js");
    const { contextBridge, ipcRenderer } = await import("electron");
    const [, api] = contextBridge.exposeInMainWorld.mock.calls[0];
    ipcRenderer.invoke.mockResolvedValue("1.2.3");
    const v = await api.getVersion();
    expect(ipcRenderer.invoke).toHaveBeenCalledWith("desktop:version");
    expect(v).toBe("1.2.3");
  });
});

describe("preload — update bridge", () => {
  it("exposes onUpdateStatus, registering an ipcRenderer.on listener", async () => {
    await import("../preload/index.js");
    const { contextBridge, ipcRenderer } = await import("electron");
    // The preload module ran on import; capture the exposed surface.
    const exposed = contextBridge.exposeInMainWorld.mock.calls.find(
      ([name]) => name === "channelDesktop",
    )[1];

    const cb = vi.fn();
    exposed.onUpdateStatus(cb);
    expect(ipcRenderer.on).toHaveBeenCalledWith("desktop:update-status", expect.any(Function));

    // Invoke the registered listener and assert the callback receives the payload.
    const [, registeredListener] = ipcRenderer.on.mock.calls.find(
      ([ch]) => ch === "desktop:update-status",
    );
    registeredListener({}, { state: "downloaded", version: "0.2.1" });
    expect(cb).toHaveBeenCalledWith({ state: "downloaded", version: "0.2.1" });
  });

  it("exposes relaunchToUpdate, invoking the ipcRenderer", async () => {
    await import("../preload/index.js");
    const { contextBridge, ipcRenderer } = await import("electron");
    const exposed = contextBridge.exposeInMainWorld.mock.calls.find(
      ([name]) => name === "channelDesktop",
    )[1];
    await exposed.relaunchToUpdate();
    expect(ipcRenderer.invoke).toHaveBeenCalledWith("desktop:relaunch-to-update");
  });
});
