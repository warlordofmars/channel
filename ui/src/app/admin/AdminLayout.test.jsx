// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AdminLayout from "./AdminLayout.jsx";
import { TOKEN_KEY } from "../../lib/auth.js";

function makeToken({ role = "user" } = {}) {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role, email: "u@ex.com" }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

function renderGate() {
  return render(
    <MemoryRouter initialEntries={["/app/admin"]}>
      <Routes>
        <Route path="/app" element={<div data-testid="chat-home" />} />
        <Route element={<AdminLayout />}>
          <Route path="/app/admin" element={<div data-testid="admin-outlet" />} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

describe("AdminLayout", () => {
  let storage;

  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the outlet when the JWT role is admin", () => {
    storage[TOKEN_KEY] = makeToken({ role: "admin" });
    renderGate();
    expect(screen.getByTestId("admin-outlet")).toBeTruthy();
    expect(screen.queryByTestId("chat-home")).toBeNull();
  });

  it("redirects to /app when the JWT role is user", () => {
    storage[TOKEN_KEY] = makeToken({ role: "user" });
    renderGate();
    expect(screen.getByTestId("chat-home")).toBeTruthy();
    expect(screen.queryByTestId("admin-outlet")).toBeNull();
  });

  it("redirects to /app when no token is present", () => {
    renderGate();
    expect(screen.getByTestId("chat-home")).toBeTruthy();
    expect(screen.queryByTestId("admin-outlet")).toBeNull();
  });

  it("redirects to /app when the token is malformed (parseToken returns null)", () => {
    storage[TOKEN_KEY] = "not-a-jwt";
    renderGate();
    expect(screen.getByTestId("chat-home")).toBeTruthy();
    expect(screen.queryByTestId("admin-outlet")).toBeNull();
  });
});
