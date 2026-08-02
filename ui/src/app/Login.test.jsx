// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Login from "./Login.jsx";
import { __resetChannelPrefsForTest } from "../hooks/useChannelPrefs.js";
import { TOKEN_KEY } from "../lib/auth.js";

// Structurally a JWT — `saveSession` refuses to persist anything else.
const DESKTOP_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkZXNrdG9wIn0.sig";

// navigateSpy is shared across the desktop-mode describe block.
// vi.mock is hoisted, so the factory runs before imports.
const navigateSpy = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useNavigate: () => navigateSpy,
  };
});

describe("Login", () => {
  let assignSpy;
  let storage;

  beforeEach(() => {
    assignSpy = vi.fn();
    storage = {};
    vi.stubGlobal("location", { ...globalThis.location, assign: assignSpy });
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    document.documentElement.removeAttribute("data-theme");
    __resetChannelPrefsForTest();
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders the 'Sign in to Channel' heading", () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: /sign in to channel/i })).toBeTruthy();
  });

  it("renders the subtext 'Your workspace for thinking with AI.'", () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    expect(screen.getByText(/workspace for thinking with AI/i)).toBeTruthy();
  });

  it("renders a 'Continue with Google' button with the Google G mark", () => {
    const { container } = render(<MemoryRouter><Login /></MemoryRouter>);
    const btn = screen.getByRole("button", { name: /continue with google/i });
    expect(btn).toBeTruthy();
    // The button contains a multi-coloured SVG (the Google G)
    const fills = Array.from(btn.querySelectorAll("svg path")).map((p) => p.getAttribute("fill"));
    expect(fills).toEqual(expect.arrayContaining(["#EA4335", "#4285F4", "#FBBC05", "#34A853"]));
  });

  it("clicking 'Continue with Google' assigns location to /auth/login", () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /continue with google/i }));
    expect(assignSpy).toHaveBeenCalledWith("/auth/login");
  });

  it("renders the fine-print legal line", () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    expect(screen.getByText(/terms/i)).toBeTruthy();
    expect(screen.getByText(/privacy policy/i)).toBeTruthy();
  });

  it("renders the 'New here?' footer line", () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    expect(screen.getByText(/new here\?/i)).toBeTruthy();
  });

  it("renders the ChannelMark logo (.ch-mark) at the top of the card", () => {
    const { container } = render(<MemoryRouter><Login /></MemoryRouter>);
    expect(container.querySelector(".ch-mark")).toBeTruthy();
  });

  it("applies the chat-app theme (theme) to data-theme on mount", () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    render(<MemoryRouter><Login /></MemoryRouter>);
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("wraps .auth in .stage.full > .win so the card centers vertically", () => {
    // Without the .stage.full > .win wrappers, .auth's `flex: 1` has no
    // flex parent and the sign-in card collapses to its content size near
    // the top of the viewport instead of centering vertically.
    const { container } = render(<MemoryRouter><Login /></MemoryRouter>);
    const stage = container.querySelector(".stage.full");
    expect(stage).toBeTruthy();
    const win = stage.querySelector(".win");
    expect(win).toBeTruthy();
    const auth = win.querySelector(".auth");
    expect(auth).toBeTruthy();
  });
});

describe("Login (desktop mode)", () => {
  let storage;

  beforeEach(() => {
    navigateSpy.mockClear();
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    document.documentElement.removeAttribute("data-theme");
    __resetChannelPrefsForTest();
    window.channelDesktop = { isDesktop: true, login: vi.fn().mockResolvedValue(DESKTOP_JWT) };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.channelDesktop;
  });

  it("renders the desktop-mode CTA when window.channelDesktop is present", () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    expect(screen.getByRole("button", { name: /sign in with google/i })).toBeInTheDocument();
  });

  it("calls window.channelDesktop.login() and stores the JWT on click", async () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /sign in with google/i }));
    expect(window.channelDesktop.login).toHaveBeenCalled();
    // Stored through saveSession, so it lands under the de-branded key in
    // the {access_token, expires_at} envelope the refresh wrapper reads.
    expect(JSON.parse(localStorage.getItem(TOKEN_KEY)).access_token).toBe(DESKTOP_JWT);
    expect(navigateSpy).toHaveBeenCalledWith("/app");
  });
});

describe("Login (desktop mode — malformed token)", () => {
  let storage;

  beforeEach(() => {
    navigateSpy.mockClear();
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    document.documentElement.removeAttribute("data-theme");
    __resetChannelPrefsForTest();
    window.channelDesktop = { isDesktop: true, login: vi.fn().mockResolvedValue("not-a-jwt") };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.channelDesktop;
  });

  it("surfaces a failure rather than persisting a malformed token", async () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /sign in with google/i }));
    expect(await screen.findByText(/login failed/i)).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(navigateSpy).not.toHaveBeenCalled();
  });
});

describe("Login (desktop mode — cancelled)", () => {
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
    document.documentElement.removeAttribute("data-theme");
    __resetChannelPrefsForTest();
    window.channelDesktop = { isDesktop: true, login: vi.fn().mockRejectedValue(new Error("USER_CANCELLED")) };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.channelDesktop;
  });

  it("surfaces an error message when login is cancelled", async () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /sign in with google/i }));
    expect(await screen.findByText(/cancelled/i)).toBeInTheDocument();
  });
});

describe("Login (desktop mode — timeout)", () => {
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
    document.documentElement.removeAttribute("data-theme");
    __resetChannelPrefsForTest();
    window.channelDesktop = { isDesktop: true, login: vi.fn().mockRejectedValue(new Error("TIMEOUT")) };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.channelDesktop;
  });

  it("surfaces a timeout message when login times out", async () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /sign in with google/i }));
    expect(await screen.findByText(/timed out/i)).toBeInTheDocument();
  });
});

describe("Login (desktop mode — other failure)", () => {
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
    document.documentElement.removeAttribute("data-theme");
    __resetChannelPrefsForTest();
    window.channelDesktop = { isDesktop: true, login: vi.fn().mockRejectedValue(new Error("STATE_MISMATCH")) };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.channelDesktop;
  });

  it("surfaces a generic error for other failures", async () => {
    render(<MemoryRouter><Login /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /sign in with google/i }));
    expect(await screen.findByText(/login failed/i)).toBeInTheDocument();
  });
});
