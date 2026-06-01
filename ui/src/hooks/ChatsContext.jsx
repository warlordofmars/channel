// Copyright (c) 2026 John Carter. All rights reserved.
import React, { createContext, useContext } from "react";
import { useChatList } from "./useChatList.js";

/**
 * Shared `useChatList` instance for the chat-app routes.
 *
 * Sidebar, ChatHome, and Conversation all need to read the chat list and
 * call createChat / renameChat / archiveChat against the same in-memory
 * state. Calling `useChatList()` independently in each component would
 * create separate hook instances with separate optimistic state, so a
 * createChat from ChatHome would not appear in Sidebar's Recents list.
 *
 * `<ChatsProvider>` wraps the `/app/*` route tree (see App.jsx) and runs
 * the hook exactly once; descendants read from it via `useChats()`.
 */
const ChatsContext = createContext(null);

export function ChatsProvider({ children }) {
  const {
    chats,
    status,
    refresh,
    createChat,
    renameChat,
    renameChatLocal,
    archiveChat,
    deleteChat,
  } = useChatList();
  const value = {
    chats,
    status,
    refresh,
    createChat,
    renameChat,
    renameChatLocal,
    archiveChat,
    deleteChat,
  };
  return <ChatsContext.Provider value={value}>{children}</ChatsContext.Provider>;
}

export function useChats() {
  const ctx = useContext(ChatsContext);
  if (ctx == null) {
    throw new Error("useChats must be used inside a <ChatsProvider>");
  }
  return ctx;
}
