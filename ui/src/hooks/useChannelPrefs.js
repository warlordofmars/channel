// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useSyncExternalStore } from "react";
import { readToken } from "../lib/auth.js";

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
  model:             "claude-sonnet-4-6",
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
// per-key PUT on each set.
//
// **A rejected PUT is neither discarded nor silent (#482).** It used to be
// both — `catch { /* swallow */ }` — so localStorage showed a value the
// server had never accepted, with nothing anywhere saying so. #469 spent its
// entire investigation inside that gap: `suggest_followups` was correctly
// honoured server-side, but the write that was supposed to change it had
// been dropped on the floor, so the toggle read as dead wiring.
//
// localStorage is still canonical and the UI still never blocks on the
// network — a pref changed offline must keep working. What changed is that
// the unconfirmed value is *kept* (`unconfirmedWrites`), re-sent with
// bounded backoff, and published on a store the hook returns as
// `prefsSyncError`.
//
// Rolling the local value back on failure was considered and rejected: it
// takes the network's word over the user's and breaks every offline change.
// `test: PUT failure does not roll back local state` pins that choice.

let hydrated = false;
let hydratePromise = null;

/** Per-key timer slot, shared by the debounce and the retry backoff. */
const pendingPuts = {};

/** Debounce before a changed pref is sent, collapsing rapid changes. */
const PUT_DEBOUNCE_MS = 200;

/**
 * Backoff before each automatic re-attempt of a failed write.
 *
 * Two retries, then the value waits for `retryPrefsSync()` or for the next
 * change to the same key. Bounded on purpose: an endpoint that has refused
 * twice is not usually one more immediate attempt away from succeeding, and
 * every attempt can drag a token renewal along behind it.
 */
export const PUT_RETRY_DELAYS_MS = Object.freeze([1_000, 5_000]);

/**
 * Hook-key → value for every write the server has not confirmed.
 *
 * This is what "not silently discarded" means mechanically: a rejected PUT
 * leaves its value here and `retryPrefsSync()` re-sends whatever is still
 * outstanding. Entries clear only on a 2xx.
 */
const unconfirmedWrites = {};

/** Per-key count of attempts already spent, so the backoff terminates. */
const putAttempts = {};

function shouldHydrate() {
  try {
    return Boolean(readToken());
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

// ---- Write-failure store -------------------------------------------------
//
// A second `useSyncExternalStore` source, deliberately separate from the
// prefs snapshot: sync health is not a preference, and folding it into
// `snapshot` would put a non-pref key in a structure that is otherwise
// exactly `STORAGE_KEYS` and round-trips through localStorage.

let syncSnapshot = Object.freeze({ error: null });
const syncListeners = new Set();

function subscribeSync(listener) {
  syncListeners.add(listener);
  return () => syncListeners.delete(listener);
}

function getSyncSnapshot() {
  return syncSnapshot;
}

function publishSyncSnapshot(error) {
  syncSnapshot = Object.freeze({ error });
  syncListeners.forEach((l) => l());
}

/**
 * Hook-key → HTTP status of its most recent failed write (null for a
 * transport-level throw, which has no status).
 *
 * Per-key rather than one shared "last status": several prefs can be
 * outstanding at once, and a later transient 500 must not overwrite an
 * earlier 401 and quietly downgrade "this needs you to sign in again" to
 * "we'll retry it".
 */
const failedWrites = new Map();

function publishWriteFailures() {
  if (!failedWrites.size) {
    publishSyncSnapshot(null);
    return;
  }
  publishSyncSnapshot(
    Object.freeze({
      keys: Object.freeze([...failedWrites.keys()]),
      statuses: Object.freeze(Object.fromEntries(failedWrites)),
      // A 401 is not a blip: the session is no longer usable, so a timer
      // retry would only spend requests (and, through `authHeader`, token
      // renewals) to be refused again. Those writes are held for a manual
      // retry once the caller has re-authenticated. True if ANY outstanding
      // write is in that state — one 401 among transient failures still
      // means the session, not the network, is what needs attention.
      unauthorized: [...failedWrites.values()].some(isUnauthorized),
    }),
  );
}

function isUnauthorized(status) {
  return status === 401;
}

function recordWriteFailure(name, status) {
  failedWrites.set(name, status);
  publishWriteFailures();
}

function clearWriteFailure(name) {
  if (failedWrites.delete(name)) publishWriteFailures();
}

/** HTTP status carried by an `ApiError`; null for a transport-level throw. */
function statusOf(err) {
  return typeof err?.status === "number" ? err.status : null;
}

function scheduleFlush(name, delay) {
  pendingPuts[name] = setTimeout(function flushTimer() {
    delete pendingPuts[name];
    void flushPref(name);
  }, delay);
}

async function flushPref(name) {
  try {
    const { putPrefs } = await import("../api.js");
    const value = unconfirmedWrites[name];
    const serverVal = BOOLEAN_PREFS.has(name) ? value === "1" : value;
    await putPrefs({ [SERVER_KEY[name]]: serverVal });
    delete unconfirmedWrites[name];
    delete putAttempts[name];
    clearWriteFailure(name);
  } catch (err) {
    const status = statusOf(err);
    recordWriteFailure(name, status);
    const delay = status === 401 ? undefined : PUT_RETRY_DELAYS_MS[putAttempts[name]];
    putAttempts[name] += 1;
    if (delay !== undefined) scheduleFlush(name, delay);
  }
}

function schedulePutPref(name, value) {
  if (!shouldHydrate()) return;
  // Latest value wins, and a fresh change supersedes any retry still
  // pending for the same key — both share the one timer slot.
  unconfirmedWrites[name] = value;
  putAttempts[name] = 0;
  clearTimeout(pendingPuts[name]);
  scheduleFlush(name, PUT_DEBOUNCE_MS);
}

/**
 * Re-send every write the server has not confirmed.
 *
 * The escape hatch for the two cases automatic backoff deliberately does
 * not cover: a 401 (retry after re-authenticating) and a failure that
 * outlived the retry budget.
 */
export async function retryPrefsSync() {
  const names = Object.keys(unconfirmedWrites);
  for (const name of names) {
    clearTimeout(pendingPuts[name]);
    delete pendingPuts[name];
    putAttempts[name] = 0;
  }
  await Promise.all(names.map((name) => flushPref(name)));
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
  Object.keys(unconfirmedWrites).forEach((k) => delete unconfirmedWrites[k]);
  Object.keys(putAttempts).forEach((k) => delete putAttempts[k]);
  failedWrites.clear();
  // Publish rather than assign, so a hook mounted before the reset re-reads
  // the cleared state instead of rendering a stale error. `syncListeners` is
  // deliberately NOT cleared for the same reason.
  publishWriteFailures();
}

export function useChannelPrefs() {
  const current = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const sync = useSyncExternalStore(subscribeSync, getSyncSnapshot, getSyncSnapshot);
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
    // `null` while every change has been confirmed by the server; otherwise
    // `{ keys, statuses, unauthorized }` naming the prefs that did not stick
    // (#482). A settings UI must not report success it cannot confirm.
    prefsSyncError: sync.error,
    retryPrefsSync,
  };
}
