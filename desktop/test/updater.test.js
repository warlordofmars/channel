// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mockElectron } from "./_helpers.js";

vi.mock("electron", () => mockElectron());
vi.mock("electron-updater", () => ({
  autoUpdater: {
    setFeedURL: vi.fn(),
    on: vi.fn(),
    checkForUpdates: vi.fn(),
    quitAndInstall: vi.fn(),
  },
}));

const { init } = await import("../main/updater.js");

let originalPlatform;
beforeEach(() => {
  vi.clearAllMocks();
  originalPlatform = process.platform;
});
afterEach(() => {
  Object.defineProperty(process, "platform", { value: originalPlatform });
});

function setPlatform(p) {
  Object.defineProperty(process, "platform", { value: p });
}

describe("updater.init platform gate", () => {
  it("does nothing on win32", async () => {
    setPlatform("win32");
    const { autoUpdater } = await import("electron-updater");
    init({ channel: "latest", webContents: { send: vi.fn() } });
    expect(autoUpdater.setFeedURL).not.toHaveBeenCalled();
    expect(autoUpdater.checkForUpdates).not.toHaveBeenCalled();
  });

  it("does nothing on linux", async () => {
    setPlatform("linux");
    const { autoUpdater } = await import("electron-updater");
    init({ channel: "latest", webContents: { send: vi.fn() } });
    expect(autoUpdater.setFeedURL).not.toHaveBeenCalled();
  });
});

describe("updater.init feed URL mapping", () => {
  beforeEach(() => setPlatform("darwin"));

  it("uses the prod URL for the latest channel", async () => {
    const { autoUpdater } = await import("electron-updater");
    init({ channel: "latest", webContents: { send: vi.fn() } });
    expect(autoUpdater.setFeedURL).toHaveBeenCalledWith({
      provider: "generic",
      url: "https://channel.warlordofmars.net/updates/latest",
    });
  });

  it("uses the dev URL for the dev channel", async () => {
    const { autoUpdater } = await import("electron-updater");
    init({ channel: "dev", webContents: { send: vi.fn() } });
    expect(autoUpdater.setFeedURL).toHaveBeenCalledWith({
      provider: "generic",
      url: "https://channel-dev.warlordofmars.net/updates/dev",
    });
  });

  it("throws on unknown channels", () => {
    expect(() =>
      init({ channel: "beta", webContents: { send: vi.fn() } }),
    ).toThrow(/unknown channel: beta/);
  });
});

describe("updater.init IPC push events", () => {
  beforeEach(() => setPlatform("darwin"));

  it("subscribes to autoUpdater events with handlers that forward to webContents", async () => {
    const { autoUpdater } = await import("electron-updater");
    const send = vi.fn();
    init({ channel: "latest", webContents: { send } });

    const eventNames = autoUpdater.on.mock.calls.map(([name]) => name);
    expect(eventNames).toEqual(
      expect.arrayContaining(["checking-for-update", "update-available", "update-downloaded", "error"]),
    );

    // Fire each registered handler and assert the IPC payload shape.
    const handlerByEvent = Object.fromEntries(autoUpdater.on.mock.calls);
    handlerByEvent["checking-for-update"]();
    expect(send).toHaveBeenLastCalledWith("desktop:update-status", { state: "checking" });

    handlerByEvent["update-available"]({ version: "0.2.1" });
    expect(send).toHaveBeenLastCalledWith("desktop:update-status", { state: "available", version: "0.2.1" });

    handlerByEvent["update-downloaded"]({ version: "0.2.1" });
    expect(send).toHaveBeenLastCalledWith("desktop:update-status", { state: "downloaded", version: "0.2.1" });

    handlerByEvent["error"](new Error("notary down"));
    expect(send).toHaveBeenLastCalledWith("desktop:update-status", { state: "error", message: "notary down" });
  });

  it("exposes relaunchToUpdate which calls autoUpdater.quitAndInstall", async () => {
    const { autoUpdater } = await import("electron-updater");
    const { relaunchToUpdate } = await import("../main/updater.js");
    relaunchToUpdate();
    expect(autoUpdater.quitAndInstall).toHaveBeenCalled();
  });
});

describe("updater.init periodic check", () => {
  beforeEach(() => {
    setPlatform("darwin");
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it("schedules a checkForUpdates every 4 hours after initial call", async () => {
    const { autoUpdater } = await import("electron-updater");
    init({ channel: "latest", webContents: { send: vi.fn() } });
    expect(autoUpdater.checkForUpdates).toHaveBeenCalledTimes(1); // initial
    vi.advanceTimersByTime(4 * 60 * 60 * 1000);
    expect(autoUpdater.checkForUpdates).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(4 * 60 * 60 * 1000);
    expect(autoUpdater.checkForUpdates).toHaveBeenCalledTimes(3);
  });
});
