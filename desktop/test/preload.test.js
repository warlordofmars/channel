// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mockElectron } from "./_helpers.js";

const electronMock = mockElectron();
vi.mock("electron", () => electronMock);

const APP_ORIGIN = "app://-";
const ORIGIN_FLAG = `--channel-app-origin=${APP_ORIGIN}`;

let originalArgv;

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  // The main process hands the preload its one permitted origin via
  // webPreferences.additionalArguments, which lands in process.argv.
  originalArgv = process.argv;
  process.argv = [...originalArgv, ORIGIN_FLAG];
  globalThis.location = { origin: APP_ORIGIN };
});

afterEach(() => {
  process.argv = originalArgv;
  delete globalThis.location;
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

describe("preload — origin guard (#472)", () => {
  it("reads the configured origin out of process.argv", async () => {
    const { expectedOriginFrom } = await import("../preload/index.js");
    expect(expectedOriginFrom(["electron", ORIGIN_FLAG])).toBe(APP_ORIGIN);
    expect(expectedOriginFrom(["electron", "--channel-app-origin=http://localhost:5173"])).toBe(
      "http://localhost:5173",
    );
  });

  it("returns null when the flag is absent or argv is missing entirely", async () => {
    const { expectedOriginFrom } = await import("../preload/index.js");
    expect(expectedOriginFrom(["electron", "--other-flag=1"])).toBeNull();
    expect(expectedOriginFrom([])).toBeNull();
    expect(expectedOriginFrom(undefined)).toBeNull();
  });

  it("installs the bridge when the document origin matches", async () => {
    const { installBridge } = await import("../preload/index.js");
    const expose = vi.fn();
    expect(
      installBridge({ argv: [ORIGIN_FLAG], documentOrigin: APP_ORIGIN, expose }),
    ).toBe(true);
    expect(expose).toHaveBeenCalledTimes(1);
    expect(expose.mock.calls[0][0]).toBe("channelDesktop");
  });

  // The whole point of #472: a hostile page must never hold login() /
  // logout() / relaunchToUpdate().
  it.each([
    ["a foreign https origin", "https://github.com"],
    ["a lookalike origin", "app://evil"],
    ["an opaque origin", "null"],
    ["a data: document", ""],
    ["a missing origin", null],
  ])("refuses to install the bridge on %s", async (_label, documentOrigin) => {
    const { installBridge } = await import("../preload/index.js");
    const expose = vi.fn();
    expect(installBridge({ argv: [ORIGIN_FLAG], documentOrigin, expose })).toBe(false);
    expect(expose).not.toHaveBeenCalled();
  });

  it("fails closed when the main process configured no origin at all", async () => {
    const { installBridge } = await import("../preload/index.js");
    const expose = vi.fn();
    // No flag AND no document origin — must not degrade to null === null.
    expect(installBridge({ argv: [], documentOrigin: null, expose })).toBe(false);
    expect(expose).not.toHaveBeenCalled();
  });

  it("builds the five-function bridge and nothing more", async () => {
    const { createBridge } = await import("../preload/index.js");
    expect(Object.keys(createBridge()).sort()).toEqual([
      "getVersion",
      "isDesktop",
      "login",
      "logout",
      "onUpdateStatus",
      "platform",
      "relaunchToUpdate",
    ]);
  });
});

describe("preload — origin guard at module load", () => {
  it("does not expose the bridge when the document is on a foreign origin", async () => {
    globalThis.location = { origin: "https://attacker.example" };
    await import("../preload/index.js");
    const { contextBridge } = await import("electron");
    expect(contextBridge.exposeInMainWorld).not.toHaveBeenCalled();
  });

  it("does not expose the bridge when no origin flag was passed", async () => {
    process.argv = [...originalArgv];
    await import("../preload/index.js");
    const { contextBridge } = await import("electron");
    expect(contextBridge.exposeInMainWorld).not.toHaveBeenCalled();
  });

  it("does not expose the bridge when the document has no location", async () => {
    delete globalThis.location;
    await import("../preload/index.js");
    const { contextBridge } = await import("electron");
    expect(contextBridge.exposeInMainWorld).not.toHaveBeenCalled();
  });
});
