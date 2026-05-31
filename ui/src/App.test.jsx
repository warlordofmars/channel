// Copyright (c) 2026 John Carter. All rights reserved.
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import { TOKEN_KEY } from "./lib/auth.js";
import { __resetChannelPrefsForTest } from "./hooks/useChannelPrefs.js";

function makeToken({ expOffsetSeconds = 3600 } = {}) {
  const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email: "u@ex.com" }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
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

  it("applies the saved theme to <html> on mount", async () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    await act(async () => render(<App />));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});
