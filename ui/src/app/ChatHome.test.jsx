// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
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

function renderChatHome() {
  return render(
    <MemoryRouter initialEntries={["/app"]}>
      <ChatHome />
    </MemoryRouter>
  );
}

function renderChatHomeWithLocationCatcher() {
  let lastPath = null;
  function PathCatcher() {
    const { pathname } = useLocation();
    lastPath = pathname;
    return null;
  }
  const result = render(
    <MemoryRouter initialEntries={["/app"]}>
      <Routes>
        <Route path="/app" element={<><ChatHome /><PathCatcher /></>} />
        <Route path="/app/c/:id" element={<PathCatcher />} />
      </Routes>
    </MemoryRouter>
  );
  return { ...result, getLastPath: () => lastPath };
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
    vi.stubGlobal("sessionStorage", {
      getItem: (k) => storage[`s:${k}`] ?? null,
      setItem: (k, v) => { storage[`s:${k}`] = String(v); },
      removeItem: (k) => { delete storage[`s:${k}`]; },
    });
    __resetChannelPrefsForTest();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the 'Back at it, <name>' greeting derived from the JWT email", () => {
    renderChatHome();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it, ada/i);
  });

  it("renders the composer (textarea)", () => {
    renderChatHome();
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("renders all four quick-action chips", () => {
    renderChatHome();
    for (const q of QUICK_ACTIONS) {
      expect(screen.getByRole("button", { name: q.label })).toBeTruthy();
    }
  });

  it("sending a typed message stashes the payload in sessionStorage and navigates to /app/c/new", () => {
    const { getLastPath } = renderChatHomeWithLocationCatcher();
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "hi there" } });
    fireEvent.click(screen.getByTitle("Send"));
    const raw = sessionStorage.getItem("channel-pending-send");
    expect(raw).toBeTruthy();
    const payload = JSON.parse(raw);
    expect(payload.text).toBe("hi there");
    expect(payload.atts).toEqual([]);
    expect(payload.modelId).toBe("claude-opus-4-8");
    expect(payload.effort).toBe("High");
    expect(getLastPath()).toBe("/app/c/new");
  });

  it("clicking a quick-action stashes the prefilled prompt and navigates to /app/c/new", () => {
    const { getLastPath } = renderChatHomeWithLocationCatcher();
    fireEvent.click(screen.getByRole("button", { name: "Write" }));
    const payload = JSON.parse(sessionStorage.getItem("channel-pending-send"));
    expect(payload.text).toMatch(/help me write something/i);
    expect(getLastPath()).toBe("/app/c/new");
  });

  it("falls back to default name 'You' when token is absent", () => {
    storage = {};
    renderChatHome();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it, you/i);
  });

  it("uses the first word of display_name when present (Google full-name)", () => {
    storage = { [TOKEN_KEY]: makeToken({ email: "j@example.com", display_name: "John Carter" }) };
    renderChatHome();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it, john$/i);
  });

  it("falls back to first MODELS entry when prefs.model is not in MODELS", () => {
    storage["channel-model"] = "nonexistent-model-id";
    __resetChannelPrefsForTest();
    renderChatHome();
    // ModelPicker shows the model.short of the fallback (Opus 4.8):
    expect(screen.getByText(/opus 4.8/i)).toBeTruthy();
  });

  it("falls back to 'You' when JWT email local-part is empty", () => {
    storage = { [TOKEN_KEY]: makeToken({ email: "@nodomain.com" }) };
    renderChatHome();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it, you/i);
  });

  it("setModelObj writes the model id back to prefs when a model is selected", () => {
    renderChatHome();
    // Open the ModelPicker popover via the model-pick button in the Composer
    fireEvent.click(screen.getByRole("button", { name: /sonnet 4\.6|opus 4\.8|haiku 4\.5/i }));
    // Click the Haiku option to trigger setModelObj
    fireEvent.click(screen.getByText("Claude Haiku 4.5"));
    // The storage key should be updated
    expect(storage["channel-model"]).toBe("claude-haiku-4-5");
  });

  it("send silently degrades when sessionStorage.setItem throws", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: () => null,
      setItem: () => { throw new Error("private mode"); },
      removeItem: () => {},
    });
    const { getLastPath } = renderChatHomeWithLocationCatcher();
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "hi" } });
    expect(() => fireEvent.click(screen.getByTitle("Send"))).not.toThrow();
    // Still navigates even though the stash failed.
    expect(getLastPath()).toBe("/app/c/new");
  });
});
