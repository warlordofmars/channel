// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { TOKEN_KEY } from "../lib/auth.js";
import { QUICK_ACTIONS } from "./data.js";
import { __resetChannelPrefsForTest } from "../hooks/useChannelPrefs.js";

const mockCreateChat = vi.fn();
const mockUseChats = vi.fn();

vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => mockUseChats(),
}));

// Import ChatHome AFTER the mock is set up so the import sees the mock.
const { default: ChatHome } = await import("./ChatHome.jsx");

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
  let lastState = null;
  function PathCatcher() {
    const loc = useLocation();
    lastPath = loc.pathname;
    lastState = loc.state;
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
  return {
    ...result,
    getLastPath: () => lastPath,
    getLastState: () => lastState,
  };
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
    mockCreateChat.mockReset();
    mockCreateChat.mockResolvedValue({ chat_id: "new-1" });
    mockUseChats.mockReset();
    mockUseChats.mockReturnValue({ createChat: mockCreateChat });
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

  it("submitting a typed message calls createChat and navigates to /app/c/{id} with firstMessage state", async () => {
    const { getLastPath, getLastState } = renderChatHomeWithLocationCatcher();
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "hi there" } });
    fireEvent.click(screen.getByTitle("Send"));

    await waitFor(() => expect(mockCreateChat).toHaveBeenCalledTimes(1));
    expect(mockCreateChat).toHaveBeenCalledWith({ modelDefault: "claude-opus-4-6" });
    await waitFor(() => expect(getLastPath()).toBe("/app/c/new-1"));
    expect(getLastState()).toEqual({
      firstMessage: {
        message: "hi there",
        // model is the short id string (not the picker object) — the
        // backend's SendMessageRequest.model is `str | None`.
        model: "claude-opus-4-6",
        effort: "High",
        attachments: [],
      },
    });
  });

  it("clicking a quick-action creates a chat and navigates with a prefilled firstMessage", async () => {
    const { getLastPath, getLastState } = renderChatHomeWithLocationCatcher();
    fireEvent.click(screen.getByRole("button", { name: "Write" }));

    await waitFor(() => expect(mockCreateChat).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getLastPath()).toBe("/app/c/new-1"));
    expect(getLastState().firstMessage.message).toMatch(/help me write something/i);
    expect(getLastState().firstMessage.attachments).toEqual([]);
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
    // ModelPicker shows the model.short of the fallback (Opus 4.6):
    expect(screen.getByText(/opus 4.6/i)).toBeTruthy();
  });

  it("falls back to 'You' when JWT email local-part is empty", () => {
    storage = { [TOKEN_KEY]: makeToken({ email: "@nodomain.com" }) };
    renderChatHome();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it, you/i);
  });

  it("setModelObj writes the model id back to prefs when a model is selected", () => {
    renderChatHome();
    // Open the ModelPicker popover via the model-pick button in the Composer
    fireEvent.click(screen.getByRole("button", { name: /sonnet 4\.6|opus 4\.6|haiku 4\.5/i }));
    // Click the Haiku option to trigger setModelObj
    fireEvent.click(screen.getByText("Claude Haiku 4.5"));
    // The storage key should be updated
    expect(storage["channel-model"]).toBe("claude-haiku-4-5");
  });
});
