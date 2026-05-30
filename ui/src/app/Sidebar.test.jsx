// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Sidebar from "./Sidebar.jsx";
import { TOKEN_KEY } from "../lib/auth.js";

function makeToken({ email = "ada@example.com" } = {}) {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email }));
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

  it("does NOT render the 'Relaunch to update' pill (Electron-only)", () => {
    renderSidebar();
    expect(screen.queryByText(/relaunch to update/i)).toBeNull();
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
});
