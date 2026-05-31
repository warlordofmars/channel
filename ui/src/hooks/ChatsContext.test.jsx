// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../api.js", () => ({
  listChats: vi.fn(),
  createChat: vi.fn(),
  patchChat: vi.fn(),
}));

import * as api from "../api.js";
import { ChatsProvider, useChats } from "./ChatsContext.jsx";

beforeEach(() => {
  vi.clearAllMocks();
  api.listChats.mockResolvedValue({ items: [], next_cursor: null });
});

describe("ChatsContext", () => {
  it("useChats throws when used outside a ChatsProvider", () => {
    // React logs the render error to console.error and jsdom dispatches
    // an unhandled "error" event on the window; silence both so the test
    // output stays clean.
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const swallow = (e) => { e.preventDefault(); };
    window.addEventListener("error", swallow);
    let caught;
    try {
      renderHook(() => useChats());
    } catch (e) {
      caught = e;
    } finally {
      window.removeEventListener("error", swallow);
      errSpy.mockRestore();
    }
    expect(caught).toBeInstanceOf(Error);
    expect(caught.message).toMatch(/must be used inside a <ChatsProvider>/);
  });

  it("ChatsProvider supplies a useChatList value to descendants", async () => {
    const wrapper = ({ children }) => <ChatsProvider>{children}</ChatsProvider>;
    const { result } = renderHook(() => useChats(), { wrapper });

    // Initial render — the hook exposes its full surface even before the
    // mount-time listChats promise resolves.
    expect(result.current).toMatchObject({
      chats: expect.any(Array),
      createChat: expect.any(Function),
      renameChat: expect.any(Function),
      archiveChat: expect.any(Function),
      refresh: expect.any(Function),
      status: expect.any(String),
    });

    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(api.listChats).toHaveBeenCalledTimes(1);
  });
});
