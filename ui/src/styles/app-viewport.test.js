// Copyright (c) 2026 John Carter. All rights reserved.
//
// Static-stylesheet regression guard for the app layer's viewport sizing
// (#467) and the safe-area insets it sits alongside (#425 / #437).
//
// There is no JS module behind any of this — it is declarations in
// `app.css` — so, exactly as `ui/src/pwa.test.js` does for the PWA's
// static assets, the acceptance criteria are asserted against the file on
// disk. jsdom is deliberately NOT used as the oracle here: it implements
// neither viewport units nor `env()`, so `getComputedStyle` would report a
// value the browser never computes and the assertion would be theatre.
// Real-browser confirmation is a manual step (see the PR for #467).
//
// What this pins:
//   1. The `100vh` → `100dvh` fallback pair on `.stage`, in that order.
//   2. That the pair is NOT trapped inside the ≤640px mobile block — a
//      notched iPhone in landscape is ~844px wide and renders the desktop
//      layout, so it needs the same dynamic height.
//   3. That `.stage` keeps `position: fixed` + `inset: 0` and never grows a
//      `100vw` width (which would add the scrollbar gutter on desktop).
//   4. That the `env(safe-area-inset-*)` padding survives — it solves
//      notch / home-indicator clearance, a different and already-correct
//      problem that #467 must not regress.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Deliberately NOT `new URL("./app.css", import.meta.url)`, the shape
// `ui/src/pwa.test.js` uses. Vite statically rewrites that exact literal
// pattern into a bundled asset URL, and for a file inside `src/` the result
// is a non-`file:` URL that `readFileSync` rejects. (`pwa.test.js` escapes
// this because everything it reads — `index.html`, `public/*` — lives
// outside `src/`.) Composing the path defeats the static match.
const appCss = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "app.css"),
  "utf8",
);

/**
 * Comment-stripped stylesheet. Every assertion below runs against this
 * rather than the raw text, so the long explanatory comment above the
 * `.stage` rule (which necessarily quotes `100dvh`, `inset: 0` and
 * `100vw`) can never satisfy — or falsely fail — an assertion.
 */
const css = appCss.replace(/\/\*[\s\S]*?\*\//g, "");

/** Body of the top-level `.stage { ... }` rule. */
function stageRule() {
  // `^` + multiline pins this to the un-indented, top-level rule, so
  // neither `.stage.full` nor any media-query-nested `.stage` can match.
  const match = css.match(/^\.stage\s*\{([^}]*)\}/m);
  return match[1];
}

/** Body of the `@media (max-width: 640px)` mobile block. */
function mobileBlock() {
  const start = css.indexOf("@media (max-width: 640px)");
  // Nested rules mean a naive `[^}]*` stops early; walk the braces instead.
  let depth = 0;
  for (let i = css.indexOf("{", start); i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(start, i + 1);
    }
  }
  throw new Error("unterminated @media (max-width: 640px) block");
}

describe(".stage viewport height (#467)", () => {
  it("declares 100vh before 100dvh so the fallback resolves correctly", () => {
    const heights = [...stageRule().matchAll(/height:\s*([^;]+);/g)].map(
      (m) => m[1].trim(),
    );

    // Order is the whole mechanism: an engine without `dvh` support drops
    // the second declaration and keeps `100vh`; one with support takes the
    // second because it comes later. Reversing these silently disables the
    // fix on every modern iOS device.
    expect(heights).toEqual(["100vh", "100dvh"]);
  });

  it("keeps the fix outside the mobile block so landscape standalone gets it", () => {
    // A notched iPhone in landscape is ~844px wide and therefore renders
    // the desktop layout, where the ≤640px block is inactive.
    // NB: the length unit must be matched as `\d+dvh` — `\bdvh` never
    // matches `100dvh`, because `0`→`d` is not a word boundary, which
    // would make the negative assertion below silently vacuous.
    expect(mobileBlock()).not.toMatch(/\d+dvh\b/);
    expect(stageRule()).toMatch(/\d+dvh\b/);
  });

  it("still pins the layer to the viewport with position: fixed + inset: 0", () => {
    // `inset: 0` supplies top/left/right; `bottom` is intentionally
    // over-constrained away by the explicit height. Dropping `inset`
    // altogether would unpin the layer entirely.
    expect(stageRule()).toMatch(/position:\s*fixed/);
    expect(stageRule()).toMatch(/inset:\s*0/);
  });

  it("never sizes width from the viewport", () => {
    // `100vw` includes the scrollbar gutter, so it would overflow the
    // desktop layout by the scrollbar width. Width must stay implied by
    // the left/right insets.
    expect(stageRule()).not.toMatch(/width:/);
    expect(css).not.toMatch(/\b100(?:d|s|l)?vw\b/);
  });
});

describe("safe-area insets survive the viewport change (#425 / #437)", () => {
  it("keeps the mobile block's safe-area padding", () => {
    const mobile = mobileBlock();
    // The four rules #437 added, each still additive — `calc(<gutter> +
    // env(...))` rather than `max()`, so the layout's own spacing is kept
    // on top of the inset.
    expect(mobile).toMatch(/height:\s*calc\(52px \+ env\(safe-area-inset-top\)\)/);
    expect(mobile).toMatch(
      /padding-bottom:\s*calc\(14px \+ env\(safe-area-inset-bottom\)\)/,
    );
    expect(mobile).toMatch(/padding-top:\s*env\(safe-area-inset-top\)/);
    expect(mobile).toMatch(/padding-left:\s*env\(safe-area-inset-left\)/);
  });

  it("keeps the ≥641px landscape/tablet companion block", () => {
    // The `min-width: 641px` block is what clears the notch in landscape
    // standalone, the same viewport the dvh fix above targets.
    expect(css).toMatch(/@media \(min-width: 641px\)/);
    expect(css).toMatch(
      /padding-bottom:\s*calc\(18px \+ env\(safe-area-inset-bottom\)\)/,
    );
  });
});
