// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { BrowserRouter, Outlet, Route, Routes } from "react-router-dom";
import AuthGate from "./components/AuthGate.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { useChannelPrefs } from "./hooks/useChannelPrefs.js";
import { ChatsProvider } from "./hooks/ChatsContext.jsx";
import AdminHome from "./app/admin/AdminHome.jsx";
import AdminLayout from "./app/admin/AdminLayout.jsx";
import Artifacts from "./app/views/Artifacts.jsx";
import ChatHome from "./app/ChatHome.jsx";
import Conversation from "./app/Conversation.jsx";
import Customize from "./app/views/Customize.jsx";
import Login from "./app/Login.jsx";
import ProjectDetail from "./app/views/ProjectDetail.jsx";
import Projects from "./app/views/Projects.jsx";
import Shell from "./app/Shell.jsx";
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
 * Placeholder for admin child routes whose real views land in
 * follow-up issues (#238 users list/detail, #239 dashboard). Those
 * issues replace the `<AdminPlaceholder />` element in the route table
 * below with their component — the route paths and the AdminLayout
 * nesting are already final.
 */
function AdminPlaceholder({ title, testid }) {
  return (
    <div className="view">
      <div className="view-inner">
        <div className="view-head" data-testid={testid}>
          <h2>{title}</h2>
          <p>Coming soon.</p>
        </div>
      </div>
    </div>
  );
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
  return (
    <ErrorBoundary>
      <BrowserRouter>
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
            {/* Admin — nested under AdminLayout so the role gate runs
                exactly once for the whole /app/admin/* subtree (#237) */}
            <Route element={<AdminLayout />}>
              <Route path="/app/admin"           element={<AdminHome />} />
              <Route path="/app/admin/users"     element={<AdminPlaceholder title="Users" testid="admin-users-placeholder" />} />
              <Route path="/app/admin/users/:id" element={<AdminPlaceholder title="User detail" testid="admin-user-detail-placeholder" />} />
              <Route path="/app/admin/dashboard" element={<AdminPlaceholder title="Dashboard" testid="admin-dashboard-placeholder" />} />
            </Route>
          </Route>

          {/* Anything else → branded 404 */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
