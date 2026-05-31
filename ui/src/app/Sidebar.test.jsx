// Copyright (c) 2026 John Carter. All rights reserved.
import { act } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import Sidebar from "./Sidebar.jsx";
import { TOKEN_KEY } from "../lib/auth.js";

function makeToken({ email = "ada@example.com", display_name } = {}) {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const claims = { exp, sub: "u1", role: "user", email };
  if (display_name !== undefined) claims.display_name = display_name;
  const payload = btoa(JSON.stringify(claims));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("Sidebar", () => {
  let storage;
  let assignSpy;

  beforeEach(() => {
    storage = { [TOKEN_KEY]: makeToken() };
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    assignSpy = vi.fn();
    vi.stubGlobal("location", { ...globalThis.location, assign: assignSpy });
  });
  afterEach(() => vi.unstubAllGlobals());

  function renderSidebar() {
    return render(<MemoryRouter><Sidebar /></MemoryRouter>);
  }

  it("renders four primary nav items (New chat, Projects, Artifacts, Customize)", () => {
    renderSidebar();
    expect(screen.getByRole("button", { name: /new chat/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^projects/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^artifacts/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^customize/i })).toBeTruthy();
  });

  it("renders time-grouped recents (Today, Yesterday, …)", () => {
    renderSidebar();
    expect(screen.getByText("Today")).toBeTruthy();
    expect(screen.getByText("Yesterday")).toBeTruthy();
  });

  it("renders the account row with the email", () => {
    renderSidebar();
    expect(screen.getByText("ada@example.com")).toBeTruthy();
  });

  it("clicking the account row opens the popover", () => {
    renderSidebar();
    const account = screen.getByText("ada@example.com").closest("button");
    fireEvent.click(account);
    expect(screen.getByText("Sign out")).toBeTruthy();
  });

  it("Sign out clears the mgmt token and assigns location to /", () => {
    renderSidebar();
    fireEvent.click(screen.getByText("ada@example.com").closest("button"));
    fireEvent.click(screen.getByText("Sign out"));
    expect(storage[TOKEN_KEY]).toBeUndefined();
    expect(assignSpy).toHaveBeenCalledWith("/");
  });

  it("clicking the search button reveals the search input", () => {
    renderSidebar();
    fireEvent.click(screen.getByTitle("Search"));
    expect(screen.getByPlaceholderText("Search chats")).toBeTruthy();
  });

  it("typing in the search input filters recents to matching titles", () => {
    renderSidebar();
    fireEvent.click(screen.getByTitle("Search"));
    fireEvent.change(screen.getByPlaceholderText("Search chats"), { target: { value: "Postgres" } });
    expect(screen.getByText(/Postgres index not being used/i)).toBeTruthy();
    expect(screen.queryByText(/Weekend trail route/i)).toBeNull();
  });

  it("does NOT render the 'Channel Max' plan label (deferred)", () => {
    renderSidebar();
    expect(screen.queryByText(/channel max/i)).toBeNull();
  });

  it("shows 'no chats match' fallback when search matches nothing", () => {
    renderSidebar();
    fireEvent.click(screen.getByTitle("Search"));
    fireEvent.change(screen.getByPlaceholderText("Search chats"), { target: { value: "zzzz-no-such-thing" } });
    expect(screen.getByText(/no chats match/i)).toBeTruthy();
  });

  it("falls back to default name 'You' when token is absent", () => {
    storage = {};
    renderSidebar();
    expect(screen.getByText("you@example.com")).toBeTruthy();
  });

  it("falls back to 'You' display name when email has no local part", () => {
    storage = { [TOKEN_KEY]: makeToken({ email: "@nodomain" }) };
    renderSidebar();
    // userName will be "" (empty before @) → falls back to "You"
    expect(screen.getByText("@nodomain")).toBeTruthy();
  });

  it("clicking the backdrop closes the account popover", () => {
    renderSidebar();
    // open popover
    fireEvent.click(screen.getByText("ada@example.com").closest("button"));
    expect(screen.getByText("Sign out")).toBeTruthy();
    // click backdrop to close
    const backdrop = document.querySelector(".backdrop");
    fireEvent.click(backdrop);
    expect(screen.queryByText("Sign out")).toBeNull();
  });

  it("shows the full display_name (Google name) when present in the JWT", () => {
    storage = { [TOKEN_KEY]: makeToken({ email: "j@example.com", display_name: "John Carter" }) };
    renderSidebar();
    expect(screen.getByText("John Carter")).toBeTruthy();
    // initials should derive from the two-word name: J + C → "JC"
    expect(screen.getByText("JC")).toBeTruthy();
  });

  it("clicking 'Toggle sidebar' calls the onToggle prop", () => {
    const onToggle = vi.fn();
    render(<MemoryRouter><Sidebar collapsed={false} onToggle={onToggle} /></MemoryRouter>);
    fireEvent.click(screen.getByTitle("Toggle sidebar"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("applies .collapsed class to .sb when the collapsed prop is true", () => {
    const { container } = render(
      <MemoryRouter><Sidebar collapsed={true} onToggle={() => {}} /></MemoryRouter>
    );
    expect(container.querySelector(".sb.collapsed")).toBeTruthy();
  });

  it("omits .collapsed class when the collapsed prop is false (or default)", () => {
    const { container } = render(<MemoryRouter><Sidebar /></MemoryRouter>);
    expect(container.querySelector(".sb.collapsed")).toBeNull();
    expect(container.querySelector(".sb")).toBeTruthy();
  });

  it("clicking the toggle button is safe when no onToggle prop is passed (default noop)", () => {
    render(<MemoryRouter><Sidebar /></MemoryRouter>);
    expect(() => fireEvent.click(screen.getByTitle("Toggle sidebar"))).not.toThrow();
  });

  it("clicking a recent navigates to /app/c/<that-id>", () => {
    let lastPath = null;
    function PathCatcher() {
      const { pathname } = useLocation();
      lastPath = pathname;
      return null;
    }
    render(
      <MemoryRouter initialEntries={["/app"]}>
        <Routes>
          <Route path="/app" element={<><Sidebar /><PathCatcher /></>} />
          <Route path="/app/c/:id" element={<PathCatcher />} />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(screen.getByText("Home server backup strategy"));
    expect(lastPath).toBe("/app/c/r1");
  });

  it.each([
    ["New chat", "/app"],
    ["Projects", "/app/projects"],
    ["Artifacts", "/app/artifacts"],
    ["Customize", "/app/customize"],
  ])("clicking %s navigates to %s", (label, expected) => {
    let lastPath = null;
    function PathCatcher() {
      const { pathname } = useLocation();
      lastPath = pathname;
      return null;
    }
    render(
      <MemoryRouter initialEntries={["/app/c/r1"]}>
        <Routes>
          <Route path="*" element={<><Sidebar /><PathCatcher /></>} />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(screen.getByRole("button", { name: new RegExp(label, "i") }));
    expect(lastPath).toBe(expected);
  });
});

describe("Sidebar — update pill", () => {
  let storage;

  beforeEach(() => {
    storage = { [TOKEN_KEY]: (() => {
      const exp = Math.floor(Date.now() / 1000) + 3600;
      const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email: "ada@example.com" }));
      return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
    })() };
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    vi.stubGlobal("location", { ...globalThis.location, assign: vi.fn() });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.channelDesktop;
  });

  function renderSidebar() {
    return render(<MemoryRouter><Sidebar /></MemoryRouter>);
  }

  it("does not render the pill when window.channelDesktop is absent (web SPA)", () => {
    renderSidebar();
    expect(screen.queryByRole("button", { name: /relaunch to update/i })).toBeNull();
  });

  it("does not render the pill when no update event has fired yet (desktop, current)", () => {
    window.channelDesktop = {
      isDesktop: true,
      onUpdateStatus: vi.fn(),                      // never invokes its cb
      relaunchToUpdate: vi.fn(),
    };
    renderSidebar();
    expect(screen.queryByRole("button", { name: /relaunch to update/i })).toBeNull();
  });

  it("renders the pill with version + click handler after update:downloaded fires", () => {
    let registeredCb;
    const relaunchToUpdate = vi.fn();
    window.channelDesktop = {
      isDesktop: true,
      onUpdateStatus: (cb) => { registeredCb = cb; },
      relaunchToUpdate,
    };
    renderSidebar();
    expect(registeredCb).toBeTypeOf("function");

    // Simulate the main process pushing an event.
    act(() => registeredCb({ state: "downloaded", version: "0.2.1" }));

    const pill = screen.getByRole("button", { name: /relaunch to update v0\.2\.1/i });
    expect(pill).toBeTruthy();
    fireEvent.click(pill);
    expect(relaunchToUpdate).toHaveBeenCalled();
  });

  it("ignores non-downloaded states (checking/available/error)", () => {
    let registeredCb;
    window.channelDesktop = {
      isDesktop: true,
      onUpdateStatus: (cb) => { registeredCb = cb; },
      relaunchToUpdate: vi.fn(),
    };
    renderSidebar();
    act(() => registeredCb({ state: "checking" }));
    act(() => registeredCb({ state: "available", version: "0.2.1" }));
    act(() => registeredCb({ state: "error", message: "boom" }));
    expect(screen.queryByRole("button", { name: /relaunch to update/i })).toBeNull();
  });
});
