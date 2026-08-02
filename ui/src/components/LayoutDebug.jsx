// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useCallback, useEffect, useState } from "react";
import {
  APP_VH_ATTRIBUTE,
  APP_VH_PROPERTY,
  measureAppViewport,
} from "../lib/appViewport.js";

/**
 * TEMPORARY on-device layout readout for #467 — remove once the dead
 * strip below the composer is confirmed fixed on a real installed iOS
 * PWA. Tracked for removal by the follow-up issue linked from #467.
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
 * Rendered only when the URL carries `?__layout-debug=1`, and the gate
 * lives in the *caller* (`App.jsx`), so without the parameter this
 * component never mounts: no state, no listeners, no DOM, no measuring.
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
export function isLayoutDebugRequested() {
  return new URLSearchParams(window.location.search).get(LAYOUT_DEBUG_PARAM) === "1";
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
        <button type="button" className="layout-debug-refresh" onClick={refresh}>
          Re-measure
        </button>
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
