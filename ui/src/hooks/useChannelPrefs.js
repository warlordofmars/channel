// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useSyncExternalStore } from "react";

export const STORAGE_KEYS = Object.freeze({
  theme:     "channel-theme",
  accent:    "channel-accent",
  density:   "channel-density",
  shape:     "channel-shape",
  font:      "channel-font",
  model:     "channel-model",
  effort:    "channel-effort",
});

export const DEFAULTS = Object.freeze({
  theme:     "dark",
  accent:    "42",
  density:   "cozy",
  shape:     "soft",
  font:      "figtree",
  model:     "claude-opus-4-8",
  effort:    "High",
});

function readPref(key, fallback) {
  try {
    const stored = localStorage.getItem(key);
    return stored == null ? fallback : stored;
  } catch {
    return fallback;
  }
}

function writePref(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* localStorage may throw in private mode; degrade silently */
  }
}

function applyAttr(name, value) {
  document.documentElement.setAttribute("data-" + name, value);
}

// Module-level shared store. All `useChannelPrefs()` calls subscribe to the
// same snapshot, so toggling a preference in one component propagates to
// every other consumer in the tree. Without this, each hook instance kept
// independent React state and the marketing theme toggle in <ThemeToggle>
// updated its own copy while <SiteLayout>'s copy stayed stale.
const PREF_NAMES = Object.keys(STORAGE_KEYS);
let snapshot = Object.freeze(
  PREF_NAMES.reduce((acc, name) => {
    acc[name] = readPref(STORAGE_KEYS[name], DEFAULTS[name]);
    return acc;
  }, {})
);
const listeners = new Set();

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return snapshot;
}

function setPref(name, value) {
  if (snapshot[name] === value) return;
  snapshot = Object.freeze({ ...snapshot, [name]: value });
  writePref(STORAGE_KEYS[name], value);
  listeners.forEach((l) => l());
}

// Test-only: reset the in-memory snapshot back to whatever localStorage
// currently contains. Used between tests that swap the localStorage stub
// so each test starts from a clean shared store.
export function __resetChannelPrefsForTest() {
  snapshot = Object.freeze(
    PREF_NAMES.reduce((acc, name) => {
      acc[name] = readPref(STORAGE_KEYS[name], DEFAULTS[name]);
      return acc;
    }, {})
  );
  listeners.clear();
}

export function useChannelPrefs() {
  const current = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const { theme, accent, density, shape, font, model, effort } = current;

  // NOTE: `theme` is intentionally NOT applied to `data-theme` here. Each
  // route wrapper (SiteLayout / Shell / Login) applies it on mount —
  // applying from the hook itself races against those wrappers' effects
  // because every useChannelPrefs() consumer would re-apply on mount.
  useEffect(() => { applyAttr("accent", accent); }, [accent]);
  useEffect(() => { applyAttr("density", density); }, [density]);
  useEffect(() => { applyAttr("shape", shape); }, [shape]);
  useEffect(() => { applyAttr("font", font); }, [font]);
  useEffect(() => { applyAttr("model", model); }, [model]);
  useEffect(() => { applyAttr("effort", effort); }, [effort]);

  useEffect(() => {
    document.documentElement.style.setProperty("--accent-h", accent);
  }, [accent]);

  const setTheme = useCallback((v) => setPref("theme", v), []);
  const setAccent = useCallback((v) => setPref("accent", v), []);
  const setDensity = useCallback((v) => setPref("density", v), []);
  const setShape = useCallback((v) => setPref("shape", v), []);
  const setFont = useCallback((v) => setPref("font", v), []);
  const setModel = useCallback((v) => setPref("model", v), []);
  const setEffort = useCallback((v) => setPref("effort", v), []);
  const toggleTheme = useCallback(
    () => setPref("theme", snapshot.theme === "dark" ? "light" : "dark"),
    []
  );

  return {
    theme, setTheme, toggleTheme,
    accent, setAccent,
    density, setDensity,
    shape, setShape,
    font, setFont,
    model, setModel,
    effort, setEffort,
  };
}
