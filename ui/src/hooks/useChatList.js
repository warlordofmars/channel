// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";

/**
 * Drives the sidebar Recents list and exposes chat-lifecycle actions
 * (create / rename / archive). Mutations apply optimistic local state
 * BEFORE awaiting the API; failures of the mutation API call do NOT
 * revert local state — callers should `try/catch` on the returned
 * promise if they want to surface the error. `status === "error"` is
 * only set when the initial fetch (or `refresh()`) fails; mutation
 * failures keep `status === "idle"`.
 */
export function useChatList() {
  const [chats, setChats] = useState([]);
  const [status, setStatus] = useState("loading");

  const refresh = useCallback(async () => {
    setStatus("loading");
    try {
      const { items } = await api.listChats();
      setChats(items);
      setStatus("idle");
    } catch {
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const createChat = useCallback(async (opts = {}) => {
    const chat = await api.createChat(opts);
    setChats((prev) => [chat, ...prev]);
    return chat;
  }, []);

  const renameChat = useCallback(async (chatId, title) => {
    setChats((prev) =>
      prev.map((c) => (c.chat_id === chatId ? { ...c, title } : c)),
    );
    await api.patchChat(chatId, { title });
  }, []);

  /**
   * Local-only rename — does NOT call the server. Phase 7d auto-title
   * uses this when the backend emits a ``title_suggested`` SSE event:
   * the server has already persisted the new title via storage.patch_chat,
   * so the client only needs to update its in-memory state.
   *
   * No-op if ``chatId`` doesn't match any local row (e.g., the chat
   * list hasn't been refetched yet); the next refresh picks up the
   * persisted title.
   */
  const renameChatLocal = useCallback((chatId, title) => {
    setChats((prev) =>
      prev.map((c) => (c.chat_id === chatId ? { ...c, title } : c)),
    );
  }, []);

  const archiveChat = useCallback(async (chatId) => {
    setChats((prev) => prev.filter((c) => c.chat_id !== chatId));
    await api.patchChat(chatId, { archived: true });
  }, []);

  return {
    chats,
    status,
    refresh,
    createChat,
    renameChat,
    renameChatLocal,
    archiveChat,
  };
}
