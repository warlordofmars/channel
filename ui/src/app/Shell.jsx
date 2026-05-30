// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Sidebar from "./Sidebar.jsx";

/**
 * Wraps every authenticated chat-app route with the persistent Sidebar.
 * Translated from the `ChannelApp` top-level orchestrator in
 * design-sources/app/shell.jsx. Phase 6c keeps it minimal: Sidebar + main;
 * Phase 6e adds the MainTop bar for share/etc.
 */
export default function Shell({ children }) {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="app-main">{children}</main>
    </div>
  );
}
