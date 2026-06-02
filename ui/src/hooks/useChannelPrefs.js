// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useSyncExternalStore } from "react";

export const STORAGE_KEYS = Object.freeze({
  theme:             "channel-theme",
  accent:            "channel-accent",
  density:           "channel-density",
  shape:             "channel-shape",
  font:              "channel-font",
  model:             "channel-model",
  effort:            "channel-effort",
  sendOnEnter:       "channel-send-on-enter",
  showReasoning:     "channel-show-reasoning",
  suggestFollowups:  "channel-suggest-followups",
});

export const DEFAULTS = Object.freeze({
  theme:             "dark",
  accent:            "42",
  density:           "cozy",
  shape:             "soft",
  font:              "figtree",
  model:             "claude-opus-4-6",
  effort:            "High",
  sendOnEnter:       "1",
  showReasoning:     "0",
  suggestFollowups:  "1",
});

// Server key (snake_case) ↔ hook key (camelCase) mapping for round-tripping
// to `/api/me/prefs`. Server stores booleans as JSON true/false; hook stores
// "1"/"0" strings to share the localStorage string-only contract.
const SERVER_KEY = Object.freeze({
  theme: "theme",
  accent: "accent",
  density: "density",
  shape: "shape",
  font: "font",
  model: "model",
  effort: "effort",
  sendOnEnter: "send_on_enter",
  showReasoning: "show_reasoning",
  suggestFollowups: "suggest_followups",
});
const HOOK_KEY = Object.fromEntries(
  Object.entries(SERVER_KEY).map(([h, s]) => [s, h]),
);
const BOOLEAN_PREFS = new Set(["sendOnEnter", "showReasoning", "suggestFollowups"]);

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

// ---- Server sync ---------------------------------------------------------
//
// One-shot hydrate on first mount when a mgmt JWT is present; debounced
// per-key PUT on each set. Failures are swallowed — localStorage stays the
// canonical source of truth so the UI never blocks on network.

let hydrated = false;
let hydratePromise = null;
const pendingPuts = {};

function shouldHydrate() {
  try {
    return Boolean(localStorage.getItem("starter_mgmt_token"));
  } catch {
    return false;
  }
}

function applySnapshotPatch(patch) {
  const next = { ...snapshot, ...patch };
  snapshot = Object.freeze(next);
  listeners.forEach((l) => l());
}

async function hydrateFromServer() {
  if (hydrated) return;
  if (hydratePromise) return hydratePromise;
  hydratePromise = (async () => {
    try {
      const { getPrefs } = await import("../api.js");
      const serverPrefs = await getPrefs();
      const patch = {};
      for (const [serverKey, val] of Object.entries(serverPrefs || {})) {
        const hookKey = HOOK_KEY[serverKey];
        if (!hookKey) continue;
        const stringVal = typeof val === "boolean" ? (val ? "1" : "0") : String(val);
        patch[hookKey] = stringVal;
        writePref(STORAGE_KEYS[hookKey], stringVal);
      }
      if (Object.keys(patch).length) applySnapshotPatch(patch);
    } catch {
      /* localStorage stays canonical; degrade silently */
    } finally {
      hydrated = true;
    }
  })();
  return hydratePromise;
}

function schedulePutPref(name, value) {
  if (!shouldHydrate()) return;
  clearTimeout(pendingPuts[name]);
  pendingPuts[name] = setTimeout(async () => {
    delete pendingPuts[name];
    try {
      const { putPrefs } = await import("../api.js");
      const serverKey = SERVER_KEY[name];
      const serverVal = BOOLEAN_PREFS.has(name) ? value === "1" : value;
      await putPrefs({ [serverKey]: serverVal });
    } catch {
      /* swallow; localStorage already updated */
    }
  }, 200);
}

function setPref(name, value) {
  if (snapshot[name] === value) return;
  snapshot = Object.freeze({ ...snapshot, [name]: value });
  writePref(STORAGE_KEYS[name], value);
  listeners.forEach((l) => l());
  schedulePutPref(name, value);
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

// Test-only: clear the server-sync state machine so tests can re-trigger
// hydrate / debounce flow.
export function __resetServerSyncForTest() {
  hydrated = false;
  hydratePromise = null;
  Object.keys(pendingPuts).forEach((k) => {
    clearTimeout(pendingPuts[k]);
    delete pendingPuts[k];
  });
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

  // One-shot server hydrate. Gated on a mgmt JWT so the marketing site
  // mounts of useChannelPrefs (for site theme) don't fire 401-spam.
  useEffect(() => {
    if (shouldHydrate()) hydrateFromServer();
  }, []);

  const setTheme = useCallback((v) => setPref("theme", v), []);
  const setAccent = useCallback((v) => setPref("accent", v), []);
  const setDensity = useCallback((v) => setPref("density", v), []);
  const setShape = useCallback((v) => setPref("shape", v), []);
  const setFont = useCallback((v) => setPref("font", v), []);
  const setModel = useCallback((v) => setPref("model", v), []);
  const setEffort = useCallback((v) => setPref("effort", v), []);
  const setSendOnEnter = useCallback((v) => setPref("sendOnEnter", v ? "1" : "0"), []);
  const setShowReasoning = useCallback((v) => setPref("showReasoning", v ? "1" : "0"), []);
  const setSuggestFollowups = useCallback(
    (v) => setPref("suggestFollowups", v ? "1" : "0"),
    [],
  );
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
    sendOnEnter: current.sendOnEnter === "1",
    setSendOnEnter,
    showReasoning: current.showReasoning === "1",
    setShowReasoning,
    suggestFollowups: current.suggestFollowups === "1",
    setSuggestFollowups,
  };
}
