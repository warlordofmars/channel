// Copyright (c) 2026 John Carter. All rights reserved.

/**
 * Standalone-viewport correction for the app layer (#467).
 *
 * ## The bug this exists for
 *
 * In an installed iOS PWA a dead strip appears below the composer — the
 * app layer stops short of the bottom of the screen and the identical
 * `var(--canvas)` background of `body` shows through, so it reads as
 * "empty app background" rather than as a short `.stage`.
 *
 * `.stage` is `position: fixed; inset: 0` with a `100vh` / `100dvh`
 * fallback pair. **Both of those size against the layout viewport** —
 * `inset` because a fixed element's containing block *is* the layout
 * viewport, and `dvh` because the viewport units resolve against it too.
 * So when iOS reports a layout viewport shorter than the window the app
 * actually occupies, every one of those declarations is short by the
 * same amount, which is exactly why removing the height (pre-#467) and
 * adding `100dvh` (#467/#471) produced the identical strip: they are
 * two spellings of the same measurement.
 *
 * Two known ways iOS produces that mismatch, and this module covers
 * both without needing to know which one fired:
 *
 * 1. **Standalone chrome accounting** — the installed window is
 *    full-screen, but the layout viewport is still computed as though
 *    the (absent) Safari toolbar were present, leaving a gap the height
 *    of the missing chrome.
 * 2. **Post-keyboard retraction** — the original #467 report. The
 *    software keyboard shrinks the layout viewport and it does not
 *    reliably re-expand on dismissal, so the fixed layer keeps the
 *    reduced height.
 *
 * ## The correction
 *
 * `window.innerHeight` is the one height iOS keeps pinned to the real
 * window: the software keyboard does not change it (only
 * `visualViewport.height` moves), and in standalone it is the
 * full-screen height. When it exceeds `documentElement.clientHeight`
 * (the layout viewport `inset: 0` / `dvh` resolve against), the app
 * layer is measurably short — so publish the true height as `--app-vh`
 * and let `html[data-app-vh] .stage` consume it.
 *
 * ## Why this cannot change desktop or mobile Safari
 *
 * The pin is **grow-only, and gated on a measured shortfall**:
 *
 * - **Desktop** — `innerHeight === documentElement.clientHeight`, so the
 *   shortfall is 0, nothing is published, and the `html[data-app-vh]`
 *   rule never matches. Byte-identical by construction, not by promise.
 * - **Mobile Safari** — `innerHeight` is the *visual* viewport (smaller,
 *   because the toolbars overlay it) while `clientHeight` is the layout
 *   viewport. The shortfall is negative, so again nothing is published.
 *   Shrinking `.stage` to the visual viewport would be a real rendering
 *   change, which is precisely what the grow-only rule forbids.
 * - **Installed PWA where the CSS was already right** — the two agree,
 *   nothing is published, no regression.
 *
 * The one place the shortfall can be non-zero outside iOS is a document
 * with a horizontal scrollbar (`clientHeight` excludes it,
 * `innerHeight` includes it). `--app-vh` is published there, but the
 * only rule that reads it is scoped to `.stage`, which exists solely on
 * `/app/*` routes where the layer is `position: fixed; overflow: hidden`
 * and can never produce a horizontal scrollbar.
 */

/** Custom property carrying the corrected app-layer height. */
export const APP_VH_PROPERTY = "--app-vh";

/**
 * Presence flag on `<html>` that arms the CSS rule.
 *
 * The custom property alone cannot gate a declaration — a rule reading
 * `var(--app-vh)` applies whether or not the property is set (falling
 * back to `auto`, which would collapse `.stage`). The attribute is what
 * makes the override strictly opt-in, so an untouched document keeps
 * the original `100vh` / `100dvh` cascade byte-for-byte.
 */
export const APP_VH_ATTRIBUTE = "data-app-vh";

/**
 * Minimum shortfall, in CSS px, before the height is pinned.
 *
 * Sub-pixel disagreement between the two APIs is normal on fractional
 * device-pixel ratios and is not the bug; requiring a whole pixel keeps
 * rounding noise from publishing a property on machines where nothing
 * is wrong.
 */
export const APP_VH_MIN_SHORTFALL_PX = 1;

/**
 * The two heights the correction compares.
 *
 * Split out so the debug readout (#467) and the sync below report from
 * one definition rather than two drifting copies.
 */
export function measureAppViewport() {
  return {
    windowHeight: window.innerHeight,
    layoutHeight: document.documentElement.clientHeight,
  };
}

/**
 * Publish (or withdraw) the corrected height. Returns the pinned height
 * in px, or `null` when no correction is warranted.
 *
 * Idempotent, and safe to call on every resize: it reads
 * `documentElement.clientHeight`, which is unaffected by the property
 * it writes, so a pinned document does not measure itself back to
 * "no shortfall" and oscillate.
 */
export function syncAppViewport() {
  const root = document.documentElement;
  const { windowHeight, layoutHeight } = measureAppViewport();
  if (windowHeight - layoutHeight >= APP_VH_MIN_SHORTFALL_PX) {
    root.style.setProperty(APP_VH_PROPERTY, `${windowHeight}px`);
    root.setAttribute(APP_VH_ATTRIBUTE, "");
    return windowHeight;
  }
  root.style.removeProperty(APP_VH_PROPERTY);
  root.removeAttribute(APP_VH_ATTRIBUTE);
  return null;
}

/**
 * Measure once, then keep the correction current. Returns the teardown
 * function, so it can be handed straight to `useEffect`.
 *
 * `visualViewport`'s `resize` is subscribed alongside `window`'s because
 * iOS fires it for keyboard show/hide without always firing a window
 * `resize` — which is the moment case 2 above needs re-measuring.
 * `pageshow` covers a restore from the back/forward cache, where the
 * layout viewport can come back stale.
 */
export function startAppViewportSync() {
  syncAppViewport();
  const viewport = window.visualViewport;
  window.addEventListener("resize", syncAppViewport);
  window.addEventListener("orientationchange", syncAppViewport);
  window.addEventListener("pageshow", syncAppViewport);
  if (viewport) viewport.addEventListener("resize", syncAppViewport);
  return function stopAppViewportSync() {
    window.removeEventListener("resize", syncAppViewport);
    window.removeEventListener("orientationchange", syncAppViewport);
    window.removeEventListener("pageshow", syncAppViewport);
    if (viewport) viewport.removeEventListener("resize", syncAppViewport);
  };
}
