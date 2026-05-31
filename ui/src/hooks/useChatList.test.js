// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

vi.mock("../api.js", () => ({
  listChats: vi.fn(),
  createChat: vi.fn(),
  patchChat: vi.fn(),
}));

import * as api from "../api.js";
import { useChatList } from "./useChatList.js";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useChatList", () => {
  it("loads chats on mount", async () => {
    api.listChats.mockResolvedValue({
      items: [{ chat_id: "a", title: "A" }],
      next_cursor: null,
    });
    const { result } = renderHook(() => useChatList());

    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.chats).toEqual([{ chat_id: "a", title: "A" }]);
  });

  it("optimistically prepends on createChat", async () => {
    api.listChats.mockResolvedValue({ items: [], next_cursor: null });
    api.createChat.mockResolvedValue({
      chat_id: "new",
      title: "New chat",
      created_at: "t",
      last_message_at: "t",
      model_default: "m",
      message_count: 0,
      archived: false,
    });
    const { result } = renderHook(() => useChatList());
    await waitFor(() => expect(result.current.status).toBe("idle"));

    let created;
    await act(async () => {
      created = await result.current.createChat();
    });

    expect(created.chat_id).toBe("new");
    expect(result.current.chats[0].chat_id).toBe("new");
  });

  it("optimistically updates title via renameChat", async () => {
    api.listChats.mockResolvedValue({
      items: [
        { chat_id: "a", title: "Old" },
        { chat_id: "b", title: "Other" },
      ],
      next_cursor: null,
    });
    api.patchChat.mockResolvedValue(undefined);
    const { result } = renderHook(() => useChatList());
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => result.current.renameChat("a", "Renamed"));
    expect(result.current.chats[0].title).toBe("Renamed");
    // Non-matching chat is left untouched (covers the false branch of the
    // map's chat_id check).
    expect(result.current.chats[1].title).toBe("Other");
  });

  it("optimistically archives a chat", async () => {
    api.listChats.mockResolvedValue({
      items: [{ chat_id: "a", archived: false }],
      next_cursor: null,
    });
    api.patchChat.mockResolvedValue(undefined);
    const { result } = renderHook(() => useChatList());
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => result.current.archiveChat("a"));
    expect(result.current.chats.find((c) => c.chat_id === "a")).toBeUndefined();
  });

  it("sets error status on initial fetch failure", async () => {
    api.listChats.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useChatList());
    await waitFor(() => expect(result.current.status).toBe("error"));
  });

  it("refresh re-fetches and replaces chats", async () => {
    api.listChats.mockResolvedValueOnce({
      items: [{ chat_id: "a", title: "First" }],
      next_cursor: null,
    });
    const { result } = renderHook(() => useChatList());
    await waitFor(() => expect(result.current.status).toBe("idle"));

    api.listChats.mockResolvedValueOnce({
      items: [{ chat_id: "b", title: "Second" }],
      next_cursor: null,
    });

    await act(async () => result.current.refresh());
    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.chats).toEqual([{ chat_id: "b", title: "Second" }]);
  });
});
