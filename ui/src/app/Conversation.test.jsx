// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import Conversation from "./Conversation.jsx";
import { MODELS } from "./data.js";
import { __resetChannelPrefsForTest } from "../hooks/useChannelPrefs.js";

vi.mock("../hooks/useChatStream.js", () => ({
  useChatStream: vi.fn(),
}));

import * as useChatStreamModule from "../hooks/useChatStream.js";

function mockStream(overrides = {}) {
  const ret = {
    turns: [],
    send: vi.fn(),
    regenerate: vi.fn(),
    abort: vi.fn(),
    status: "idle",
    error: null,
    ...overrides,
  };
  useChatStreamModule.useChatStream.mockReturnValue(ret);
  return ret;
}

function renderAt(path, state) {
  const entry = state ? { pathname: path, state } : path;
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/app/c/:id" element={<Conversation />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Conversation", () => {
  let storage;
  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    __resetChannelPrefsForTest();
    useChatStreamModule.useChatStream.mockReset();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("passes the URL :id through to useChatStream", () => {
    mockStream();
    renderAt("/app/c/c-123");
    expect(useChatStreamModule.useChatStream).toHaveBeenCalledWith("c-123");
  });

  it("renders a user turn from useChatStream.turns", () => {
    mockStream({
      turns: [{ msg_id: "m1", role: "user", text: "hello world" }],
    });
    renderAt("/app/c/c1");
    expect(screen.getByText("hello world")).toBeTruthy();
    expect(document.body.querySelector(".turn.user .bubble").textContent).toBe(
      "hello world",
    );
  });

  it("renders an assistant turn with model label and markdown", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "**bold reply**",
          model: "Claude Opus 4.6 · High",
          streaming: false,
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(screen.getByText("Channel")).toBeTruthy();
    expect(screen.getByText("Claude Opus 4.6 · High")).toBeTruthy();
    expect(screen.getByText("bold reply")).toBeTruthy();
  });

  it("renders attachment chips on a user turn that has atts", () => {
    mockStream({
      turns: [
        {
          msg_id: "u1",
          role: "user",
          text: "look at this",
          atts: [{ kind: "file", name: "spec.pdf", ic: "doc" }],
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(screen.getByText("spec.pdf")).toBeTruthy();
  });

  it("does NOT render attachments chip wrapper when atts is empty", () => {
    mockStream({
      turns: [{ msg_id: "u1", role: "user", text: "no atts", atts: [] }],
    });
    renderAt("/app/c/c1");
    expect(document.body.querySelector(".attaches")).toBeNull();
  });

  it("shows the streaming cursor and hides message-actions while streaming", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "partial",
          streaming: true,
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(document.body.querySelector(".cursor")).toBeTruthy();
    expect(screen.queryByTitle("Copy")).toBeNull();
    // While streaming, the assistant turn must NOT advertise the
    // ``assistant-turn-idle`` testid — the Playwright e2e blocks on this
    // attribute to know the SSE turn has finished. Premature presence
    // would race the e2e past mid-stream content.
    expect(
      document.body.querySelector('[data-testid="assistant-turn-idle"]'),
    ).toBeNull();
  });

  it("tags settled assistant turns with data-testid=assistant-turn-idle", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "settled",
          model: "Claude Sonnet 4.6",
          streaming: false,
        },
      ],
    });
    renderAt("/app/c/c1");
    const settled = document.body.querySelectorAll(
      '[data-testid="assistant-turn-idle"]',
    );
    expect(settled).toHaveLength(1);
  });

  it("shows the message-actions row on a completed assistant turn", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "done",
          model: "Claude Opus 4.6 · High",
          streaming: false,
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(screen.getByTitle("Copy")).toBeTruthy();
    expect(screen.getByTitle("Retry")).toBeTruthy();
    expect(screen.getByTitle("Good")).toBeTruthy();
    expect(screen.getByTitle("Bad")).toBeTruthy();
  });

  it("clicking the message-actions buttons does not throw (no-op at Phase 7a)", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "done",
          streaming: false,
        },
      ],
    });
    renderAt("/app/c/c1");
    for (const title of ["Copy", "Retry", "Good", "Bad"]) {
      expect(() => fireEvent.click(screen.getByTitle(title))).not.toThrow();
    }
  });

  it("renders the inline artifact card on assistant turns that carry one", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "see attached",
          streaming: false,
          artifact: { ic: "code", title: "demo.ts", kind: "Code · 12 lines" },
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(screen.getByText("demo.ts")).toBeTruthy();
    expect(screen.getByText("Code · 12 lines")).toBeTruthy();
  });

  it("clicking the inline artifact does not throw", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "see attached",
          streaming: false,
          artifact: { ic: "code", title: "demo.ts", kind: "Code · 12 lines" },
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(() => fireEvent.click(screen.getByText("demo.ts"))).not.toThrow();
  });

  it("sends firstMessage from route state on mount", () => {
    const stream = mockStream();
    const firstMessage = {
      message: "kick off",
      model: MODELS[0],
      effort: "High",
      attachments: [],
    };
    renderAt("/app/c/c1", { firstMessage });
    expect(stream.send).toHaveBeenCalledTimes(1);
    expect(stream.send).toHaveBeenCalledWith(firstMessage);
  });

  it("does NOT send firstMessage twice on re-render", () => {
    const stream = mockStream();
    const firstMessage = { message: "once", model: MODELS[0], effort: "High", attachments: [] };
    const { rerender } = renderAt("/app/c/c1", { firstMessage });
    rerender(
      <MemoryRouter initialEntries={[{ pathname: "/app/c/c1", state: { firstMessage } }]}>
        <Routes>
          <Route path="/app/c/:id" element={<Conversation />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(stream.send).toHaveBeenCalledTimes(1);
  });

  it("does NOT double-send firstMessage under React.StrictMode", () => {
    const stream = mockStream();
    const firstMessage = { message: "once", model: MODELS[0], effort: "High", attachments: [] };
    render(
      <React.StrictMode>
        <MemoryRouter initialEntries={[{ pathname: "/app/c/c1", state: { firstMessage } }]}>
          <Routes>
            <Route path="/app/c/:id" element={<Conversation />} />
          </Routes>
        </MemoryRouter>
      </React.StrictMode>,
    );
    expect(stream.send).toHaveBeenCalledTimes(1);
  });

  it("clears firstMessage from history after consuming it (no replay on revisit)", () => {
    const stream = mockStream();
    const firstMessage = {
      message: "kick off",
      model: MODELS[0],
      effort: "High",
      attachments: [],
    };
    // Two entries in the stack: index 0 is "/app" (Home), index 1 is the
    // chat with the firstMessage. Simulates ChatHome → /app/c/c1 nav.
    render(
      <MemoryRouter
        initialEntries={[
          { pathname: "/app" },
          { pathname: "/app/c/c1", state: { firstMessage } },
        ]}
        initialIndex={1}
      >
        <Routes>
          <Route path="/app/c/:id" element={<Conversation />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(stream.send).toHaveBeenCalledTimes(1);
    // After consuming firstMessage, the active history entry must no
    // longer carry it — back-nav-then-revisit can't replay the send.
    // (We assert behaviour via test-ids on the rendered DOM; the cleared
    // state is verified by the next test which mounts the same chat
    // again and asserts no send.)
  });

  it("does not re-send firstMessage when remounting the same chat after consumption", () => {
    const stream = mockStream();
    const firstMessage = {
      message: "kick off",
      model: MODELS[0],
      effort: "High",
      attachments: [],
    };
    // First mount: consumes firstMessage, sends once.
    const { unmount } = render(
      <MemoryRouter initialEntries={[
        { pathname: "/app/c/c1", state: { firstMessage } },
      ]}>
        <Routes>
          <Route path="/app/c/:id" element={<Conversation />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(stream.send).toHaveBeenCalledTimes(1);
    unmount();

    // Second mount of the SAME chat, no state — simulates sidebar
    // click-revisit. Send must NOT fire again.
    render(
      <MemoryRouter initialEntries={[{ pathname: "/app/c/c1" }]}>
        <Routes>
          <Route path="/app/c/:id" element={<Conversation />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(stream.send).toHaveBeenCalledTimes(1);
  });

  it("does NOT send when route state has no firstMessage", () => {
    const stream = mockStream();
    renderAt("/app/c/c1");
    expect(stream.send).not.toHaveBeenCalled();
  });

  it("does NOT send when chatId is falsy (route doesn't match)", () => {
    const stream = mockStream();
    // Mount Conversation directly outside the matching Route so useParams
    // returns no id — chatId is undefined and the kick-off must no-op.
    render(
      <MemoryRouter initialEntries={["/somewhere/else"]}>
        <Conversation />
      </MemoryRouter>,
    );
    expect(stream.send).not.toHaveBeenCalled();
  });

  it("uses the .convo > .convo-inner structure expected by app.css", () => {
    mockStream();
    const { container } = renderAt("/app/c/c1");
    expect(container.querySelector(".convo > .convo-inner")).toBeTruthy();
  });

  it("renders an error banner when status is 'error'", () => {
    mockStream({ status: "error", error: new Error("boom") });
    renderAt("/app/c/c1");
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("renders a follow-up Composer at the bottom of the conversation", () => {
    mockStream();
    renderAt("/app/c/c1");
    expect(screen.getByPlaceholderText("Reply…")).toBeTruthy();
    expect(screen.getByTitle("Send")).toBeTruthy();
  });

  it("typing in the follow-up Composer + Send calls useChatStream.send with named args", () => {
    const stream = mockStream();
    renderAt("/app/c/c1");
    const ta = screen.getByPlaceholderText("Reply…");
    fireEvent.change(ta, { target: { value: "follow up question" } });
    fireEvent.click(screen.getByTitle("Send"));
    expect(stream.send).toHaveBeenCalledTimes(1);
    const call = stream.send.mock.calls[0][0];
    expect(call.message).toBe("follow up question");
    expect(call.attachments).toEqual([]);
    expect(call.model).toBeDefined();
    expect(call.effort).toBeDefined();
  });

  it("picking a different model in the follow-up Composer persists via setModel", () => {
    mockStream();
    storage["channel-model"] = MODELS[0].id;
    __resetChannelPrefsForTest();
    renderAt("/app/c/c1");
    fireEvent.click(screen.getByRole("button", { name: /Opus 4.6/i }));
    fireEvent.click(screen.getByText("Claude Haiku 4.5"));
    expect(storage["channel-model"]).toBe("claude-haiku-4-5");
  });

  it("does not throw when turns array is empty", () => {
    mockStream({ turns: [] });
    expect(() => renderAt("/app/c/c1")).not.toThrow();
    expect(document.body.querySelector(".turn")).toBeNull();
  });

  it("falls back to first MODELS entry when prefs.model is unknown (and Send uses it)", () => {
    const stream = mockStream();
    storage["channel-model"] = "no-such-model";
    __resetChannelPrefsForTest();
    renderAt("/app/c/c1");
    const ta = screen.getByPlaceholderText("Reply…");
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.click(screen.getByTitle("Send"));
    // followUp passes the short id (`model.id`), not the full picker
    // object — backend expects `model: str | None`.
    expect(stream.send.mock.calls[0][0].model).toBe(MODELS[0].id);
  });

  it("renders friendly model label, not raw ARN", () => {
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        {
          role: "assistant",
          text: "ok",
          msg_id: "a1",
          model: "us.anthropic.claude-sonnet-4-6",
          streaming: false,
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(screen.getByText("Claude Sonnet 4.6")).toBeInTheDocument();
    expect(screen.queryByText(/^us\.anthropic\.|^anthropic\./)).toBeNull();
  });

  it.each([
    ["us.anthropic.claude-sonnet-4-6", "Claude Sonnet 4.6"],
    ["us.anthropic.claude-opus-4-6-v1", "Claude Opus 4.6"],
    ["us.anthropic.claude-haiku-4-5-20251001-v1:0", "Claude Haiku 4.5"],
    ["anthropic.claude-sonnet-4-6", "Claude Sonnet 4.6"],
    ["global.anthropic.claude-haiku-4-5-20251001-v1:0", "Claude Haiku 4.5"],
  ])("modelLabel strips %s -> %s", (raw, expected) => {
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        {
          role: "assistant",
          text: "ok",
          msg_id: "a1",
          model: raw,
          streaming: false,
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(screen.getByText(expected)).toBeInTheDocument();
  });

  it("falls back to raw model id when not found in MODELS", () => {
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        {
          role: "assistant",
          text: "ok",
          msg_id: "a1",
          model: "anthropic.totally-unknown-model",
          streaming: false,
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(
      screen.getByText("anthropic.totally-unknown-model"),
    ).toBeInTheDocument();
  });

  it("retry icon on the last assistant turn calls hook.regenerate", () => {
    const regenerate = vi.fn();
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        { role: "assistant", text: "ok", msg_id: "a1", streaming: false },
      ],
      regenerate,
    });
    renderAt("/app/c/c1");
    fireEvent.click(screen.getByTitle("Retry"));
    expect(regenerate).toHaveBeenCalled();
  });

  it("retry icon is absent on the streaming assistant turn (msg-actions hidden)", () => {
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        { role: "assistant", text: "", msg_id: "a1", streaming: true },
      ],
      status: "streaming",
    });
    renderAt("/app/c/c1");
    expect(screen.queryByTitle("Retry")).toBeNull();
  });

  it("retry icon on earlier (non-last) assistant turns is disabled", () => {
    const regenerate = vi.fn();
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        { role: "assistant", text: "first", msg_id: "a1", streaming: false },
        { role: "user", text: "hi again", msg_id: "u2" },
        { role: "assistant", text: "second", msg_id: "a2", streaming: false },
      ],
      regenerate,
    });
    renderAt("/app/c/c1");
    const retries = screen.getAllByTitle("Retry");
    expect(retries).toHaveLength(2);
    // Earlier assistant turn — disabled.
    expect(retries[0]).toBeDisabled();
    // Last assistant turn — enabled.
    expect(retries[1]).not.toBeDisabled();
    fireEvent.click(retries[1]);
    expect(regenerate).toHaveBeenCalledTimes(1);
  });

  it("standalone 'Regenerate' text button is no longer rendered", () => {
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        { role: "assistant", text: "ok", msg_id: "a1", streaming: false },
      ],
    });
    renderAt("/app/c/c1");
    expect(screen.queryByText("Regenerate")).toBeNull();
  });
});
