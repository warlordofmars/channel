// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { __resetChannelPrefsForTest } from "../hooks/useChannelPrefs.js";
import { TOKEN_KEY } from "../lib/auth.js";

const mockCreateChat = vi.fn();
const mockUseChats = vi.fn();

vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => mockUseChats(),
}));

// Import Shell AFTER the mock is set up so the import sees the mock.
const { default: Shell } = await import("./Shell.jsx");

function makeToken() {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email: "a@b.com" }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("Shell", () => {
  let storage;
  beforeEach(() => {
    storage = { [TOKEN_KEY]: makeToken() };
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    document.documentElement.removeAttribute("data-theme");
    __resetChannelPrefsForTest();
    mockCreateChat.mockReset();
    mockUseChats.mockReset();
    mockUseChats.mockReturnValue({
      chats: [],
      createChat: mockCreateChat,
    });
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

  it("uses the design's .stage.full > .win > .body > Sidebar + .main shape", () => {
    // app.css makes `.win` `flex-direction: column` and `.body` the row that
    // holds the sidebar + main. Skipping `.body` makes the column direction
    // stack Sidebar above main (or breaks `.main`'s `flex: 1` vertical math
    // and the greeting renders at the bottom of the viewport instead of
    // centered). The `.body` wrapper is required.
    const { container } = render(
      <MemoryRouter>
        <Shell>
          <div data-testid="child" />
        </Shell>
      </MemoryRouter>
    );
    const stage = container.querySelector(".stage.full");
    expect(stage).toBeTruthy();
    const win = stage.querySelector(":scope > .win");
    expect(win).toBeTruthy();
    const body = win.querySelector(":scope > .body");
    expect(body).toBeTruthy();
    const main = body.querySelector(":scope > main.main");
    expect(main).toBeTruthy();
    expect(main.querySelector("[data-testid='child']")).toBeTruthy();
  });

  it("applies the chat-app theme (theme) to data-theme on mount", async () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    render(
      <MemoryRouter>
        <Shell><div /></Shell>
      </MemoryRouter>
    );
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("does NOT render the .main-top expand bar when the sidebar is open (default)", () => {
    const { container } = render(
      <MemoryRouter><Shell><div /></Shell></MemoryRouter>
    );
    expect(container.querySelector(".main-top")).toBeNull();
  });

  it("clicking the sidebar toggle hides the sidebar and shows the .main-top expand bar", () => {
    const { container } = render(
      <MemoryRouter><Shell><div /></Shell></MemoryRouter>
    );
    fireEvent.click(screen.getByTitle("Toggle sidebar"));
    expect(container.querySelector(".sb.collapsed")).toBeTruthy();
    expect(container.querySelector(".main-top")).toBeTruthy();
    // Clicking the .main-top expand button toggles back open
    fireEvent.click(screen.getByTitle("Show sidebar"));
    expect(container.querySelector(".sb.collapsed")).toBeNull();
    expect(container.querySelector(".main-top")).toBeNull();
  });

  it("renders chats from useChatList into the Sidebar Recents list", () => {
    mockUseChats.mockReturnValue({
      chats: [
        {
          chat_id: "abc",
          title: "Hello from the hook",
          last_message_at: new Date().toISOString(),
          archived: false,
        },
      ],
      createChat: mockCreateChat,
    });
    render(
      <MemoryRouter><Shell><div /></Shell></MemoryRouter>
    );
    expect(screen.getByText("Hello from the hook")).toBeTruthy();
  });

  it("clicking 'New chat' navigates to /app (the welcome screen) without creating a chat upfront", async () => {
    // Mirrors Claude Desktop: the chat record is created lazily by
    // ChatHome when the user sends their first message. The sidebar
    // button is just navigation, no API call.
    let lastPath = null;
    function PathCatcher() {
      const { pathname } = useLocation();
      lastPath = pathname;
      return null;
    }
    render(
      <MemoryRouter initialEntries={["/app/c/existing"]}>
        <Routes>
          <Route
            path="*"
            element={
              <Shell>
                <PathCatcher />
              </Shell>
            }
          />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));
    await waitFor(() => expect(lastPath).toBe("/app"));
    expect(mockCreateChat).not.toHaveBeenCalled();
  });

  it("mobile drawer starts closed (no mobile-open class, no backdrop) (#425)", () => {
    const { container } = render(
      <MemoryRouter><Shell><div /></Shell></MemoryRouter>
    );
    expect(container.querySelector(".sb.mobile-open")).toBeNull();
    expect(container.querySelector(".mobile-nav-backdrop")).toBeNull();
  });

  it("tapping the hamburger opens the off-canvas drawer + backdrop (#425)", () => {
    const { container } = render(
      <MemoryRouter><Shell><div /></Shell></MemoryRouter>
    );
    fireEvent.click(screen.getByRole("button", { name: /open menu/i }));
    expect(container.querySelector(".sb.mobile-open")).toBeTruthy();
    expect(container.querySelector(".mobile-nav-backdrop")).toBeTruthy();
  });

  it("tapping the backdrop closes the mobile drawer (#425)", () => {
    const { container } = render(
      <MemoryRouter><Shell><div /></Shell></MemoryRouter>
    );
    fireEvent.click(screen.getByRole("button", { name: /open menu/i }));
    expect(container.querySelector(".sb.mobile-open")).toBeTruthy();
    // The backdrop is a real <button aria-label="Close menu"> so keyboard
    // + assistive-tech users get a focusable dismiss path (Copilot #427).
    fireEvent.click(screen.getByRole("button", { name: /close menu/i }));
    expect(container.querySelector(".sb.mobile-open")).toBeNull();
    expect(container.querySelector(".mobile-nav-backdrop")).toBeNull();
  });

  it("navigating via a sidebar nav item closes an open mobile drawer (#425)", async () => {
    // Route change is the primary close path: tapping New chat / Projects
    // / a recent navigates, which slides the drawer shut so the user lands
    // on the destination with the drawer closed. The close is driven by a
    // `useLocation().pathname` effect, so assert via `waitFor` to let the
    // navigation → effect flush settle (rather than assuming a single
    // synchronous tick).
    const { container } = render(
      <MemoryRouter><Shell><div /></Shell></MemoryRouter>
    );
    fireEvent.click(screen.getByRole("button", { name: /open menu/i }));
    expect(container.querySelector(".sb.mobile-open")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^projects/i }));
    await waitFor(() =>
      expect(container.querySelector(".sb.mobile-open")).toBeNull()
    );
  });
});
