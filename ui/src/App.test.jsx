// Copyright (c) 2026 John Carter. All rights reserved.
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import { TOKEN_KEY } from "./lib/auth.js";
import { __resetChannelPrefsForTest } from "./hooks/useChannelPrefs.js";

function makeToken({ expOffsetSeconds = 3600, role = "user" } = {}) {
  const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role, email: "u@ex.com" }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

/**
 * Stub fetch for the admin-view routing tests (#238): admin endpoints
 * resolve with `body`; everything else rejects, matching the
 * no-network status quo the other routing tests run under.
 */
function stubAdminFetch(body) {
  vi.stubGlobal("fetch", vi.fn(function fakeFetch(url) {
    return String(url).startsWith("/api/admin/users")
      ? Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(body),
        })
      : Promise.reject(new Error(`unexpected fetch in test: ${url}`));
  }));
}

describe("App routing", () => {
  let storage;

  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    __resetChannelPrefsForTest();
  });

  afterEach(() => { vi.unstubAllGlobals(); window.history.pushState({}, "", "/"); });

  it.each([
    ["/",         /workspace for thinking with AI/i],
    ["/product",  null], // Any h1 is OK
    ["/models",   /models|claude/i],
    ["/pricing",  /pricing|simple, honest/i],
    ["/download", /download|get channel/i],
    ["/about",    null],
    ["/blog",     null],
    ["/careers",  null],
    ["/privacy",  /privacy/i],
  ])("renders marketing route %s with a real page (h1 present%s)", async (path, headingRegex) => {
    window.history.pushState({}, "", path);
    await act(async () => render(<App />));
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toBeTruthy();
    if (headingRegex) {
      expect(h1.textContent).toMatch(headingRegex);
    }
  });

  it("renders the branded NotFound page for unknown routes", async () => {
    window.history.pushState({}, "", "/this-route-does-not-exist");
    await act(async () => render(<App />));
    expect(screen.getByText(/wandered off/i)).toBeTruthy();
  });

  it("renders the Login page at /app/login (no auth required)", async () => {
    window.history.pushState({}, "", "/app/login");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { name: /sign in to channel/i })).toBeTruthy();
  });

  it("redirects /app to /app/login when no token", async () => {
    window.history.pushState({}, "", "/app");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { name: /sign in to channel/i })).toBeTruthy();
  });

  it("renders the ChatHome at /app with a valid token", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it/i);
  });


  it("/app/c/r1 renders the Conversation component (not the placeholder)", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app/c/r1");
    await act(async () => render(<App />));
    // The real Conversation mounts the follow-up Composer at the bottom
    // (placeholder="Reply…"); the placeholder testid is no longer present.
    expect(screen.getByPlaceholderText("Reply…")).toBeTruthy();
    expect(screen.queryByTestId("app-conversation")).toBeNull();
  });

  it("/app/projects renders the Projects view (not the placeholder)", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app/projects");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 2, name: "Projects" })).toBeTruthy();
    expect(screen.queryByTestId("app-projects")).toBeNull();
  });

  it("/app/projects/p1 renders the ProjectDetail view (not the placeholder)", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app/projects/p1");
    await act(async () => render(<App />));
    // p1 = "Analytics Rewrite" per PROJECTS[0].
    expect(screen.getByRole("heading", { level: 2, name: "Analytics Rewrite" })).toBeTruthy();
    expect(screen.queryByTestId("app-project-detail")).toBeNull();
  });

  it("/app/artifacts renders the Artifacts view (not the placeholder)", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app/artifacts");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 2, name: "Artifacts" })).toBeTruthy();
    expect(screen.queryByTestId("app-artifacts")).toBeNull();
  });

  it("/app/customize renders the Customize view (not the placeholder)", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app/customize");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 2, name: "Customize" })).toBeTruthy();
    expect(screen.queryByTestId("app-customize")).toBeNull();
  });

  it("/app/admin renders AdminHome for an admin-role token (#237)", async () => {
    storage[TOKEN_KEY] = makeToken({ role: "admin" });
    window.history.pushState({}, "", "/app/admin");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 2, name: "Admin" })).toBeTruthy();
  });

  it("/app/admin redirects a user-role token back to ChatHome (#237)", async () => {
    storage[TOKEN_KEY] = makeToken({ role: "user" });
    window.history.pushState({}, "", "/app/admin");
    await act(async () => render(<App />));
    // AdminLayout's Navigate replace lands on /app → ChatHome greeting.
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it/i);
    expect(screen.queryByRole("heading", { level: 2, name: "Admin" })).toBeNull();
  });

  it("/app/admin/users renders the Users view for an admin (#238)", async () => {
    storage[TOKEN_KEY] = makeToken({ role: "admin" });
    stubAdminFetch({ items: [], next_cursor: null });
    window.history.pushState({}, "", "/app/admin/users");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 2, name: "Users" })).toBeTruthy();
    expect(screen.queryByTestId("admin-users-placeholder")).toBeNull();
  });

  it("/app/admin/users/u42 renders the UserDetail view for an admin (#238)", async () => {
    storage[TOKEN_KEY] = makeToken({ role: "admin" });
    stubAdminFetch({
      user: {
        user_id: "u42",
        email: "u42@ex.com",
        created_at: null,
        last_login_at: null,
        chat_count: 0,
        last_chat_at: null,
      },
      recent_chats: [],
      recent_audit_events: [],
    });
    window.history.pushState({}, "", "/app/admin/users/u42");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 2, name: "u42@ex.com" })).toBeTruthy();
    expect(screen.queryByTestId("admin-user-detail-placeholder")).toBeNull();
  });

  it("/app/admin/dashboard renders the Dashboard view for an admin (#239)", async () => {
    storage[TOKEN_KEY] = makeToken({ role: "admin" });
    // Reject the metrics endpoints so the view renders its degraded panels.
    // That keeps Recharts out of the tree here (no ResizeObserver needed in
    // this routing test); the full chart rendering is covered in
    // Dashboard.test.jsx with recharts mocked at the module level.
    vi.stubGlobal("fetch", vi.fn(function fakeMetricsFetch(url) {
      return Promise.reject(new Error(`metrics unavailable in test: ${url}`));
    }));
    window.history.pushState({}, "", "/app/admin/dashboard");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 2, name: "Dashboard" })).toBeTruthy();
    expect(screen.queryByTestId("admin-dashboard-placeholder")).toBeNull();
  });

  it.each([
    "/app/admin/users",
    "/app/admin/users/u42",
    "/app/admin/dashboard",
  ])("%s redirects a user-role token back to ChatHome (#237)", async (path) => {
    storage[TOKEN_KEY] = makeToken({ role: "user" });
    window.history.pushState({}, "", path);
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it/i);
  });

  it("applies the saved theme to <html> on mount", async () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    await act(async () => render(<App />));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("corrects the app-layer height when the layout viewport is short (#467)", async () => {
    // The installed-iOS-PWA shape: the window is taller than the layout
    // viewport `inset: 0` / `100dvh` resolve against, so `.stage` needs
    // the measured height published to it.
    //
    // `Object.defineProperty` replaces jsdom's `innerHeight` accessor with
    // a value property that no vitest cleanup undoes, so the original
    // descriptor is captured and restored — otherwise the override
    // outlives this test and every later one mounts against a 852px
    // window, silently re-pinning `--app-vh` on <html>.
    const originalInnerHeight = Object.getOwnPropertyDescriptor(window, "innerHeight");
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 852 });
    Object.defineProperty(document.documentElement, "clientHeight", {
      configurable: true,
      value: 756,
    });
    try {
      await act(async () => render(<App />));

      expect(document.documentElement.style.getPropertyValue("--app-vh")).toBe("852px");
      expect(document.documentElement.hasAttribute("data-app-vh")).toBe(true);
    } finally {
      document.documentElement.style.removeProperty("--app-vh");
      document.documentElement.removeAttribute("data-app-vh");
      delete document.documentElement.clientHeight;
      Object.defineProperty(window, "innerHeight", originalInnerHeight);
    }
  });

  it("mounts the temporary layout readout only for ?__layout-debug=1 (#467)", async () => {
    await act(async () => render(<App />));
    expect(screen.queryByRole("region", { name: /layout debug/i })).toBeNull();
    cleanup();

    window.history.pushState({}, "", "/?__layout-debug=1");
    await act(async () => render(<App />));
    expect(screen.getByRole("region", { name: /layout debug/i })).toBeTruthy();
  });
});
