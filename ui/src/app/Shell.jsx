// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Sidebar from "./Sidebar.jsx";
import ChannelMark from "../components/ChannelMark.jsx";
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
 *
 * Mobile (#425): on narrow viewports (≤640px, gated entirely in app.css)
 * the fixed sidebar becomes an off-canvas drawer. `mobileNavOpen` drives
 * the `mobile-open` class on the Sidebar (slides it in) plus a
 * `.mobile-nav-backdrop` click-catcher; a compact `.mobile-topbar` with a
 * hamburger opens it. Any route change closes the drawer so tapping a nav
 * item lands the user on the destination with the drawer shut; the
 * backdrop closes it directly. Desktop is byte-for-byte unchanged — the
 * mobile chrome is `display:none` above 640px.
 */
export default function Shell({ children }) {
  const { theme } = useChannelPrefs();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { chats } = useChats();
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
  // Close the mobile drawer whenever the route changes — tapping New
  // chat / Projects / a recent / etc. navigates, which slides the drawer
  // shut. Only real navigations close it, so opening the account popover
  // (no route change) leaves the drawer open. No-op on desktop where the
  // drawer never opens.
  useEffect(function closeDrawerOnNavigate() {
    setMobileNavOpen(false);
  }, [location.pathname]);
  const toggleSidebar = () => setCollapsed((c) => !c);
  function openMobileNav() {
    setMobileNavOpen(true);
  }
  function closeMobileNav() {
    setMobileNavOpen(false);
  }
  // Match Claude Desktop's behaviour: "+ New chat" takes you to the
  // welcome / greeting screen at /app. The actual chat record is
  // created lazily by ChatHome when the user sends their first
  // message — so the sidebar never accumulates empty "New chat"
  // zombies.
  const handleNewChat = () => {
    navigate("/app");
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
            mobileOpen={mobileNavOpen}
          />
          {mobileNavOpen && (
            <div
              className="mobile-nav-backdrop"
              aria-hidden="true"
              onClick={closeMobileNav}
            />
          )}
          <main className="main">
            <div className="mobile-topbar">
              <button
                type="button"
                className="icon-btn mobile-menu-btn"
                aria-label="Open menu"
                onClick={openMobileNav}
              >
                <Icon name="menu" size={22} />
              </button>
              <span className="mobile-topbar-brand" aria-hidden="true">
                <ChannelMark size={22} />
              </span>
            </div>
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
