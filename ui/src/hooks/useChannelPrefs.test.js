// Copyright (c) 2026 John Carter. All rights reserved.
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api.js";
import {
  useChannelPrefs,
  DEFAULTS,
  PUT_RETRY_DELAYS_MS,
  STORAGE_KEYS,
  __resetChannelPrefsForTest,
  __resetServerSyncForTest,
} from "./useChannelPrefs.js";
import { TOKEN_KEY } from "../lib/auth.js";

/** Comfortably past the hook's 200ms write debounce. */
const PUT_DEBOUNCE = 250;

describe("useChannelPrefs", () => {
  let storage;

  beforeEach(() => {
    storage = {};
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
    for (const a of Array.from(document.documentElement.attributes)) {
      if (a.name.startsWith("data-")) document.documentElement.removeAttribute(a.name);
    }
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("returns the default value for each pref when storage is empty", () => {
    const { result } = renderHook(() => useChannelPrefs());
    expect(result.current.theme).toBe(DEFAULTS.theme);
    expect(result.current.accent).toBe(DEFAULTS.accent);
    expect(result.current.density).toBe(DEFAULTS.density);
    expect(result.current.shape).toBe(DEFAULTS.shape);
    expect(result.current.font).toBe(DEFAULTS.font);
    expect(result.current.model).toBe(DEFAULTS.model);
    expect(result.current.effort).toBe(DEFAULTS.effort);
  });

  it("reads each pref from localStorage when present", () => {
    storage[STORAGE_KEYS.theme] = "light";
    storage[STORAGE_KEYS.accent] = "150";
    storage[STORAGE_KEYS.density] = "compact";
    storage[STORAGE_KEYS.shape] = "sharp";
    storage[STORAGE_KEYS.font] = "space";
    storage[STORAGE_KEYS.model] = "claude-haiku-4-5";
    storage[STORAGE_KEYS.effort] = "Low";
    __resetChannelPrefsForTest();
    const { result } = renderHook(() => useChannelPrefs());
    expect(result.current.theme).toBe("light");
    expect(result.current.accent).toBe("150");
    expect(result.current.density).toBe("compact");
    expect(result.current.shape).toBe("sharp");
    expect(result.current.font).toBe("space");
    expect(result.current.model).toBe("claude-haiku-4-5");
    expect(result.current.effort).toBe("Low");
  });

  it("writes pref to localStorage and applies data-{pref} on <html>", () => {
    // `data-theme` is intentionally NOT applied by the hook — route
    // wrappers (SiteLayout / Shell / Login) own that. setTheme still
    // persists to localStorage; the wrapper picks it up on next render.
    const { result } = renderHook(() => useChannelPrefs());
    act(() => result.current.setTheme("light"));
    expect(storage[STORAGE_KEYS.theme]).toBe("light");
    expect(result.current.theme).toBe("light");

    act(() => result.current.setAccent("300"));
    expect(storage[STORAGE_KEYS.accent]).toBe("300");
    expect(document.documentElement.getAttribute("data-accent")).toBe("300");

    act(() => result.current.setDensity("compact"));
    expect(document.documentElement.getAttribute("data-density")).toBe("compact");

    act(() => result.current.setShape("sharp"));
    expect(document.documentElement.getAttribute("data-shape")).toBe("sharp");

    act(() => result.current.setFont("space"));
    expect(document.documentElement.getAttribute("data-font")).toBe("space");

    act(() => result.current.setModel("claude-haiku-4-5"));
    expect(document.documentElement.getAttribute("data-model")).toBe("claude-haiku-4-5");

    act(() => result.current.setEffort("Max"));
    expect(document.documentElement.getAttribute("data-effort")).toBe("Max");
  });

  it("toggleTheme flips dark ↔ light", () => {
    storage[STORAGE_KEYS.theme] = "dark";
    __resetChannelPrefsForTest();
    const { result } = renderHook(() => useChannelPrefs());
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("light");
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("dark");
  });

  it("STORAGE_KEYS are all distinct and channel-prefixed", () => {
    const keys = Object.values(STORAGE_KEYS);
    expect(new Set(keys).size).toBe(keys.length);
    for (const k of keys) expect(k).toMatch(/^channel-/);
  });

  it("readPref falls back to default when localStorage.getItem throws", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => { throw new Error("denied"); },
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    // Re-seed the shared snapshot using the throwing localStorage so the
    // catch branch in readPref actually fires.
    __resetChannelPrefsForTest();
    const { result } = renderHook(() => useChannelPrefs());
    expect(result.current.theme).toBe(DEFAULTS.theme);
  });

  it("propagates updates across separate hook instances (shared store)", () => {
    // Regression: each useChannelPrefs() call must subscribe to the same
    // underlying store. Without this, ThemeToggle's toggleTheme updates
    // only its local copy and SiteLayout never re-renders to see the change.
    const a = renderHook(() => useChannelPrefs());
    const b = renderHook(() => useChannelPrefs());
    expect(a.result.current.theme).toBe(DEFAULTS.theme);
    expect(b.result.current.theme).toBe(DEFAULTS.theme);

    act(() => a.result.current.toggleTheme());
    const flipped = DEFAULTS.theme === "dark" ? "light" : "dark";
    expect(a.result.current.theme).toBe(flipped);
    expect(b.result.current.theme).toBe(flipped);

    act(() => b.result.current.setAccent("99"));
    expect(a.result.current.accent).toBe("99");
    expect(b.result.current.accent).toBe("99");
  });

  it("setPref short-circuits when the value is unchanged", () => {
    // Hitting the early return keeps the snapshot reference stable so
    // subscribers don't re-render unnecessarily.
    const { result } = renderHook(() => useChannelPrefs());
    const before = result.current.theme;
    act(() => result.current.setTheme(before));
    expect(result.current.theme).toBe(before);
  });

  it("writePref silently degrades when localStorage.setItem throws", () => {
    // Storage that throws on writes — the hook should not crash.
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => { throw new Error("quota"); },
      removeItem: vi.fn(),
    });
    const { result } = renderHook(() => useChannelPrefs());
    // setTheme triggers a writePref under the hood; should not throw
    expect(() => act(() => result.current.setTheme("light"))).not.toThrow();
  });

  // ---- Behavior toggles (sendOnEnter / showReasoning / suggestFollowups) -

  it("exposes behavior toggles as booleans + setters that coerce", () => {
    // Defaults: sendOnEnter true, showReasoning false, suggestFollowups true.
    const { result } = renderHook(() => useChannelPrefs());
    expect(result.current.sendOnEnter).toBe(true);
    expect(result.current.showReasoning).toBe(false);
    expect(result.current.suggestFollowups).toBe(true);

    act(() => result.current.setSendOnEnter(false));
    expect(result.current.sendOnEnter).toBe(false);
    expect(storage[STORAGE_KEYS.sendOnEnter]).toBe("0");
    // Flip back — covers the truthy branch of the v ? "1" : "0" coercion.
    act(() => result.current.setSendOnEnter(true));
    expect(result.current.sendOnEnter).toBe(true);
    expect(storage[STORAGE_KEYS.sendOnEnter]).toBe("1");

    act(() => result.current.setShowReasoning(true));
    expect(result.current.showReasoning).toBe(true);
    expect(storage[STORAGE_KEYS.showReasoning]).toBe("1");
    // Flip back — covers the falsy branch.
    act(() => result.current.setShowReasoning(false));
    expect(result.current.showReasoning).toBe(false);
    expect(storage[STORAGE_KEYS.showReasoning]).toBe("0");

    act(() => result.current.setSuggestFollowups(false));
    expect(result.current.suggestFollowups).toBe(false);
    expect(storage[STORAGE_KEYS.suggestFollowups]).toBe("0");
    // Flip back — covers the truthy branch.
    act(() => result.current.setSuggestFollowups(true));
    expect(result.current.suggestFollowups).toBe(true);
    expect(storage[STORAGE_KEYS.suggestFollowups]).toBe("1");
  });

  // ---- Server hydrate ----------------------------------------------------

  it("hydrates from server when a mgmt JWT is present", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    const getPrefsSpy = vi.spyOn(api, "getPrefs").mockResolvedValue({
      theme: "light",
      accent: "150",
      send_on_enter: false,
    });
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(getPrefsSpy).toHaveBeenCalled());
    await waitFor(() => expect(result.current.theme).toBe("light"));
    expect(result.current.accent).toBe("150");
    expect(result.current.sendOnEnter).toBe(false);
    // Server values are persisted to localStorage for refresh-without-call.
    expect(storage[STORAGE_KEYS.theme]).toBe("light");
    expect(storage[STORAGE_KEYS.sendOnEnter]).toBe("0");
  });

  it("does NOT call /api/me/prefs when no mgmt JWT is present", () => {
    const getPrefsSpy = vi.spyOn(api, "getPrefs").mockResolvedValue({});
    renderHook(() => useChannelPrefs());
    expect(getPrefsSpy).not.toHaveBeenCalled();
  });

  it("ignores unknown server keys during hydrate", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    vi.spyOn(api, "getPrefs").mockResolvedValue({
      theme: "light",
      bogus_key: "ignored",
    });
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(result.current.theme).toBe("light"));
  });

  it("hydrates boolean true values (covers the truthy boolean branch)", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    vi.spyOn(api, "getPrefs").mockResolvedValue({
      show_reasoning: true,
      suggest_followups: true,
    });
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(result.current.showReasoning).toBe(true));
    expect(result.current.suggestFollowups).toBe(true);
    expect(storage[STORAGE_KEYS.showReasoning]).toBe("1");
  });

  it("hydrate is a no-op when the server returns an empty prefs object", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    const getPrefsSpy = vi.spyOn(api, "getPrefs").mockResolvedValue({});
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(getPrefsSpy).toHaveBeenCalled());
    // Defaults remain — no snapshot patch was applied (covers the false branch
    // of `if (Object.keys(patch).length)`).
    expect(result.current.theme).toBe(DEFAULTS.theme);
    expect(result.current.accent).toBe(DEFAULTS.accent);
  });

  it("hydrate handles a null prefs response (covers `serverPrefs || {}`)", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    const getPrefsSpy = vi.spyOn(api, "getPrefs").mockResolvedValue(null);
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(getPrefsSpy).toHaveBeenCalled());
    expect(result.current.theme).toBe(DEFAULTS.theme);
  });

  it("swallows getPrefs errors during hydrate", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    const getPrefsSpy = vi
      .spyOn(api, "getPrefs")
      .mockRejectedValue(new Error("network"));
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(getPrefsSpy).toHaveBeenCalled());
    // localStorage stays canonical — defaults remain.
    expect(result.current.theme).toBe(DEFAULTS.theme);
  });

  it("hydrate is one-shot across mounts (second mount does not re-fetch)", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    const getPrefsSpy = vi
      .spyOn(api, "getPrefs")
      .mockResolvedValue({ theme: "light" });
    const first = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(getPrefsSpy).toHaveBeenCalledTimes(1));
    first.unmount();
    renderHook(() => useChannelPrefs());
    // Second mount does NOT re-fetch.
    expect(getPrefsSpy).toHaveBeenCalledTimes(1);
  });

  it("hydrate is gated when localStorage.getItem throws for the token", () => {
    // Make the token probe in shouldHydrate throw; hydrate must not run.
    vi.stubGlobal("localStorage", {
      getItem: () => { throw new Error("blocked"); },
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    const getPrefsSpy = vi.spyOn(api, "getPrefs").mockResolvedValue({});
    renderHook(() => useChannelPrefs());
    expect(getPrefsSpy).not.toHaveBeenCalled();
  });

  // ---- Debounced PUT -----------------------------------------------------

  it("debounces PUTs and collapses rapid changes to the latest value", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    // Stub getPrefs first so the hydrate doesn't reject + leak.
    vi.spyOn(api, "getPrefs").mockResolvedValue({});
    const putPrefsSpy = vi.spyOn(api, "putPrefs").mockResolvedValue();
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(api.getPrefs).toHaveBeenCalled());

    vi.useFakeTimers();
    act(() => result.current.setAccent("18"));
    act(() => result.current.setAccent("150"));
    expect(putPrefsSpy).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(250);
    });
    vi.useRealTimers();
    expect(putPrefsSpy).toHaveBeenCalledTimes(1);
    expect(putPrefsSpy).toHaveBeenCalledWith({ accent: "150" });
  });

  it("PUTs booleans as JSON true/false (not '1' / '0')", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    vi.spyOn(api, "getPrefs").mockResolvedValue({});
    const putPrefsSpy = vi.spyOn(api, "putPrefs").mockResolvedValue();
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(api.getPrefs).toHaveBeenCalled());

    vi.useFakeTimers();
    act(() => result.current.setSendOnEnter(false));
    await act(async () => {
      vi.advanceTimersByTime(250);
    });
    vi.useRealTimers();
    expect(putPrefsSpy).toHaveBeenCalledWith({ send_on_enter: false });

    vi.useFakeTimers();
    act(() => result.current.setSuggestFollowups(false));
    await act(async () => {
      vi.advanceTimersByTime(250);
    });
    vi.useRealTimers();
    expect(putPrefsSpy).toHaveBeenLastCalledWith({ suggest_followups: false });
  });

  it("PUT failure does not roll back local state", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    vi.spyOn(api, "getPrefs").mockResolvedValue({});
    vi.spyOn(api, "putPrefs").mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(api.getPrefs).toHaveBeenCalled());

    vi.useFakeTimers();
    act(() => result.current.setAccent("18"));
    await act(async () => {
      vi.advanceTimersByTime(250);
    });
    vi.useRealTimers();
    expect(result.current.accent).toBe("18");
  });

  it("does not schedule a PUT when no mgmt JWT is present", async () => {
    // No token → schedulePutPref should short-circuit.
    const putPrefsSpy = vi.spyOn(api, "putPrefs").mockResolvedValue();
    const { result } = renderHook(() => useChannelPrefs());
    vi.useFakeTimers();
    act(() => result.current.setAccent("18"));
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    vi.useRealTimers();
    expect(putPrefsSpy).not.toHaveBeenCalled();
  });

  it("__resetServerSyncForTest clears pending debounced PUT timers", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    vi.spyOn(api, "getPrefs").mockResolvedValue({});
    const putPrefsSpy = vi.spyOn(api, "putPrefs").mockResolvedValue();
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(api.getPrefs).toHaveBeenCalled());

    vi.useFakeTimers();
    act(() => result.current.setAccent("18"));
    // Reset BEFORE the 200ms debounce fires; the pending timer should be cleared.
    __resetServerSyncForTest();
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    vi.useRealTimers();
    expect(putPrefsSpy).not.toHaveBeenCalled();
  });

  // ---- Write failures are visible, not swallowed (#482) -------------------
  //
  // Half 1 of #482. Before this, `schedulePutPref` ended in
  // `catch { /* swallow */ }`: the browser kept showing a value the server
  // had rejected, and nothing anywhere recorded that it had. #469's whole
  // investigation was spent inside that gap. Every test below asserts the
  // FAILURE path — a suite that only proves a successful PUT is what let
  // this ship.

  /** Mount with a token + stubbed hydrate, and return the hook handle. */
  async function mountWithToken() {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    vi.spyOn(api, "getPrefs").mockResolvedValue({});
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(api.getPrefs).toHaveBeenCalled());
    return result;
  }

  it("surfaces a failed PUT as prefsSyncError instead of swallowing it", async () => {
    const result = await mountWithToken();
    vi.spyOn(api, "putPrefs").mockRejectedValue(new Error("network down"));
    expect(result.current.prefsSyncError).toBeNull();

    vi.useFakeTimers();
    act(() => result.current.setSuggestFollowups(false));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    vi.useRealTimers();

    expect(result.current.prefsSyncError).not.toBeNull();
    expect(result.current.prefsSyncError.keys).toEqual(["suggestFollowups"]);
    // A transport-level throw carries no HTTP status.
    expect(result.current.prefsSyncError.statuses.suggestFollowups).toBeNull();
    expect(result.current.prefsSyncError.unauthorized).toBe(false);
  });

  it("flags a 401 as unauthorized and does NOT burn retries on it", async () => {
    const result = await mountWithToken();
    const putPrefsSpy = vi
      .spyOn(api, "putPrefs")
      .mockRejectedValue(new api.ApiError("putPrefs failed:", 401));

    vi.useFakeTimers();
    act(() => result.current.setAccent("18"));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    expect(putPrefsSpy).toHaveBeenCalledTimes(1);
    expect(result.current.prefsSyncError.statuses.accent).toBe(401);
    expect(result.current.prefsSyncError.unauthorized).toBe(true);

    // The session is gone; a timer retry would only spend requests (and
    // token renewals) to be refused again. The value waits for re-auth.
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    vi.useRealTimers();
    expect(putPrefsSpy).toHaveBeenCalledTimes(1);
  });

  it("retries a transient failure with backoff and clears the error on success", async () => {
    const result = await mountWithToken();
    const putPrefsSpy = vi
      .spyOn(api, "putPrefs")
      .mockRejectedValueOnce(new api.ApiError("putPrefs failed:", 503))
      .mockResolvedValueOnce();

    vi.useFakeTimers();
    act(() => result.current.setSuggestFollowups(false));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    expect(putPrefsSpy).toHaveBeenCalledTimes(1);
    expect(result.current.prefsSyncError.statuses.suggestFollowups).toBe(503);

    await act(async () => {
      vi.advanceTimersByTime(PUT_RETRY_DELAYS_MS[0]);
    });
    vi.useRealTimers();
    expect(putPrefsSpy).toHaveBeenCalledTimes(2);
    // Same value, re-sent — the failed write was held, not discarded.
    expect(putPrefsSpy).toHaveBeenLastCalledWith({ suggest_followups: false });
    expect(result.current.prefsSyncError).toBeNull();
  });

  it("stops retrying once the backoff budget is spent, keeping the error", async () => {
    const result = await mountWithToken();
    const putPrefsSpy = vi
      .spyOn(api, "putPrefs")
      .mockRejectedValue(new api.ApiError("putPrefs failed:", 500));

    vi.useFakeTimers();
    act(() => result.current.setAccent("18"));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    for (const delay of PUT_RETRY_DELAYS_MS) {
      await act(async () => {
        vi.advanceTimersByTime(delay);
      });
    }
    expect(putPrefsSpy).toHaveBeenCalledTimes(1 + PUT_RETRY_DELAYS_MS.length);

    // Budget spent: no further automatic attempts, and the failure stays
    // visible rather than quietly resolving itself.
    await act(async () => {
      vi.advanceTimersByTime(120_000);
    });
    vi.useRealTimers();
    expect(putPrefsSpy).toHaveBeenCalledTimes(1 + PUT_RETRY_DELAYS_MS.length);
    expect(result.current.prefsSyncError.statuses.accent).toBe(500);
  });

  it("retryPrefsSync re-sends the held write after a 401 and clears the error", async () => {
    const result = await mountWithToken();
    const putPrefsSpy = vi
      .spyOn(api, "putPrefs")
      .mockRejectedValueOnce(new api.ApiError("putPrefs failed:", 401));

    vi.useFakeTimers();
    act(() => result.current.setSuggestFollowups(false));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    vi.useRealTimers();
    expect(result.current.prefsSyncError.unauthorized).toBe(true);

    // Caller has re-authenticated; the write is still there to send.
    putPrefsSpy.mockResolvedValue();
    await act(async () => {
      await result.current.retryPrefsSync();
    });
    expect(putPrefsSpy).toHaveBeenCalledTimes(2);
    expect(putPrefsSpy).toHaveBeenLastCalledWith({ suggest_followups: false });
    expect(result.current.prefsSyncError).toBeNull();
  });

  it("retryPrefsSync is a no-op when every write has been confirmed", async () => {
    const result = await mountWithToken();
    const putPrefsSpy = vi.spyOn(api, "putPrefs").mockResolvedValue();

    vi.useFakeTimers();
    act(() => result.current.setAccent("18"));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    vi.useRealTimers();
    expect(putPrefsSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.retryPrefsSync();
    });
    expect(putPrefsSpy).toHaveBeenCalledTimes(1);
    expect(result.current.prefsSyncError).toBeNull();
  });

  it("clears only the pref that recovered, not every reported failure", async () => {
    const result = await mountWithToken();
    // 401 so neither failure schedules a backoff retry — the only write that
    // runs after this point is the one the test drives explicitly.
    const putPrefsSpy = vi
      .spyOn(api, "putPrefs")
      .mockRejectedValue(new api.ApiError("putPrefs failed:", 401));

    vi.useFakeTimers();
    act(() => result.current.setAccent("18"));
    act(() => result.current.setSuggestFollowups(false));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    expect([...result.current.prefsSyncError.keys].sort()).toEqual([
      "accent",
      "suggestFollowups",
    ]);

    // Re-authenticated, and the user changes ONLY the accent. Its write
    // lands; `suggest_followups` is still unconfirmed and must still be
    // reported — a recovery clears the pref that recovered, not the store.
    putPrefsSpy.mockResolvedValue();
    act(() => result.current.setAccent("150"));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    vi.useRealTimers();
    expect(putPrefsSpy).toHaveBeenLastCalledWith({ accent: "150" });
    expect(result.current.prefsSyncError.keys).toEqual(["suggestFollowups"]);
    expect(result.current.prefsSyncError.unauthorized).toBe(true);
  });

  it("a fresh change supersedes a pending retry for the same pref", async () => {
    const result = await mountWithToken();
    const putPrefsSpy = vi
      .spyOn(api, "putPrefs")
      .mockRejectedValueOnce(new api.ApiError("putPrefs failed:", 500))
      .mockResolvedValue();

    vi.useFakeTimers();
    act(() => result.current.setAccent("18"));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    expect(putPrefsSpy).toHaveBeenLastCalledWith({ accent: "18" });

    // Change it again before the backoff elapses: the retry must carry the
    // NEW value, and must fire once — not once per scheduled timer.
    act(() => result.current.setAccent("150"));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE + PUT_RETRY_DELAYS_MS[0]);
    });
    vi.useRealTimers();
    expect(putPrefsSpy).toHaveBeenCalledTimes(2);
    expect(putPrefsSpy).toHaveBeenLastCalledWith({ accent: "150" });
    expect(result.current.prefsSyncError).toBeNull();
  });

  it("a later transient failure never masks an outstanding 401", async () => {
    // Each key keeps its own status. With one shared "last failure status"
    // the 500 below overwrote the 401 and `unauthorized` flipped to false —
    // so a UI would promise an automatic retry for a write that is actually
    // stuck until the user signs in again.
    const result = await mountWithToken();
    const putPrefsSpy = vi
      .spyOn(api, "putPrefs")
      .mockRejectedValueOnce(new api.ApiError("putPrefs failed:", 401));

    vi.useFakeTimers();
    act(() => result.current.setSuggestFollowups(false));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    expect(result.current.prefsSyncError.unauthorized).toBe(true);

    putPrefsSpy.mockRejectedValue(new api.ApiError("putPrefs failed:", 500));
    act(() => result.current.setAccent("18"));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    vi.useRealTimers();

    expect(result.current.prefsSyncError.statuses).toEqual({
      suggestFollowups: 401,
      accent: 500,
    });
    expect(result.current.prefsSyncError.unauthorized).toBe(true);
  });

  it("a write confirmed after a newer change does not discard the newer value", async () => {
    // A PUT is in flight for as long as the network takes, and the user can
    // change the same pref meanwhile. Acting on the older request's success
    // would delete the newer value before anything had sent it — the next
    // flush then PUTs `undefined` and the change is lost silently, which is
    // the exact class of bug this issue exists to remove.
    const result = await mountWithToken();
    let releaseFirst;
    const putPrefsSpy = vi
      .spyOn(api, "putPrefs")
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            releaseFirst = resolve;
          }),
      )
      .mockResolvedValue();

    vi.useFakeTimers();
    act(() => result.current.setAccent("18"));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    expect(putPrefsSpy).toHaveBeenCalledWith({ accent: "18" });

    act(() => result.current.setAccent("150"));
    await act(async () => {
      releaseFirst();
    });
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    vi.useRealTimers();

    expect(putPrefsSpy).toHaveBeenCalledTimes(2);
    expect(putPrefsSpy).toHaveBeenLastCalledWith({ accent: "150" });
    expect(result.current.prefsSyncError).toBeNull();
  });

  it("a write rejected after a newer change does not report the superseded value", async () => {
    const result = await mountWithToken();
    let rejectFirst;
    const putPrefsSpy = vi
      .spyOn(api, "putPrefs")
      .mockImplementationOnce(
        () =>
          new Promise((resolve, reject) => {
            rejectFirst = reject;
          }),
      )
      .mockResolvedValue();

    vi.useFakeTimers();
    act(() => result.current.setSuggestFollowups(false));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });

    // Superseded before the failure lands: the newer write is already
    // queued and owns the key, so this outcome must not be reported and
    // must not schedule a retry over the newer write's debounce slot.
    act(() => result.current.setSuggestFollowups(true));
    await act(async () => {
      rejectFirst(new api.ApiError("putPrefs failed:", 500));
    });
    // Asserted HERE, while the newer write is still only queued. Checking
    // after it lands proves nothing: its own success would clear a wrongly
    // recorded error on the way past.
    expect(result.current.prefsSyncError).toBeNull();

    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    vi.useRealTimers();

    expect(putPrefsSpy).toHaveBeenLastCalledWith({ suggest_followups: true });
    expect(result.current.prefsSyncError).toBeNull();
  });

  it("__resetServerSyncForTest clears a surfaced write failure", async () => {
    const result = await mountWithToken();
    vi.spyOn(api, "putPrefs").mockRejectedValue(new Error("boom"));

    vi.useFakeTimers();
    act(() => result.current.setAccent("18"));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    vi.useRealTimers();
    expect(result.current.prefsSyncError).not.toBeNull();

    act(() => __resetServerSyncForTest());
    expect(result.current.prefsSyncError).toBeNull();
  });
});
