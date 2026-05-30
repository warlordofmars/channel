// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatHome from "./ChatHome.jsx";
import { TOKEN_KEY } from "../lib/auth.js";
import { QUICK_ACTIONS } from "./data.js";
import { __resetChannelPrefsForTest } from "../hooks/useChannelPrefs.js";

function makeToken({ email = "ada@example.com", display_name } = {}) {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const claims = { exp, sub: "u1", role: "user", email };
  if (display_name !== undefined) claims.display_name = display_name;
  const payload = btoa(JSON.stringify(claims));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("ChatHome", () => {
  let storage;

  beforeEach(() => {
    storage = { [TOKEN_KEY]: makeToken() };
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    __resetChannelPrefsForTest();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the 'Back at it, <name>' greeting derived from the JWT email", () => {
    render(<ChatHome />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it, ada/i);
  });

  it("renders the composer (textarea)", () => {
    render(<ChatHome />);
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("renders all four quick-action chips", () => {
    render(<ChatHome />);
    for (const q of QUICK_ACTIONS) {
      expect(screen.getByRole("button", { name: q.label })).toBeTruthy();
    }
  });

  it("clicking a quick-action does not throw (no-op send at Phase 6c)", () => {
    render(<ChatHome />);
    expect(() => fireEvent.click(screen.getByRole("button", { name: "Write" }))).not.toThrow();
  });

  it("clicking Send on a typed message does not throw (no-op send at Phase 6c)", () => {
    render(<ChatHome />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "hi" } });
    expect(() => fireEvent.click(screen.getByTitle("Send"))).not.toThrow();
  });

  it("falls back to default name 'You' when token is absent", () => {
    storage = {};
    render(<ChatHome />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it, you/i);
  });

  it("uses the first word of display_name when present (Google full-name)", () => {
    storage = { [TOKEN_KEY]: makeToken({ email: "j@example.com", display_name: "John Carter" }) };
    render(<ChatHome />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it, john$/i);
  });

  it("falls back to first MODELS entry when prefs.model is not in MODELS", () => {
    storage["channel-model"] = "nonexistent-model-id";
    __resetChannelPrefsForTest();
    render(<ChatHome />);
    // ModelPicker shows the model.short of the fallback (Opus 4.8):
    expect(screen.getByText(/opus 4.8/i)).toBeTruthy();
  });

  it("falls back to 'You' when JWT email local-part is empty", () => {
    storage = { [TOKEN_KEY]: makeToken({ email: "@nodomain.com" }) };
    render(<ChatHome />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it, you/i);
  });

  it("setModelObj writes the model id back to prefs when a model is selected", () => {
    render(<ChatHome />);
    // Open the ModelPicker popover via the model-pick button in the Composer
    fireEvent.click(screen.getByRole("button", { name: /sonnet 4\.6|opus 4\.8|haiku 4\.5/i }));
    // Click the Haiku option to trigger setModelObj
    fireEvent.click(screen.getByText("Claude Haiku 4.5"));
    // The storage key should be updated
    expect(storage["channel-model"]).toBe("claude-haiku-4-5");
  });
});
