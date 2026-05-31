// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import ProjectDetail from "./ProjectDetail.jsx";
import { PROJECTS, PROJECT_DOCS } from "../data.js";

// Mirrors PROJECT_CHATS_PLACEHOLDER in ProjectDetail.jsx — first row only.
// Kept local so the component remains free to evolve its placeholder list
// without breaking these tests beyond the assertions that actually matter
// (row count + first-row title + nav target).
const FIRST_PROJECT_CHAT = { id: "r1", title: "Home server backup strategy" };
import { __resetChannelPrefsForTest } from "../../hooks/useChannelPrefs.js";

function renderAt(path) {
  let lastPath = null;
  function PathCatcher() {
    const { pathname } = useLocation();
    lastPath = pathname;
    return null;
  }
  const result = render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/projects/:id" element={<><ProjectDetail /><PathCatcher /></>} />
        <Route path="/app/projects" element={<PathCatcher />} />
        <Route path="/app/c/:id" element={<PathCatcher />} />
      </Routes>
    </MemoryRouter>
  );
  return { ...result, getLastPath: () => lastPath };
}

describe("ProjectDetail", () => {
  let storage;
  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("sessionStorage", {
      getItem: (k) => storage[`s:${k}`] ?? null,
      setItem: (k, v) => { storage[`s:${k}`] = String(v); },
      removeItem: (k) => { delete storage[`s:${k}`]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    __resetChannelPrefsForTest();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the project's name + description in the header when :id matches PROJECTS", () => {
    renderAt("/app/projects/p1");
    const p1 = PROJECTS.find((p) => p.id === "p1");
    expect(screen.getByRole("heading", { level: 2, name: p1.name })).toBeTruthy();
    expect(screen.getByText(p1.desc)).toBeTruthy();
  });

  it("renders all PROJECT_DOCS chips", () => {
    renderAt("/app/projects/p1");
    for (const d of PROJECT_DOCS) {
      expect(screen.getByText(d)).toBeTruthy();
    }
  });

  it("renders 6 placeholder 'Chats in this project' entries", () => {
    const { container } = renderAt("/app/projects/p1");
    const rows = container.querySelectorAll(".proj-chat");
    expect(rows.length).toBe(6);
    expect(rows[0].textContent).toContain(FIRST_PROJECT_CHAT.title);
  });

  it("clicking a chat row navigates to /app/c/<that-chat-id>", () => {
    const { getLastPath } = renderAt("/app/projects/p1");
    fireEvent.click(screen.getByText(FIRST_PROJECT_CHAT.title));
    expect(getLastPath()).toBe(`/app/c/${FIRST_PROJECT_CHAT.id}`);
  });

  it("clicking the back button navigates to /app/projects", () => {
    const { getLastPath } = renderAt("/app/projects/p1");
    fireEvent.click(screen.getByRole("button", { name: /^Projects$/i }));
    expect(getLastPath()).toBe("/app/projects");
  });

  it("typing in the Composer + Send stashes the pending payload and navigates to /app/c/new", () => {
    const { getLastPath } = renderAt("/app/projects/p1");
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "kick off a new chat" } });
    fireEvent.click(screen.getByTitle("Send"));
    const raw = sessionStorage.getItem("channel-pending-send");
    expect(raw).toBeTruthy();
    const payload = JSON.parse(raw);
    expect(payload.text).toBe("kick off a new chat");
    expect(payload.modelId).toBe("claude-opus-4-8");
    expect(payload.effort).toBe("High");
    expect(getLastPath()).toBe("/app/c/new");
  });

  it("Composer placeholder names the active project", () => {
    renderAt("/app/projects/p1");
    expect(screen.getByPlaceholderText(/New chat in Analytics Rewrite/)).toBeTruthy();
  });

  it("unknown :id renders an empty state with a link back to /app/projects", () => {
    const { getLastPath } = renderAt("/app/projects/does-not-exist");
    expect(screen.getByText(/Project not found/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Back to projects/i }));
    expect(getLastPath()).toBe("/app/projects");
  });

  it("Composer falls through silently when sessionStorage.setItem throws (private mode)", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: () => null,
      setItem: () => { throw new Error("private mode"); },
      removeItem: () => {},
    });
    const { getLastPath } = renderAt("/app/projects/p1");
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "still works" } });
    expect(() => fireEvent.click(screen.getByTitle("Send"))).not.toThrow();
    expect(getLastPath()).toBe("/app/c/new");
  });

  it("clicking 'Add' in the Project knowledge section does not throw (Phase 6e no-op)", () => {
    renderAt("/app/projects/p1");
    expect(() => fireEvent.click(screen.getByRole("button", { name: /^Add$/i }))).not.toThrow();
  });

  it("project badge background is themed by the project's color hue", () => {
    const { container } = renderAt("/app/projects/p1");
    const badge = container.querySelector(".proj-head .badge");
    expect(badge).toBeTruthy();
    expect(badge.style.background).toContain("oklch(0.92 0.05 42)");
  });

  it("falls back to MODELS[0] when prefs.model points at an unknown id", () => {
    // Seed an unknown model id in storage so prefs.model = "no-such-id".
    storage["channel-model"] = "no-such-id";
    __resetChannelPrefsForTest();
    renderAt("/app/projects/p1");
    // Sending should still work — Composer renders the first MODELS entry's short name.
    expect(screen.getByRole("button", { name: /Opus 4.8/i })).toBeTruthy();
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "with fallback model" } });
    fireEvent.click(screen.getByTitle("Send"));
    const payload = JSON.parse(sessionStorage.getItem("channel-pending-send"));
    expect(payload.modelId).toBe("claude-opus-4-8");
  });

  it("picking a different model in the Composer persists via setModel", () => {
    storage["channel-model"] = "claude-opus-4-8";
    __resetChannelPrefsForTest();
    renderAt("/app/projects/p1");
    // Open the ModelPicker via the current-model button in the Composer.
    fireEvent.click(screen.getByRole("button", { name: /Opus 4.8/i }));
    fireEvent.click(screen.getByText("Claude Haiku 4.5"));
    expect(storage["channel-model"]).toBe("claude-haiku-4-5");
  });
});
