// Copyright (c) 2026 John Carter. All rights reserved.
import { vi } from "vitest";

/**
 * Returns a stub object suitable for vi.mock("electron", () => mockElectron(...)).
 * Pass overrides keyed by submodule name to extend or replace individual mocks
 * in a specific test.
 */
export function mockElectron(overrides = {}) {
  return {
    app: {
      on: vi.fn(),
      whenReady: vi.fn(() => Promise.resolve()),
      quit: vi.fn(),
      getVersion: vi.fn(() => "0.0.0"),
      getPath: vi.fn(() => "/tmp"),
      ...overrides.app,
    },
    BrowserWindow: vi.fn().mockImplementation(function () {
      Object.assign(this, {
        loadURL: vi.fn(() => Promise.resolve()),
        on: vi.fn(),
        once: vi.fn(),
        hide: vi.fn(),
        show: vi.fn(),
        webContents: { id: 1, on: vi.fn() },
        ...overrides.windowInstance,
      });
    }),
    ipcMain: { handle: vi.fn(), removeHandler: vi.fn() },
    ipcRenderer: { invoke: vi.fn(), on: vi.fn() },
    contextBridge: { exposeInMainWorld: vi.fn() },
    shell: { openExternal: vi.fn(() => Promise.resolve()) },
    protocol: {
      registerSchemesAsPrivileged: vi.fn(),
      handle: vi.fn(),
    },
    ...overrides,
  };
}
