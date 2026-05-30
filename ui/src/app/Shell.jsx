// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Sidebar from "./Sidebar.jsx";

/**
 * Wraps every authenticated chat-app route with the persistent Sidebar.
 * Translated from the `ChannelApp` top-level orchestrator in
 * design-sources/app/shell.jsx. Phase 6c keeps it minimal: Sidebar + main;
 * Phase 6e adds the MainTop bar for share/etc.
 *
 * Layout uses the design source's `.stage.full > .win > Sidebar + .main`
 * shape (app.css). `.stage` is `position: fixed; inset: 0` so the chat app
 * fills the viewport; `.stage.full` removes the desktop wallpaper padding;
 * `.win` is the flex row holding the 264px sidebar + the `flex: 1` main
 * pane. `.main` already arranges its children (Home/Conversation/Projects/
 * etc.) as a flex column that centers the empty-state per `.home`.
 */
export default function Shell({ children }) {
  return (
    <div className="stage full">
      <div className="win">
        <Sidebar />
        <main className="main">{children}</main>
      </div>
    </div>
  );
}
