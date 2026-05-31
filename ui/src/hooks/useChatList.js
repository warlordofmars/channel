// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";

/**
 * Drives the sidebar Recents list and exposes optimistic chat-lifecycle
 * actions (create / rename / archive). Failures revert state and set
 * `status === "error"` for the caller to display.
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

  const archiveChat = useCallback(async (chatId) => {
    setChats((prev) => prev.filter((c) => c.chat_id !== chatId));
    await api.patchChat(chatId, { archived: true });
  }, []);

  return { chats, status, refresh, createChat, renameChat, archiveChat };
}
