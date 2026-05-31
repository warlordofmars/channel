// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Sidebar from "./Sidebar.jsx";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
import { useChats } from "../hooks/ChatsContext.jsx";

/**
 * Wraps every authenticated chat-app route with the persistent Sidebar.
 * Translated from the `ChannelApp` top-level orchestrator in
 * design-sources/app/shell.jsx. Phase 6c keeps it minimal: Sidebar + main;
 * Phase 6e adds the MainTop bar's share/settings buttons.
 *
 * Layout uses the design source's `.stage.full > .win > Sidebar + .main`
 * shape (app.css). `.stage` is `position: fixed; inset: 0` so the chat app
 * fills the viewport; `.stage.full` removes the desktop wallpaper padding;
 * `.win` is the flex row holding the 264px sidebar + the `flex: 1` main
 * pane. `.main` already arranges its children (Home/Conversation/Projects/
 * etc.) as a flex column that centers the empty-state per `.home`.
 *
 * Applies the global `theme` to `data-theme` while mounted — SiteLayout
 * and Login do the same, so the single user preference carries across
 * routes without race conditions on `data-theme`.
 *
 * Owns `collapsed` state for the Sidebar — the design's prototype hides
 * the sidebar via `.sb.collapsed { width: 0 }` and renders an expand
 * button at the top of `.main` (`.main-top`) when collapsed.
 */
export default function Shell({ children }) {
  const { theme } = useChannelPrefs();
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const { chats, createChat } = useChats();
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
  const toggleSidebar = () => setCollapsed((c) => !c);
  const handleNewChat = async () => {
    const chat = await createChat();
    navigate(`/app/c/${chat.chat_id}`);
  };
  return (
    <div className="stage full">
      <div className="win">
        <div className="body">
          <Sidebar
            collapsed={collapsed}
            onToggle={toggleSidebar}
            chats={chats}
            onNewChat={handleNewChat}
          />
          <main className="main">
            {collapsed && (
              <div className="main-top">
                <button
                  type="button"
                  className="icon-btn"
                  title="Show sidebar"
                  aria-label="Show sidebar"
                  onClick={toggleSidebar}
                >
                  <Icon name="sidebar" size={18} />
                </button>
                <div className="spacer" />
              </div>
            )}
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
