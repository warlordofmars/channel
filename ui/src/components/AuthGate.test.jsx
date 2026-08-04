// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Outlet, Route, Routes, useNavigate } from "react-router-dom";

vi.mock("../api.js", () => ({
  ensureAccessToken: vi.fn(),
}));

import * as api from "../api.js";
import AuthGate from "./AuthGate.jsx";
import { TOKEN_KEY } from "../lib/auth.js";

function makeToken({ expOffsetSeconds = 3600, sub = "u1" } = {}) {
  const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
  const payload = btoa(JSON.stringify({ exp, sub, role: "user", email: "u@ex.com" }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

/** A promise whose settling this test controls, to hold a refresh in flight. */
function deferred() {
  let settle;
  const promise = new Promise((resolve) => {
    settle = resolve;
  });
  return { promise, settle: (value) => settle(value) };
}

let storage;

/**
 * Mirrors `App.jsx`: ONE AuthGate above an `Outlet`, mounted as a layout
 * route. That shape is load-bearing for these tests — it is what makes
 * the gate re-evaluate on every in-app navigation without remounting,
 * which is the render path #520 is about.
 */
function Layout() {
  return (
    <AuthGate>
      <Outlet />
    </AuthGate>
  );
}

function Nav() {
  const navigate = useNavigate();
  function goProjects() {
    navigate("/app/projects");
  }
  return <button type="button" data-testid="nav" onClick={goProjects} />;
}

function Harness({ path = "/app", marker = 0 }) {
  return (
    <MemoryRouter initialEntries={[path]}>
      <Nav />
      <Routes>
        <Route path="/app/login" element={<div data-testid="login" />} />
        <Route element={<Layout />}>
          <Route path="/app" element={<div data-testid="app-home">{marker}</div>} />
          <Route path="/app/projects" element={<div data-testid="projects" />} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

/** What `api.js` does on a successful rotation: store it, hand it back. */
function refreshSucceeds(token) {
  api.ensureAccessToken.mockImplementation(async () => {
    storage[TOKEN_KEY] = JSON.stringify({
      access_token: token,
      expires_at: Date.now() + 3_600_000,
    });
    return token;
  });
}

/**
 * What `api.js` does on a **401** with an unusable stored token: it ends
 * the session itself (`endSession` → `clearSession` + route to login) and
 * answers with no token. The 401-only narrowness of that rule is pinned
 * in `api.test.js`; what matters here is that AuthGate defers to it.
 */
function refreshRefused() {
  api.ensureAccessToken.mockImplementation(async () => {
    delete storage[TOKEN_KEY];
    return "";
  });
}

/**
 * What `api.js` does on anything that is NOT a 401 — offline, DNS, 5xx, a
 * malformed body, #294's 429. None of those is a verdict on the
 * credential, so local state survives and the caller gets the (still
 * unusable) stored token back.
 */
function refreshUnreachable(staleToken) {
  api.ensureAccessToken.mockImplementation(async () => staleToken);
}

beforeEach(() => {
  vi.clearAllMocks();
  storage = {};
  vi.stubGlobal("localStorage", {
    getItem: (k) => storage[k] ?? null,
    setItem: (k, v) => { storage[k] = String(v); },
    removeItem: (k) => { delete storage[k]; },
  });
});

afterEach(() => vi.unstubAllGlobals());

describe("AuthGate", () => {
  // ---- the common path: a usable token, on every render ------------------

  it("renders children when the token is valid", () => {
    storage[TOKEN_KEY] = makeToken();
    render(<Harness />);
    expect(screen.getByTestId("app-home")).toBeTruthy();
    expect(screen.queryByTestId("login")).toBeNull();
  });

  it("never attempts a refresh, and never shows the hold, for a valid token", () => {
    // Constraint: no flash on the common path. This gate runs on EVERY
    // navigation, so a pending state here would blink on each one.
    storage[TOKEN_KEY] = makeToken();
    render(<Harness />);
    expect(screen.queryByTestId("auth-pending")).toBeNull();
    expect(api.ensureAccessToken).not.toHaveBeenCalled();
  });

  // ---- no session at all: unchanged, still a synchronous redirect --------

  it("redirects to /app/login when no token is stored", () => {
    render(<Harness />);
    expect(screen.getByTestId("login")).toBeTruthy();
    expect(screen.queryByTestId("app-home")).toBeNull();
  });

  it("does not call /auth/refresh when there is no session to renew", () => {
    render(<Harness />);
    expect(api.ensureAccessToken).not.toHaveBeenCalled();
    expect(screen.queryByTestId("auth-pending")).toBeNull();
  });

  it("gates other /app/* routes too", () => {
    render(<Harness path="/app/projects" />);
    expect(screen.getByTestId("login")).toBeTruthy();
  });

  // ---- the #520 path: an expired token gets one refresh attempt ----------

  it("holds on a neutral pending state while the refresh is in flight", async () => {
    storage[TOKEN_KEY] = makeToken({ expOffsetSeconds: -3600 });
    const held = deferred();
    api.ensureAccessToken.mockReturnValue(held.promise);

    render(<Harness />);

    // The bug was redirecting HERE, before any request was issued.
    expect(screen.getByTestId("auth-pending")).toBeTruthy();
    expect(screen.queryByTestId("login")).toBeNull();
    expect(screen.queryByTestId("app-home")).toBeNull();

    await act(async () => {
      held.settle("");
    });
    expect(screen.getByTestId("login")).toBeTruthy();
  });

  it("renders children when the expired token is successfully refreshed", async () => {
    storage[TOKEN_KEY] = makeToken({ expOffsetSeconds: -3600 });
    refreshSucceeds(makeToken({ expOffsetSeconds: 3600, sub: "rotated" }));

    render(<Harness />);

    await waitFor(() => expect(screen.getByTestId("app-home")).toBeTruthy());
    expect(screen.queryByTestId("login")).toBeNull();
    expect(api.ensureAccessToken).toHaveBeenCalledTimes(1);
  });

  it("redirects to /app/login when the refresh is refused with a 401", async () => {
    storage[TOKEN_KEY] = makeToken({ expOffsetSeconds: -3600 });
    refreshRefused();

    render(<Harness />);

    await waitFor(() => expect(screen.getByTestId("login")).toBeTruthy());
    expect(screen.queryByTestId("app-home")).toBeNull();
    expect(api.ensureAccessToken).toHaveBeenCalledTimes(1);
  });

  it("redirects WITHOUT ending the session when the refresh never lands", async () => {
    // Offline / DNS / 5xx / a malformed body / #294's 429 all say nothing
    // about the credential. Wiping local state over a transient blip that
    // happened to straddle expiry is the spurious logout this change
    // exists to remove, so the stored session must survive the bounce and
    // a later attempt must still be able to recover it.
    const expired = makeToken({ expOffsetSeconds: -3600 });
    storage[TOKEN_KEY] = expired;
    refreshUnreachable(expired);

    render(<Harness />);

    // The attempt has to have been MADE — a bounce that skipped it would
    // satisfy the storage assertion below for the wrong reason, which is
    // precisely the pre-#520 behaviour.
    expect(screen.getByTestId("auth-pending")).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("login")).toBeTruthy());
    expect(api.ensureAccessToken).toHaveBeenCalledTimes(1);
    expect(storage[TOKEN_KEY]).toBe(expired);
  });

  it("tries to recover a malformed stored token before giving up", async () => {
    // A corrupt localStorage value is not evidence that the refresh
    // cookie is dead, so it earns the same one attempt an expired token
    // does.
    storage[TOKEN_KEY] = "not.a.jwt";
    refreshRefused();

    render(<Harness />);

    await waitFor(() => expect(screen.getByTestId("login")).toBeTruthy());
    expect(api.ensureAccessToken).toHaveBeenCalledTimes(1);
  });

  // ---- idempotence: one attempt per episode, not one per render ----------

  it("does not re-attempt the refresh on repeated re-renders", async () => {
    storage[TOKEN_KEY] = makeToken({ expOffsetSeconds: -3600 });
    refreshRefused();

    const { rerender } = render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("login")).toBeTruthy());

    rerender(<Harness marker={1} />);
    rerender(<Harness marker={2} />);
    rerender(<Harness marker={3} />);

    // #290 revokes the whole device family on a re-presented token, so a
    // per-render attempt would sign the user out everywhere. The redirect
    // render must not re-arm it either.
    expect(api.ensureAccessToken).toHaveBeenCalledTimes(1);
  });

  it("does not re-attempt the refresh while a navigation happens mid-flight", async () => {
    // The reported shape: an ordinary in-session navigation about an hour
    // after sign-in. AuthGate is a layout route, so it stays mounted and
    // simply re-renders — and must not fire a second rotation.
    storage[TOKEN_KEY] = makeToken({ expOffsetSeconds: -3600 });
    const held = deferred();
    api.ensureAccessToken.mockReturnValue(held.promise);

    render(<Harness />);
    expect(screen.getByTestId("auth-pending")).toBeTruthy();

    fireEvent.click(screen.getByTestId("nav"));
    expect(screen.getByTestId("auth-pending")).toBeTruthy();

    await act(async () => {
      held.settle("");
    });
    expect(screen.getByTestId("login")).toBeTruthy();
    expect(api.ensureAccessToken).toHaveBeenCalledTimes(1);
  });

  it("falls through to login when the refresh never answers at all", async () => {
    // A stalled connection is not a refusal — `ensureAccessToken` simply
    // never settles. Holding the empty state until the browser's own
    // fetch timeout (minutes, if it exists) would trade an unwanted
    // bounce for a blank screen, which is worse than the behaviour this
    // change replaced. Fake timers go up BEFORE render because the hold
    // timer is scheduled in the mount effect.
    vi.useFakeTimers();
    try {
      const expired = makeToken({ expOffsetSeconds: -3600 });
      storage[TOKEN_KEY] = expired;
      // Never settles.
      api.ensureAccessToken.mockReturnValue(deferred().promise);

      await act(async () => {
        render(<Harness />);
      });
      expect(screen.getByTestId("auth-pending")).toBeTruthy();

      await act(async () => {
        vi.advanceTimersByTime(8_000);
      });

      expect(screen.getByTestId("login")).toBeTruthy();
      // Falling through is not a verdict on the credential, so the
      // session survives for a later attempt to recover.
      expect(storage[TOKEN_KEY]).toBe(expired);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores a refresh that lands after the gate has unmounted", async () => {
    storage[TOKEN_KEY] = makeToken({ expOffsetSeconds: -3600 });
    const held = deferred();
    api.ensureAccessToken.mockReturnValue(held.promise);

    const { unmount } = render(<Harness />);
    expect(screen.getByTestId("auth-pending")).toBeTruthy();

    unmount();
    await act(async () => {
      held.settle("");
    });

    expect(screen.queryByTestId("auth-pending")).toBeNull();
    expect(api.ensureAccessToken).toHaveBeenCalledTimes(1);
  });
});
