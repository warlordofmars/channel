// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Shell from "./Shell.jsx";
import { TOKEN_KEY } from "../lib/auth.js";

function makeToken() {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email: "a@b.com" }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("Shell", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: (k) => (k === TOKEN_KEY ? makeToken() : null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the supplied children inside a <main>", () => {
    render(
      <MemoryRouter>
        <Shell>
          <div data-testid="child" />
        </Shell>
      </MemoryRouter>
    );
    expect(screen.getByTestId("child")).toBeTruthy();
  });

  it("renders the Sidebar alongside the children", () => {
    render(
      <MemoryRouter>
        <Shell>
          <div />
        </Shell>
      </MemoryRouter>
    );
    expect(screen.getByRole("button", { name: /new chat/i })).toBeTruthy();
  });
});
