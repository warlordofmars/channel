// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockElectron } from "./_helpers.js";

const mocked = mockElectron();
vi.mock("electron", () => mocked);

const { createMainWindow } = await import("../main/window.js");

beforeEach(() => {
  vi.clearAllMocks();
  delete process.env.VITE_DEV_SERVER_URL;
});

describe("createMainWindow", () => {
  it("constructs a BrowserWindow with the secure web preferences", async () => {
    const { BrowserWindow } = await import("electron");
    createMainWindow({ preloadPath: "/preload.js" });
    expect(BrowserWindow).toHaveBeenCalledTimes(1);
    const opts = BrowserWindow.mock.calls[0][0];
    expect(opts.webPreferences).toEqual(
      expect.objectContaining({
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        preload: "/preload.js",
      }),
    );
  });

  it("loads app://-/app in production (no VITE_DEV_SERVER_URL)", async () => {
    const win = createMainWindow({ preloadPath: "/preload.js" });
    expect(win.loadURL).toHaveBeenCalledWith("app://-/app");
  });

  it("loads <VITE_DEV_SERVER_URL>/app when set", async () => {
    process.env.VITE_DEV_SERVER_URL = "http://localhost:5173";
    const win = createMainWindow({ preloadPath: "/preload.js" });
    expect(win.loadURL).toHaveBeenCalledWith("http://localhost:5173/app");
  });

  it("intercepts close to hide the window instead of destroying it", async () => {
    const win = createMainWindow({ preloadPath: "/preload.js" });
    const closeListener = win.on.mock.calls.find(([event]) => event === "close")[1];
    const event = { preventDefault: vi.fn() };
    closeListener(event);
    expect(event.preventDefault).toHaveBeenCalled();
    expect(win.hide).toHaveBeenCalled();
  });

  it("allows close when app is quitting", async () => {
    const win = createMainWindow({ preloadPath: "/preload.js", isQuitting: () => true });
    const closeListener = win.on.mock.calls.find(([event]) => event === "close")[1];
    const event = { preventDefault: vi.fn() };
    closeListener(event);
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(win.hide).not.toHaveBeenCalled();
  });

  it("hides the titlebar with inset traffic lights on darwin", async () => {
    const { BrowserWindow } = await import("electron");
    createMainWindow({ preloadPath: "/preload.js", platform: "darwin" });
    const opts = BrowserWindow.mock.calls[0][0];
    expect(opts.titleBarStyle).toBe("hidden");
    expect(opts.trafficLightPosition).toEqual({ x: 14, y: 14 });
  });

  it("keeps default chrome on non-darwin platforms", async () => {
    const { BrowserWindow } = await import("electron");
    createMainWindow({ preloadPath: "/preload.js", platform: "win32" });
    const opts = BrowserWindow.mock.calls[0][0];
    expect(opts.titleBarStyle).toBeUndefined();
    expect(opts.trafficLightPosition).toBeUndefined();
  });
});
