// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect } from "react";
import { BrowserRouter, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { setSessionEndNavigator } from "./api.js";
import AuthGate from "./components/AuthGate.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { startAppViewportSync } from "./lib/appViewport.js";
import { useChannelPrefs } from "./hooks/useChannelPrefs.js";
import { ChatsProvider } from "./hooks/ChatsContext.jsx";
import AdminHome from "./app/admin/AdminHome.jsx";
import AdminLayout from "./app/admin/AdminLayout.jsx";
import Dashboard from "./app/admin/Dashboard.jsx";
import Artifacts from "./app/views/Artifacts.jsx";
import ChatHome from "./app/ChatHome.jsx";
import Conversation from "./app/Conversation.jsx";
import Customize from "./app/views/Customize.jsx";
import Login from "./app/Login.jsx";
import ProjectDetail from "./app/views/ProjectDetail.jsx";
import Projects from "./app/views/Projects.jsx";
import Sessions from "./app/views/Sessions.jsx";
import Shell from "./app/Shell.jsx";
import UserDetail from "./app/admin/UserDetail.jsx";
import Users from "./app/admin/Users.jsx";
import About from "./marketing/pages/About.jsx";
import Blog from "./marketing/pages/Blog.jsx";
import Careers from "./marketing/pages/Careers.jsx";
import Download from "./marketing/pages/Download.jsx";
import Home from "./marketing/pages/Home.jsx";
import Models from "./marketing/pages/Models.jsx";
import NotFound from "./marketing/pages/NotFound.jsx";
import Pricing from "./marketing/pages/Pricing.jsx";
import Privacy from "./marketing/pages/Privacy.jsx";
import Product from "./marketing/pages/Product.jsx";


/**
 * Effect cleanup for {@link SessionEndNavigator}, at module scope so the
 * effect registers a stable reference rather than a fresh closure per run.
 */
function unregisterSessionEndNavigator() {
  setSessionEndNavigator(null);
}

/**
 * Hands `api.js` this router's `navigate`, so a session that ends
 * mid-visit redirects in-app instead of reloading the document (#483).
 *
 * `endSession` is a plain module function and cannot call `useNavigate()`
 * itself, so something inside the router has to pass it down; this
 * component is that one line. It renders nothing and sits directly under
 * `BrowserRouter` rather than inside a route element, because it must
 * outlive every individual route: `endSession` fires from `api.js` on any
 * screen that talks to the API, and a registrant scoped to one route
 * would be unmounted — and therefore unregistered — for the others.
 *
 * Unregistering on unmount is not tidiness. `api.js` cannot tell a live
 * `navigate` from one belonging to a torn-down router, and the latter
 * silently does nothing; clearing the slot is what makes `endSession`
 * fall back to its hard navigation in that window instead of leaving the
 * user on a page whose session it just destroyed.
 */
function SessionEndNavigator() {
  const navigate = useNavigate();
  useEffect(function registerNavigator() {
    setSessionEndNavigator(navigate);
    return unregisterSessionEndNavigator;
  }, [navigate]);
  return null;
}

/**
 * Layout for every authenticated `/app/*` route. Mounts the AuthGate +
 * ChatsProvider exactly once so the chat-list hook instance persists
 * across route navigations (no double-fetch when moving between
 * `/app`, `/app/c/{id}`, `/app/projects`, etc.) and Sidebar / ChatHome /
 * Conversation all read the same in-memory chat-list state.
 */
function AppLayout() {
  return (
    <AuthGate>
      <ChatsProvider>
        <Shell>
          <Outlet />
        </Shell>
      </ChatsProvider>
    </AuthGate>
  );
}

export default function App() {
  useChannelPrefs();
  // Corrects the app layer's height when iOS reports a layout viewport
  // shorter than the window the installed PWA actually fills (#467).
  // A no-op wherever the two agree, which is every desktop engine — see
  // `lib/appViewport.js`. Returns its own teardown.
  useEffect(startAppViewportSync, []);
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <SessionEndNavigator />
        <Routes>
          {/* Marketing — public */}
          <Route path="/"          element={<Home />} />
          <Route path="/product"   element={<Product />} />
          <Route path="/models"    element={<Models />} />
          <Route path="/pricing"   element={<Pricing />} />
          <Route path="/download"  element={<Download />} />
          <Route path="/about"     element={<About />} />
          <Route path="/blog"      element={<Blog />} />
          <Route path="/careers"   element={<Careers />} />
          <Route path="/privacy"   element={<Privacy />} />

          {/* App — /app/login is public; everything else gates on the JWT */}
          <Route path="/app/login"          element={<Login />} />
          <Route element={<AppLayout />}>
            <Route path="/app"                element={<ChatHome />} />
            <Route path="/app/c/:id"          element={<Conversation />} />
            <Route path="/app/projects"       element={<Projects />} />
            <Route path="/app/projects/:id"   element={<ProjectDetail />} />
            <Route path="/app/artifacts"      element={<Artifacts />} />
            <Route path="/app/customize"      element={<Customize />} />
            <Route path="/app/sessions"       element={<Sessions />} />
            {/* Admin — nested under AdminLayout so the role gate runs
                exactly once for the whole /app/admin/* subtree (#237) */}
            <Route element={<AdminLayout />}>
              <Route path="/app/admin"           element={<AdminHome />} />
              <Route path="/app/admin/users"     element={<Users />} />
              <Route path="/app/admin/users/:id" element={<UserDetail />} />
              <Route path="/app/admin/dashboard" element={<Dashboard />} />
            </Route>
          </Route>

          {/* Anything else → branded 404 */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
