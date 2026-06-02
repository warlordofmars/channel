// Copyright (c) 2026 John Carter. All rights reserved.
import { act } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import Sidebar, { groupNameFor } from "./Sidebar.jsx";
import { TOKEN_KEY } from "../lib/auth.js";

// Module-scoped mocks so individual tests can assert on the archive /
// rename / delete callbacks. vi.fn() identity stays stable across
// renders within a test; call history is cleared in beforeEach
// (vi.restoreAllMocks does NOT reset history on plain vi.fn() instances).
const mockChatsCtx = {
  renameChat: vi.fn().mockResolvedValue(undefined),
  archiveChat: vi.fn().mockResolvedValue(undefined),
  deleteChat: vi.fn().mockResolvedValue(undefined),
  renameChatLocal: vi.fn(),
};
vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => mockChatsCtx,
}));

function makeToken({ email = "ada@example.com", display_name } = {}) {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const claims = { exp, sub: "u1", role: "user", email };
  if (display_name !== undefined) claims.display_name = display_name;
  const payload = btoa(JSON.stringify(claims));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

// Fixed reference time used by the test fixtures. The Sidebar reads
// `Date.now()` inside the grouping memo; tests pin it to this value so
// time-bucket assertions are deterministic.
const NOW = new Date("2026-05-30T12:00:00Z").getTime();
const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

function isoAgo(ms) {
  return new Date(NOW - ms).toISOString();
}

function makeChats() {
  return [
    {
      chat_id: "c1",
      title: "Home server backup strategy",
      last_message_at: isoAgo(2 * HOUR),
      archived: false,
    },
    {
      chat_id: "c1b",
      title: "Another chat from today",
      last_message_at: isoAgo(5 * HOUR),
      archived: false,
    },
    {
      chat_id: "c2",
      title: "Postgres index not being used",
      last_message_at: isoAgo(30 * HOUR),
      archived: false,
    },
    {
      chat_id: "c3",
      title: "Reading list for systems design",
      last_message_at: isoAgo(3 * DAY),
      archived: false,
    },
    {
      chat_id: "c4",
      title: "Tax documents checklist",
      last_message_at: isoAgo(20 * DAY),
      archived: false,
    },
    // Note: archived chats are filtered out by the API server-side
    // (issue #143); the Sidebar trusts the list it receives and does
    // not double-filter. The dedicated server-trust test below
    // exercises the contract.
  ];
}

describe("Sidebar", () => {
  let storage;
  let assignSpy;

  beforeEach(() => {
    storage = { [TOKEN_KEY]: makeToken() };
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    assignSpy = vi.fn();
    vi.stubGlobal("location", { ...globalThis.location, assign: assignSpy });
    vi.spyOn(Date, "now").mockReturnValue(NOW);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function renderSidebar(props = {}) {
    return render(
      <MemoryRouter>
        <Sidebar chats={makeChats()} {...props} />
      </MemoryRouter>
    );
  }

  it("renders four primary nav items (New chat, Projects, Artifacts, Customize)", () => {
    renderSidebar();
    expect(screen.getByRole("button", { name: /new chat/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^projects/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^artifacts/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^customize/i })).toBeTruthy();
  });

  it("renders time-grouped recents (Today, Yesterday, …) from the chats prop", () => {
    renderSidebar();
    expect(screen.getByText("Today")).toBeTruthy();
    expect(screen.getByText("Yesterday")).toBeTruthy();
    expect(screen.getByText("Previous 7 days")).toBeTruthy();
    expect(screen.getByText("Older")).toBeTruthy();
    // And a chat from each bucket renders
    expect(screen.getByText("Home server backup strategy")).toBeTruthy();
    expect(screen.getByText("Postgres index not being used")).toBeTruthy();
    expect(screen.getByText("Reading list for systems design")).toBeTruthy();
    expect(screen.getByText("Tax documents checklist")).toBeTruthy();
  });

  it("trusts the API to omit archived chats (server-side filter, issue #143)", () => {
    // If an archived row does slip into the prop (e.g. stale cache),
    // the Sidebar renders it — the server is the source of truth and
    // the SPA no longer double-filters.
    render(
      <MemoryRouter>
        <Sidebar
          chats={[
            {
              chat_id: "stale",
              title: "Archived secret chat",
              last_message_at: isoAgo(1 * HOUR),
              archived: true,
            },
          ]}
        />
      </MemoryRouter>
    );
    expect(screen.getByText("Archived secret chat")).toBeTruthy();
  });

  it("renders safely with an empty chats array (no recents, no error)", () => {
    render(
      <MemoryRouter><Sidebar chats={[]} /></MemoryRouter>
    );
    expect(screen.queryByText("Today")).toBeNull();
    expect(screen.queryByText("Yesterday")).toBeNull();
  });

  it("renders safely with no chats prop at all (default = [])", () => {
    render(<MemoryRouter><Sidebar /></MemoryRouter>);
    expect(screen.queryByText("Today")).toBeNull();
    // Primary nav is still present
    expect(screen.getByRole("button", { name: /new chat/i })).toBeTruthy();
  });

  it("renders chats with a missing title without throwing", () => {
    render(
      <MemoryRouter>
        <Sidebar
          chats={[
            { chat_id: "x", last_message_at: isoAgo(HOUR), archived: false },
          ]}
        />
      </MemoryRouter>
    );
    expect(screen.getByText("Today")).toBeTruthy();
  });

  it("clicking 'New chat' triggers the onNewChat callback (not navigate)", () => {
    const onNewChat = vi.fn();
    render(
      <MemoryRouter>
        <Sidebar chats={makeChats()} onNewChat={onNewChat} />
      </MemoryRouter>
    );
    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("clicking 'New chat' is safe when no onNewChat prop is passed (default noop)", () => {
    renderSidebar();
    expect(() =>
      fireEvent.click(screen.getByRole("button", { name: /new chat/i }))
    ).not.toThrow();
  });

  it("renders the account row with the email", () => {
    renderSidebar();
    expect(screen.getByText("ada@example.com")).toBeTruthy();
  });

  it("clicking the account row opens the popover", () => {
    renderSidebar();
    const account = screen.getByText("ada@example.com").closest("button");
    fireEvent.click(account);
    expect(screen.getByText("Sign out")).toBeTruthy();
  });

  it("Sign out clears the mgmt token and assigns location to /", () => {
    renderSidebar();
    fireEvent.click(screen.getByText("ada@example.com").closest("button"));
    fireEvent.click(screen.getByText("Sign out"));
    expect(storage[TOKEN_KEY]).toBeUndefined();
    expect(assignSpy).toHaveBeenCalledWith("/");
  });

  it("clicking the search button reveals the search input", () => {
    renderSidebar();
    fireEvent.click(screen.getByTitle("Search"));
    expect(screen.getByPlaceholderText("Search chats")).toBeTruthy();
  });

  it("typing in the search input filters recents to matching titles", () => {
    renderSidebar();
    fireEvent.click(screen.getByTitle("Search"));
    fireEvent.change(screen.getByPlaceholderText("Search chats"), { target: { value: "Postgres" } });
    expect(screen.getByText(/Postgres index not being used/i)).toBeTruthy();
    expect(screen.queryByText(/Home server backup strategy/i)).toBeNull();
  });

  it("does NOT render the 'Channel Max' plan label (deferred)", () => {
    renderSidebar();
    expect(screen.queryByText(/channel max/i)).toBeNull();
  });

  it("shows 'no chats match' fallback when search matches nothing", () => {
    renderSidebar();
    fireEvent.click(screen.getByTitle("Search"));
    fireEvent.change(screen.getByPlaceholderText("Search chats"), { target: { value: "zzzz-no-such-thing" } });
    expect(screen.getByText(/no chats match/i)).toBeTruthy();
  });

  it("does NOT show 'no chats match' fallback when search is empty and chats are empty", () => {
    render(<MemoryRouter><Sidebar chats={[]} /></MemoryRouter>);
    expect(screen.queryByText(/no chats match/i)).toBeNull();
  });

  it("falls back to default name 'You' when token is absent", () => {
    storage = {};
    renderSidebar();
    expect(screen.getByText("you@example.com")).toBeTruthy();
  });

  it("falls back to 'You' display name when email has no local part", () => {
    storage = { [TOKEN_KEY]: makeToken({ email: "@nodomain" }) };
    renderSidebar();
    // userName will be "" (empty before @) → falls back to "You"
    expect(screen.getByText("@nodomain")).toBeTruthy();
  });

  it("clicking the backdrop closes the account popover", () => {
    renderSidebar();
    // open popover
    fireEvent.click(screen.getByText("ada@example.com").closest("button"));
    expect(screen.getByText("Sign out")).toBeTruthy();
    // click backdrop to close
    const backdrop = document.querySelector(".backdrop");
    fireEvent.click(backdrop);
    expect(screen.queryByText("Sign out")).toBeNull();
  });

  it("shows the full display_name (Google name) when present in the JWT", () => {
    storage = { [TOKEN_KEY]: makeToken({ email: "j@example.com", display_name: "John Carter" }) };
    renderSidebar();
    expect(screen.getByText("John Carter")).toBeTruthy();
    // initials should derive from the two-word name: J + C → "JC"
    expect(screen.getByText("JC")).toBeTruthy();
  });

  it("clicking 'Toggle sidebar' calls the onToggle prop", () => {
    const onToggle = vi.fn();
    render(
      <MemoryRouter><Sidebar collapsed={false} onToggle={onToggle} chats={[]} /></MemoryRouter>
    );
    fireEvent.click(screen.getByTitle("Toggle sidebar"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("applies .collapsed class to .sb when the collapsed prop is true", () => {
    const { container } = render(
      <MemoryRouter><Sidebar collapsed={true} onToggle={() => {}} chats={[]} /></MemoryRouter>
    );
    expect(container.querySelector(".sb.collapsed")).toBeTruthy();
  });

  it("omits .collapsed class when the collapsed prop is false (or default)", () => {
    const { container } = render(<MemoryRouter><Sidebar chats={[]} /></MemoryRouter>);
    expect(container.querySelector(".sb.collapsed")).toBeNull();
    expect(container.querySelector(".sb")).toBeTruthy();
  });

  it("clicking the toggle button is safe when no onToggle prop is passed (default noop)", () => {
    render(<MemoryRouter><Sidebar chats={[]} /></MemoryRouter>);
    expect(() => fireEvent.click(screen.getByTitle("Toggle sidebar"))).not.toThrow();
  });

  it("clicking a recent navigates to /app/c/<that-chat_id>", () => {
    let lastPath = null;
    function PathCatcher() {
      const { pathname } = useLocation();
      lastPath = pathname;
      return null;
    }
    render(
      <MemoryRouter initialEntries={["/app"]}>
        <Routes>
          <Route path="/app" element={<><Sidebar chats={makeChats()} /><PathCatcher /></>} />
          <Route path="/app/c/:id" element={<PathCatcher />} />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(screen.getByText("Home server backup strategy"));
    expect(lastPath).toBe("/app/c/c1");
  });

  it.each([
    ["Projects", "/app/projects"],
    ["Artifacts", "/app/artifacts"],
    ["Customize", "/app/customize"],
  ])("clicking %s navigates to %s", (label, expected) => {
    let lastPath = null;
    function PathCatcher() {
      const { pathname } = useLocation();
      lastPath = pathname;
      return null;
    }
    render(
      <MemoryRouter initialEntries={["/app/c/r1"]}>
        <Routes>
          <Route path="*" element={<><Sidebar chats={makeChats()} /><PathCatcher /></>} />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(screen.getByRole("button", { name: new RegExp(label, "i") }));
    expect(lastPath).toBe(expected);
  });
});

describe("Sidebar — update pill", () => {
  let storage;

  beforeEach(() => {
    storage = { [TOKEN_KEY]: (() => {
      const exp = Math.floor(Date.now() / 1000) + 3600;
      const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email: "ada@example.com" }));
      return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
    })() };
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    vi.stubGlobal("location", { ...globalThis.location, assign: vi.fn() });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.channelDesktop;
  });

  function renderSidebar() {
    return render(<MemoryRouter><Sidebar /></MemoryRouter>);
  }

  it("does not render the pill when window.channelDesktop is absent (web SPA)", () => {
    renderSidebar();
    expect(screen.queryByRole("button", { name: /relaunch to update/i })).toBeNull();
  });

  it("does not render the pill when no update event has fired yet (desktop, current)", () => {
    window.channelDesktop = {
      isDesktop: true,
      onUpdateStatus: vi.fn(),                      // never invokes its cb
      relaunchToUpdate: vi.fn(),
    };
    renderSidebar();
    expect(screen.queryByRole("button", { name: /relaunch to update/i })).toBeNull();
  });

  it("renders the pill with version + click handler after update:downloaded fires", () => {
    let registeredCb;
    const relaunchToUpdate = vi.fn();
    window.channelDesktop = {
      isDesktop: true,
      onUpdateStatus: (cb) => { registeredCb = cb; },
      relaunchToUpdate,
    };
    renderSidebar();
    expect(registeredCb).toBeTypeOf("function");

    // Simulate the main process pushing an event.
    act(() => registeredCb({ state: "downloaded", version: "0.2.1" }));

    const pill = screen.getByRole("button", { name: /relaunch to update v0\.2\.1/i });
    expect(pill).toBeTruthy();
    fireEvent.click(pill);
    expect(relaunchToUpdate).toHaveBeenCalled();
  });

  it("ignores non-downloaded states (checking/available/error)", () => {
    let registeredCb;
    window.channelDesktop = {
      isDesktop: true,
      onUpdateStatus: (cb) => { registeredCb = cb; },
      relaunchToUpdate: vi.fn(),
    };
    renderSidebar();
    act(() => registeredCb({ state: "checking" }));
    act(() => registeredCb({ state: "available", version: "0.2.1" }));
    act(() => registeredCb({ state: "error", message: "boom" }));
    expect(screen.queryByRole("button", { name: /relaunch to update/i })).toBeNull();
  });
});

describe("Sidebar — per-row menu", () => {
  let storage;

  beforeEach(() => {
    storage = { [TOKEN_KEY]: makeToken() };
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    vi.stubGlobal("location", { ...globalThis.location, assign: vi.fn() });
    vi.spyOn(Date, "now").mockReturnValue(NOW);
    // Module-scoped ChatsContext mocks survive vi.restoreAllMocks;
    // clear their call history explicitly.
    mockChatsCtx.renameChat.mockClear();
    mockChatsCtx.archiveChat.mockClear();
    mockChatsCtx.deleteChat.mockClear();
    mockChatsCtx.renameChatLocal.mockClear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function renderSidebarWithChat(chat) {
    return render(
      <MemoryRouter>
        <Sidebar chats={[chat]} />
      </MemoryRouter>,
    );
  }

  it("renders a more-vertical button per chat row", () => {
    renderSidebarWithChat({
      chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString(),
    });
    expect(screen.getByLabelText(/more options for alpha/i)).toBeTruthy();
  });

  it("clicking the menu button opens ChatRowMenu", () => {
    renderSidebarWithChat({
      chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString(),
    });
    fireEvent.click(screen.getByLabelText(/more options for alpha/i));
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /rename/i })).toBeTruthy();
  });

  it("clicking Rename opens RenameChatModal", () => {
    renderSidebarWithChat({
      chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString(),
    });
    fireEvent.click(screen.getByLabelText(/more options for alpha/i));
    fireEvent.click(screen.getByRole("menuitem", { name: /rename/i }));
    expect(screen.getByText(/rename chat/i)).toBeTruthy();
    expect(screen.getByLabelText(/title/i).value).toBe("alpha");
  });

  it("clicking Delete opens DeleteChatModal", () => {
    renderSidebarWithChat({
      chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString(),
    });
    fireEvent.click(screen.getByLabelText(/more options for alpha/i));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
    expect(screen.getByText(/delete chat\?/i)).toBeTruthy();
  });

  it("clicking Archive calls archiveChat (no confirm modal)", () => {
    renderSidebarWithChat({
      chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString(),
    });
    fireEvent.click(screen.getByLabelText(/more options for alpha/i));
    fireEvent.click(screen.getByRole("menuitem", { name: /^archive$/i }));
    expect(mockChatsCtx.archiveChat).toHaveBeenCalledWith("c1");
    // No "Archive chat?" confirm dialog appears — archive is non-destructive.
    expect(screen.queryByText(/archive chat\?/i)).toBeNull();
  });

  it("archiving the active chat navigates to /app", () => {
    let pathname;
    function PathnameSpy() {
      pathname = useLocation().pathname;
      return null;
    }
    // Route shape mirrors App.jsx: `/app/c/:id` — useParams() inside
    // the Sidebar reads `id`, so the route MUST declare it.
    render(
      <MemoryRouter initialEntries={["/app/c/c1"]}>
        <Routes>
          <Route
            path="/app/c/:id"
            element={
              <>
                <Sidebar chats={[{ chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString() }]} />
                <PathnameSpy />
              </>
            }
          />
          <Route
            path="/app"
            element={
              <>
                <Sidebar chats={[{ chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString() }]} />
                <PathnameSpy />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByLabelText(/more options for alpha/i));
    act(() => {
      fireEvent.click(screen.getByRole("menuitem", { name: /^archive$/i }));
    });
    expect(mockChatsCtx.archiveChat).toHaveBeenCalledWith("c1");
    expect(pathname).toBe("/app");
  });

  it("archiving a non-active chat does NOT navigate", () => {
    let pathname;
    function PathnameSpy() {
      pathname = useLocation().pathname;
      return null;
    }
    render(
      <MemoryRouter initialEntries={["/app/c/other"]}>
        <Routes>
          <Route
            path="/app/c/:id"
            element={
              <>
                <Sidebar chats={[{ chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString() }]} />
                <PathnameSpy />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByLabelText(/more options for alpha/i));
    act(() => {
      fireEvent.click(screen.getByRole("menuitem", { name: /^archive$/i }));
    });
    expect(mockChatsCtx.archiveChat).toHaveBeenCalledWith("c1");
    expect(pathname).toBe("/app/c/other");
  });

  it("RenameChatModal onClose clears renameFor (modal closes)", () => {
    renderSidebarWithChat({
      chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString(),
    });
    fireEvent.click(screen.getByLabelText(/more options for alpha/i));
    fireEvent.click(screen.getByRole("menuitem", { name: /rename/i }));
    expect(screen.getByText(/rename chat/i)).toBeTruthy();
    // Click Cancel to invoke onClose
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByText(/rename chat/i)).toBeNull();
  });

  it("DeleteChatModal onClose clears deleteFor (modal closes)", () => {
    renderSidebarWithChat({
      chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString(),
    });
    fireEvent.click(screen.getByLabelText(/more options for alpha/i));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
    expect(screen.getByText(/delete chat\?/i)).toBeTruthy();
    // Click Cancel to invoke onClose
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByText(/delete chat\?/i)).toBeNull();
  });

  it("marks the active row with the 'active' class", () => {
    render(
      // App.jsx defines the route as ``/app/c/:id`` — the param is
      // named ``id``, not ``chatId``. This test must match production.
      <MemoryRouter initialEntries={["/app/c/c1"]}>
        <Routes>
          <Route
            path="/app/c/:id"
            element={
              <Sidebar
                chats={[
                  { chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString() },
                  { chat_id: "c2", title: "beta", last_message_at: new Date().toISOString() },
                ]}
              />
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    const alpha = screen.getByRole("button", { name: /^alpha$/i });
    const beta = screen.getByRole("button", { name: /^beta$/i });
    expect(alpha.className).toMatch(/\bactive\b/);
    expect(beta.className).not.toMatch(/\bactive\b/);
  });
});

describe("groupNameFor", () => {
  const now = new Date("2026-05-30T12:00:00Z").getTime();

  it.each([
    [1 * HOUR, "Today"],
    [23 * HOUR, "Today"],
    [25 * HOUR, "Yesterday"],
    [47 * HOUR, "Yesterday"],
    [49 * HOUR, "Previous 7 days"],
    [6 * DAY, "Previous 7 days"],
    [8 * DAY, "Older"],
    [60 * DAY, "Older"],
  ])("ageMs=%i ms → %s", (ageMs, expected) => {
    expect(groupNameFor(new Date(now - ageMs).toISOString(), now)).toBe(expected);
  });
});
