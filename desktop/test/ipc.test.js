// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockElectron } from "./_helpers.js";

vi.mock("electron", () => mockElectron());

const { registerIpc } = await import("../main/ipc.js");

beforeEach(() => {
  vi.clearAllMocks();
});

function makeEvent(senderId) {
  return { sender: { id: senderId } };
}

describe("registerIpc", () => {
  it("registers all channels on ipcMain", async () => {
    const { ipcMain } = await import("electron");
    registerIpc({
      mainWindowId: 7,
      handlers: { login: vi.fn(), logout: vi.fn(), getVersion: vi.fn(), relaunchToUpdate: vi.fn() },
    });
    const channels = ipcMain.handle.mock.calls.map(([ch]) => ch);
    expect(channels).toEqual([
      "desktop:login",
      "desktop:logout",
      "desktop:version",
      "desktop:relaunch-to-update",
    ]);
  });

  it("forwards login to the provided handler when sender matches", async () => {
    const { ipcMain } = await import("electron");
    const login = vi.fn().mockResolvedValue("the-jwt");
    registerIpc({ mainWindowId: 7, handlers: { login, logout: vi.fn(), getVersion: vi.fn() } });
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:login")[1];
    const result = await handler(makeEvent(7));
    expect(login).toHaveBeenCalled();
    expect(result).toBe("the-jwt");
  });

  it("rejects with SENDER_FORBIDDEN when sender does not match the main window", async () => {
    const { ipcMain } = await import("electron");
    registerIpc({
      mainWindowId: 7,
      handlers: { login: vi.fn(), logout: vi.fn(), getVersion: vi.fn() },
    });
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:login")[1];
    await expect(handler(makeEvent(99))).rejects.toThrow("SENDER_FORBIDDEN");
  });

  it("forwards logout to its handler", async () => {
    const { ipcMain } = await import("electron");
    const logout = vi.fn().mockResolvedValue(undefined);
    registerIpc({ mainWindowId: 7, handlers: { login: vi.fn(), logout, getVersion: vi.fn() } });
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:logout")[1];
    await handler(makeEvent(7));
    expect(logout).toHaveBeenCalled();
  });

  it("forwards getVersion to its handler", async () => {
    const { ipcMain } = await import("electron");
    const getVersion = vi.fn().mockReturnValue("9.9.9");
    registerIpc({ mainWindowId: 7, handlers: { login: vi.fn(), logout: vi.fn(), getVersion } });
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:version")[1];
    expect(await handler(makeEvent(7))).toBe("9.9.9");
  });

  it("registers desktop:relaunch-to-update and forwards to the relaunchToUpdate handler", async () => {
    const { ipcMain } = await import("electron");
    const relaunchToUpdate = vi.fn();
    registerIpc({
      mainWindowId: 7,
      handlers: { login: vi.fn(), logout: vi.fn(), getVersion: vi.fn(), relaunchToUpdate },
    });
    const channels = ipcMain.handle.mock.calls.map(([ch]) => ch);
    expect(channels).toContain("desktop:relaunch-to-update");
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:relaunch-to-update")[1];
    await handler(makeEvent(7));
    expect(relaunchToUpdate).toHaveBeenCalled();
  });
});
