// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import Conversation from "./Conversation.jsx";
import { MODELS, SAMPLE_REPLY, SAMPLE_USER, RECENTS } from "./data.js";
import { __resetChannelPrefsForTest } from "../hooks/useChannelPrefs.js";

const REAL_RECENT_ID = RECENTS[0].id; // "r1"

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/c/:id" element={<Conversation />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("Conversation", () => {
  let storage;
  beforeEach(() => {
    vi.useFakeTimers();
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
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("renders the SAMPLE_USER + SAMPLE_REPLY pair when the URL :id matches a Recent", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(screen.getByText(SAMPLE_USER)).toBeTruthy();
    // Reply is split across multiple <p>/<li> elements — assert one of the
    // unique bold-list phrases is present.
    expect(screen.getByText(/Batch your writes/)).toBeTruthy();
  });

  it("shows the assistant-head with 'Channel' + model label when a recent is loaded", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(screen.getByText("Channel")).toBeTruthy();
    expect(screen.getByText(/Claude Opus 4.8 · High/)).toBeTruthy();
  });

  it("shows the inline artifact card on assistant turns once streaming is done", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(screen.getByText("ingestion-buffer.ts")).toBeTruthy();
    expect(screen.getByText("Code · 64 lines")).toBeTruthy();
  });

  it("shows the message-actions row (copy/retry/thumbs) on completed assistant turns", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(screen.getByTitle("Copy")).toBeTruthy();
    expect(screen.getByTitle("Retry")).toBeTruthy();
    expect(screen.getByTitle("Good")).toBeTruthy();
    expect(screen.getByTitle("Bad")).toBeTruthy();
  });

  it("clicking the message-actions buttons does not throw (no-op at Phase 6d)", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    for (const title of ["Copy", "Retry", "Good", "Bad"]) {
      expect(() => fireEvent.click(screen.getByTitle(title))).not.toThrow();
    }
  });

  it("clicking the inline artifact does not throw (no-op at Phase 6d; opens panel in 6e)", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(() => fireEvent.click(screen.getByText("ingestion-buffer.ts"))).not.toThrow();
  });

  it("/app/c/new consumes a pending send from sessionStorage and starts streaming", () => {
    storage["s:channel-pending-send"] = JSON.stringify({
      text: "hello",
      atts: [],
      modelId: MODELS[1].id, // sonnet
      effort: "Low",
    });
    renderAt("/app/c/new");
    // The user turn appears immediately.
    expect(screen.getByText("hello")).toBeTruthy();
    // The assistant header shows the picked model + effort.
    expect(screen.getByText(/Claude Sonnet 4.6 · Low/)).toBeTruthy();
    // The pending-send key is consumed.
    expect(storage["s:channel-pending-send"]).toBeUndefined();
    // The streaming cursor is present before time advances.
    const container = document.body;
    expect(container.querySelector(".cursor")).toBeTruthy();
    // After draining the timer, the cursor + streaming flag are gone and
    // the artifact card appears.
    act(() => vi.advanceTimersByTime(38 * 400));
    expect(container.querySelector(".cursor")).toBeNull();
    expect(screen.getByText("ingestion-buffer.ts")).toBeTruthy();
  });

  it("/app/c/new with no pending payload renders an empty conversation (no user/assistant turns)", () => {
    renderAt("/app/c/new");
    expect(screen.queryByText(SAMPLE_USER)).toBeNull();
    expect(document.body.querySelector(".turn")).toBeNull();
  });

  it("/app/c/<unknown-id> renders an empty conversation", () => {
    renderAt("/app/c/does-not-exist");
    expect(document.body.querySelector(".turn")).toBeNull();
  });

  it("/app/c/new with a malformed JSON payload renders empty (and clears the key)", () => {
    storage["s:channel-pending-send"] = "not-json{";
    renderAt("/app/c/new");
    expect(document.body.querySelector(".turn")).toBeNull();
    expect(storage["s:channel-pending-send"]).toBeUndefined();
  });

  it("/app/c/new with a payload referencing an unknown modelId falls back to the first MODELS entry", () => {
    storage["s:channel-pending-send"] = JSON.stringify({
      text: "hi",
      atts: [],
      modelId: "no-such-model",
      effort: "Max",
    });
    renderAt("/app/c/new");
    expect(screen.getByText(/Claude Opus 4.8 · Max/)).toBeTruthy();
  });

  it("renders a per-turn attachment chip when the pending payload includes atts", () => {
    storage["s:channel-pending-send"] = JSON.stringify({
      text: "look at this",
      atts: [{ kind: "file", name: "spec.pdf", ic: "doc" }],
      modelId: MODELS[0].id,
      effort: "High",
    });
    renderAt("/app/c/new");
    expect(screen.getByText("spec.pdf")).toBeTruthy();
  });

  it("uses the .convo > .convo-inner structure expected by app.css", () => {
    const { container } = renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(container.querySelector(".convo > .convo-inner")).toBeTruthy();
  });

  it("does NOT show the message-actions row while a turn is streaming", () => {
    storage["s:channel-pending-send"] = JSON.stringify({
      text: "hi",
      atts: [],
      modelId: MODELS[0].id,
      effort: "High",
    });
    renderAt("/app/c/new");
    // Mid-stream: actions row should be absent.
    expect(screen.queryByTitle("Copy")).toBeNull();
    act(() => vi.advanceTimersByTime(38 * 400));
    expect(screen.getByTitle("Copy")).toBeTruthy();
  });

  it("/app/c/new renders empty when sessionStorage.getItem throws (private-mode guard)", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: () => { throw new Error("SecurityError"); },
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    renderAt("/app/c/new");
    expect(document.body.querySelector(".turn")).toBeNull();
  });

  it("/app/c/new with a payload missing atts field falls back to empty atts array", () => {
    storage["s:channel-pending-send"] = JSON.stringify({
      text: "no atts here",
      modelId: MODELS[0].id,
      effort: "High",
      // atts intentionally omitted
    });
    renderAt("/app/c/new");
    expect(screen.getByText("no atts here")).toBeTruthy();
  });

  it("guards against StrictMode double-fire — send is only called once on mount", () => {
    storage["s:channel-pending-send"] = JSON.stringify({
      text: "once",
      atts: [],
      modelId: MODELS[0].id,
      effort: "High",
    });
    // StrictMode wraps the route, simulating dev-mode double-fire.
    render(
      <React.StrictMode>
        <MemoryRouter initialEntries={["/app/c/new"]}>
          <Routes>
            <Route path="/app/c/:id" element={<Conversation />} />
          </Routes>
        </MemoryRouter>
      </React.StrictMode>
    );
    // Exactly one user turn with text "once" — not two.
    const userTurns = document.body.querySelectorAll(".turn.user .bubble");
    expect(userTurns.length).toBe(1);
    expect(userTurns[0].textContent).toBe("once");
  });

  it("renders a follow-up Composer at the bottom of the conversation", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    // Composer's textarea has placeholder "Reply…".
    expect(screen.getByPlaceholderText("Reply…")).toBeTruthy();
    // The Send button is part of the Composer.
    expect(screen.getByTitle("Send")).toBeTruthy();
  });

  it("typing in the follow-up Composer + Send appends a new user turn (no navigate)", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    const ta = screen.getByPlaceholderText("Reply…");
    fireEvent.change(ta, { target: { value: "follow up question" } });
    fireEvent.click(screen.getByTitle("Send"));
    // Two user turns now: the canned SAMPLE_USER and the follow-up.
    const bubbles = document.body.querySelectorAll(".turn.user .bubble");
    expect(bubbles.length).toBe(2);
    expect(bubbles[1].textContent).toBe("follow up question");
  });

  it("picking a different model in the follow-up Composer persists via setModel", () => {
    storage["channel-model"] = MODELS[0].id;
    __resetChannelPrefsForTest();
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    // Open the ModelPicker via the visible current-model button in the Composer.
    fireEvent.click(screen.getByRole("button", { name: /Opus 4.8/i }));
    fireEvent.click(screen.getByText("Claude Haiku 4.5"));
    expect(storage["channel-model"]).toBe("claude-haiku-4-5");
  });

  it("re-fires loadSample when the URL :id changes (sidebar recent click while mounted)", () => {
    // Navigate from /app/c/r1 to /app/c/r5 without remount — both ids resolve
    // to the canned SAMPLE_USER/SAMPLE_REPLY pair, but the kickedIdRef must
    // recognise the id change and re-fire loadSample. Without that, the
    // previous conversation would persist.
    function Catcher() {
      const navigate = useNavigate();
      return (
        <button data-testid="goto-r5" onClick={() => navigate("/app/c/r5")}>
          go
        </button>
      );
    }
    render(
      <MemoryRouter initialEntries={["/app/c/r1"]}>
        <Routes>
          <Route
            path="/app/c/:id"
            element={
              <>
                <Conversation />
                <Catcher />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    );
    const initialUserCount = document.body.querySelectorAll(".turn.user").length;
    expect(initialUserCount).toBe(1);
    // Click the catcher button to navigate without unmounting Conversation.
    fireEvent.click(screen.getByTestId("goto-r5"));
    // loadSample replaces the turns array, so after the route param change
    // the conversation should still have exactly 1 user turn (not stacked).
    const afterUserCount = document.body.querySelectorAll(".turn.user").length;
    expect(afterUserCount).toBe(1);
    // And the conversation still shows SAMPLE_USER (because both r1 and r5
    // resolve to the same canned pair).
    expect(screen.getByText(SAMPLE_USER)).toBeTruthy();
  });
});
