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

  it.each([
    ["/app/c/r1", "app-conversation"],
    ["/app/projects", "app-projects"],
    ["/app/projects/p1", "app-project-detail"],
    ["/app/artifacts", "app-artifacts"],
    ["/app/customize", "app-customize"],
  ])("renders the %s placeholder when authed", async (path, testId) => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", path);
    await act(async () => render(<App />));
    expect(screen.getByTestId(testId)).toBeTruthy();
  });

  it("applies the saved theme to <html> on mount", async () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    await act(async () => render(<App />));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});
