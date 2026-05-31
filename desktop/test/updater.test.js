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
