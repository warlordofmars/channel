// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";

vi.mock("./components/LoginPage.jsx", () => ({
  default: () => <div data-testid="login-page" />,
}));
vi.mock("./components/UsersPanel.jsx", () => ({
  default: () => <div data-testid="users-panel" />,
}));
vi.mock("./components/EmptyState.jsx", () => ({
  default: ({ title }) => <div data-testid="empty-state">{title}</div>,
}));

/** Build a syntactically-valid mgmt JWT with given claims. */
function makeToken({ expOffsetSeconds = 3600, role = "user", email = "u@example.com" } = {}) {
  const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
  const payload = btoa(JSON.stringify({ exp, sub: "test-user", role, email }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

/** Fetch mock: /health returns version. */
function makeFetch() {
  return vi.fn().mockImplementation(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ status: "ok", version: "1.2.3" }),
    }),
  );
}

describe("App routing", () => {
  let _storage;

  beforeEach(() => {
    _storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => _storage[k] ?? null,
      setItem: (k, v) => { _storage[k] = v; },
      removeItem: (k) => { delete _storage[k]; },
    });
    vi.stubGlobal("matchMedia", (q) => ({
      matches: q === "(prefers-color-scheme: dark)" ? false : false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    vi.stubGlobal("fetch", makeFetch());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows LoginPage at / when not authenticated", async () => {
    await act(async () => render(<App />));
    expect(screen.getByTestId("login-page")).toBeTruthy();
  });

  it("redirects / to /app when already authenticated", async () => {
    _storage["starter_mgmt_token"] = makeToken({ role: "admin" });
    await act(async () => render(<App />));
    await waitFor(() => expect(screen.getByTestId("users-panel")).toBeTruthy());
  });

  it("renders the branded NotFoundPage on unknown routes", async () => {
    window.history.pushState({}, "", "/unknown-path");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeTruthy();
    window.history.pushState({}, "", "/");
  });
});

describe("AppShell", () => {
  let _storage;

  beforeEach(() => {
    _storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => _storage[k] ?? null,
      setItem: (k, v) => { _storage[k] = v; },
      removeItem: (k) => { delete _storage[k]; },
    });
    vi.stubGlobal("matchMedia", (q) => ({
      matches: q === "(prefers-color-scheme: dark)" ? false : false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    vi.stubGlobal("fetch", makeFetch());
    _storage["starter_mgmt_token"] = makeToken({ role: "admin" });
    window.history.pushState({}, "", "/app");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.pushState({}, "", "/");
  });

  it("renders header with AgentCore Starter title", async () => {
    await act(async () => render(<App />));
    expect(screen.getByText("AgentCore Starter")).toBeTruthy();
  });

  it("renders Users tab for admin", async () => {
    await act(async () => render(<App />));
    expect(screen.getByText("Users")).toBeTruthy();
  });

  it("shows UsersPanel on initial render for admin", async () => {
    await act(async () => render(<App />));
    expect(screen.getByTestId("users-panel")).toBeTruthy();
  });

  it("shows EmptyState for non-admin user", async () => {
    _storage["starter_mgmt_token"] = makeToken({ role: "user" });
    await act(async () => render(<App />));
    expect(screen.getByTestId("empty-state")).toBeTruthy();
    expect(screen.queryByText("Users")).toBeNull();
  });

  it("shows LoginPage when no token is stored", async () => {
    delete _storage["starter_mgmt_token"];
    await act(async () => render(<App />));
    expect(screen.getByTestId("login-page")).toBeTruthy();
    expect(screen.queryByTestId("users-panel")).toBeNull();
  });

  it("shows LoginPage when token is expired", async () => {
    _storage["starter_mgmt_token"] = makeToken({ expOffsetSeconds: -3600 });
    await act(async () => render(<App />));
    expect(screen.getByTestId("login-page")).toBeTruthy();
  });

  it("shows LoginPage when token is malformed", async () => {
    _storage["starter_mgmt_token"] = "not.a.jwt";
    await act(async () => render(<App />));
    expect(screen.getByTestId("login-page")).toBeTruthy();
  });

  it("displays user email in header", async () => {
    _storage["starter_mgmt_token"] = makeToken({ role: "admin", email: "alice@example.com" });
    await act(async () => render(<App />));
    expect(screen.getByText("alice@example.com")).toBeTruthy();
  });

  it("does not show email when token has no email claim", async () => {
    _storage["starter_mgmt_token"] = makeToken({ email: null });
    await act(async () => render(<App />));
    expect(screen.getByText("AgentCore Starter")).toBeTruthy();
    expect(screen.queryByText("null")).toBeNull();
  });

  it("clicking logo navigates to /", async () => {
    await act(async () => render(<App />));
    fireEvent.click(screen.getByText("AgentCore Starter"));
    // HomeRoute redirects back to /app since token is valid — no crash expected
    expect(screen.getByText("AgentCore Starter")).toBeTruthy();
  });

  it("sign out button clears mgmt token and reloads", async () => {
    const replaceMock = vi.fn();
    vi.stubGlobal("location", { ...window.location, replace: replaceMock });
    await act(async () => render(<App />));
    fireEvent.click(screen.getByText("Sign out"));
    expect(_storage["starter_mgmt_token"]).toBeUndefined();
    expect(replaceMock).toHaveBeenCalledWith("/");
  });

  it("shows version in footer after health check", async () => {
    await act(async () => render(<App />));
    await waitFor(() => expect(screen.getByText("AgentCore Starter 1.2.3")).toBeTruthy());
  });

  it("hides footer when health check returns no version", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ status: "ok" }),
        }),
      ),
    );
    await act(async () => render(<App />));
    await waitFor(() => {});
    expect(screen.queryByText(/AgentCore Starter \d/)).toBeNull();
  });

  it("does not crash when health check fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.reject(new Error("Network error"))),
    );
    await act(async () => render(<App />));
    expect(screen.getByText("Users")).toBeTruthy();
    expect(screen.queryByText(/AgentCore Starter \d/)).toBeNull();
  });

  it("renders dark mode toggle button", async () => {
    await act(async () => render(<App />));
    const toggle = screen.getByRole("button", { name: /switch to dark mode/i });
    expect(toggle).toBeTruthy();
    expect(toggle.querySelector("svg")).toBeTruthy();
  });

  it("clicking dark mode toggle changes aria-label", async () => {
    await act(async () => render(<App />));
    const toggle = screen.getByRole("button", { name: /switch to dark mode/i });
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: /switch to light mode/i })).toBeTruthy();
  });

  it("active tab has brand bottom border class", async () => {
    await act(async () => render(<App />));
    const usersBtn = screen.getByText("Users");
    expect(usersBtn.className).toContain("border-b-brand");
  });

  it("clicking the desktop Users tab keeps UsersPanel mounted", async () => {
    await act(async () => render(<App />));
    fireEvent.click(screen.getByText("Users"));
    expect(screen.getByTestId("users-panel")).toBeTruthy();
  });

  it("version in footer links to /changelog", async () => {
    await act(async () => render(<App />));
    await waitFor(() => expect(screen.getByText("AgentCore Starter 1.2.3")).toBeTruthy());
    const link = screen.getByText("AgentCore Starter 1.2.3").closest("a");
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toBe("/changelog");
  });

  it("starter:switch-tab event handler is registered without error", async () => {
    await act(async () => render(<App />));
    expect(screen.getByTestId("users-panel")).toBeTruthy();
    act(() => window.dispatchEvent(new CustomEvent("starter:switch-tab", { detail: "users" })));
    expect(screen.getByTestId("users-panel")).toBeTruthy();
  });

  it("footer changelog link has hover:underline class", async () => {
    await act(async () => render(<App />));
    await waitFor(() => expect(screen.getByText("AgentCore Starter 1.2.3")).toBeTruthy());
    const link = screen.getByText("AgentCore Starter 1.2.3").closest("a");
    expect(link.className).toContain("hover:underline");
  });

  it("footer changelog link has focus:underline class", async () => {
    await act(async () => render(<App />));
    await waitFor(() => expect(screen.getByText("AgentCore Starter 1.2.3")).toBeTruthy());
    const link = screen.getByText("AgentCore Starter 1.2.3").closest("a");
    expect(link.className).toContain("focus:underline");
  });

  it("renders hamburger toggle button", async () => {
    await act(async () => render(<App />));
    expect(screen.getByRole("button", { name: /toggle navigation/i })).toBeTruthy();
  });

  it("hamburger click shows mobile nav", async () => {
    await act(async () => render(<App />));
    expect(screen.queryByTestId("mobile-nav")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /toggle navigation/i }));
    expect(screen.getByTestId("mobile-nav")).toBeTruthy();
  });

  it("clicking tab in mobile nav closes the menu", async () => {
    await act(async () => render(<App />));
    fireEvent.click(screen.getByRole("button", { name: /toggle navigation/i }));
    const mobileNav = screen.getByTestId("mobile-nav");
    const mobileButtons = mobileNav.querySelectorAll("button[type='button']");
    fireEvent.click(mobileButtons[0]);
    expect(screen.queryByTestId("mobile-nav")).toBeNull();
    expect(screen.getByTestId("users-panel")).toBeTruthy();
  });

  it("mobile nav active tab has an orange left-border indicator", async () => {
    await act(async () => render(<App />));
    fireEvent.click(screen.getByRole("button", { name: /toggle navigation/i }));
    const mobileNav = screen.getByTestId("mobile-nav");
    const buttons = mobileNav.querySelectorAll("button[type='button']");
    // Users is the default active tab
    expect(buttons[0].className).toContain("border-l-brand");
    expect(buttons[0].className.split(/\s+/)).not.toContain("bg-white/5");
  });
});
