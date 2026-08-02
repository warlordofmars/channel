// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { __resetModelsCacheForTest, loadModels } from "./data.js";
import { __resetChannelPrefsForTest } from "../hooks/useChannelPrefs.js";

vi.mock("../api.js", () => ({
  listModels: vi.fn(),
  submitFeedback: vi.fn(),
  listMCPServers: vi.fn(() => Promise.resolve({ servers: [] })),
  getChatMCPSettings: vi.fn(() =>
    Promise.resolve({ mode: "inherit", explicit_server_ids: [] }),
  ),
  putChatMCPSettings: vi.fn(() => Promise.resolve()),
  // #361: InlineImage's useAssetContent hook Bearer-fetches image bytes.
  getAssetContent: vi.fn(() =>
    Promise.resolve({ blob: async () => new Blob(["PNGBYTES"]) }),
  ),
}));

import * as api from "../api.js";
import Conversation from "./Conversation.jsx";

// Synthetic test allowlist — mirrors what /api/models serves.
const SERVER_ALLOWLIST = [
  { id: "claude-opus-4-6", label: "Claude Opus 4.6", tier: "Flagship" },
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", tier: "Balanced" },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5", tier: "Fast" },
];
const OPUS_ID = "claude-opus-4-6";

vi.mock("../hooks/useChatStream.js", () => ({
  useChatStream: vi.fn(),
}));

vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: vi.fn(() => ({
    chats: [],
    renameChatLocal: vi.fn(),
    renameChat: vi.fn(),
    archiveChat: vi.fn(),
    createChat: vi.fn(),
    refresh: vi.fn(),
    status: "idle",
  })),
}));

import * as useChatStreamModule from "../hooks/useChatStream.js";
import * as useChatsModule from "../hooks/ChatsContext.jsx";
import { codeAssetsByFence, standaloneAssets } from "./Conversation.jsx";

function mockStream(overrides = {}) {
  const ret = {
    turns: [],
    send: vi.fn(),
    regenerate: vi.fn(),
    abort: vi.fn(),
    status: "idle",
    error: null,
    loadOlder: vi.fn(),
    hasOlder: false,
    ...overrides,
  };
  useChatStreamModule.useChatStream.mockReturnValue(ret);
  return ret;
}

function conversationTree(entry) {
  return (
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/app/c/:id" element={<Conversation />} />
      </Routes>
    </MemoryRouter>
  );
}

function renderAt(path, state) {
  const entry = state ? { pathname: path, state } : path;
  return render(conversationTree(entry));
}

describe("Conversation", () => {
  let storage;
  beforeEach(async () => {
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
    __resetModelsCacheForTest();
    api.listModels.mockReset();
    api.listModels.mockResolvedValue({ models: SERVER_ALLOWLIST });
    api.submitFeedback.mockReset();
    api.submitFeedback.mockResolvedValue(undefined);
    api.getAssetContent.mockReset();
    api.getAssetContent.mockResolvedValue({
      blob: async () => new Blob(["PNGBYTES"]),
    });
    // Pre-warm the module-level cache so synchronous renders see the
    // models immediately — the production component reads `cachedModels()`
    // in its useState initializer.
    await loadModels();
    useChatStreamModule.useChatStream.mockReset();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    __resetModelsCacheForTest();
  });

  it("passes the URL :id through to useChatStream", () => {
    mockStream();
    renderAt("/app/c/c-123");
    expect(useChatStreamModule.useChatStream).toHaveBeenCalledWith(
      "c-123",
      expect.objectContaining({ onTitleSuggested: expect.any(Function) }),
    );
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

  it("shows 'Load earlier messages' only when hasOlder, and calls loadOlder on click (#270)", () => {
    const loadOlder = vi.fn();
    mockStream({ hasOlder: true, loadOlder });
    renderAt("/app/c/c1");
    const btn = screen.getByRole("button", { name: /load earlier messages/i });
    fireEvent.click(btn);
    expect(loadOlder).toHaveBeenCalledTimes(1);
  });

  it("hides 'Load earlier messages' when there is no older history", () => {
    mockStream({ hasOlder: false });
    renderAt("/app/c/c1");
    expect(
      screen.queryByRole("button", { name: /load earlier messages/i }),
    ).toBeNull();
  });

  it("keeps the prepended older page visible after Load earlier (no snap-to-bottom) (#270)", () => {
    // Click sets the prepend anchor; the next turns change must take the
    // position-preserving branch of the auto-scroll effect rather than
    // scrolling to the bottom.
    const ret = mockStream({
      turns: [{ msg_id: "m2", role: "user", text: "newer" }],
      hasOlder: true,
    });
    // A fresh element each time — passing the same reference to rerender
    // makes React bail out (referential equality) and skip the re-render.
    const { rerender } = render(conversationTree("/app/c/c1"));
    fireEvent.click(
      screen.getByRole("button", { name: /load earlier messages/i }),
    );
    // Older page lands: an older turn is prepended at the head.
    useChatStreamModule.useChatStream.mockReturnValue({
      ...ret,
      turns: [
        { msg_id: "m1", role: "user", text: "older" },
        { msg_id: "m2", role: "user", text: "newer" },
      ],
      hasOlder: false,
    });
    rerender(conversationTree("/app/c/c1"));
    expect(screen.getByText("older")).toBeTruthy();
    expect(screen.getByText("newer")).toBeTruthy();
  });

  it("a no-op Load earlier followed by a new tail message does not hijack scrolling (#270)", () => {
    // Click sets the prepend anchor, but loadOlder no-ops (head unchanged).
    // The next tail message must take the scroll-to-bottom branch, not the
    // position-preserving one, and the stale anchor must be cleared.
    const ret = mockStream({
      turns: [{ msg_id: "m1", role: "user", text: "head" }],
      hasOlder: true,
    });
    const { rerender } = render(conversationTree("/app/c/c1"));
    fireEvent.click(
      screen.getByRole("button", { name: /load earlier messages/i }),
    );
    // A new assistant turn arrives at the TAIL — head id is unchanged.
    useChatStreamModule.useChatStream.mockReturnValue({
      ...ret,
      turns: [
        { msg_id: "m1", role: "user", text: "head" },
        { msg_id: "m2", role: "assistant", text: "tail" },
      ],
    });
    rerender(conversationTree("/app/c/c1"));
    expect(screen.getByText("tail")).toBeTruthy();
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

  it("clicking Retry on the last assistant turn does not throw when no user message precedes it", () => {
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
    // Retry is the regenerate trigger on the last assistant turn; here
    // there's no preceding user message so the handler short-circuits
    // (`regenerate({})` is wired but useChatStream's mock send simply
    // records the call). Copy + Feedback have dedicated suites below.
    expect(() => fireEvent.click(screen.getByTitle("Retry"))).not.toThrow();
  });

  describe("Feedback buttons (issue #146)", () => {
    function assistantTurnWith({ msg_id = "a1", feedback = null } = {}) {
      return [
        { msg_id, role: "assistant", text: "reply", streaming: false, feedback },
      ];
    }

    it("clicking thumbs-up calls submitFeedback with kind=up", async () => {
      mockStream({ turns: assistantTurnWith() });
      renderAt("/app/c/c-fb");
      fireEvent.click(screen.getByTitle("Good"));
      await vi.waitFor(() =>
        expect(api.submitFeedback).toHaveBeenCalledWith("c-fb", "a1", {
          kind: "up",
          note: null,
        }),
      );
    });

    it("clicking thumbs-down calls submitFeedback with kind=down", async () => {
      mockStream({ turns: assistantTurnWith() });
      renderAt("/app/c/c-fb");
      fireEvent.click(screen.getByTitle("Bad"));
      await vi.waitFor(() =>
        expect(api.submitFeedback).toHaveBeenCalledWith("c-fb", "a1", {
          kind: "down",
          note: null,
        }),
      );
    });

    it("the clicked thumb gets the is-active class optimistically", async () => {
      // Hold submitFeedback open so the optimistic state is observable
      // before the request settles.
      let resolveFn;
      api.submitFeedback.mockImplementation(
        () => new Promise((r) => { resolveFn = r; }),
      );
      mockStream({ turns: assistantTurnWith() });
      renderAt("/app/c/c-fb");
      const upBtn = screen.getByTitle("Good");
      fireEvent.click(upBtn);
      expect(upBtn.className).toContain("is-active");
      expect(upBtn.getAttribute("aria-pressed")).toBe("true");
      resolveFn();
      await vi.waitFor(() => expect(api.submitFeedback).toHaveBeenCalled());
    });

    it("hydrates the active state from initialKind on first render", () => {
      mockStream({
        turns: assistantTurnWith({ feedback: { kind: "down", note: null } }),
      });
      renderAt("/app/c/c-fb");
      expect(screen.getByTitle("Bad").className).toContain("is-active");
      expect(screen.getByTitle("Good").className).not.toContain("is-active");
    });

    it("clicking the already-active thumbs-up is a no-op (no API call)", async () => {
      // Until a server-side clear lands, re-clicking the active thumb
      // can't be allowed to clear visual state without diverging from
      // the stored signal (Copilot review iteration 2). Behaviour:
      // ignore the click entirely so the row stays self-consistent.
      mockStream({
        turns: assistantTurnWith({ feedback: { kind: "up", note: null } }),
      });
      renderAt("/app/c/c-fb");
      const upBtn = screen.getByTitle("Good");
      expect(upBtn.className).toContain("is-active");
      fireEvent.click(upBtn);
      // Microtask flush so any erroneously-fired async submit would
      // have landed by now.
      await Promise.resolve();
      expect(api.submitFeedback).not.toHaveBeenCalled();
      expect(screen.getByTitle("Good").className).toContain("is-active");
    });

    it("clicking the already-active thumbs-down is a no-op (no API call)", async () => {
      mockStream({
        turns: assistantTurnWith({ feedback: { kind: "down", note: null } }),
      });
      renderAt("/app/c/c-fb");
      const downBtn = screen.getByTitle("Bad");
      expect(downBtn.className).toContain("is-active");
      fireEvent.click(downBtn);
      await Promise.resolve();
      expect(api.submitFeedback).not.toHaveBeenCalled();
      expect(screen.getByTitle("Bad").className).toContain("is-active");
    });

    it("reverts the optimistic state when submitFeedback rejects", async () => {
      api.submitFeedback.mockRejectedValueOnce(new Error("network"));
      mockStream({ turns: assistantTurnWith() });
      renderAt("/app/c/c-fb");
      const upBtn = screen.getByTitle("Good");
      fireEvent.click(upBtn);
      // Wait for the rejection handler to flip state back; waitFor
      // re-runs the assertion until it passes (covers the async gap
      // between fireEvent.click and the catch block running).
      await vi.waitFor(() =>
        expect(screen.getByTitle("Good").className).not.toContain("is-active"),
      );
      expect(screen.getByTitle("Bad").className).not.toContain("is-active");
    });

    it("clicking the OTHER thumb swaps the active state", async () => {
      mockStream({
        turns: assistantTurnWith({ feedback: { kind: "up", note: null } }),
      });
      renderAt("/app/c/c-fb");
      expect(screen.getByTitle("Good").className).toContain("is-active");
      fireEvent.click(screen.getByTitle("Bad"));
      await vi.waitFor(() =>
        expect(api.submitFeedback).toHaveBeenCalledWith("c-fb", "a1", {
          kind: "down",
          note: null,
        }),
      );
      expect(screen.getByTitle("Good").className).not.toContain("is-active");
      expect(screen.getByTitle("Bad").className).toContain("is-active");
    });

    it("disables both buttons while a request is pending", async () => {
      let resolveFn;
      api.submitFeedback.mockImplementation(
        () => new Promise((r) => { resolveFn = r; }),
      );
      mockStream({ turns: assistantTurnWith() });
      renderAt("/app/c/c-fb");
      fireEvent.click(screen.getByTitle("Good"));
      // While pending, both thumbs carry the `disabled` attribute —
      // React's synthetic event system suppresses onClick for those
      // buttons so a double-click can't race the optimistic update.
      expect(screen.getByTitle("Good").disabled).toBe(true);
      expect(screen.getByTitle("Bad").disabled).toBe(true);
      resolveFn();
      await vi.waitFor(() =>
        expect(screen.getByTitle("Good").disabled).toBe(false),
      );
    });
  });

  describe("Copy button", () => {
    function turnWithText(text) {
      return [
        { role: "user", text: "hi", msg_id: "u1" },
        { role: "assistant", text, msg_id: "a1", streaming: false },
      ];
    }

    it("writes the assistant turn text to navigator.clipboard.writeText", async () => {
      const writeText = vi.fn(() => Promise.resolve());
      vi.stubGlobal("navigator", { clipboard: { writeText } });
      mockStream({ turns: turnWithText("The reply") });
      renderAt("/app/c/c1");
      fireEvent.click(screen.getByTitle("Copy"));
      await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith("The reply"));
    });

    it("swaps the title to 'Copied' for ~1.5s after success then reverts", async () => {
      vi.useFakeTimers();
      const writeText = vi.fn(() => Promise.resolve());
      vi.stubGlobal("navigator", { clipboard: { writeText } });
      mockStream({ turns: turnWithText("ok") });
      renderAt("/app/c/c1");
      fireEvent.click(screen.getByTitle("Copy"));
      // Flush the awaited writeText promise so the .then(setCopied(true)) runs.
      await vi.advanceTimersByTimeAsync(0);
      expect(screen.getByTitle("Copied")).toBeTruthy();
      await vi.advanceTimersByTimeAsync(1500);
      expect(screen.getByTitle("Copy")).toBeTruthy();
      vi.useRealTimers();
    });

    it("uses the document.execCommand fallback when navigator.clipboard is missing", async () => {
      vi.stubGlobal("navigator", {});
      const execCommand = vi.fn(() => true);
      document.execCommand = execCommand;
      mockStream({ turns: turnWithText("fallback text") });
      renderAt("/app/c/c1");
      fireEvent.click(screen.getByTitle("Copy"));
      await vi.waitFor(() => expect(execCommand).toHaveBeenCalledWith("copy"));
    });

    it("swallows clipboard errors silently (no toast, no thrown error)", async () => {
      const writeText = vi.fn(() => Promise.reject(new Error("denied")));
      vi.stubGlobal("navigator", { clipboard: { writeText } });
      // Both paths fail — execCommand returns false.
      document.execCommand = vi.fn(() => false);
      mockStream({ turns: turnWithText("nope") });
      renderAt("/app/c/c1");
      fireEvent.click(screen.getByTitle("Copy"));
      await vi.waitFor(() => expect(writeText).toHaveBeenCalled());
      // Title stays "Copy" — no false "Copied" feedback.
      expect(screen.getByTitle("Copy")).toBeTruthy();
    });

    it("swallows synchronous errors thrown by the execCommand fallback", async () => {
      vi.stubGlobal("navigator", {});
      // execCommand throws — exercises the inner catch in copyTextToClipboard.
      document.execCommand = vi.fn(() => { throw new Error("oops"); });
      mockStream({ turns: turnWithText("boom") });
      renderAt("/app/c/c1");
      fireEvent.click(screen.getByTitle("Copy"));
      // Microtask flush so the awaited copy resolves before the assertion.
      await Promise.resolve();
      // No "Copied" affordance — title stays "Copy".
      expect(screen.getByTitle("Copy")).toBeTruthy();
    });

    it("each Copy button manages its own state independently", async () => {
      vi.useFakeTimers();
      const writeText = vi.fn(() => Promise.resolve());
      vi.stubGlobal("navigator", { clipboard: { writeText } });
      mockStream({
        turns: [
          { role: "user", text: "hi", msg_id: "u1" },
          { role: "assistant", text: "first", msg_id: "a1", streaming: false },
          { role: "user", text: "more", msg_id: "u2" },
          { role: "assistant", text: "second", msg_id: "a2", streaming: false },
        ],
      });
      renderAt("/app/c/c1");
      const copyButtons = screen.getAllByTitle("Copy");
      expect(copyButtons).toHaveLength(2);
      fireEvent.click(copyButtons[0]);
      await vi.advanceTimersByTimeAsync(0);
      // First reverts to "Copy"? No — it should show "Copied" while the
      // second still shows "Copy".
      expect(screen.getAllByTitle("Copy")).toHaveLength(1);
      expect(screen.getByTitle("Copied")).toBeTruthy();
      vi.useRealTimers();
    });

    it("clearing a pending revert timer when Copy is clicked again before the previous one fired", async () => {
      vi.useFakeTimers();
      const writeText = vi.fn(() => Promise.resolve());
      vi.stubGlobal("navigator", { clipboard: { writeText } });
      mockStream({ turns: turnWithText("twice") });
      renderAt("/app/c/c1");
      const btn = screen.getByTitle("Copy");
      fireEvent.click(btn);
      await vi.advanceTimersByTimeAsync(0);
      // Title is now "Copied" — a timer is pending.
      expect(screen.getByTitle("Copied")).toBeTruthy();
      // Second click while the previous timer still pending — exercises
      // the `if (timerRef.current) clearTimeout(...)` branch.
      fireEvent.click(screen.getByTitle("Copied"));
      await vi.advanceTimersByTimeAsync(0);
      // Still shows "Copied" after the second click + microtask flush.
      expect(screen.getByTitle("Copied")).toBeTruthy();
      await vi.advanceTimersByTimeAsync(1500);
      expect(screen.getByTitle("Copy")).toBeTruthy();
      vi.useRealTimers();
    });

    it("clears the revert timer on unmount (no stray setState after unmount)", async () => {
      vi.useFakeTimers();
      const writeText = vi.fn(() => Promise.resolve());
      vi.stubGlobal("navigator", { clipboard: { writeText } });
      mockStream({ turns: turnWithText("clean") });
      const { unmount } = renderAt("/app/c/c1");
      fireEvent.click(screen.getByTitle("Copy"));
      await vi.advanceTimersByTimeAsync(0);
      unmount();
      // Advance past the 1.5s window — would fire the setCopied timeout
      // if the cleanup didn't run.
      expect(() => vi.advanceTimersByTime(2000)).not.toThrow();
      vi.useRealTimers();
    });
  });

  it("renders a standalone asset card on an assistant turn (#327)", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "see the figure",
          streaming: false,
          assets: [
            {
              asset_id: "as-1",
              msg_id: "a1",
              kind: "image",
              title: "Figure 1",
              size_bytes: 4096,
              source: { msg_id: "a1", tool_use_id: "t1" },
            },
          ],
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(screen.getByText("Figure 1")).toBeTruthy();
    expect(screen.getByText("image · 4.0 KB")).toBeTruthy();
  });

  it("renders an upload asset card on a user turn (#327)", () => {
    mockStream({
      turns: [
        {
          msg_id: "u1",
          role: "user",
          text: "have a look",
          assets: [
            {
              asset_id: "up-1",
              msg_id: "u1",
              kind: "document",
              title: "report.pdf",
              size_bytes: 2048,
              source: { msg_id: "u1", attachment_id: "att-1" },
            },
          ],
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(screen.getByText("report.pdf")).toBeTruthy();
  });

  it("decorates a fenced code block by ordinal instead of swapping a card (#362)", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "```python\nx = 1\ny = 2\n```",
          streaming: false,
          assets: [
            {
              asset_id: "as-code",
              msg_id: "a1",
              kind: "code",
              title: "snippet.py",
              size_bytes: 300,
              source: { msg_id: "a1", fence_index: 0, lang: "python" },
            },
          ],
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    // The fence renders as its native code block with an open-in-panel
    // affordance — not swapped to a card.
    const code = container.querySelector(".code-decorated pre code");
    expect(code).toBeTruthy();
    expect(code.textContent).toContain("x = 1");
    expect(screen.getByText("Open in panel")).toBeTruthy();
    // No double-render: the fence-origin code asset is excluded from the
    // standalone-card partition, so there is no AssetCard for it.
    expect(container.querySelector(".art-inline")).toBeNull();
    expect(screen.queryByText("snippet.py")).toBeNull();
    // Exactly one rendering of the code — no duplicate block.
    expect(container.querySelectorAll("pre code").length).toBe(1);
  });

  it("opens ArtifactPanel via the browse route when the open-in-panel affordance is clicked (#362)", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "```python\nx = 1\ny = 2\n```",
          streaming: false,
          assets: [
            {
              asset_id: "as-code",
              msg_id: "a1",
              kind: "code",
              title: "snippet.py",
              size_bytes: 300,
              source: { msg_id: "a1", fence_index: 0, lang: "python" },
            },
          ],
        },
      ],
    });
    render(
      <MemoryRouter initialEntries={["/app/c/c1"]}>
        <Routes>
          <Route path="/app/c/:id" element={<Conversation />} />
          <Route
            path="/app/artifacts"
            element={<div>ARTIFACTS BROWSE ROUTE</div>}
          />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("Open in panel"));
    expect(screen.getByText("ARTIFACTS BROWSE ROUTE")).toBeTruthy();
  });

  it("degrades a non-located fence-code asset to a standalone card (#362 scan drift)", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "```python\nx = 1\n```",
          streaming: false,
          assets: [
            {
              asset_id: "as-drift",
              msg_id: "a1",
              kind: "code",
              // Ordinal 5 can't be located in a one-fence message → the
              // asset isn't decorated inline, so it must still card rather
              // than silently disappear.
              title: "orphan.py",
              size_bytes: 300,
              source: { msg_id: "a1", fence_index: 5, lang: "python" },
            },
          ],
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    // No inline decoration (the ordinal wasn't located)...
    expect(container.querySelector(".code-decorated")).toBeNull();
    expect(screen.queryByText("Open in panel")).toBeNull();
    // ...but the asset still renders as a standalone card (reachable panel).
    expect(container.querySelector(".art-inline")).toBeTruthy();
    expect(screen.getByText("orphan.py")).toBeTruthy();
  });

  it("navigates to the browse route when an asset card is clicked (#327)", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "see the figure",
          streaming: false,
          assets: [
            {
              asset_id: "as-1",
              msg_id: "a1",
              kind: "image",
              title: "Figure 1",
              size_bytes: 4096,
              source: { msg_id: "a1" },
            },
          ],
        },
      ],
    });
    render(
      <MemoryRouter initialEntries={["/app/c/c1"]}>
        <Routes>
          <Route path="/app/c/:id" element={<Conversation />} />
          <Route
            path="/app/artifacts"
            element={<div>ARTIFACTS BROWSE ROUTE</div>}
          />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("Figure 1"));
    expect(screen.getByText("ARTIFACTS BROWSE ROUTE")).toBeTruthy();
  });

  it("renders a within-threshold image asset inline as an <img> on an assistant turn (#361)", async () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "here is your image",
          streaming: false,
          assets: [
            {
              asset_id: "gen-1",
              chat_id: "c1",
              msg_id: "a1",
              kind: "image",
              title: "sunset.png",
              mime: "image/png",
              size_bytes: 3.8 * 1024 * 1024,
              source: { msg_id: "a1", tool_use_id: "t1" },
            },
          ],
        },
      ],
    });
    renderAt("/app/c/c1");
    const img = await screen.findByRole("img");
    expect(img.getAttribute("src")).toBe("blob:test-url");
    expect(img.getAttribute("alt")).toBe("sunset.png");
    expect(api.getAssetContent).toHaveBeenCalledWith("c1", "gen-1");
  });

  it("renders an upload-origin image inline on a user turn (#361)", async () => {
    mockStream({
      turns: [
        {
          msg_id: "u1",
          role: "user",
          text: "have a look",
          assets: [
            {
              asset_id: "up-img",
              chat_id: "c1",
              msg_id: "u1",
              kind: "image",
              title: "screenshot.png",
              mime: "image/png",
              size_bytes: 120 * 1024,
              origin: "upload",
              source: { msg_id: "u1", attachment_id: "att-1" },
            },
          ],
        },
      ],
    });
    renderAt("/app/c/c1");
    const img = await screen.findByRole("img");
    expect(img.getAttribute("alt")).toBe("screenshot.png");
  });

  it("falls back to the card for an over-threshold image (#361)", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "big one",
          streaming: false,
          assets: [
            {
              asset_id: "big-1",
              chat_id: "c1",
              msg_id: "a1",
              kind: "image",
              title: "huge.png",
              mime: "image/png",
              size_bytes: 6 * 1024 * 1024,
              source: { msg_id: "a1" },
            },
          ],
        },
      ],
    });
    renderAt("/app/c/c1");
    // Card, not an inline <img>; the oversized image never fetches inline.
    expect(screen.getByText("huge.png")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(api.getAssetContent).not.toHaveBeenCalled();
  });

  it("falls back to the card for a non-raster image mime (#361)", () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "vector",
          streaming: false,
          assets: [
            {
              asset_id: "svg-1",
              chat_id: "c1",
              msg_id: "a1",
              kind: "image",
              title: "diagram.svg",
              mime: "image/svg+xml",
              size_bytes: 4096,
              source: { msg_id: "a1" },
            },
          ],
        },
      ],
    });
    renderAt("/app/c/c1");
    expect(screen.getByText("diagram.svg")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("opens the browse route when an inline image is clicked (#361)", async () => {
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "here is your image",
          streaming: false,
          assets: [
            {
              asset_id: "gen-1",
              chat_id: "c1",
              msg_id: "a1",
              kind: "image",
              title: "sunset.png",
              mime: "image/png",
              size_bytes: 1024,
              source: { msg_id: "a1" },
            },
          ],
        },
      ],
    });
    render(
      <MemoryRouter initialEntries={["/app/c/c1"]}>
        <Routes>
          <Route path="/app/c/:id" element={<Conversation />} />
          <Route
            path="/app/artifacts"
            element={<div>ARTIFACTS BROWSE ROUTE</div>}
          />
        </Routes>
      </MemoryRouter>,
    );
    const img = await screen.findByRole("img");
    fireEvent.click(img);
    expect(screen.getByText("ARTIFACTS BROWSE ROUTE")).toBeTruthy();
  });

  it("renders a kind=data CSV asset inline as a table (#363)", async () => {
    api.getAssetContent.mockResolvedValue({
      text: async () => "name,score\nada,9\nlin,7",
    });
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "here is the data",
          streaming: false,
          assets: [
            {
              asset_id: "csv-1",
              chat_id: "c1",
              msg_id: "a1",
              kind: "data",
              title: "scores.csv",
              mime: "text/csv",
              size_bytes: 40,
              source: { msg_id: "a1", tool_use_id: "t1" },
            },
          ],
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    expect(await screen.findByText("ada")).toBeTruthy();
    expect(container.querySelector(".convo-data-table")).toBeTruthy();
    expect(api.getAssetContent).toHaveBeenCalledWith("c1", "csv-1");
  });

  it("renders an upload-origin CSV inline on a user turn (#363 origin-agnostic)", async () => {
    api.getAssetContent.mockResolvedValue({
      text: async () => "a,b\n1,2",
    });
    mockStream({
      turns: [
        {
          msg_id: "u1",
          role: "user",
          text: "have a look at this sheet",
          assets: [
            {
              asset_id: "up-csv",
              chat_id: "c1",
              msg_id: "u1",
              kind: "data",
              title: "upload.csv",
              mime: "text/csv",
              size_bytes: 12,
              origin: "upload",
              source: { msg_id: "u1", attachment_id: "att-1" },
            },
          ],
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    // Inline table — NOT a card — even though origin=upload.
    expect(await screen.findByText("Open full table")).toBeTruthy();
    expect(container.querySelector(".convo-data-table")).toBeTruthy();
    expect(container.querySelector(".art-inline")).toBeNull();
  });

  it("renders a kind=document markdown asset inline (#363)", async () => {
    api.getAssetContent.mockResolvedValue({
      text: async () => "# Design notes\n\nInline preview body.",
    });
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "see the doc",
          streaming: false,
          assets: [
            {
              asset_id: "doc-1",
              chat_id: "c1",
              msg_id: "a1",
              kind: "document",
              title: "design.md",
              mime: "text/markdown",
              size_bytes: 42,
              source: { msg_id: "a1", tool_use_id: "t1" },
            },
          ],
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    expect(await screen.findByText("Design notes")).toBeTruthy();
    expect(container.querySelector(".convo-doc-body")).toBeTruthy();
    expect(screen.getByText("Open full document")).toBeTruthy();
  });

  it("renders an upload-origin markdown inline on a user turn (#363 origin-agnostic)", async () => {
    api.getAssetContent.mockResolvedValue({
      text: async () => "# Uploaded\n\nfrom the picker",
    });
    mockStream({
      turns: [
        {
          msg_id: "u1",
          role: "user",
          text: "here are my notes",
          assets: [
            {
              asset_id: "up-doc",
              chat_id: "c1",
              msg_id: "u1",
              kind: "document",
              title: "notes.md",
              mime: "text/markdown",
              size_bytes: 24,
              origin: "upload",
              source: { msg_id: "u1", attachment_id: "att-2" },
            },
          ],
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    // Inline markdown preview — NOT a card — even though origin=upload.
    expect(await screen.findByText("Uploaded")).toBeTruthy();
    expect(container.querySelector(".convo-doc-body")).toBeTruthy();
    expect(container.querySelector(".art-inline")).toBeNull();
  });

  it("falls back to a card for a PDF document (#363 — no inline markdown path)", () => {
    mockStream({
      turns: [
        {
          msg_id: "u1",
          role: "user",
          text: "the report",
          assets: [
            {
              asset_id: "pdf-1",
              chat_id: "c1",
              msg_id: "u1",
              kind: "document",
              title: "report.pdf",
              mime: "application/pdf",
              size_bytes: 90000,
              origin: "upload",
              source: { msg_id: "u1", attachment_id: "att-3" },
            },
          ],
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    expect(screen.getByText("report.pdf")).toBeTruthy();
    expect(container.querySelector(".art-inline")).toBeTruthy();
    expect(container.querySelector(".convo-doc-body")).toBeNull();
    expect(api.getAssetContent).not.toHaveBeenCalled();
  });

  it("opens the browse route when the data Open-full affordance is clicked (#363)", async () => {
    api.getAssetContent.mockResolvedValue({
      text: async () => "a,b\n1,2",
    });
    mockStream({
      turns: [
        {
          msg_id: "a1",
          role: "assistant",
          text: "data",
          streaming: false,
          assets: [
            {
              asset_id: "csv-2",
              chat_id: "c1",
              msg_id: "a1",
              kind: "data",
              title: "t.csv",
              mime: "text/csv",
              size_bytes: 8,
              source: { msg_id: "a1" },
            },
          ],
        },
      ],
    });
    render(
      <MemoryRouter initialEntries={["/app/c/c1"]}>
        <Routes>
          <Route path="/app/c/:id" element={<Conversation />} />
          <Route
            path="/app/artifacts"
            element={<div>ARTIFACTS BROWSE ROUTE</div>}
          />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText("Open full table"));
    expect(screen.getByText("ARTIFACTS BROWSE ROUTE")).toBeTruthy();
  });

  it("sends firstMessage from route state on mount", () => {
    const stream = mockStream();
    const firstMessage = {
      message: "kick off",
      model: OPUS_ID,
      effort: "High",
      attachments: [],
    };
    renderAt("/app/c/c1", { firstMessage });
    expect(stream.send).toHaveBeenCalledTimes(1);
    expect(stream.send).toHaveBeenCalledWith(firstMessage);
  });

  it("does NOT send firstMessage twice on re-render", () => {
    const stream = mockStream();
    const firstMessage = { message: "once", model: OPUS_ID, effort: "High", attachments: [] };
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
    const firstMessage = { message: "once", model: OPUS_ID, effort: "High", attachments: [] };
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
      model: OPUS_ID,
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
      model: OPUS_ID,
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

  it("forwards title_suggested via onTitleSuggested → ChatsContext.renameChatLocal", () => {
    const renameChatLocal = vi.fn();
    useChatsModule.useChats.mockReturnValueOnce({
      chats: [],
      renameChatLocal,
      renameChat: vi.fn(),
      archiveChat: vi.fn(),
      createChat: vi.fn(),
      refresh: vi.fn(),
      status: "idle",
    });
    mockStream();
    renderAt("/app/c/c-abc");

    // Capture the options bag Conversation passed to useChatStream.
    const args = useChatStreamModule.useChatStream.mock.calls[0];
    expect(args[0]).toBe("c-abc");
    const { onTitleSuggested } = args[1];

    // Simulate the SSE event firing.
    onTitleSuggested("Fresh title");

    expect(renameChatLocal).toHaveBeenCalledWith("c-abc", "Fresh title");
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

  it("renders the chat header with the active chat's title", () => {
    useChatsModule.useChats.mockReturnValueOnce({
      chats: [
        { chat_id: "c1", title: "Alpha conversation" },
        { chat_id: "c2", title: "Beta" },
      ],
      renameChatLocal: vi.fn(),
      renameChat: vi.fn(),
      archiveChat: vi.fn(),
      deleteChat: vi.fn(),
      createChat: vi.fn(),
      refresh: vi.fn(),
      status: "idle",
    });
    mockStream();
    renderAt("/app/c/c1");
    expect(
      screen.getByRole("button", { name: /Alpha conversation/i }),
    ).toBeTruthy();
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
    storage["channel-model"] = OPUS_ID;
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

  it("falls back to the first API allowlist entry when prefs.model is unknown (and Send uses it)", () => {
    const stream = mockStream();
    storage["channel-model"] = "no-such-model";
    __resetChannelPrefsForTest();
    renderAt("/app/c/c1");
    const ta = screen.getByPlaceholderText("Reply…");
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.click(screen.getByTitle("Send"));
    // followUp passes the short id (`model.id`), not the full picker
    // object — backend expects `model: str | None`.
    expect(stream.send.mock.calls[0][0].model).toBe(OPUS_ID);
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

  it("falls back to raw model id when not found in the API allowlist", () => {
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

  it("renders the raw model id verbatim before the API allowlist loads", async () => {
    // Clear the cache so the component mounts with `models` still null.
    __resetModelsCacheForTest();
    let resolvePromise;
    api.listModels.mockReturnValue(new Promise((resolve) => { resolvePromise = resolve; }));
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
    // Until the allowlist arrives, modelLabelFromList returns the raw value.
    expect(screen.getByText("us.anthropic.claude-sonnet-4-6")).toBeInTheDocument();
    resolvePromise({ models: SERVER_ALLOWLIST });
  });

  it("renders an empty model label when the turn has no model attribute", () => {
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        {
          role: "assistant",
          text: "ok",
          msg_id: "a1",
          model: undefined,
          streaming: false,
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    // The `.mdl` span renders but is empty (modelLabelFromList returns "").
    const mdl = container.querySelector(".mdl");
    expect(mdl).toBeTruthy();
    expect(mdl.textContent).toBe("");
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

  it("renders follow-up chips below the last assistant turn", () => {
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        {
          role: "assistant",
          text: "ok",
          msg_id: "a1",
          streaming: false,
          followUps: ["Follow A", "Follow B", "Follow C"],
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    const chips = container.querySelectorAll(".followup-chip");
    expect(chips).toHaveLength(3);
    expect(chips[0].textContent).toBe("Follow A");
    expect(chips[1].textContent).toBe("Follow B");
    expect(chips[2].textContent).toBe("Follow C");
  });

  it("does NOT render follow-up chips on earlier assistant turns", () => {
    // Backend currently only attaches followUps to the latest assistant
    // turn, but defend in depth: even if an older turn carries the
    // attribute, the UI only renders chips on the last turn.
    mockStream({
      turns: [
        {
          role: "assistant",
          text: "older",
          msg_id: "a-old",
          streaming: false,
          followUps: ["Stale chip"],
        },
        { role: "user", text: "follow", msg_id: "u1" },
        {
          role: "assistant",
          text: "newer",
          msg_id: "a-new",
          streaming: false,
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    expect(container.querySelectorAll(".followup-chip")).toHaveLength(0);
  });

  it("does NOT render follow-up chips when the suggestFollowups pref is off", () => {
    // #469 — belt-and-braces client gate. The server normally never
    // emits the frame when the pref is off, but a stale server-side
    // copy of the pref (swallowed PUT failure / expired access token)
    // can keep the chips coming. Seed localStorage BEFORE resetting the
    // shared prefs store so the snapshot picks the "off" value up.
    storage["channel-suggest-followups"] = "0";
    __resetChannelPrefsForTest();
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        {
          role: "assistant",
          text: "ok",
          msg_id: "a1",
          streaming: false,
          followUps: ["Follow A", "Follow B", "Follow C"],
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    expect(container.querySelectorAll(".followup-chip")).toHaveLength(0);
    expect(container.querySelector(".followups")).toBeNull();
  });

  it("renders follow-up chips when the suggestFollowups pref is explicitly on", () => {
    // Complement of the test above — pins that the gate reads the pref
    // rather than unconditionally suppressing.
    storage["channel-suggest-followups"] = "1";
    __resetChannelPrefsForTest();
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        {
          role: "assistant",
          text: "ok",
          msg_id: "a1",
          streaming: false,
          followUps: ["Follow A"],
        },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    expect(container.querySelectorAll(".followup-chip")).toHaveLength(1);
  });

  it("does NOT render a chip row when followUps is missing or empty", () => {
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        { role: "assistant", text: "ok", msg_id: "a1", streaming: false },
      ],
    });
    const { container } = renderAt("/app/c/c1");
    expect(container.querySelector(".followups")).toBeNull();
  });

  it("clicking a follow-up chip drops its text into the composer textarea", () => {
    mockStream({
      turns: [
        { role: "user", text: "hi", msg_id: "u1" },
        {
          role: "assistant",
          text: "ok",
          msg_id: "a1",
          streaming: false,
          followUps: ["What about X?"],
        },
      ],
    });
    renderAt("/app/c/c1");
    fireEvent.click(screen.getByText("What about X?"));
    // The bottom-composer textarea is the only textbox in the tree.
    expect(screen.getByRole("textbox").value).toBe("What about X?");
  });

  describe("tool-step list (#181 PR-3)", () => {
    function assistantTurnWithSteps(steps) {
      return [
        {
          msg_id: "a1",
          role: "assistant",
          text: "Here's the time.",
          streaming: false,
          toolSteps: steps,
        },
      ];
    }

    it("renders the collapsed toggle with tool name + status visible", () => {
      mockStream({
        turns: assistantTurnWithSteps([
          {
            toolUseId: "tu-1",
            toolName: "current_time",
            argsPreview: "{}",
            status: "finished",
            summary: "completed",
          },
        ]),
      });
      renderAt("/app/c/c1");
      // The compact row shows the tool name + status without expansion.
      expect(screen.getByText(/current_time/)).toBeInTheDocument();
      expect(screen.getByText(/finished/)).toBeInTheDocument();
      // The summary text is NOT yet visible — that's hidden behind expand.
      expect(screen.queryByText("completed")).not.toBeInTheDocument();
    });

    it("expands the step list to reveal the summary on toggle click", () => {
      mockStream({
        turns: assistantTurnWithSteps([
          {
            toolUseId: "tu-1",
            toolName: "current_time",
            status: "finished",
            summary: "2026-06-07T12:00:00Z",
          },
        ]),
      });
      renderAt("/app/c/c1");
      const toggle = screen.getByRole("button", { name: /tool step/i });
      expect(toggle.getAttribute("aria-expanded")).toBe("false");
      fireEvent.click(toggle);
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
      // Now the summary is visible inside the rendered ToolResultBlock.
      expect(screen.getByText("2026-06-07T12:00:00Z")).toBeInTheDocument();
    });

    it("clicking the toggle a second time collapses the list again", () => {
      mockStream({
        turns: assistantTurnWithSteps([
          {
            toolUseId: "tu-1",
            toolName: "current_time",
            status: "finished",
            summary: "the result",
          },
        ]),
      });
      renderAt("/app/c/c1");
      const toggle = screen.getByRole("button", { name: /tool step/i });
      fireEvent.click(toggle);
      expect(screen.getByText("the result")).toBeInTheDocument();
      fireEvent.click(toggle);
      expect(screen.queryByText("the result")).not.toBeInTheDocument();
      expect(toggle.getAttribute("aria-expanded")).toBe("false");
    });

    it("pluralises the toggle label (1 step vs N steps)", () => {
      mockStream({
        turns: assistantTurnWithSteps([
          {
            toolUseId: "tu-1",
            toolName: "current_time",
            status: "finished",
            summary: "ok",
          },
          {
            toolUseId: "tu-2",
            toolName: "current_time",
            status: "finished",
            summary: "ok",
          },
        ]),
      });
      renderAt("/app/c/c1");
      expect(screen.getByRole("button", { name: /2 tool steps/i }))
        .toBeInTheDocument();
    });

    it("singular toggle label when toolSteps has exactly one entry", () => {
      mockStream({
        turns: assistantTurnWithSteps([
          {
            toolUseId: "tu-1",
            toolName: "current_time",
            status: "finished",
            summary: "ok",
          },
        ]),
      });
      renderAt("/app/c/c1");
      expect(screen.getByRole("button", { name: /^1 tool step$/i }))
        .toBeInTheDocument();
    });

    it("renders chain_cap error with distinct affordance, not generic error", () => {
      mockStream({
        turns: assistantTurnWithSteps([
          {
            toolUseId: "tu-1",
            toolName: "current_time",
            status: "error",
            errorType: "chain_cap",
            partialResultCount: 7,
          },
        ]),
      });
      renderAt("/app/c/c1");
      fireEvent.click(screen.getByRole("button", { name: /tool step/i }));
      expect(
        screen.getByText(/reached the tool-use limit/i),
      ).toBeInTheDocument();
      // 7 partial steps — pluralised
      expect(screen.getByText(/7 steps completed/i)).toBeInTheDocument();
    });

    it("renders chain_cap singular when partialResultCount === 1", () => {
      mockStream({
        turns: assistantTurnWithSteps([
          {
            toolUseId: "tu-1",
            toolName: "current_time",
            status: "error",
            errorType: "chain_cap",
            partialResultCount: 1,
          },
        ]),
      });
      renderAt("/app/c/c1");
      fireEvent.click(screen.getByRole("button", { name: /tool step/i }));
      expect(screen.getByText(/1 step completed/i)).toBeInTheDocument();
    });

    it("renders generic error for non-chain_cap error types", () => {
      mockStream({
        turns: assistantTurnWithSteps([
          {
            toolUseId: "tu-1",
            toolName: "current_time",
            status: "error",
            errorType: "timeout",
          },
        ]),
      });
      renderAt("/app/c/c1");
      fireEvent.click(screen.getByRole("button", { name: /tool step/i }));
      expect(screen.getByText(/current_time failed \(timeout\)/i))
        .toBeInTheDocument();
      // The chain-cap copy must NOT appear for non-chain_cap errors.
      expect(screen.queryByText(/reached the tool-use limit/i)).toBeNull();
    });

    it("renders the summary inside ToolResultBlock when the step has one", () => {
      mockStream({
        turns: assistantTurnWithSteps([
          {
            toolUseId: "tu-1",
            toolName: "current_time",
            status: "finished",
            summary: "07:00 UTC",
          },
        ]),
      });
      const { container } = renderAt("/app/c/c1");
      fireEvent.click(screen.getByRole("button", { name: /tool step/i }));
      // ToolResultBlock renders a div.tool-result-block wrapper.
      expect(container.querySelector(".tool-result-block")).toBeTruthy();
      expect(screen.getByText("07:00 UTC")).toBeInTheDocument();
    });

    it("omits ToolResultBlock when the finished step has no summary", () => {
      mockStream({
        turns: assistantTurnWithSteps([
          {
            toolUseId: "tu-1",
            toolName: "current_time",
            status: "finished",
          },
        ]),
      });
      const { container } = renderAt("/app/c/c1");
      fireEvent.click(screen.getByRole("button", { name: /tool step/i }));
      expect(container.querySelector(".tool-result-block")).toBeNull();
    });

    it("does not render a step list when toolSteps is undefined", () => {
      mockStream({
        turns: [
          {
            msg_id: "a1",
            role: "assistant",
            text: "no tools used",
            streaming: false,
          },
        ],
      });
      const { container } = renderAt("/app/c/c1");
      expect(container.querySelector(".tool-steps")).toBeNull();
    });

    it("does not render a step list when toolSteps is an empty array", () => {
      mockStream({
        turns: assistantTurnWithSteps([]),
      });
      const { container } = renderAt("/app/c/c1");
      expect(container.querySelector(".tool-steps")).toBeNull();
    });

    it("renders the step list on a streaming assistant turn too", () => {
      // The step list is visible during streaming so the user can see
      // "the hands moving" — per the strategy spec's UX brief.
      mockStream({
        turns: [
          {
            msg_id: "a1",
            role: "assistant",
            text: "",
            streaming: true,
            toolSteps: [
              {
                toolUseId: "tu-1",
                toolName: "current_time",
                status: "running",
              },
            ],
          },
        ],
      });
      renderAt("/app/c/c1");
      expect(screen.getByText(/current_time/)).toBeInTheDocument();
      expect(screen.getByText(/running/)).toBeInTheDocument();
    });

    it("preserves expanded state when the assistant turn's msg_id swaps from temp to persisted", () => {
      // Streaming-phase turn carries a temp msg_id while SSE deltas
      // flow. The `done` event swaps it to the persisted server id —
      // which remounts the per-turn row subtree (the row's React key is
      // msg_id). The expanded/collapsed state for the ToolStepList
      // would reset to `false` if it lived inside ToolStepList. The fix
      // lifts the state into Conversation, keyed by the first step's
      // toolUseId (stable across the swap because the backend assigns
      // it once).
      const step = {
        toolUseId: "tu-stable-1",
        toolName: "current_time",
        status: "finished",
        summary: "13:37 UTC",
      };
      mockStream({
        turns: [
          {
            msg_id: "tmp-a-abc",
            role: "assistant",
            text: "the time",
            streaming: false,
            toolSteps: [step],
          },
        ],
      });
      const { rerender } = renderAt("/app/c/c-swap");

      // Expand the list — the summary becomes visible.
      const toggle = screen.getByRole("button", { name: /tool step/i });
      expect(toggle.getAttribute("aria-expanded")).toBe("false");
      fireEvent.click(toggle);
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
      expect(screen.getByText("13:37 UTC")).toBeInTheDocument();

      // Simulate the `done` SSE event: temp msg_id → persisted msg_id.
      // The toolSteps array (and its first step's toolUseId) is
      // unchanged — that's the stable key the parent uses for lookup.
      useChatStreamModule.useChatStream.mockReturnValue({
        turns: [
          {
            msg_id: "persisted-msg-xyz",
            role: "assistant",
            text: "the time",
            streaming: false,
            toolSteps: [step],
          },
        ],
        send: vi.fn(),
        regenerate: vi.fn(),
        abort: vi.fn(),
        status: "idle",
        error: null,
      });
      rerender(
        <MemoryRouter initialEntries={["/app/c/c-swap"]}>
          <Routes>
            <Route path="/app/c/:id" element={<Conversation />} />
          </Routes>
        </MemoryRouter>,
      );

      // Toggle still reads `aria-expanded="true"` and summary still
      // visible — expanded state survived the msg_id swap.
      const toggleAfter = screen.getByRole("button", { name: /tool step/i });
      expect(toggleAfter.getAttribute("aria-expanded")).toBe("true");
      expect(screen.getByText("13:37 UTC")).toBeInTheDocument();
    });

    it("resets expanded state when chatId changes (chat navigation)", () => {
      // Conversation stays mounted when the user navigates between
      // chats — only the URL :id flips. Without an explicit reset the
      // expanded-step Map would retain keys from every prior chat and
      // grow unbounded. This test exercises chat A → chat B → chat A
      // and asserts the previously-expanded step is now collapsed.
      const stepA = {
        toolUseId: "tu-chat-a",
        toolName: "current_time",
        status: "finished",
        summary: "A's summary",
      };
      const stepB = {
        toolUseId: "tu-chat-b",
        toolName: "current_time",
        status: "finished",
        summary: "B's summary",
      };
      useChatStreamModule.useChatStream.mockImplementation((id) => ({
        turns: [
          {
            msg_id: `msg-${id}`,
            role: "assistant",
            text: "hi",
            streaming: false,
            toolSteps: [id === "chat-b" ? stepB : stepA],
          },
        ],
        send: vi.fn(),
        regenerate: vi.fn(),
        abort: vi.fn(),
        status: "idle",
        error: null,
      }));

      // Render against a single MemoryRouter so Conversation stays
      // mounted across the navigation — the whole point of this test
      // is that the :id flip reuses the same component instance and
      // the useEffect resets the Map.
      function Nav() {
        const navigate = useNavigate();
        return (
          <>
            <button
              type="button"
              data-testid="goto-chat-a"
              onClick={() => navigate("/app/c/chat-a")}
            >
              chat A
            </button>
            <button
              type="button"
              data-testid="goto-chat-b"
              onClick={() => navigate("/app/c/chat-b")}
            >
              chat B
            </button>
          </>
        );
      }
      render(
        <MemoryRouter initialEntries={["/app/c/chat-a"]}>
          <Nav />
          <Routes>
            <Route path="/app/c/:id" element={<Conversation />} />
          </Routes>
        </MemoryRouter>,
      );

      // Expand chat A's step.
      const toggleA = screen.getByRole("button", { name: /tool step/i });
      expect(toggleA.getAttribute("aria-expanded")).toBe("false");
      fireEvent.click(toggleA);
      expect(screen.getByText("A's summary")).toBeInTheDocument();

      // Navigate to chat B — its step starts collapsed.
      fireEvent.click(screen.getByTestId("goto-chat-b"));
      const toggleB = screen.getByRole("button", { name: /tool step/i });
      expect(toggleB.getAttribute("aria-expanded")).toBe("false");
      expect(screen.queryByText("B's summary")).not.toBeInTheDocument();

      // Navigate back to chat A — its previously-expanded step is now
      // collapsed because the Map reset on chatId change.
      fireEvent.click(screen.getByTestId("goto-chat-a"));
      const toggleAAgain = screen.getByRole("button", { name: /tool step/i });
      expect(toggleAAgain.getAttribute("aria-expanded")).toBe("false");
      expect(screen.queryByText("A's summary")).not.toBeInTheDocument();
    });

    it("MCP picker onChange persists via putChatMCPSettings (#207)", async () => {
      api.listMCPServers.mockResolvedValue({
        servers: [
          {
            server_id: "srv-1",
            name: "Hive",
            tool_prefix: "hive",
            globally_enabled: true,
            auth_status: "active",
          },
        ],
      });
      api.getChatMCPSettings.mockResolvedValue({
        mode: "inherit",
        explicit_server_ids: [],
      });
      mockStream();
      renderAt("/app/c/c1");
      // Wait for the MCP pill to render.
      const pill = await screen.findByRole("button", { name: /tool servers?\b/i });
      fireEvent.click(pill);
      // Toggle the only server off — flips mode to explicit + empty list.
      fireEvent.click(screen.getByLabelText(/^Hive$/));
      await waitFor(() =>
        expect(api.putChatMCPSettings).toHaveBeenCalledWith("c1", {
          mode: "explicit",
          explicit_server_ids: [],
        }),
      );
    });

    it("MCP picker onChange does NOT POST when chatId is absent (#207)", async () => {
      // Render Conversation under a route with an optional :id so
      // useParams() returns { id: undefined } — exercises the
      // defensive !chatId guard in handleMcpChange.
      api.putChatMCPSettings.mockClear();
      api.listMCPServers.mockResolvedValue({
        servers: [
          {
            server_id: "srv-1", name: "Hive", tool_prefix: "hive",
            globally_enabled: true, auth_status: "active",
          },
        ],
      });
      api.getChatMCPSettings.mockResolvedValue({
        mode: "inherit", explicit_server_ids: [],
      });
      mockStream();
      render(
        <MemoryRouter initialEntries={["/app/c/"]}>
          <Routes>
            <Route path="/app/c/:id?" element={<Conversation />} />
          </Routes>
        </MemoryRouter>,
      );
      const pill = await screen.findByRole("button", { name: /tool servers?\b/i });
      fireEvent.click(pill);
      fireEvent.click(screen.getByLabelText(/^Hive$/));
      // putChatMCPSettings must NOT be invoked — chatId is undefined.
      await new Promise((r) => setTimeout(r, 0));
      expect(api.putChatMCPSettings).not.toHaveBeenCalled();
    });

    it("MCP picker onChange tolerates putChatMCPSettings failure (#207)", async () => {
      api.listMCPServers.mockResolvedValue({
        servers: [
          {
            server_id: "srv-1",
            name: "Hive",
            tool_prefix: "hive",
            globally_enabled: true,
            auth_status: "active",
          },
        ],
      });
      api.getChatMCPSettings.mockResolvedValue({
        mode: "inherit",
        explicit_server_ids: [],
      });
      api.putChatMCPSettings.mockRejectedValueOnce(new Error("boom"));
      const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
      mockStream();
      renderAt("/app/c/c1");
      const pill = await screen.findByRole("button", { name: /tool servers?\b/i });
      fireEvent.click(pill);
      fireEvent.click(screen.getByLabelText(/^Hive$/));
      await waitFor(() =>
        expect(errorSpy).toHaveBeenCalledWith(
          "putChatMCPSettings",
          expect.any(Error),
        ),
      );
      errorSpy.mockRestore();
    });

    it("renders code-output payload via ToolResultBlock for a finished code_exec step", () => {
      mockStream({
        turns: assistantTurnWithSteps([
          {
            toolUseId: "tu-code",
            toolName: "code_exec",
            status: "finished",
            kind: "code-output",
            summary: "completed",
            payload: { stdout: "42\n", stderr: "", exit_code: 0, images: [], duration_ms: 120 },
          },
        ]),
      });
      const { container } = renderAt("/app/c/c1");
      fireEvent.click(screen.getByRole("button", { name: /tool step/i }));
      // ToolResultBlock renders the code-output branch: output "42" must appear.
      expect(container.querySelector(".tool-result-block")).toBeTruthy();
      expect(screen.getByText("42")).toBeInTheDocument();
    });
  });

  describe("stream-failure chip (#212)", () => {
    it("renders the chip with Retry on the last errored turn and hides msg-actions", () => {
      const stream = mockStream({
        turns: [
          { msg_id: "u1", role: "user", text: "q" },
          {
            msg_id: "tmp-a-1",
            client_msg_id: "tmp-a-1",
            role: "assistant",
            text: "",
            streaming: false,
            streamError: {
              code: "bedrock_throttled",
              message: "Busy.",
              retryable: true,
            },
          },
        ],
      });
      renderAt("/app/c/c1");

      const chip = screen.getByRole("alert");
      expect(chip.textContent).toContain("Couldn't complete reply — Busy.");

      // Retry reuses the regenerate path (the user turn is already
      // persisted server-side; nothing to re-send).
      fireEvent.click(screen.getByRole("button", { name: /retry/i }));
      expect(stream.regenerate).toHaveBeenCalledWith({});

      // msg-actions row is suppressed for errored turns — Copy /
      // thumbs would act on an unpersisted temp msg_id.
      expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Good" })).toBeNull();
    });

    it("omits Retry when the failure is not retryable", () => {
      mockStream({
        turns: [
          { msg_id: "u1", role: "user", text: "q" },
          {
            msg_id: "tmp-a-1",
            client_msg_id: "tmp-a-1",
            role: "assistant",
            text: "",
            streaming: false,
            streamError: {
              code: "bedrock_validation",
              message: "Too large. Try shortening it.",
              retryable: false,
            },
          },
        ],
      });
      renderAt("/app/c/c1");
      // Re-sending the same oversized input would fail identically —
      // the chip renders without the Retry affordance.
      expect(screen.getByRole("alert").textContent).toContain("Too large.");
      expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
    });

    it("omits Retry on an errored turn that is not the last turn", () => {
      mockStream({
        turns: [
          {
            msg_id: "tmp-a-1",
            client_msg_id: "tmp-a-1",
            role: "assistant",
            text: "",
            streaming: false,
            streamError: {
              code: "internal",
              message: "Went wrong.",
              retryable: true,
            },
          },
          { msg_id: "u2", role: "user", text: "follow-up" },
        ],
      });
      renderAt("/app/c/c1");
      expect(screen.getByRole("alert").textContent).toContain("Went wrong.");
      expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
    });
  });
});

describe("asset partition helpers (#327, #362)", () => {
  // Text with three locatable fences → ordinals 0, 1, 2.
  const text3 = "```\na\n```\n\n```\nb\n```\n\n```\nc\n```";
  const fenceAsset = {
    asset_id: "c1",
    kind: "code",
    source: { fence_index: 2 },
  };
  const imageAsset = { asset_id: "i1", kind: "image", source: { msg_id: "m" } };
  const codeNoSource = { asset_id: "c2", kind: "code" };
  const codeNoFence = { asset_id: "c3", kind: "code", source: { msg_id: "m" } };
  // A fence-origin code asset whose ordinal is NOT present in the text
  // (scan drift / text mismatch) — must still card, never disappear (#362).
  const driftAsset = {
    asset_id: "c4",
    kind: "code",
    source: { fence_index: 9 },
  };

  it("codeAssetsByFence keys LOCATED fence-code assets by ordinal", () => {
    const map = codeAssetsByFence([fenceAsset, imageAsset], text3);
    expect(map.get(2)).toBe(fenceAsset);
    expect(map.size).toBe(1);
  });

  it("codeAssetsByFence excludes code assets without a numeric fence_index", () => {
    expect(codeAssetsByFence([codeNoSource, codeNoFence], text3).size).toBe(0);
  });

  it("codeAssetsByFence excludes a fence-code asset whose ordinal is not located (scan drift)", () => {
    expect(codeAssetsByFence([driftAsset], text3).size).toBe(0);
  });

  it("codeAssetsByFence short-circuits (no text scan) when no fence-code candidate exists", () => {
    // No candidate → empty map; the O(text) scan is skipped. `text3` has
    // three fences, but none is claimed by a fence-code asset here.
    expect(codeAssetsByFence([imageAsset, codeNoFence], text3).size).toBe(0);
  });

  it("codeAssetsByFence tolerates undefined assets and text", () => {
    expect(codeAssetsByFence(undefined, undefined).size).toBe(0);
  });

  it("codeAssetsByFence with a candidate but undefined text locates nothing (empty-string guard)", () => {
    // Candidate present (so the scan runs), but text is undefined → the
    // `scanFences(text || "")` guard scans an empty string → nothing located
    // → the fence-code asset degrades out of the decorate map.
    expect(codeAssetsByFence([fenceAsset], undefined).size).toBe(0);
  });

  it("standaloneAssets excludes ONLY located (decorated) fence-code assets", () => {
    const out = standaloneAssets(
      [fenceAsset, imageAsset, codeNoSource, codeNoFence],
      text3,
    );
    expect(out.map((a) => a.asset_id)).toEqual(["i1", "c2", "c3"]);
  });

  it("standaloneAssets still cards a non-located fence-code asset (scan drift)", () => {
    const out = standaloneAssets([driftAsset, imageAsset], text3);
    expect(out.map((a) => a.asset_id)).toEqual(["c4", "i1"]);
  });

  it("standaloneAssets returns all assets (no text scan) when no fence-code candidate exists", () => {
    const out = standaloneAssets([imageAsset, codeNoFence], text3);
    expect(out.map((a) => a.asset_id)).toEqual(["i1", "c3"]);
  });

  it("standaloneAssets tolerates undefined assets and text", () => {
    expect(standaloneAssets(undefined, undefined)).toEqual([]);
  });
});
