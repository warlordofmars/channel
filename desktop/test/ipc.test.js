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
      "desktop:token-read",
      "desktop:token-write",
      "desktop:token-clear",
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

  // #297 — the keychain channels. Each is checked for its own routing
  // AND for the sender guard, because these are the three that hand back
  // (or overwrite) a 30-day credential.
  it("forwards token-read to its handler and returns the stored session", async () => {
    const { ipcMain } = await import("electron");
    const tokenRead = vi.fn().mockResolvedValue({ refresh_token: "rt" });
    registerIpc({ mainWindowId: 7, handlers: { tokenRead } });
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:token-read")[1];
    expect(await handler(makeEvent(7))).toEqual({ refresh_token: "rt" });
  });

  it("forwards token-write to its handler, passing the session through", async () => {
    const { ipcMain } = await import("electron");
    const tokenWrite = vi.fn().mockResolvedValue(undefined);
    registerIpc({ mainWindowId: 7, handlers: { tokenWrite } });
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:token-write")[1];
    await handler(makeEvent(7), { refresh_token: "rt" });
    expect(tokenWrite).toHaveBeenCalledWith({ refresh_token: "rt" });
  });

  it("forwards token-clear to its handler", async () => {
    const { ipcMain } = await import("electron");
    const tokenClear = vi.fn().mockResolvedValue(undefined);
    registerIpc({ mainWindowId: 7, handlers: { tokenClear } });
    const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === "desktop:token-clear")[1];
    await handler(makeEvent(7));
    expect(tokenClear).toHaveBeenCalled();
  });

  it.each(["desktop:token-read", "desktop:token-write", "desktop:token-clear"])(
    "rejects %s from any sender but the main window",
    async (channel) => {
      const { ipcMain } = await import("electron");
      const handlers = { tokenRead: vi.fn(), tokenWrite: vi.fn(), tokenClear: vi.fn() };
      registerIpc({ mainWindowId: 7, handlers });
      const handler = ipcMain.handle.mock.calls.find(([ch]) => ch === channel)[1];
      await expect(handler(makeEvent(99))).rejects.toThrow("SENDER_FORBIDDEN");
      expect(handlers.tokenRead).not.toHaveBeenCalled();
      expect(handlers.tokenWrite).not.toHaveBeenCalled();
      expect(handlers.tokenClear).not.toHaveBeenCalled();
    },
  );

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
