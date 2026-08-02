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
//   5. That the mobile `.home` block keeps BOTH of its anchors: the
//      greeting's `flex-start` top anchor and the composer's
//      `margin-top: auto` bottom anchor. This is the pair that actually
//      fixed #467 — the on-device measurement found no viewport
//      mismatch at all (`innerHeight` === `clientHeight` ===
//      `visualViewport.height` === 793), just children stacked at the
//      top of a correctly-sized container.
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

/**
 * Body of the top-level `.stage { ... }` rule.
 *
 * Throws a named error rather than letting a failed match surface as
 * `Cannot read properties of null` — if the selector is ever renamed the
 * reader should be told the rule vanished, not handed a TypeError from
 * inside a regex helper.
 */
function stageRule() {
  // `^` + multiline pins this to the un-indented, top-level rule, so
  // neither `.stage.full` nor any media-query-nested `.stage` can match.
  const match = css.match(/^\.stage\s*\{([^}]*)\}/m);
  if (!match) {
    throw new Error(
      "no top-level `.stage { ... }` rule in app.css — it was renamed or " +
        "removed; these #467 assertions need repointing at its replacement",
    );
  }
  return match[1];
}

/** Body of the `@media (max-width: 640px)` mobile block. */
function mobileBlock() {
  const start = css.indexOf("@media (max-width: 640px)");
  if (start === -1) {
    throw new Error(
      "no `@media (max-width: 640px)` block in app.css — the mobile section " +
        "was renamed or removed; see #425 / #437",
    );
  }
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
    // desktop layout by the scrollbar width. `.stage` width must stay
    // implied by the left/right insets.
    //
    // Both assertions are deliberately scoped to `.stage` rather than the
    // whole stylesheet: other rules use viewport-relative widths quite
    // legitimately (e.g. `min(84vw, 300px)` on the mobile drawer), and a
    // file-wide ban would fail on edits that have nothing to do with the
    // app layer's height.
    expect(stageRule()).not.toMatch(/width:/);
    expect(stageRule()).not.toMatch(/\d+(?:d|s|l)?vw\b/);
  });
});

describe("standalone viewport correction (#467, reopened)", () => {
  /**
   * The one rule that consumes `--app-vh`. Kept as a named helper for the
   * same reason as `stageRule()` — a rename should read as "the rule
   * vanished", not as a null dereference three assertions later.
   */
  function correctionRule() {
    const match = css.match(/html\[data-app-vh\]\s+\.stage\s*\{([^}]*)\}/);
    if (!match) {
      throw new Error(
        "no `html[data-app-vh] .stage { ... }` rule in app.css — the #467 " +
          "standalone correction was renamed or removed; see ui/src/lib/appViewport.js",
      );
    }
    return match[1];
  }

  it("overrides .stage's height from the measured custom property", () => {
    expect(correctionRule()).toMatch(/height:\s*var\(--app-vh\)/);
  });

  it("gates the override on the attribute, never on the property alone", () => {
    // A rule reading `var(--app-vh)` unconditionally would apply on every
    // browser — falling back to `auto` where the property is unset, which
    // collapses the fixed layer. The attribute selector is what keeps an
    // untouched document on the original `100vh` / `100dvh` cascade, so it
    // is the load-bearing half of the desktop-unchanged guarantee.
    expect(css).not.toMatch(/(?<!html\[data-app-vh\]\s)\.stage\s*\{[^}]*var\(--app-vh\)/);
  });

  it("changes only the height, so position and insets stay with .stage", () => {
    expect(correctionRule()).not.toMatch(/position:/);
    expect(correctionRule()).not.toMatch(/inset:/);
    expect(correctionRule()).not.toMatch(/width:/);
  });

  it("is not trapped inside a width media query", () => {
    // An installed PWA on an iPad renders the ≥641px layout and is
    // equally affected; the correct gate is the measured shortfall in
    // appViewport.js, not the viewport width.
    expect(mobileBlock()).not.toMatch(/var\(--app-vh\)/);
  });
});

describe("mobile home layout — greeting top, composer bottom (#467)", () => {
  /**
   * Every `.home { ... }` rule body inside the ≤640px block. There is
   * more than one — the layout rule here and the `env(safe-area-inset-*)`
   * rule appended further down the same block — so this returns them all
   * rather than picking one and silently asserting against the wrong half.
   */
  function mobileHomeRules() {
    const bodies = [...mobileBlock().matchAll(/\.home\s*\{([^}]*)\}/g)].map(
      (m) => m[1],
    );
    if (bodies.length === 0) {
      throw new Error(
        "no `.home { ... }` rule inside @media (max-width: 640px) — the " +
          "mobile home layout was renamed or removed; see #467",
      );
    }
    return bodies;
  }

  /** Body of the mobile block's `.home > .composer-wrap { ... }` rule. */
  function mobileHomeComposerRule() {
    const match = mobileBlock().match(
      /\.home\s*>\s*\.composer-wrap\s*\{([^}]*)\}/,
    );
    if (!match) {
      throw new Error(
        "no `.home > .composer-wrap { ... }` rule inside " +
          "@media (max-width: 640px) — the #467 bottom anchor was renamed " +
          "or removed, which parks the composer in the upper third again",
      );
    }
    return match[1];
  }

  it("keeps the greeting anchored to the top of the container", () => {
    // `flex-start` is half the fix, not the bug: it is what stops the
    // greeting drifting to the vertical centre. Reverting it to `center`
    // would re-centre the whole stack and undo the 8vh anchor.
    expect(mobileHomeRules().join("\n")).toMatch(
      /justify-content:\s*flex-start/,
    );
  });

  it("pushes the composer (and the quick actions after it) to the bottom", () => {
    // `.home`'s children are `.greet` → `.composer-wrap` → `.quick`, so an
    // auto top margin on the middle child absorbs every pixel of free
    // space above it and carries `.quick` down with it. Without this the
    // measured layout on a 793px iPhone ended at y=341 (#467).
    expect(mobileHomeComposerRule()).toMatch(/margin-top:\s*auto/);
  });

  it("scopes the bottom anchor to .home so the chat view is untouched", () => {
    // `.bottom-composer .composer-wrap` is already bottom-anchored by its
    // own container; an unscoped `.composer-wrap { margin-top: auto }`
    // would reach it (and ProjectDetail's composer) and push those around
    // too. Every `.composer-wrap` rule carrying the auto margin must name
    // `.home` in its selector.
    const unscoped = [
      ...css.matchAll(/([^{}]*\.composer-wrap[^{}]*)\{([^}]*)\}/g),
    ].filter(
      (m) => /margin-top:\s*auto/.test(m[2]) && !m[1].includes(".home"),
    );
    expect(unscoped.map((m) => m[1].trim())).toEqual([]);
  });

  it("keeps the home-indicator clearance beneath the bottomed composer", () => {
    // Now that the composer sits at the bottom edge, this padding is what
    // stands between it and the home indicator — #437's rule, unchanged,
    // and load-bearing for a reason it was not originally written for.
    expect(mobileHomeRules().join("\n")).toMatch(
      /padding-bottom:\s*calc\(12px \+ env\(safe-area-inset-bottom\)\)/,
    );
  });

  it("leaves the desktop .home rule centred and un-anchored", () => {
    // The whole change lives inside the ≤640px block. The base rule must
    // keep `justify-content: center`, and no top-level rule may carry the
    // auto margin — either would move the desktop composer.
    const base = css.match(/^\.home\s*\{([^}]*)\}/m);
    expect(base).not.toBeNull();
    expect(base[1]).toMatch(/justify-content:\s*center/);
    expect(base[1]).not.toMatch(/margin-top:/);
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
