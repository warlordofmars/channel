// Copyright (c) 2026 John Carter. All rights reserved.
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  useChannelPrefs,
  DEFAULTS,
  STORAGE_KEYS,
  __resetChannelPrefsForTest,
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
  });

  afterEach(() => vi.unstubAllGlobals());

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
});
