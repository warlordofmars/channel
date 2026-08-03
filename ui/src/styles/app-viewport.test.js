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
//   5. That the mobile `.home` block declares no `justify-content` of
//      its own, so the empty state keeps the base rule's vertical
//      centring. Two earlier #467 attempts overrode it (`flex-start`,
//      then `flex-start` + an auto top margin on the composer) and both
//      were rejected on-device; this pins the mobile block out of that
//      property entirely.
//   6. That the mobile conversation composer is flush to the bottom
//      edge — no bottom padding on `.bottom-composer`, with the
//      home-indicator clearance moved inside `.bottom-composer
//      .composer` so the surface reaches y=viewport-height while its
//      contents still clear the indicator. Measured on an installed
//      iOS PWA (393x793, inset 34px): `.bottom-composer` already ended
//      at 793 but `.composer` stopped at 745, and that 48px strip of
//      bare canvas is the gap #467 is actually about.
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

/**
 * Selector prelude of the rule containing character offset `i` — the text
 * between the previous brace of either kind and the `{` that opens the rule.
 *
 * This exists because the two scoping guards below search from the
 * *declaration* outwards — they start at every matching declaration and ask
 * which selector owns it — rather than from a selector known to mention the
 * class of interest. Inverting the search is the point: a rule carrying the
 * declaration cannot escape the check by having a selector shape the pattern
 * didn't anticipate. Recovering the owner then needs a brace walk rather than
 * a rule-shaped regex.
 *
 * Walking back to the nearest brace of EITHER kind is what keeps that correct
 * for a rule nested in an at-rule: for the first rule inside `@media ... {`
 * the preceding brace is the media block's own `{`, so the slice is still
 * just the selector.
 *
 * Callers split the result on commas and judge each compound selector on its
 * own, so a grouped rule (`.home .greet, .composer-wrap { ... }`) cannot
 * borrow a class from a sibling in the list to pass — that masking vector is
 * real and was verified by hand against an earlier whole-prelude form of the
 * check. (A comment naming the class is not a vector: `css` is
 * comment-stripped above.) `split(",")` is selector-list-naive — it would
 * also split inside `:is(.home, .convo) > .composer-wrap`. `app.css` uses no
 * `:is()` or `:where()` today, and the failure direction is safe: it flags a
 * correct rule rather than passing a broken one.
 */
function selectorOfRuleAt(i) {
  const open = css.lastIndexOf("{", i);
  const prev = Math.max(
    css.lastIndexOf("{", open - 1),
    css.lastIndexOf("}", open - 1),
  );
  return css.slice(prev + 1, open);
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

describe("mobile home layout — vertically centred as one group (#467)", () => {
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

  it("never overrides the base rule's vertical centring", () => {
    // The wanted layout is the centred stack the base rule already
    // produces, so the mobile block's job is to stay out of this
    // property entirely. Asserting the *absence* of any
    // `justify-content` — rather than the presence of `center` — is what
    // makes this catch the next variant too: `flex-start`, `flex-end`
    // and `space-between` would each fail without needing to be named.
    expect(mobileHomeRules().join("\n")).not.toMatch(/justify-content:/);
  });

  it("keeps the greeting and the composer in one un-split group", () => {
    // An auto margin on any child of `.home` absorbs the free space that
    // the centring is made of, splitting the stack: the greeting rides
    // to the top and the composer to the bottom. That was PR #507's
    // `.home > .composer-wrap { margin-top: auto }`, rejected on-device.
    // The check is file-wide and declaration-first — it starts from
    // every `margin-top: auto` in `app.css` and rejects the ones a
    // `.home` selector owns — so a differently-shaped selector
    // (`.home .quick`, `.home > *`) cannot slip past a `.composer-wrap`
    // -shaped pattern.
    const homeAnchors = [...css.matchAll(/margin-top:\s*auto/g)]
      .map((m) => selectorOfRuleAt(m.index))
      .flatMap((selectorList) => selectorList.split(","))
      .map((selector) => selector.trim())
      .filter((s) => s.includes(".home"));
    expect(homeAnchors).toEqual([]);
  });

  it("keeps the home-indicator clearance under the centred stack", () => {
    // ChatHome's composer lives inside `.home`, not `.bottom-composer`,
    // so — unlike the conversation composer below — it is not flush to
    // the screen edge and still takes its clearance from the container.
    expect(mobileHomeRules().join("\n")).toMatch(
      /padding-bottom:\s*calc\(12px \+ env\(safe-area-inset-bottom\)\)/,
    );
  });

  it("leaves the desktop .home rule centred and un-anchored", () => {
    // The whole change lives inside the ≤640px block. The base rule must
    // keep `justify-content: center`, and no top-level rule may carry an
    // auto margin — either would move the desktop composer.
    const base = css.match(/^\.home\s*\{([^}]*)\}/m);
    expect(base).not.toBeNull();
    expect(base[1]).toMatch(/justify-content:\s*center/);
    expect(base[1]).not.toMatch(/margin-top:/);
  });
});

describe("mobile conversation composer flush to the bottom edge (#467)", () => {
  /**
   * Every `.bottom-composer { ... }` rule body inside the ≤640px block —
   * the gutter rule and the `env(safe-area-inset-*)` rule appended
   * further down the same block. Returned in source order, because which
   * declaration wins here is a cascade question.
   */
  function mobileBottomComposerRules() {
    // `\s*\{` immediately after the class is what keeps the descendant
    // rules (`.bottom-composer .composer`, `.bottom-composer
    // .composer-wrap`) out of this list — their selectors continue past
    // the class rather than opening a block.
    const bodies = [
      ...mobileBlock().matchAll(/\.bottom-composer\s*\{([^}]*)\}/g),
    ].map((m) => m[1]);
    if (bodies.length === 0) {
      throw new Error(
        "no `.bottom-composer { ... }` rule inside @media (max-width: 640px) " +
          "— the mobile conversation composer strip was renamed or removed; " +
          "see #467",
      );
    }
    return bodies;
  }

  it("leaves no bottom padding on the strip, so the surface reaches the edge", () => {
    // Last declaration wins at equal specificity, and the safe-area
    // sub-block is appended last — so the effective value is the final
    // `padding-bottom` any `.bottom-composer` rule in the block declares.
    const declared = [
      ...mobileBottomComposerRules().join("\n").matchAll(/padding-bottom:\s*([^;]+);/g),
    ].map((m) => m[1].trim());
    expect(declared.at(-1)).toBe("0");
  });

  it("moves the home-indicator clearance inside the composer's surface", () => {
    // Same 34px of clearance as before, just paid from inside the box:
    // 8px is the mobile `.composer` bottom padding, so the controls keep
    // their existing spacing and gain only the inset.
    expect(mobileBlock()).toMatch(
      /\.bottom-composer\s+\.composer\s*\{[^}]*padding-bottom:\s*calc\(8px \+ env\(safe-area-inset-bottom\)\)/,
    );
  });

  it("scopes the inner clearance so ChatHome's composer is untouched", () => {
    // A bare `.composer { padding-bottom: calc(... + env(...)) }` would
    // reach ChatHome's composer (inside `.home`) and ProjectDetail's,
    // neither of which sits at the screen edge — they would grow a
    // stray 34px of internal padding for nothing.
    const unscoped = [
      ...css.matchAll(/padding-bottom:\s*calc\([^;]*safe-area-inset-bottom[^;]*\);/g),
    ]
      .map((m) => selectorOfRuleAt(m.index))
      .flatMap((selectorList) => selectorList.split(","))
      .map((selector) => selector.trim())
      .filter((s) => /\.composer$/.test(s) && !s.includes(".bottom-composer"));
    expect(unscoped).toEqual([]);
  });

  it("leaves the desktop strip's bottom gutter alone", () => {
    // The base rule is what desktop renders; #467 is a ≤640px change.
    const base = css.match(/^\.bottom-composer\s*\{([^}]*)\}/m);
    expect(base).not.toBeNull();
    expect(base[1]).toMatch(/padding:\s*0 24px 18px/);
  });
});

describe("safe-area insets survive the viewport change (#425 / #437)", () => {
  it("keeps the mobile block's safe-area padding", () => {
    const mobile = mobileBlock();
    // The four rules #437 added, each still additive — `calc(<gutter> +
    // env(...))` rather than `max()`, so the layout's own spacing is kept
    // on top of the inset.
    //
    // The bottom inset the conversation composer takes is asserted by the
    // flush-to-the-edge block above, not here: #467 moved it from
    // `.bottom-composer` (`14px + inset`) onto `.bottom-composer .composer`
    // (`8px + inset`). The clearance survives — it is paid from inside the
    // surface now — so what this block still owns is `.home`'s copy, the
    // one container inset that did not move.
    expect(mobile).toMatch(/height:\s*calc\(52px \+ env\(safe-area-inset-top\)\)/);
    expect(mobile).toMatch(
      /padding-bottom:\s*calc\(12px \+ env\(safe-area-inset-bottom\)\)/,
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
