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

  it("loads app://-/ in production (no VITE_DEV_SERVER_URL)", async () => {
    const win = createMainWindow({ preloadPath: "/preload.js" });
    expect(win.loadURL).toHaveBeenCalledWith("app://-/");
  });

  it("loads VITE_DEV_SERVER_URL when set", async () => {
    process.env.VITE_DEV_SERVER_URL = "http://localhost:5173";
    const win = createMainWindow({ preloadPath: "/preload.js" });
    expect(win.loadURL).toHaveBeenCalledWith("http://localhost:5173");
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
});
