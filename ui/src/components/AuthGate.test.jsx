// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AuthGate from "./AuthGate.jsx";
import { TOKEN_KEY } from "../lib/auth.js";

function makeToken({ expOffsetSeconds = 3600 } = {}) {
  const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email: "u@ex.com" }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

function renderAt(path, storage) {
  vi.stubGlobal("localStorage", {
    getItem: (k) => storage[k] ?? null,
    setItem: (k, v) => { storage[k] = String(v); },
    removeItem: (k) => { delete storage[k]; },
  });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/login" element={<div data-testid="login" />} />
        <Route path="/app" element={<AuthGate><div data-testid="app-home" /></AuthGate>} />
        <Route path="/app/projects" element={<AuthGate><div data-testid="projects" /></AuthGate>} />
      </Routes>
    </MemoryRouter>
  );
}

describe("AuthGate", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders children when the token is valid", () => {
    renderAt("/app", { [TOKEN_KEY]: makeToken() });
    expect(screen.getByTestId("app-home")).toBeTruthy();
    expect(screen.queryByTestId("login")).toBeNull();
  });

  it("redirects to /app/login when no token is stored", () => {
    renderAt("/app", {});
    expect(screen.getByTestId("login")).toBeTruthy();
    expect(screen.queryByTestId("app-home")).toBeNull();
  });

  it("redirects to /app/login when token is expired", () => {
    renderAt("/app", { [TOKEN_KEY]: makeToken({ expOffsetSeconds: -3600 }) });
    expect(screen.getByTestId("login")).toBeTruthy();
  });

  it("redirects to /app/login when token is malformed", () => {
    renderAt("/app", { [TOKEN_KEY]: "not.a.jwt" });
    expect(screen.getByTestId("login")).toBeTruthy();
  });

  it("gates other /app/* routes too", () => {
    renderAt("/app/projects", {});
    expect(screen.getByTestId("login")).toBeTruthy();
  });
});
