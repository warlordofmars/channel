// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import AuthGate from "./components/AuthGate.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { useChannelPrefs } from "./hooks/useChannelPrefs.js";
import ChatHome from "./app/ChatHome.jsx";
import Conversation from "./app/Conversation.jsx";
import Login from "./app/Login.jsx";
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

// App-side placeholders — replaced in Phase 6c (chat shell + login + home).
const ph = (testid, label) => (
  <div data-testid={testid} style={{ padding: 24, color: "var(--ink)" }}>
    {label} — placeholder, implemented in a later phase.
  </div>
);
const AppProjects      = () => ph("app-projects", "App: Projects");
const AppProjectDetail = () => ph("app-project-detail", "App: Project Detail");
const AppArtifacts     = () => ph("app-artifacts", "App: Artifacts");
const AppCustomize     = () => ph("app-customize", "App: Customize");

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
          <Route path="/app"                element={<AuthGate><Shell><ChatHome /></Shell></AuthGate>} />
          <Route path="/app/c/:id"          element={<AuthGate><Shell><Conversation /></Shell></AuthGate>} />
          <Route path="/app/projects"       element={<AuthGate><AppProjects /></AuthGate>} />
          <Route path="/app/projects/:id"   element={<AuthGate><AppProjectDetail /></AuthGate>} />
          <Route path="/app/artifacts"      element={<AuthGate><AppArtifacts /></AuthGate>} />
          <Route path="/app/customize"      element={<AuthGate><AppCustomize /></AuthGate>} />

          {/* Anything else → branded 404 */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
