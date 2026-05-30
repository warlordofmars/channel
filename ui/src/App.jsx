// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import AuthGate from "./components/AuthGate.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { useChannelPrefs } from "./hooks/useChannelPrefs.js";

// Placeholders — each subsequent phase (6b/6c/6d/6e/6f) replaces the
// matching placeholder with a real component.
const ph = (testid, label) => (
  <div data-testid={testid} style={{ padding: 24, color: "var(--ink)" }}>
    {label} — placeholder, implemented in a later phase.
  </div>
);

const MarketingHome      = () => ph("marketing-home", "Marketing: Home");
const MarketingProduct   = () => ph("marketing-product", "Marketing: Product");
const MarketingModels    = () => ph("marketing-models", "Marketing: Models");
const MarketingPricing   = () => ph("marketing-pricing", "Marketing: Pricing");
const MarketingDownload  = () => ph("marketing-download", "Marketing: Download");
const MarketingAbout     = () => ph("marketing-about", "Marketing: About");
const MarketingBlog      = () => ph("marketing-blog", "Marketing: Blog");
const MarketingCareers   = () => ph("marketing-careers", "Marketing: Careers");
const MarketingPrivacy   = () => ph("marketing-privacy", "Marketing: Privacy");
const MarketingNotFound  = () => ph("marketing-notfound", "404");

const AppLogin           = () => ph("app-login", "App: Login");
const AppHome            = () => ph("app-home", "App: Home");
const AppConversation    = () => ph("app-conversation", "App: Conversation");
const AppProjects        = () => ph("app-projects", "App: Projects");
const AppProjectDetail   = () => ph("app-project-detail", "App: Project Detail");
const AppArtifacts       = () => ph("app-artifacts", "App: Artifacts");
const AppCustomize       = () => ph("app-customize", "App: Customize");

export default function App() {
  // Apply theme/accent/density/etc. to <html> on every mount.
  useChannelPrefs();

  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Routes>
          {/* Marketing — public */}
          <Route path="/"          element={<MarketingHome />} />
          <Route path="/product"   element={<MarketingProduct />} />
          <Route path="/models"    element={<MarketingModels />} />
          <Route path="/pricing"   element={<MarketingPricing />} />
          <Route path="/download"  element={<MarketingDownload />} />
          <Route path="/about"     element={<MarketingAbout />} />
          <Route path="/blog"      element={<MarketingBlog />} />
          <Route path="/careers"   element={<MarketingCareers />} />
          <Route path="/privacy"   element={<MarketingPrivacy />} />

          {/* App — /app/login is public; everything else gates on the JWT */}
          <Route path="/app/login"          element={<AppLogin />} />
          <Route path="/app"                element={<AuthGate><AppHome /></AuthGate>} />
          <Route path="/app/c/:id"          element={<AuthGate><AppConversation /></AuthGate>} />
          <Route path="/app/projects"       element={<AuthGate><AppProjects /></AuthGate>} />
          <Route path="/app/projects/:id"   element={<AuthGate><AppProjectDetail /></AuthGate>} />
          <Route path="/app/artifacts"      element={<AuthGate><AppArtifacts /></AuthGate>} />
          <Route path="/app/customize"      element={<AuthGate><AppCustomize /></AuthGate>} />

          {/* Anything else (marketing or app catch-all) → branded 404 */}
          <Route path="*" element={<MarketingNotFound />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
