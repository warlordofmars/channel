// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockElectron } from "./_helpers.js";

const mocked = mockElectron();
vi.mock("electron", () => mocked);

const { createMainWindow, originOf, isAppOrigin, openExternalIfSafe, installNavigationGuards } =
  await import("../main/window.js");

beforeEach(() => {
  vi.clearAllMocks();
  delete process.env.VITE_DEV_SERVER_URL;
});

// Let queued microtasks (the swallowed openExternal rejection) settle.
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Pull a listener registered on the window's webContents by event name. */
function webContentsListener(win, event) {
  return win.webContents.on.mock.calls.find(([name]) => name === event)[1];
}

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

  it("opens at the landscape 1280x900 default with 720x600 minimums", async () => {
    const { BrowserWindow } = await import("electron");
    createMainWindow({ preloadPath: "/preload.js" });
    const opts = BrowserWindow.mock.calls[0][0];
    expect(opts.width).toBe(1280);
    expect(opts.height).toBe(900);
    expect(opts.minWidth).toBe(720);
    expect(opts.minHeight).toBe(600);
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

  it("starts hidden to avoid the pre-render white flash", async () => {
    const { BrowserWindow } = await import("electron");
    const win = createMainWindow({ preloadPath: "/preload.js" });
    expect(BrowserWindow.mock.calls[0][0].show).toBe(false);
    expect(win.show).not.toHaveBeenCalled();
  });

  it("shows the window once ready-to-show fires", async () => {
    const win = createMainWindow({ preloadPath: "/preload.js" });
    const readyListener = win.once.mock.calls.find(([event]) => event === "ready-to-show")[1];
    readyListener();
    expect(win.show).toHaveBeenCalledTimes(1);
  });
});

describe("originOf", () => {
  it("serialises an http origin including the port", () => {
    expect(originOf("http://localhost:5173/app/chat")).toBe("http://localhost:5173");
  });

  it("serialises the custom app: scheme rather than collapsing it to 'null'", () => {
    // URL.origin returns the string "null" for non-special schemes, so
    // every custom-scheme URL would compare equal — exactly the trap the
    // hand-built tuple avoids.
    expect(originOf("app://-/app")).toBe("app://-");
    expect(new URL("app://-/app").origin).toBe("null");
  });

  it("returns null for an unparseable URL", () => {
    expect(originOf("::::not a url")).toBeNull();
  });
});

describe("isAppOrigin", () => {
  it("accepts a URL on the app's own origin", () => {
    expect(isAppOrigin("app://-/app/chat/123", "app://-")).toBe(true);
  });

  it("rejects a URL on a foreign origin", () => {
    expect(isAppOrigin("https://github.com/warlordofmars/channel/pull/1", "app://-")).toBe(false);
  });

  it("rejects an unparseable URL even when the app origin is also null", () => {
    // Guards the null === null trap: a misconfigured app origin must not
    // turn every unparseable target into a same-origin navigation.
    expect(isAppOrigin("::::not a url", null)).toBe(false);
  });
});

describe("openExternalIfSafe", () => {
  it("hands http URLs to the system browser", () => {
    const openExternal = vi.fn(() => Promise.resolve());
    expect(openExternalIfSafe("http://example.com/x", openExternal)).toBe(true);
    expect(openExternal).toHaveBeenCalledWith("http://example.com/x");
  });

  it("hands https URLs to the system browser", () => {
    const openExternal = vi.fn(() => Promise.resolve());
    expect(openExternalIfSafe("https://github.com/a/b/pull/9", openExternal)).toBe(true);
    expect(openExternal).toHaveBeenCalledWith("https://github.com/a/b/pull/9");
  });

  // The trap this fix must not fall into: shell.openExternal delegates to
  // the OS handler, so a file: / javascript: / custom-scheme pass-through
  // would trade a stranded-window bug for a launch-anything bug.
  it.each([
    ["file:///etc/passwd"],
    ["file:///Applications/Calculator.app"],
    ["javascript:alert(document.cookie)"],
    ["JavaScript:alert(1)"],
    ["data:text/html,<script>alert(1)</script>"],
    ["smb://attacker.example/share"],
    ["app://-/app"],
    ["ms-msdt:/id"],
    ["vbscript:msgbox(1)"],
  ])("refuses to hand %s to the OS", (url) => {
    const openExternal = vi.fn(() => Promise.resolve());
    expect(openExternalIfSafe(url, openExternal)).toBe(false);
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("refuses an unparseable URL", () => {
    const openExternal = vi.fn(() => Promise.resolve());
    expect(openExternalIfSafe("::::not a url", openExternal)).toBe(false);
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("passes the re-serialised URL, not the raw string", () => {
    const openExternal = vi.fn(() => Promise.resolve());
    // Embedded tab/newline are stripped by the URL parser; the OS must see
    // what the parser validated, not the original bytes.
    openExternalIfSafe("https://exa\tmple.com/\npath", openExternal);
    expect(openExternal).toHaveBeenCalledWith("https://example.com/path");
  });

  it("swallows a rejected hand-off instead of leaving an unhandled rejection", async () => {
    const openExternal = vi.fn(() => Promise.reject(new Error("no handler")));
    expect(openExternalIfSafe("https://example.com", openExternal)).toBe(true);
    await flush();
  });

  it("tolerates a hand-off that returns nothing", async () => {
    const openExternal = vi.fn(() => undefined);
    expect(openExternalIfSafe("https://example.com", openExternal)).toBe(true);
    await flush();
  });

  it("contains a hand-off that throws synchronously", () => {
    // Promise.resolve() never gets to wrap a synchronous throw, so without
    // the try/catch this escapes into the will-navigate listener and takes
    // down the main process.
    const openExternal = vi.fn(() => {
      throw new Error("EINVAL");
    });
    expect(() => openExternalIfSafe("https://example.com", openExternal)).not.toThrow();
    expect(openExternal).toHaveBeenCalledTimes(1);
  });
});

describe("installNavigationGuards", () => {
  function harness() {
    const listeners = {};
    const webContents = {
      on: vi.fn((event, fn) => {
        listeners[event] = fn;
      }),
      setWindowOpenHandler: vi.fn(),
    };
    const openExternal = vi.fn(() => Promise.resolve());
    installNavigationGuards({ webContents, appOrigin: "app://-", openExternal });
    return { listeners, webContents, openExternal };
  }

  it.each([["will-navigate"], ["will-redirect"]])(
    "allows a same-origin %s without opening a browser",
    (eventName) => {
      const { listeners, openExternal } = harness();
      const event = { preventDefault: vi.fn() };
      listeners[eventName](event, "app://-/app/chat/abc");
      expect(event.preventDefault).not.toHaveBeenCalled();
      expect(openExternal).not.toHaveBeenCalled();
    },
  );

  it.each([["will-navigate"], ["will-redirect"]])(
    "blocks a foreign-origin %s and opens it externally",
    (eventName) => {
      const { listeners, openExternal } = harness();
      const event = { preventDefault: vi.fn() };
      listeners[eventName](event, "https://github.com/warlordofmars/channel/pull/472");
      expect(event.preventDefault).toHaveBeenCalledTimes(1);
      expect(openExternal).toHaveBeenCalledWith(
        "https://github.com/warlordofmars/channel/pull/472",
      );
    },
  );

  it("blocks a foreign-origin navigation to a dangerous scheme WITHOUT opening it", () => {
    const { listeners, openExternal } = harness();
    const event = { preventDefault: vi.fn() };
    listeners["will-navigate"](event, "file:///etc/passwd");
    expect(event.preventDefault).toHaveBeenCalledTimes(1);
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("denies window.open and routes http(s) targets to the system browser", () => {
    const { webContents, openExternal } = harness();
    const handler = webContents.setWindowOpenHandler.mock.calls[0][0];
    expect(handler({ url: "https://accounts.google.com/o/oauth2/v2/auth" })).toEqual({
      action: "deny",
    });
    expect(openExternal).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth");
  });

  it("denies window.open for a non-http(s) target without opening it", () => {
    const { webContents, openExternal } = harness();
    const handler = webContents.setWindowOpenHandler.mock.calls[0][0];
    expect(handler({ url: "file:///Applications/Calculator.app" })).toEqual({ action: "deny" });
    expect(openExternal).not.toHaveBeenCalled();
  });
});

describe("createMainWindow — navigation hardening (#472)", () => {
  it("registers will-navigate, will-redirect and a window-open handler", () => {
    const win = createMainWindow({ preloadPath: "/preload.js" });
    const events = win.webContents.on.mock.calls.map(([event]) => event);
    expect(events).toContain("will-navigate");
    expect(events).toContain("will-redirect");
    expect(win.webContents.setWindowOpenHandler).toHaveBeenCalledTimes(1);
  });

  it("sends an external link to the system browser and keeps the window on /app", async () => {
    const { shell } = await import("electron");
    // No openExternal injected — exercises the shell.openExternal default.
    const win = createMainWindow({ preloadPath: "/preload.js" });
    const event = { preventDefault: vi.fn() };
    webContentsListener(win, "will-navigate")(event, "https://github.com/some/pr");
    expect(event.preventDefault).toHaveBeenCalledTimes(1);
    expect(shell.openExternal).toHaveBeenCalledWith("https://github.com/some/pr");
    // The window itself was never asked to navigate anywhere but /app.
    expect(win.loadURL).toHaveBeenCalledTimes(1);
    expect(win.loadURL).toHaveBeenCalledWith("app://-/app");
    await flush();
  });

  it("lets in-app routing navigate freely in the packaged app", () => {
    const win = createMainWindow({ preloadPath: "/preload.js" });
    const event = { preventDefault: vi.fn() };
    webContentsListener(win, "will-navigate")(event, "app://-/app/chat/9f3");
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  it("lets in-app routing navigate freely against the dev server", () => {
    process.env.VITE_DEV_SERVER_URL = "http://localhost:5173";
    const win = createMainWindow({ preloadPath: "/preload.js" });
    const event = { preventDefault: vi.fn() };
    webContentsListener(win, "will-navigate")(event, "http://localhost:5173/app/login");
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  it("blocks a dev-mode navigation to a different localhost port", () => {
    // The API origin is a *different* origin than the SPA's, so it counts
    // as external as far as the bridge is concerned.
    process.env.VITE_DEV_SERVER_URL = "http://localhost:5173";
    const win = createMainWindow({ preloadPath: "/preload.js", openExternal: vi.fn() });
    const event = { preventDefault: vi.fn() };
    webContentsListener(win, "will-navigate")(event, "http://localhost:8001/auth/login");
    expect(event.preventDefault).toHaveBeenCalledTimes(1);
  });

  it("tells the preload which origin may hold the bridge (packaged)", async () => {
    const { BrowserWindow } = await import("electron");
    createMainWindow({ preloadPath: "/preload.js" });
    expect(BrowserWindow.mock.calls[0][0].webPreferences.additionalArguments).toEqual([
      "--channel-app-origin=app://-",
    ]);
  });

  it("tells the preload which origin may hold the bridge (dev server)", async () => {
    const { BrowserWindow } = await import("electron");
    process.env.VITE_DEV_SERVER_URL = "http://localhost:5173";
    createMainWindow({ preloadPath: "/preload.js" });
    expect(BrowserWindow.mock.calls[0][0].webPreferences.additionalArguments).toEqual([
      "--channel-app-origin=http://localhost:5173",
    ]);
  });

  it("passes no origin flag when the base URL is unparseable, so the preload fails closed", async () => {
    const { BrowserWindow } = await import("electron");
    process.env.VITE_DEV_SERVER_URL = "::::not a url";
    createMainWindow({ preloadPath: "/preload.js" });
    expect(BrowserWindow.mock.calls[0][0].webPreferences.additionalArguments).toEqual([]);
  });
});
