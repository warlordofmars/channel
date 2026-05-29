// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Menu, Moon, Sun, X } from "lucide-react";
import { trackEvent, trackPageView } from "./analytics.js";
import EmptyState from "./components/EmptyState.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import LoginPage from "./components/LoginPage.jsx";
import NotFoundPage from "./components/NotFoundPage.jsx";
import UsersPanel from "./components/UsersPanel.jsx";
import { Button } from "./components/ui/button.jsx";
import { Toaster } from "./components/ui/sonner.jsx";
import { useTheme } from "./hooks/useTheme.js";

const TOKEN_KEY = "starter_mgmt_token";
const SWITCH_TAB_EVENT = "starter:switch-tab";

const ADMIN_TABS = [
  { id: "users", label: "Users" },
];

function parseToken(token) {
  if (!token) return null;
  try {
    return JSON.parse(atob(token.split(".")[1].replaceAll("-", "+").replaceAll("_", "/")));
  } catch {
    return null;
  }
}

function isTokenValid(token) {
  const payload = parseToken(token);
  return payload ? payload.exp * 1000 > Date.now() : false;
}

function signOut() {
  localStorage.removeItem(TOKEN_KEY);
  globalThis.location.replace("/");
}

function AppShell() {
  const [tab, setTab] = useState("users");
  const [menuOpen, setMenuOpen] = useState(false);
  const [version, setVersion] = useState(null);
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();

  function switchTab(id) {
    setTab(id);
    trackEvent("tab_view", { tab_name: id });
  }

  const token = localStorage.getItem(TOKEN_KEY) ?? "";
  const authenticated = isTokenValid(token);

  useEffect(() => {
    fetch("/health")
      .then((r) => r.json())
      .then((data) => setVersion(data.version ?? null))
      .catch(() => {});
  }, []);

  useEffect(() => {
    function onSwitchTab(e) { switchTab(e.detail); }
    globalThis.addEventListener(SWITCH_TAB_EVENT, onSwitchTab);
    return () => globalThis.removeEventListener(SWITCH_TAB_EVENT, onSwitchTab);
  }, []);

  if (!authenticated) {
    return <LoginPage />;
  }

  const claims = parseToken(token);
  const isAdmin = claims.role === "admin";
  const userEmail = claims.email ?? "";
  const tabs = isAdmin ? ADMIN_TABS : [];

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-navy text-white px-4 md:px-6 flex items-center gap-3 md:gap-6 h-14 relative">
        <button
          onClick={() => navigate("/")}
          className="flex items-center gap-2 cursor-pointer bg-transparent border-none p-0 text-inherit"
        >
          <img src="/logo.svg" alt="Channel" className="w-7 h-7" />
          <span className="font-bold text-xl tracking-wide">Channel</span>
        </button>

        {/* Desktop tab nav — single active tab today; the active/inactive
            split returns when the gutted Dashboard / additional admin
            surfaces ship (see #35). */}
        <nav className="hidden md:flex gap-1 flex-1">
          {tabs.map((t) => (
            <Button
              key={t.id}
              data-tab-id={t.id}
              variant="ghost"
              size="sm"
              onClick={() => switchTab(t.id)}
              className="text-sm border-b-2 rounded-none pb-0 border-b-brand"
            >
              {t.label}
            </Button>
          ))}
        </nav>

        {/* Spacer on mobile */}
        <div className="flex-1 md:hidden" />

        {userEmail && (
          <span className="hidden md:inline text-[13px] text-white/70">{userEmail}</span>
        )}

        <Button variant="outline" size="sm" onClick={signOut}>
          Sign out
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={toggle}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </Button>

        {/* Hamburger — mobile only */}
        <Button
          variant="ghost"
          size="sm"
          className="md:hidden text-white hover:bg-white/10"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-label="Toggle navigation"
          aria-expanded={menuOpen}
        >
          {menuOpen ? <X size={20} /> : <Menu size={20} />}
        </Button>

        {/* Mobile nav dropdown */}
        {menuOpen && (
          <nav
            data-testid="mobile-nav"
            className="absolute top-14 left-0 right-0 bg-navy border-t border-white/10 z-50"
          >
            {/* Mobile tabs — single active tab today; the active/inactive
                split returns alongside the desktop nav split. */}
            {tabs.map((t) => (
              <button
                key={t.id}
                data-tab-id={t.id}
                type="button"
                className="w-full text-left px-6 py-3 text-sm text-white bg-transparent cursor-pointer font-[inherit] min-h-[44px] hover:bg-white/5 border-l-2 font-semibold border-l-brand"
                onClick={() => { switchTab(t.id); setMenuOpen(false); }}
              >
                {t.label}
              </button>
            ))}
          </nav>
        )}
      </header>

      <main className="flex-1 p-4 md:p-6 max-w-[1100px] mx-auto w-full">
        {isAdmin ? (
          <>
            {tab === "users" && <UsersPanel />}
          </>
        ) : (
          <EmptyState
            variant="users"
            title="Welcome"
            description="You're signed in. Contact an admin to get access."
          />
        )}
      </main>

      {version && (
        <footer className="text-center py-2 text-xs text-[var(--text-muted)] border-t border-[var(--border)]">
          <a
            href="/changelog"
            className="text-inherit no-underline hover:underline focus:underline"
          >
            Channel {version}
          </a>
        </footer>
      )}

      <Toaster />
    </div>
  );
}

function HomeRoute() {
  const token = localStorage.getItem(TOKEN_KEY) ?? "";
  if (isTokenValid(token)) {
    return <Navigate to="/app" replace />;
  }
  return <LoginPage />;
}

function RouteTracker() {
  const location = useLocation();
  useEffect(() => {
    trackPageView(location.pathname);
  }, [location.pathname]);
  return null;
}

export default function App() {
  useTheme(); // apply data-theme to <html> for all routes
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <RouteTracker />
        <Routes>
          <Route path="/" element={<HomeRoute />} />
          <Route path="/app" element={<AppShell />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
