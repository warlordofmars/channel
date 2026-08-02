// Copyright (c) 2026 John Carter. All rights reserved.
import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {
  APP_VH_ATTRIBUTE,
  APP_VH_PROPERTY,
  measureAppViewport,
} from "../lib/appViewport.js";

/**
 * TEMPORARY on-device layout readout for #467 — remove once the dead
 * strip below the composer is confirmed fixed on a real installed iOS
 * PWA. **Removal is tracked by #503**, which lists every file this
 * touches and what stays behind (the `lib/appViewport.js` correction is
 * the fix, not instrumentation, and does not come out with it).
 *
 * ## Why this exists
 *
 * The strip reproduces *only* in an installed iOS PWA: not in a desktop
 * browser, not in an emulated viewport, and not even in mobile Safari,
 * where the browser chrome occupies the same region and hides it. There
 * is no Web Inspector on the target device, so every blind CSS attempt
 * costs a full deploy-and-screenshot round trip. One instrumented look
 * settles what three guesses cannot.
 *
 * ## Contract
 *
 * Two gates, OR'd — see `isLayoutDebugRequested`. Both live in the
 * *caller* (`App.jsx`), so with neither armed this component never
 * mounts: no state, no listeners, no DOM, no measuring.
 *
 * 1. `?__layout-debug=1` on the URL — the desktop-browser path.
 * 2. A `localStorage` flag toggled by five taps within two seconds on
 *    the mobile top bar's brand mark (#504). **A query parameter can
 *    never be set inside an installed iOS PWA** — Add-to-Home-Screen
 *    drops it (the manifest's `start_url` wins), tapping a link opens
 *    the default browser instead of the standalone window, and there
 *    is no address bar to type into. Since the PWA is the only place
 *    #467 reproduces, the readout needs a gate reachable from inside
 *    the running app. The flag persists, so it survives navigation and
 *    relaunch; repeating the gesture disarms it.
 *
 * **It deliberately ships in the production bundle** rather than behind
 * `import.meta.env.DEV`. The bug exists only in the *installed* PWA,
 * which is by definition a production build served from the deployed
 * origin — a dev-only gate would make the readout unreachable in the one
 * environment that can produce the measurement. What it exposes is the
 * page's own public layout state (class names already in the DOM,
 * computed styles already readable from any console, viewport
 * dimensions): no tokens, no user data, no API state. Nothing here is
 * privileged, so the query-param gate is proportionate for the short
 * window this lives.
 *
 * With the parameter it covers the screen with a high-contrast
 * monospace readout of every number needed to tell the two candidate
 * causes apart — the window/layout-viewport disagreement that
 * `lib/appViewport.js` corrects, the resolved `env(safe-area-inset-*)`
 * values, and the box + computed styles of every element from the
 * composer up to `<html>`.
 *
 * Colours come from the token set (`--canvas` / `--ink` / `--accent` /
 * `--border`), same as everything else in `app.css`.
 */

/** Query parameter that arms the readout. */
export const LAYOUT_DEBUG_PARAM = "__layout-debug";

/** `localStorage` key holding the gesture-armed state (#504). */
export const LAYOUT_DEBUG_STORAGE_KEY = "channel:layout-debug";

/** Window event announcing a change to the armed state. */
export const LAYOUT_DEBUG_EVENT = "channel:layout-debug-change";

/**
 * Taps required to toggle, and the window they must all land in.
 *
 * Five-within-two-seconds is deliberate enough that no ordinary use of
 * the top bar reaches it — the brand mark is not a control, so the only
 * stray taps it sees are single mis-hits aimed at the hamburger beside
 * it — while still being describable in one sentence over the phone.
 */
export const LAYOUT_DEBUG_TAP_COUNT = 5;
export const LAYOUT_DEBUG_TAP_WINDOW_MS = 2000;

/** Insets read from the probe, in the order they are displayed. */
export const SAFE_AREA_SIDES = ["top", "right", "bottom", "left"];

/**
 * Computed properties reported for each element in the chain.
 *
 * Chosen to answer "which box stops short, and why": the sizing inputs
 * (`height` / `min-height` / `max-height` / `flex`), the stretch
 * decisions (`display` / `align-items` / `justify-content`), the
 * scroll-container pair (`overflow-y` with `min-height`, whose absence
 * is a classic flex-child-refuses-to-shrink bug), and the bottom
 * spacing (`padding-bottom` / `margin-bottom` / `bottom`) that a
 * double-applied safe-area inset would show up in.
 */
export const CHAIN_PROPS = [
  "position",
  "display",
  "height",
  "min-height",
  "max-height",
  "flex",
  "align-items",
  "justify-content",
  "overflow-y",
  "top",
  "bottom",
  "padding-top",
  "padding-bottom",
  "margin-bottom",
  "box-sizing",
];

/**
 * Where the chain starts, most specific first.
 *
 * `.bottom-composer .composer` is the Conversation view's composer (the
 * one in the bug report); `.home .composer` is ChatHome's, which lives
 * in a different parent; the bare `.composer` and `.stage` are
 * last-resort anchors so the readout still reports something useful on
 * a route with no composer at all.
 */
export const CHAIN_ROOT_SELECTORS = [
  ".bottom-composer .composer",
  ".home .composer",
  ".composer",
  ".stage",
];

/** True when the current URL asks for the readout. */
export function isLayoutDebugParamRequested() {
  return new URLSearchParams(window.location.search).get(LAYOUT_DEBUG_PARAM) === "1";
}

/**
 * Armed state when `localStorage` refuses to hold it, else `null`.
 *
 * Once storage has proven unusable it stops being the source of truth —
 * a blocked write leaves nothing for the next read to find, so the gate
 * would report disarmed a moment after the gesture said otherwise.
 * Holding the value here keeps the toggle honest for the page's life,
 * which is the whole of what a device with blocked storage can offer.
 */
let inMemoryArmed = null;

/** Drop the fallback so storage is authoritative again. Tests only. */
export function __resetLayoutDebugFallbackForTest() {
  inMemoryArmed = null;
}

/**
 * True when the tap gesture has armed the readout.
 *
 * Storage access is guarded because this runs during `App`'s render:
 * Safari with site data blocked throws on `localStorage`, and a throw
 * here would take the whole app down rather than one debug readout.
 */
export function isLayoutDebugArmed() {
  if (inMemoryArmed !== null) return inMemoryArmed;
  try {
    return localStorage.getItem(LAYOUT_DEBUG_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Either gate — the URL parameter or the persisted tap-gesture flag. */
export function isLayoutDebugRequested() {
  return isLayoutDebugParamRequested() || isLayoutDebugArmed();
}

/**
 * Flip the persisted flag and announce it. Returns the new state.
 *
 * The announcement is a plain window event rather than a React context
 * so the gesture's owner (`Shell`) and the readout's mount site (`App`)
 * stay decoupled — `App` is above the router, `Shell` is well below it.
 */
function writeArmed(armed) {
  try {
    if (armed) localStorage.setItem(LAYOUT_DEBUG_STORAGE_KEY, "1");
    else localStorage.removeItem(LAYOUT_DEBUG_STORAGE_KEY);
    inMemoryArmed = null;
  } catch {
    // Storage refused the write, so fall back to memory: the toggle
    // still applies for this page life, it just won't survive a
    // relaunch. Swallowing the tap outright would be worse — a device
    // with blocked storage is exactly where this readout is needed.
    inMemoryArmed = armed;
  }
}

export function toggleLayoutDebugArmed() {
  const armed = !isLayoutDebugArmed();
  writeArmed(armed);
  window.dispatchEvent(new Event(LAYOUT_DEBUG_EVENT));
  return armed;
}

/**
 * Turn **both** gates off — what the readout's own Close button does.
 *
 * This is the only way out from inside an installed PWA. The readout is
 * `position: fixed; inset: 0; z-index: 9999` over an opaque background,
 * so once it mounts it covers the brand mark and the arming gesture
 * cannot be repeated to undo itself; there is no address bar to edit
 * either, and the gate is `param || armed`, so dropping the parameter
 * alone could never subtract a persisted flag anyway. Without this,
 * arming the readout on a phone would be a one-way trip out of the app.
 *
 * The parameter is cleared through `replaceState` rather than a reload,
 * so closing the readout doesn't discard the page it was measuring.
 */
export function disarmLayoutDebug() {
  writeArmed(false);
  if (isLayoutDebugParamRequested()) {
    const url = new URL(window.location.href);
    url.searchParams.delete(LAYOUT_DEBUG_PARAM);
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }
  window.dispatchEvent(new Event(LAYOUT_DEBUG_EVENT));
}

/**
 * Notify `onChange` whenever either gate may have moved. `storage`
 * covers the other-tab case; the custom event covers this one.
 */
export function subscribeLayoutDebug(onChange) {
  window.addEventListener(LAYOUT_DEBUG_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return function unsubscribeLayoutDebug() {
    window.removeEventListener(LAYOUT_DEBUG_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/**
 * `isLayoutDebugRequested` as reactive state, so arming the readout
 * takes effect immediately instead of on the next reload — there is no
 * way to reload a standalone PWA without killing the app.
 */
export function useLayoutDebugRequested() {
  return useSyncExternalStore(subscribeLayoutDebug, isLayoutDebugRequested);
}

/**
 * Handler that toggles the readout after `LAYOUT_DEBUG_TAP_COUNT` taps
 * inside `LAYOUT_DEBUG_TAP_WINDOW_MS`.
 *
 * Timestamps in a ref rather than a reset timer: nothing is scheduled,
 * so an abandoned half-gesture costs nothing and there is no timeout to
 * tear down. Stale taps are dropped on the next tap instead.
 */
export function useLayoutDebugTapGesture() {
  const taps = useRef([]);
  return useCallback(function onLayoutDebugTap() {
    const now = Date.now();
    const recent = taps.current.filter(function withinWindow(at) {
      return now - at < LAYOUT_DEBUG_TAP_WINDOW_MS;
    });
    recent.push(now);
    if (recent.length < LAYOUT_DEBUG_TAP_COUNT) {
      taps.current = recent;
      return;
    }
    taps.current = [];
    toggleLayoutDebugArmed();
  }, []);
}

/** One decimal place — enough to expose sub-pixel gaps, short enough to read. */
export function round(value) {
  return Math.round(value * 10) / 10;
}

/**
 * Resolve `env(safe-area-inset-*)` by measuring them.
 *
 * There is no API that reads an `env()` value directly, so the standard
 * trick applies: set each inset as padding on a throwaway probe and read
 * the computed padding back.
 */
export function readSafeAreaInsets() {
  const probe = document.createElement("div");
  probe.style.cssText = [
    "position:absolute",
    "visibility:hidden",
    "pointer-events:none",
    "top:0",
    "left:0",
    "width:0",
    "height:0",
    ...SAFE_AREA_SIDES.map((side) => `padding-${side}:env(safe-area-inset-${side},0px)`),
  ].join(";");
  document.body.appendChild(probe);
  const computed = window.getComputedStyle(probe);
  const insets = SAFE_AREA_SIDES.map(function readSide(side) {
    const raw = computed.getPropertyValue(`padding-${side}`);
    return [`env(safe-area-inset-${side})`, raw || "(empty)"];
  });
  probe.remove();
  return insets;
}

/** `div.bottom-composer.foo` — tag plus every class, dotted. */
export function elementLabel(el) {
  const className = typeof el.className === "string" ? el.className.trim() : "";
  const tag = el.tagName.toLowerCase();
  return className ? `${tag}.${className.split(/\s+/).join(".")}` : tag;
}

/** Box + the computed properties that decide that box. */
export function describeElement(el) {
  const rect = el.getBoundingClientRect();
  const computed = window.getComputedStyle(el);
  return {
    label: elementLabel(el),
    rect: `top=${round(rect.top)} bottom=${round(rect.bottom)} height=${round(rect.height)}`,
    styles: CHAIN_PROPS.map(function readProp(prop) {
      return `${prop}:${computed.getPropertyValue(prop)}`;
    }).join("  "),
  };
}

/** First selector in `CHAIN_ROOT_SELECTORS` that matches, or `null`. */
export function findChainRoot() {
  for (const selector of CHAIN_ROOT_SELECTORS) {
    const found = document.querySelector(selector);
    if (found) return found;
  }
  return null;
}

/** The element and every ancestor up to and including `<html>`. */
export function collectChain(start) {
  const chain = [];
  let node = start;
  while (node) {
    chain.push(describeElement(node));
    node = node.parentElement;
  }
  return chain;
}

/** `(display-mode: <mode>)` as a boolean. */
export function matchesDisplayMode(mode) {
  return window.matchMedia(`(display-mode: ${mode})`).matches;
}

/** Everything the readout renders, gathered in one pass. */
export function collectSnapshot() {
  const { windowHeight, layoutHeight } = measureAppViewport();
  const viewport = window.visualViewport;
  const root = document.documentElement;
  const start = findChainRoot();
  return {
    rows: [
      ["window.innerHeight", `${windowHeight}`],
      ["window.innerWidth", `${window.innerWidth}`],
      ["documentElement.clientHeight", `${layoutHeight}`],
      ["shortfall (inner − client)", `${windowHeight - layoutHeight}`],
      ["visualViewport.height", viewport ? `${round(viewport.height)}` : "(absent)"],
      ["visualViewport.offsetTop", viewport ? `${round(viewport.offsetTop)}` : "(absent)"],
      ["visualViewport.scale", viewport ? `${viewport.scale}` : "(absent)"],
      ["screen.height", `${window.screen.height}`],
      ["devicePixelRatio", `${window.devicePixelRatio}`],
      ["navigator.standalone", `${navigator.standalone}`],
      ["display-mode: standalone", `${matchesDisplayMode("standalone")}`],
      ["display-mode: browser", `${matchesDisplayMode("browser")}`],
      [APP_VH_PROPERTY, root.style.getPropertyValue(APP_VH_PROPERTY) || "(unset)"],
      [`html[${APP_VH_ATTRIBUTE}]`, `${root.hasAttribute(APP_VH_ATTRIBUTE)}`],
      ...readSafeAreaInsets(),
    ],
    chainRoot: start ? elementLabel(start) : "(nothing matched)",
    chain: start ? collectChain(start) : [],
  };
}

export default function LayoutDebug() {
  const [snapshot, setSnapshot] = useState(collectSnapshot);
  const refresh = useCallback(function refreshSnapshot() {
    setSnapshot(collectSnapshot());
  }, []);
  // Re-measure once after mount (the router subtree is in the DOM by
  // then, so the composer exists), and on every viewport change —
  // `visualViewport`'s resize is what iOS fires for keyboard show/hide,
  // which is the transition the original #467 report is about.
  useEffect(function trackViewport() {
    const viewport = window.visualViewport;
    refresh();
    window.addEventListener("resize", refresh);
    window.addEventListener("orientationchange", refresh);
    if (viewport) viewport.addEventListener("resize", refresh);
    return function untrackViewport() {
      window.removeEventListener("resize", refresh);
      window.removeEventListener("orientationchange", refresh);
      if (viewport) viewport.removeEventListener("resize", refresh);
    };
  }, [refresh]);
  return (
    <div className="layout-debug" role="region" aria-label="Layout debug readout">
      <div className="layout-debug-head">
        <span className="layout-debug-title">LAYOUT DEBUG · #467 · temporary</span>
        <span className="layout-debug-actions">
          <button type="button" className="layout-debug-refresh" onClick={refresh}>
            Re-measure
          </button>
          {/* The only way out on a phone — see `disarmLayoutDebug`. */}
          <button
            type="button"
            className="layout-debug-refresh"
            onClick={disarmLayoutDebug}
          >
            Close
          </button>
        </span>
      </div>
      <dl className="layout-debug-rows">
        {snapshot.rows.map(([label, value]) => (
          <div className="layout-debug-row" key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="layout-debug-chain-head">
        chain: {snapshot.chainRoot} → html
      </div>
      <ol className="layout-debug-chain">
        {snapshot.chain.map((entry, index) => (
          <li className="layout-debug-node" key={`${index}-${entry.label}`}>
            <span className="layout-debug-node-label">{entry.label}</span>
            <span className="layout-debug-node-rect">{entry.rect}</span>
            <span className="layout-debug-node-styles">{entry.styles}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
