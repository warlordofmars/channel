// Copyright (c) 2026 John Carter. All rights reserved.
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api.js";
import {
  useChannelPrefs,
  DEFAULTS,
  STORAGE_KEYS,
  __resetChannelPrefsForTest,
  __resetServerSyncForTest,
} from "./useChannelPrefs.js";

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

    act(() => result.current.setShowReasoning(true));
    expect(result.current.showReasoning).toBe(true);
    expect(storage[STORAGE_KEYS.showReasoning]).toBe("1");

    act(() => result.current.setSuggestFollowups(false));
    expect(result.current.suggestFollowups).toBe(false);
    expect(storage[STORAGE_KEYS.suggestFollowups]).toBe("0");
  });

  // ---- Server hydrate ----------------------------------------------------

  it("hydrates from server when a mgmt JWT is present", async () => {
    storage["starter_mgmt_token"] = "tok";
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
    storage["starter_mgmt_token"] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    vi.spyOn(api, "getPrefs").mockResolvedValue({
      theme: "light",
      bogus_key: "ignored",
    });
    const { result } = renderHook(() => useChannelPrefs());
    await waitFor(() => expect(result.current.theme).toBe("light"));
  });

  it("swallows getPrefs errors during hydrate", async () => {
    storage["starter_mgmt_token"] = "tok";
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
    storage["starter_mgmt_token"] = "tok";
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
    storage["starter_mgmt_token"] = "tok";
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
    storage["starter_mgmt_token"] = "tok";
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
    storage["starter_mgmt_token"] = "tok";
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
});
