// Copyright (c) 2026 John Carter. All rights reserved.
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import { TOKEN_KEY } from "./lib/auth.js";

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
  });

  afterEach(() => { vi.unstubAllGlobals(); window.history.pushState({}, "", "/"); });

  it.each([
    ["/", "marketing-home"],
    ["/product", "marketing-product"],
    ["/models", "marketing-models"],
    ["/pricing", "marketing-pricing"],
    ["/download", "marketing-download"],
    ["/about", "marketing-about"],
    ["/blog", "marketing-blog"],
    ["/careers", "marketing-careers"],
    ["/privacy", "marketing-privacy"],
  ])("renders marketing route %s with placeholder", async (path, testId) => {
    window.history.pushState({}, "", path);
    await act(async () => render(<App />));
    expect(screen.getByTestId(testId)).toBeTruthy();
  });

  it("renders 404 placeholder for unknown routes", async () => {
    window.history.pushState({}, "", "/this-route-does-not-exist");
    await act(async () => render(<App />));
    expect(screen.getByTestId("marketing-notfound")).toBeTruthy();
  });

  it("renders the app-login placeholder at /app/login (no auth required)", async () => {
    window.history.pushState({}, "", "/app/login");
    await act(async () => render(<App />));
    expect(screen.getByTestId("app-login")).toBeTruthy();
  });

  it("redirects /app to /app/login when no token", async () => {
    window.history.pushState({}, "", "/app");
    await act(async () => render(<App />));
    expect(screen.getByTestId("app-login")).toBeTruthy();
  });

  it("renders the app-home placeholder at /app with a valid token", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app");
    await act(async () => render(<App />));
    expect(screen.getByTestId("app-home")).toBeTruthy();
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
    await act(async () => render(<App />));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});
