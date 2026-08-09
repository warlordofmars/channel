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
//      edge — no bottom padding on `.bottom-composer`, and no rule
//      re-adding the home-indicator inset inside the composer's own
//      surface either. Measured on an installed iOS PWA (393x793,
//      inset 34px): `.bottom-composer` already ended at 793 but
//      `.composer` stopped at 745, and that 48px strip of bare canvas
//      is the gap #467 is actually about. #508 fixed it by moving the
//      clearance inside the surface, which just relocated the empty
//      strip *into* the box as 42px of grey, so #509 dropped the
//      clearance outright — the controls now sit ~8px above the screen
//      bottom, inside the home-indicator region, deliberately.
//   7. That the mobile control row keeps the send button flush to the
//      row's right edge **when the row wraps** — the case `.spacer`
//      alone does not cover (#571). Unlike 1-6 this is not a declaration
//      match: it lays the row out. See the section comment above that
//      block for what is real in it and what is modelled.
//   8. That no popover can be drawn outside the viewport it opens in
//      (#582). Like 7 this is an outcome, not a declaration match, and it
//      renders the real popovers. See the section comment above that block.
import { act, fireEvent, render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

// #571's assertions lay out the REAL `.composer-row`, which means rendering
// the real `Composer` — and that drags in the attachment client as a module
// import. `listModels` has to resolve because `ModelPicker` asks for the
// list at mount. `logout` is `Sidebar`'s, imported for #582's account
// popover; nothing below drives an upload or a sign-out, so these are
// stubs, not models.
vi.mock("../api.js", () => ({
  listModels: vi.fn().mockResolvedValue({ models: [] }),
  sha256Hex: vi.fn(),
  presignAttachment: vi.fn(),
  uploadToPresigned: vi.fn(),
  finalizeAttachment: vi.fn(),
  logout: vi.fn(),
}));

// `Sidebar` reads the chat list through this context (#582 renders it for
// the real `.sb-foot > .account-wrap > .pop` ancestry). Nothing below
// archives a chat.
vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => ({ archiveChat: vi.fn() }),
}));

import Composer from "../app/Composer.jsx";
import Sidebar from "../app/Sidebar.jsx";

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

/**
 * Does `selector` use `className` as a whole class token?
 *
 * `selector.includes(".home")` also matches `.home-row` and `.homepage`,
 * because `-` and alphanumerics are all valid class-name characters. The
 * negative lookahead is what makes this a token match rather than a substring
 * match. The un-split-group guard below uses it to *select* rules, where a
 * substring match would flag an unrelated `.home-row` rule — noisy, but safe.
 * (#508 had a second call site that used it to *exclude* rules, the unsafe
 * direction, where a substring match would silently skip a
 * `.not-bottom-composer .composer` rule; #509 removed the exclusion along
 * with the rule it exempted.)
 */
function usesClass(selector, className) {
  return new RegExp(`\\.${className}(?![\\w-])`).test(selector);
}

/**
 * The bottom component of a `padding` shorthand value.
 *
 * CSS shorthand arity: 1 value sets all four sides; 2 is `<block> <inline>`,
 * so the first is also the bottom; 3 and 4 both put the bottom third.
 *
 * The whitespace split is `calc()`-naive — `calc(1px + 1px)` would split into
 * three tokens. No `padding` shorthand in `app.css` uses `calc()` today (every
 * safe-area rule uses longhands precisely so the inset is legible), and the
 * failure direction is safe: a mis-split yields a token that isn't the
 * expected value, so the caller's assertion fails on a rule it could not read
 * rather than passing one it should have caught.
 */
function shorthandBottom(value) {
  const parts = value.trim().split(/\s+/);
  return parts.length >= 3 ? parts[2] : parts[0];
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
      .filter((s) => usesClass(s, "home"));
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

describe("mobile conversation composer flush to the bottom edge (#467, #509)", () => {
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

  /**
   * The *effective* `padding-bottom` on `.bottom-composer` at ≤640px.
   *
   * Deliberately resolved rather than read off one declaration. Two rules in
   * the block set this side — the gutter shorthand (`padding: 0 12px 14px`)
   * and the safe-area longhand (`padding-bottom: env(safe-area-inset-bottom)`,
   * #518) — at equal specificity,
   * so the winner is whichever comes last in source order. A guard that
   * matched only `padding-bottom:` would miss the shorthand entirely and go
   * green off the longhand alone; move the shorthand below the safe-area
   * sub-block and the rendered value would silently regress to `14px` with
   * the test none the wiser. Walking every declaration of either form, in
   * order, is what makes the assertion independent of that ordering.
   */
  function effectiveBottomPadding() {
    let value = null;
    for (const body of mobileBottomComposerRules()) {
      // `padding(-bottom)?:` cannot match `padding-left:` / `-right:` /
      // `-top:` — the optional group fails and the `:` then meets a `-`.
      for (const [, longhand, raw] of body.matchAll(
        /(?:^|;)\s*padding(-bottom)?:\s*([^;]+)/g,
      )) {
        value = longhand ? raw.trim() : shorthandBottom(raw);
      }
    }
    return value;
  }

  it("clears the home indicator beneath the strip (#518)", () => {
    // #510 drove this to 0 while the viewport was still mis-positioned and
    // the box stopped 59px short of the screen anyway. #517 fixed the
    // position, at which point flush read as *too* flush. 34px was picked
    // on-device from a live A/B of 0/12/24/34/48px, matching the Claude
    // iOS app. Pinned as the inset expression, not a flat `34px`: a fixed
    // value would park 34px of dead space on devices with no home
    // indicator, where the inset resolves to 0. The clearance stays out
    // here rather than inside `.composer`, which the next test pins.
    expect(effectiveBottomPadding()).toBe("env(safe-area-inset-bottom)");
  });

  it("re-adds no inset inside the surface, so the box holds no empty band", () => {
    // #508 first moved the clearance inside the surface, as
    // `.bottom-composer .composer { padding-bottom: calc(8px + env(...)) }`.
    // On the device that measures 42px, and it renders as 42px of empty
    // grey inside the rounded box — the box reads as padded rather than
    // as sitting at the bottom (#509). So the clearance is gone: the
    // composer keeps only its plain mobile `8px`, and the controls end
    // ~8px above the screen bottom, inside the home-indicator region.
    // That is the requested geometry; re-adding clearance "for safety"
    // is the exact regression this pins.
    //
    // This subsumes #508's separate ChatHome-scoping guard. That one
    // allowed the inset on a `.bottom-composer`-scoped rule and rejected
    // it anywhere else; the surviving `.composer` rule with a bottom
    // inset is now *none*, so the filter drops the exclusion and the
    // check gets strictly stricter. Declaration-first and file-wide for
    // the same reason it was before: starting from every safe-area
    // bottom padding in `app.css` and asking which selector owns it
    // means no rule can escape by having a selector shape a
    // rule-shaped pattern didn't anticipate.
    //
    // The declaration pattern deliberately requires NO terminating `;`.
    // #508's form ended in `\);` and CSS lets the last declaration in a
    // block drop its semicolon, so re-adding the rule as
    // `{ padding-bottom: calc(8px + env(safe-area-inset-bottom)) }` slipped
    // past it — verified against #508's guard, which stayed green. Nothing
    // in the repo would have caught the formatting either: `ui` lints only
    // `.js`/`.jsx`, with no prettier or stylelint anywhere. Excluding `}`
    // from the spans is what makes dropping the terminator safe — without
    // it a match could run past the end of its own rule and be attributed
    // to the wrong selector.
    const insetComposers = [
      ...css.matchAll(
        /padding-bottom:\s*calc\([^;}]*safe-area-inset-bottom[^;}]*\)/g,
      ),
    ]
      .map((m) => selectorOfRuleAt(m.index))
      .flatMap((selectorList) => selectorList.split(","))
      .map((selector) => selector.trim())
      .filter((s) => /\.composer$/.test(s));
    expect(insetComposers).toEqual([]);
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
    // The conversation composer's bottom inset has moved around and is
    // asserted by the block above, not here: #508 shifted it from
    // `.bottom-composer` (`14px + inset`) onto `.bottom-composer
    // .composer` (`8px + inset`), #509 dropped that, #510 zeroed the
    // container too, and #518 restored it on the container as a bare
    // `env(safe-area-inset-bottom)`. What this block still owns is
    // `.home`'s copy, the one container inset that never moved.
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


// ===========================================================================
// #571 — the composer control row, laid out
// ===========================================================================
//
// Everything above asserts declarations. This section asserts an OUTCOME:
// which side of the row the send button ends up on. That needs a layout, and
// jsdom implements none — every `getBoundingClientRect` is zeroes — so the
// two steps of the flexbox algorithm that decide the question are modelled
// here:
//
//   1. Collecting items into flex lines (CSS Flexbox §9.3). An item
//      contributes its flex base size, so `.spacer`'s `flex: 1` (basis `0`)
//      contributes nothing — and an auto margin contributes nothing either.
//   2. Sizing a line: flexible lengths resolve first (§9.7), and only the
//      free space LEFT OVER reaches auto margins (§9.5).
//
// Step 2's order is the whole reason `margin-left: auto` is safe here. On a
// single line `.spacer` grows into all the free space, so the auto margin
// receives zero and send does not move; on a wrapped line, which has no
// spacer to grow, the auto margin receives all of it. Both halves are
// asserted below, and so is the step-1 consequence: the fix cannot move the
// wrap point.
//
// Two inputs are real rather than invented, deliberately — a model fed on
// invented inputs is exactly the "unit test passing against a DOM that
// cannot occur on a phone" failure #505 is named for:
//
//   * The DECLARATIONS come from `app.css` on disk. `sendHasAutoLeftMargin()`
//     reads the fix itself and feeds every mobile layout below, so deleting
//     the declaration turns the wrapped-case assertions red rather than
//     leaving them vacuously green.
//   * The CHILDREN come from rendering the real `Composer` with the MCP
//     picker present, in the order the browser gets them. Reordering the row,
//     dropping `.spacer`, or adding a control this model cannot size each
//     fail here instead of being silently mismodelled.
//
// What stays modelled is the intrinsic width of the two text-bearing
// controls (the MCP pill, the model picker), which only real text layout can
// supply. So the assertions sweep a range of plausible widths and hold the
// property across EVERY configuration that actually wraps, rather than
// trusting one stand-in. That makes them a left-vs-right conclusion, not a
// pixel claim — the same thing the harness in the issue established. A real
// phone remains the only oracle for the pixels, and confirming it there is
// the reporter's step.

/**
 * Body of the first rule in `block` whose selector matches the regex source
 * `selectorSource`, or `null` when there is no such rule.
 *
 * `null` rather than a throw because the missing-rule case is not
 * hypothetical here — it is exactly what the mutation check produces, and it
 * should surface as a failed assertion naming the send button rather than as
 * a `TypeError` from inside a regex helper.
 *
 * The `(?:^|[{};])` prefix is what stops `\\.send` matching the
 * `.composer-row .send` rule: a descendant selector puts the class name mid
 * prelude, where this pattern cannot start.
 */
function ruleBodyIn(block, selectorSource) {
  const match = block.match(
    new RegExp(`(?:^|[{};])\\s*${selectorSource}\\s*\\{([^}]*)\\}`),
  );
  return match ? match[1] : null;
}

/** Body of an un-indented, top-level rule. Throws if it isn't there. */
function topLevelRuleBody(selectorSource, label) {
  const match = css.match(new RegExp(`^${selectorSource}\\s*\\{([^}]*)\\}`, "m"));
  if (match === null) {
    throw new Error(
      `no top-level \`${label} { ... }\` rule in app.css — it was renamed or ` +
        "removed, and the #571 layout model reads desktop geometry from it",
    );
  }
  return match[1];
}

/** Integer `px` value of `prop` in a rule body. Throws if it isn't there. */
function pxDecl(body, prop, label) {
  // The leading `(?:^|[;{\s])` stops `width` matching inside `max-width`.
  const match = body.match(new RegExp(`(?:^|[;{\\s])${prop}:\\s*(\\d+)px`));
  if (match === null) {
    throw new Error(
      `no \`${prop}: <n>px\` in the ${label} rule of app.css — the #571 ` +
        "layout model reads its geometry from the stylesheet and can no " +
        "longer size this control",
    );
  }
  return Number(match[1]);
}

/**
 * Is `margin-left: auto` declared on `.composer-row .send` inside the ≤640px
 * block? **This is the fix under test.** Every mobile layout below takes its
 * send item's `marginLeftAuto` from here, so removing the declaration from
 * `app.css` is a real mutation of these assertions rather than a change they
 * merely fail to notice.
 */
function sendHasAutoLeftMargin() {
  const body = ruleBodyIn(mobileBlock(), "\\.composer-row\\s+\\.send");
  return body !== null && /margin-left:\s*auto/.test(body);
}

/** Row + control geometry as the ≤640px block declares it. */
function mobileRowGeometry() {
  const row = ruleBodyIn(mobileBlock(), "\\.composer-row");
  return {
    gap: pxDecl(row, "gap", "mobile `.composer-row`"),
    wrap: /flex-wrap:\s*wrap/.test(row),
    send: pxDecl(ruleBodyIn(mobileBlock(), "\\.send"), "width", "mobile `.send`"),
    round: pxDecl(
      ruleBodyIn(mobileBlock(), "\\.cbtn\\.round"),
      "width",
      "mobile `.cbtn.round`",
    ),
  };
}

/** The same geometry as a ≥641px viewport gets: the base rules, unmodified. */
function desktopRowGeometry() {
  const row = topLevelRuleBody("\\.composer-row", ".composer-row");
  return {
    gap: pxDecl(row, "gap", "base `.composer-row`"),
    wrap: /flex-wrap:\s*wrap/.test(row),
    send: pxDecl(topLevelRuleBody("\\.send", ".send"), "width", "base `.send`"),
    round: pxDecl(
      topLevelRuleBody("\\.cbtn\\.round", ".cbtn.round"),
      "width",
      "base `.cbtn.round`",
    ),
  };
}

/** Does `child` match `selector`, either itself or through a descendant? */
function has(child, selector) {
  return child.matches(selector) || child.querySelector(selector) !== null;
}

/**
 * What kind of flex item a real `.composer-row` child is.
 *
 * `AttachMenu` / `MCPPicker` / `ModelPicker` each render a `position:
 * relative` wrapper around a single visible control, with their popover
 * markup either unmounted (closed, as here) or absolutely positioned — so
 * the wrapper is content-sized to that control, and the control's width is
 * the item's width. The mic and send buttons are direct children, hence the
 * self-or-descendant match.
 */
function classifyRowChild(child) {
  if (has(child, ".spacer")) return { kind: "spacer", grow: 1 };
  if (has(child, ".send")) return { kind: "send", grow: 0 };
  if (has(child, ".cbtn.round")) return { kind: "round", grow: 0 };
  if (has(child, ".mcp-pill")) return { kind: "mcp", grow: 0 };
  if (has(child, ".model-pick")) return { kind: "model", grow: 0 };
  throw new Error(
    "unrecognised `.composer-row` child " +
      `<${child.tagName.toLowerCase()} class="${child.className}"> — the row ` +
      "grew a control this #571 layout model cannot size, so its conclusions " +
      "no longer describe the real row; give the new control a width here " +
      "before trusting them again",
  );
}

/** Flex base size: a growable item contributes its `flex-basis: 0`, not a width. */
function baseSizeOf(item) {
  return item.grow > 0 ? 0 : item.width;
}

/**
 * Collect items into flex lines (§9.3).
 *
 * Auto margins are absent from this step on purpose: they resolve during
 * free-space distribution, not line breaking, which is precisely why adding
 * one cannot change where the row breaks.
 */
function collectLines({ containerWidth, gap, wrap, items }) {
  const lines = [];
  let current = [];
  let used = 0;
  items.forEach((item) => {
    const hypothetical = baseSizeOf(item);
    const lead = current.length > 0 ? gap : 0;
    if (wrap && current.length > 0 && used + lead + hypothetical > containerWidth) {
      lines.push(current);
      current = [item];
      used = hypothetical;
    } else {
      used += lead + hypothetical;
      current.push(item);
    }
  });
  lines.push(current);
  return lines;
}

/**
 * Lay out a `display: flex; flex-direction: row` container, returning one
 * `{ kind, line, left, right, autoMargin }` box per item.
 *
 * `flex-shrink` is not modelled: with `flex-wrap: wrap` a line can only
 * overflow when a single item is wider than the container, which no swept
 * configuration produces, and the desktop widths are chosen wide enough to
 * leave free space. That precondition is asserted rather than assumed — see
 * "no configuration overflows its line".
 */
function layoutFlexRow({ containerWidth, gap, wrap, items }) {
  const boxes = [];
  collectLines({ containerWidth, gap, wrap, items }).forEach((line, lineIndex) => {
    const widths = line.map(baseSizeOf);
    const growTotal = line.reduce((total, item) => total + item.grow, 0);
    let free =
      containerWidth - widths.reduce((a, b) => a + b, 0) - gap * (line.length - 1);

    // §9.7 — flexible lengths resolve first. The spacer takes everything.
    if (free > 0 && growTotal > 0) {
      line.forEach((item, i) => {
        widths[i] += (free * item.grow) / growTotal;
      });
      free = 0;
    }

    // §9.5 — only then do auto margins see anything.
    const autoCount = line.filter((item) => item.marginLeftAuto).length;
    const autoShare = free > 0 && autoCount > 0 ? free / autoCount : 0;

    let x = 0;
    line.forEach((item, i) => {
      if (item.marginLeftAuto) x += autoShare;
      boxes.push({
        kind: item.kind,
        line: lineIndex,
        left: x,
        right: x + widths[i],
        autoMargin: item.marginLeftAuto ? autoShare : 0,
      });
      x += widths[i] + gap;
    });
  });
  return boxes;
}

// A registered, authed MCP server — enough for `MCPPicker` to render its
// pill, which is the control whose presence tips the row over.
const MCP_SERVERS = [
  {
    server_id: "srv-1",
    name: "Hive",
    tool_prefix: "hive",
    globally_enabled: true,
    auth_status: "active",
  },
];

/**
 * Render the real `Composer` and return its `.composer-row` children,
 * classified.
 *
 * Rendered once per structure rather than once per swept width, because the
 * DOM does not depend on the widths — only on whether the MCP picker is
 * there.
 */
async function composerRowKinds(mcpServers) {
  // iOS Safari ships the Web Speech API, so the phone this was reported from
  // renders the mic button. jsdom ships nothing and `Composer` feature-detects
  // at mount, so without the stub the modelled row would be one control short
  // of the real one.
  vi.stubGlobal("SpeechRecognition", function FakeSpeechRecognition() {});
  let view;
  await act(async () => {
    view = render(
      createElement(Composer, {
        model: { id: "m", name: "Claude Opus 4.6", short: "Opus 4.6" },
        effort: "High",
        setModel: vi.fn(),
        setEffort: vi.fn(),
        onSend: vi.fn(),
        mcpServers,
        mcpSettings: { mode: "inherit", explicit_server_ids: [] },
        setMcpSettings: vi.fn(),
      }),
    );
  });
  const row = view.container.querySelector(".composer-row");
  if (row === null) {
    throw new Error(
      "the rendered Composer has no `.composer-row` — the control row was " +
        "renamed or removed, and these #571 assertions need repointing",
    );
  }
  return [...row.children].map(classifyRowChild);
}

/**
 * Turn classified children into flex items of given widths.
 *
 * `.send` is the only item that carries an auto margin; nothing else in the
 * row declares one, and `classifyRowChild` refuses any child that might.
 */
function itemsFrom(kinds, widths, marginLeftAuto) {
  return kinds.map((k) => ({
    kind: k.kind,
    grow: k.grow,
    width: k.grow > 0 ? 0 : widths[k.kind],
    marginLeftAuto: marginLeftAuto && k.kind === "send",
  }));
}

// The row's inner width on a phone: a 390-430px device, less the
// `.bottom-composer` gutter and the `.composer` padding the mobile block
// declares. Swept rather than pinned so no assertion rests on one device's
// arithmetic.
const CONTAINER_WIDTHS = [340, 350, 360, 370, 380];
// Stand-ins for the two text-bearing controls. Each renders an icon, a short
// label and a chevron inside a padded pill, which lands either side of 130px
// at the app's 13-14px control type.
const MCP_WIDTHS = [100, 120, 140, 160];
const MODEL_WIDTHS = [100, 130, 150, 170];

/**
 * Lay the row out once per (container × mcp width × model width)
 * combination, so a property can be asserted over all of them rather than
 * over one lucky stand-in.
 */
function sweepMobileLayouts(kinds, marginLeftAuto) {
  const geometry = mobileRowGeometry();
  const results = [];
  CONTAINER_WIDTHS.forEach((containerWidth) => {
    MCP_WIDTHS.forEach((mcp) => {
      MODEL_WIDTHS.forEach((model) => {
        const items = itemsFrom(
          kinds,
          { send: geometry.send, round: geometry.round, mcp, model },
          marginLeftAuto,
        );
        results.push({
          containerWidth,
          boxes: layoutFlexRow({
            containerWidth,
            gap: geometry.gap,
            wrap: geometry.wrap,
            items,
          }),
        });
      });
    });
  });
  return results;
}

/** The single box for `kind` in a laid-out row. */
function box(boxes, kind) {
  return boxes.find((b) => b.kind === kind);
}

describe("composer control row — send stays right when the row wraps (#571)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("declares the fix, and only inside the ≤640px block", () => {
    // Presence: every wrapped-case assertion below is fed from this, so if it
    // silently went false they would fail too — but failing here first is
    // what names the cause.
    expect(sendHasAutoLeftMargin()).toBe(true);

    // Scope: exactly one `.composer-row .send` rule in the file, and it is
    // the one inside the mobile block. A second copy at top level would
    // right-align send on desktop too, where `.spacer` already does the job
    // and the row never wraps.
    expect([...css.matchAll(/\.composer-row\s+\.send\s*\{/g)]).toHaveLength(1);
    expect(mobileBlock()).toMatch(
      /\.composer-row\s+\.send\s*\{[^}]*margin-left:\s*auto/,
    );
  });

  it("lays out the row the real Composer renders, in its real order", async () => {
    // The #505 guard. Everything below is a claim about this list, so if the
    // row's structure drifts the claims stop describing the app and this is
    // where that surfaces. `send` last, and `spacer` ahead of it, are the two
    // properties the fix's reasoning actually rests on.
    expect(await composerRowKinds(MCP_SERVERS)).toEqual([
      { kind: "round", grow: 0 }, // AttachMenu's `+`
      { kind: "mcp", grow: 0 }, // MCPPicker's pill — the control that overflows the row
      { kind: "spacer", grow: 1 },
      { kind: "model", grow: 0 }, // ModelPicker
      { kind: "round", grow: 0 }, // dictation mic
      { kind: "send", grow: 0 },
    ]);
    // The row's own geometry being the wrapping kind is a premise of the
    // rest; asserted here rather than assumed at each use.
    expect(mobileRowGeometry().wrap).toBe(true);
  });

  it("is the MCP picker's presence that tips the row into wrapping", async () => {
    // The bug report's precondition, restated as a property of the model:
    // without the pill the row fits on one line at every swept width, and
    // with it there are widths where it does not. Without this the
    // wrapped-case assertion below could go vacuous without saying so.
    const auto = sendHasAutoLeftMargin();
    const without = sweepMobileLayouts(await composerRowKinds(undefined), auto);
    const present = sweepMobileLayouts(await composerRowKinds(MCP_SERVERS), auto);

    expect(without.every((r) => r.boxes.every((b) => b.line === 0))).toBe(true);
    expect(present.some((r) => r.boxes.some((b) => b.line > 0))).toBe(true);
  });

  it("pins send flush to the right edge in every wrapping configuration", async () => {
    // THE bug. Before the fix send sat at the left of the second line with
    // essentially the whole row empty to its right; the assertion is that its
    // right edge now coincides with the row's, on whatever line it lands on.
    const kinds = await composerRowKinds(MCP_SERVERS);
    const wrapped = sweepMobileLayouts(kinds, sendHasAutoLeftMargin()).filter(
      (r) => box(r.boxes, "send").line > 0,
    );

    // Non-vacuity: a sweep that produced no wrapped row would pass the loop
    // below by describing nothing.
    expect(wrapped.length).toBeGreaterThan(0);
    wrapped.forEach((r) => {
      const send = box(r.boxes, "send");
      expect(send.right).toBeCloseTo(r.containerWidth, 6);
      // …and it got there via the auto margin, the spacer having stayed
      // behind on an earlier line. That is what distinguishes the fixed row
      // from one that merely happens to end flush.
      expect(send.autoMargin).toBeGreaterThan(0);
      expect(box(r.boxes, "spacer").line).toBeLessThan(send.line);
    });
  });

  it("leaves the single-line case to .spacer, the auto margin taking nothing", async () => {
    // The other half of "nothing else changes", at mobile widths: where the
    // row already fitted, the fix must be inert. `autoMargin === 0` is the
    // mechanism — flexible lengths resolve first, so the spacer has already
    // consumed the free space by the time auto margins are served.
    const kinds = await composerRowKinds(MCP_SERVERS);
    const single = sweepMobileLayouts(kinds, sendHasAutoLeftMargin()).filter((r) =>
      r.boxes.every((b) => b.line === 0),
    );

    expect(single.length).toBeGreaterThan(0);
    single.forEach((r) => {
      const send = box(r.boxes, "send");
      expect(send.right).toBeCloseTo(r.containerWidth, 6);
      expect(send.autoMargin).toBe(0);
    });
  });

  it("does not move the wrap point", async () => {
    // Auto margins resolve after line breaking, so the fix can change where
    // an item sits on its line but never which line it lands on. Asserted by
    // laying the same rows out with the margin off and comparing the line
    // assignment.
    const kinds = await composerRowKinds(MCP_SERVERS);
    const fixed = sweepMobileLayouts(kinds, true);
    const unfixed = sweepMobileLayouts(kinds, false);
    const lines = (results) => results.map((r) => r.boxes.map((b) => b.line));

    expect(lines(fixed)).toEqual(lines(unfixed));

    // And the unfixed row really is the reported bug: somewhere in the sweep
    // send lands on a later line with space to its right. Without this the
    // comparison above would also pass a "fix" that did nothing at all.
    const broken = unfixed.filter((r) => {
      const send = box(r.boxes, "send");
      return send.line > 0 && r.containerWidth - send.right > 1;
    });
    expect(broken.length).toBeGreaterThan(0);
  });

  it("leaves desktop unchanged", async () => {
    // At ≥641px the media block does not apply, so the row is `nowrap` with
    // no auto margin and `.spacer` does all the work — exactly as before.
    const geometry = desktopRowGeometry();
    expect(geometry.wrap).toBe(false);

    const items = itemsFrom(
      await composerRowKinds(MCP_SERVERS),
      { send: geometry.send, round: geometry.round, mcp: 160, model: 170 },
      false,
    );
    [640, 720, 840].forEach((containerWidth) => {
      const boxes = layoutFlexRow({
        containerWidth,
        gap: geometry.gap,
        wrap: geometry.wrap,
        items,
      });
      expect(boxes.every((b) => b.line === 0)).toBe(true);
      expect(box(boxes, "send").right).toBeCloseTo(containerWidth, 6);
      expect(box(boxes, "send").autoMargin).toBe(0);
    });
  });

  it("no configuration overflows its line, so shrinking never applies", async () => {
    // `layoutFlexRow`'s stated precondition. A line whose items overflowed
    // would need `flex-shrink`, which is not implemented, and every layout
    // above would be describing something the browser does not do. An
    // overflowing line shows up as the last box on it ending past the
    // container's right edge.
    const kinds = await composerRowKinds(MCP_SERVERS);
    sweepMobileLayouts(kinds, sendHasAutoLeftMargin()).forEach((r) => {
      r.boxes.forEach((b) => {
        expect(b.right).toBeLessThanOrEqual(r.containerWidth + 1e-9);
      });
    });
  });

  it("refuses to model a row child it cannot size", () => {
    // The #505 guard's teeth. A control added to the row has to be given a
    // width here; silently treating it as zero-width would let the sweep go
    // on reporting a wrap behaviour the real row no longer has.
    const stranger = document.createElement("div");
    stranger.className = "some-new-control";
    expect(() => classifyRowChild(stranger)).toThrow(
      /unrecognised `\.composer-row` child/,
    );
  });
});

// ===========================================================================
// #582 — popovers, clamped to the viewport
// ===========================================================================
//
// Like #571 above, this section asserts an OUTCOME rather than a
// declaration: where a popover's left and right edges land on a phone. That
// needs a layout and jsdom implements none, so the two rules that decide it
// are modelled here:
//
//   1. Which ancestor establishes the containing block (CSS 2.1 §10.1) —
//      the nearest ancestor whose `position` is not `static`. This is the
//      whole mechanism of the fix: the ≤640px block makes `.pop-anchor`
//      static, which hands the popover up to `.composer-wrap`.
//   2. How `left` / `right` / `min-width` / `max-width` size and place an
//      absolutely-positioned box (§10.3.7), including the over-constrained
//      case — with both edges pinned and a clamp in play, `right` is the
//      one that gives way in `ltr`.
//
// Three inputs are real rather than invented, for the reason #571 states:
//
//   * The DECLARATIONS come from `app.css` on disk — every gutter, every
//     anchor offset, every clamp. Nothing below hardcodes a pixel the
//     stylesheet doesn't declare, so deleting the fix moves the model.
//   * The POPOVERS are the real ones, opened by clicking the real trigger
//     inside the real `Composer` / `Sidebar`, so their ancestry and their
//     class lists are the browser's, not a fixture's.
//   * The VIEWPORTS are swept, so no assertion rests on one device.
//
// What stays modelled is a popover's intrinsic content width, which only
// real text layout can supply — so that too is swept, and one of the
// assertions is that the answer does not depend on it. That is the
// property the fix actually buys, and the reason the anchor was pinned at
// both edges rather than flipped: a flipped anchor's safe edge is still a
// function of content width and trigger position, so a longer server name
// re-breaks it silently.
//
// SCOPE, stated plainly: these assertions cover ≤640px, which is #582's
// scope and the viewport the bug was reported from. They deliberately do
// NOT assert the trigger-anchored geometry at ≥641px — see the PR for
// #582, which reports that the right-anchored MCP popover can still clip
// on a narrow desktop window and proposes a follow-up. Asserting a bound
// this change does not deliver would be worse than not asserting it.
//
// A real phone remains the only oracle for the pixels, and confirming it
// there is the reporter's step.

/** The `@media (max-width: N px)` breakpoint, read rather than assumed. */
function mobileBreakpointPx() {
  const match = css.match(/@media \(max-width: (\d+)px\)/);
  if (match === null) {
    throw new Error(
      "no `@media (max-width: <n>px)` block in app.css — the #582 model " +
        "takes its breakpoint from the stylesheet and can no longer find one",
    );
  }
  return Number(match[1]);
}

/**
 * Every un-indented, top-level rule body for `selectorSource`, in source
 * order.
 *
 * Plural on purpose: `app.css` declares `.composer-wrap` twice at top level
 * — the sizing rule in the composer section and `position: relative` three
 * hundred lines later — so a helper that returned the first match would
 * report the wrapper as `static` and the containing-block walk below would
 * sail straight past the element the whole fix depends on.
 *
 * `^` + the `m` flag is what confines this to top level: rules nested in a
 * media query are indented, the same assumption `stageRule()` above makes.
 */
function topLevelRuleBodies(selectorSource) {
  return [
    ...css.matchAll(new RegExp(`^${selectorSource}\\s*\\{([^}]*)\\}`, "gm")),
  ].map((m) => m[1]);
}

/** Every rule body in `block` whose selector is exactly `selectorSource`. */
function ruleBodiesIn(block, selectorSource) {
  return [
    ...block.matchAll(
      new RegExp(`(?:^|[{};])\\s*${selectorSource}\\s*\\{([^}]*)\\}`, "g"),
    ),
  ].map((m) => m[1]);
}

/**
 * Last declared value of `prop` across `bodies`, in source order, or `null`.
 *
 * Resolved rather than read off one declaration for the reason
 * `effectiveBottomPadding` gives above: `app.css` sets several of these
 * properties twice — once in the layout rule, once in the safe-area
 * sub-block appended lower down the same media query — and which one wins
 * is a cascade question, not a lookup.
 *
 * The leading `(?:^|[;{\s])` is what stops `width` matching inside
 * `max-width`; `[^;}]` keeps a value that drops its terminating semicolon
 * (legal for the last declaration in a block) from running past its rule.
 */
function effectiveDecl(bodies, prop) {
  let value = null;
  for (const body of bodies) {
    for (const [, raw] of body.matchAll(
      new RegExp(`(?:^|[;{\\s])${prop}:\\s*([^;}]+)`, "g"),
    )) {
      value = raw.trim();
    }
  }
  return value;
}

/** `effectiveDecl`, but a missing declaration is an error rather than null. */
function requireDecl(bodies, prop, label) {
  const value = effectiveDecl(bodies, prop);
  if (value === null) {
    throw new Error(
      `no \`${prop}\` in the ${label} rule of app.css — the #582 model reads ` +
        "the popover's geometry from the stylesheet and can no longer place it",
    );
  }
  return value;
}

/** The `side` component of a `padding` shorthand value. */
function shorthandSide(value, side) {
  const parts = value.trim().split(/\s+/);
  if (side === "right") return parts.length >= 2 ? parts[1] : parts[0];
  if (parts.length >= 4) return parts[3];
  return parts.length >= 2 ? parts[1] : parts[0];
}

/**
 * Effective `padding-left` / `padding-right` across `bodies`, walking both
 * the shorthand and the longhand forms in source order.
 */
function effectivePaddingSide(bodies, side) {
  let value = null;
  for (const body of bodies) {
    for (const [, longhand, raw] of body.matchAll(
      /(?:^|[;{\s])padding(-top|-right|-bottom|-left)?:\s*([^;}]+)/g,
    )) {
      if (longhand === undefined) value = shorthandSide(raw, side);
      else if (longhand === `-${side}`) value = raw.trim();
    }
  }
  return value;
}

/** Split on top-level commas — `min(a, calc(b - c))` is two terms, not three. */
function splitTopLevel(expr) {
  const parts = [];
  let depth = 0;
  let token = "";
  for (const ch of expr) {
    if (ch === "(") depth += 1;
    if (ch === ")") depth -= 1;
    if (ch === "," && depth === 0) {
      parts.push(token);
      token = "";
      continue;
    }
    token += ch;
  }
  parts.push(token);
  return parts;
}

/**
 * Evaluate a `calc()` body. CSS requires whitespace around `+` and `-`, so
 * the separator test is unambiguous and a `-` inside an identifier
 * (`safe-area-inset-left`) is never mistaken for an operator.
 */
function calcPx(expr, viewport) {
  let total = 0;
  let sign = 1;
  let depth = 0;
  let token = "";
  for (let i = 0; i < expr.length; i += 1) {
    const ch = expr[i];
    if (ch === "(") depth += 1;
    if (ch === ")") depth -= 1;
    const spaced = /\s/.test(expr[i - 1] ?? "") && /\s/.test(expr[i + 1] ?? "");
    if (depth === 0 && (ch === "+" || ch === "-") && spaced) {
      total += sign * lengthPx(token, viewport);
      token = "";
      sign = ch === "+" ? 1 : -1;
      continue;
    }
    token += ch;
  }
  return total + sign * lengthPx(token, viewport);
}

/**
 * A CSS length in px at `viewport`.
 *
 * Throws on any shape it does not implement rather than guessing — the
 * #505 discipline applied to the stylesheet side. A value this cannot read
 * is a value the model is not entitled to reason about.
 *
 * `env(safe-area-inset-*)` resolves to 0, which is what it resolves to on
 * every non-notched device and in every desktop browser. On a notched one
 * the insets only ever *increase* a gutter, which moves both edges further
 * inside the viewport, so 0 is also the worst case for these bounds.
 *
 * `vw` is taken as the plain viewport width. Real browsers count the
 * classic scrollbar in `vw`, but the app layer is `position: fixed;
 * inset: 0` and never scrolls the document, so there is no gutter to
 * over-count.
 */
function lengthPx(value, viewport) {
  const v = String(value).trim();
  if (/^-?\d+(?:\.\d+)?$/.test(v)) {
    if (Number(v) === 0) return 0;
    throw new Error(`unitless non-zero length \`${v}\` in app.css (#582 model)`);
  }
  let m = /^(-?\d+(?:\.\d+)?)px$/.exec(v);
  if (m) return Number(m[1]);
  m = /^(-?\d+(?:\.\d+)?)vw$/.exec(v);
  if (m) return (Number(m[1]) / 100) * viewport;
  m = /^env\(safe-area-inset-(?:top|right|bottom|left)\)$/.exec(v);
  if (m) return 0;
  m = /^calc\((.+)\)$/.exec(v);
  if (m) return calcPx(m[1], viewport);
  m = /^min\((.+)\)$/.exec(v);
  if (m) {
    return Math.min(...splitTopLevel(m[1]).map((p) => lengthPx(p, viewport)));
  }
  m = /^max\((.+)\)$/.exec(v);
  if (m) {
    return Math.max(...splitTopLevel(m[1]).map((p) => lengthPx(p, viewport)));
  }
  throw new Error(
    `app.css declares the length \`${v}\`, which the #582 viewport model ` +
      "cannot evaluate — teach `lengthPx` this shape before trusting any " +
      "bound computed from it",
  );
}

/** Rule bodies that apply to a bare class at `viewport`: base, then mobile. */
function bodiesForClass(className, viewport) {
  if (!/^[a-z][a-z0-9-]*$/.test(className)) {
    throw new Error(
      `class \`${className}\` is not a plain identifier — the #582 model ` +
        "interpolates class names into regexes and will not do so blindly",
    );
  }
  const selector = `\\.${className}`;
  const bodies = topLevelRuleBodies(selector);
  if (viewport <= mobileBreakpointPx()) {
    bodies.push(...ruleBodiesIn(mobileBlock(), selector));
  }
  return bodies;
}

/**
 * The `position` app.css gives `el` at `viewport`.
 *
 * Throws when no rule in the file mentions any of the element's classes:
 * an ancestor the stylesheet says nothing about is one this model cannot
 * claim is `static`, and claiming it wrongly would silently re-point every
 * containing block below it.
 */
function effectivePosition(el, viewport) {
  const classes = [...el.classList];
  if (classes.length === 0) {
    throw new Error(
      `<${el.tagName.toLowerCase()}> has no class, so the #582 model cannot ` +
        "tell whether it establishes a containing block — the walk should " +
        "have stopped at a positioned ancestor before reaching it",
    );
  }
  // The ≤640px correction itself. Read from the stylesheet, not assumed, so
  // deleting it moves every assertion below rather than none of them.
  if (
    viewport <= mobileBreakpointPx() &&
    classes.includes("pop-anchor") &&
    el.closest(".composer-row") !== null
  ) {
    const mobile = effectiveDecl(
      ruleBodiesIn(mobileBlock(), "\\.composer-row\\s+\\.pop-anchor"),
      "position",
    );
    if (mobile !== null) return mobile;
  }
  let known = false;
  let position = "static";
  for (const className of classes) {
    const bodies = bodiesForClass(className, viewport);
    if (bodies.length === 0) continue;
    known = true;
    const declared = effectiveDecl(bodies, "position");
    if (declared !== null) position = declared;
  }
  if (!known) {
    throw new Error(
      `no rule in app.css matches any class of <${el.tagName.toLowerCase()} ` +
        `class="${el.className}">, so the #582 model cannot say whether it ` +
        "establishes a containing block",
    );
  }
  return position;
}

/** The ancestor that establishes `popEl`'s containing block (CSS 2.1 §10.1). */
function containingBlockElement(popEl, viewport) {
  for (let el = popEl.parentElement; el !== null; el = el.parentElement) {
    if (effectivePosition(el, viewport) !== "static") return el;
  }
  throw new Error(
    "walked to the document root without finding a positioned ancestor for " +
      "a `.pop` — in the app it is always inside one, so the rendered tree " +
      "no longer matches the one these #582 assertions describe",
  );
}

/** Horizontal extent of a `.composer-wrap` inside `host` at `viewport`. */
function composerWrapRect(viewport, host) {
  const bodies = bodiesForClass(host, viewport);
  if (bodies.length === 0) {
    throw new Error(
      `no \`.${host}\` rule in app.css — the #582 model takes the composer's ` +
        "gutters from its host and can no longer find them",
    );
  }
  const left = lengthPx(
    effectivePaddingSide(bodies, "left") ?? "0",
    viewport,
  );
  const right = lengthPx(
    effectivePaddingSide(bodies, "right") ?? "0",
    viewport,
  );
  // `.composer-wrap { max-width: none }` inside the media query, so at these
  // widths the wrapper fills its host and the host's padding IS the gutter.
  const wrapMax = effectiveDecl(bodiesForClass("composer-wrap", viewport), "max-width");
  if (wrapMax !== "none") {
    throw new Error(
      "`.composer-wrap` no longer releases its max-width at ≤640px " +
        `(reads \`${wrapMax}\`), so it may be centred rather than filling ` +
        "its host and the #582 model's gutters no longer describe it",
    );
  }
  return { left, right: viewport - right };
}

/** Horizontal extent of the sidebar's `.account-wrap` at `viewport`. */
function accountWrapRect(viewport) {
  const sb = bodiesForClass("sb", viewport);
  const width = lengthPx(requireDecl(sb, "width", "`.sb`"), viewport);
  const inset = lengthPx(effectivePaddingSide(sb, "left") ?? "0", viewport);
  const foot = bodiesForClass("sb-foot", viewport);
  const pad = lengthPx(
    effectivePaddingSide(foot, "left") ?? "0",
    viewport,
  );
  const padRight = lengthPx(
    effectivePaddingSide(foot, "right") ?? "0",
    viewport,
  );
  // The open drawer sits at x=0 (`.sb.mobile-open { transform: translateX(0) }`
  // over the fixed `left: 0`); the closed one is off-canvas, where "inside
  // the viewport" is not a claim worth making. Tests render it open.
  return { left: inset + pad, right: width - padRight };
}

/**
 * Horizontal extent of a `.composer-row` trigger wrapper at `viewport`,
 * laid out with #571's flex model.
 *
 * Only reached when `.pop-anchor` is still `position: relative` at this
 * width — i.e. by the assertion that reproduces the reported failure, and
 * by nothing in the fixed configuration.
 */
function triggerRect(anchorEl, viewport, host, widths) {
  const wrap = composerWrapRect(viewport, host);
  const composer = bodiesForClass("composer", viewport);
  const padLeft = lengthPx(effectivePaddingSide(composer, "left"), viewport);
  const padRight = lengthPx(effectivePaddingSide(composer, "right"), viewport);
  const rowLeft = wrap.left + padLeft;
  const rowWidth = wrap.right - padRight - rowLeft;

  const row = anchorEl.closest(".composer-row");
  if (row === null) {
    throw new Error(
      "a `.pop-anchor` outside `.composer-row` reached the #582 trigger " +
        "model, which only knows how to lay that row out",
    );
  }
  const children = [...row.children];
  const index = children.indexOf(anchorEl);
  const geometry =
    viewport <= mobileBreakpointPx() ? mobileRowGeometry() : desktopRowGeometry();
  const items = itemsFrom(
    children.map(classifyRowChild),
    { send: geometry.send, round: geometry.round, ...widths },
    viewport <= mobileBreakpointPx() && sendHasAutoLeftMargin(),
  );
  const boxes = layoutFlexRow({
    containerWidth: rowWidth,
    gap: geometry.gap,
    wrap: geometry.wrap,
    items,
  });
  return { left: rowLeft + boxes[index].left, right: rowLeft + boxes[index].right };
}

/** Every horizontal declaration in a `.pop`'s inline `style` attribute. */
function inlineHorizontal(popEl) {
  const found = {};
  ["left", "right", "width", "min-width", "max-width"].forEach((prop) => {
    const value = popEl.style.getPropertyValue(prop);
    if (value !== "") found[prop] = value;
  });
  return found;
}

const KNOWN_POP_CLASSES = new Set([
  "pop",
  "pop-above",
  "pop-start",
  "pop-end",
  "pop-compact",
]);

/**
 * Resolve a `.pop`'s horizontal box properties at `viewport`.
 *
 * This is the #505 guard for #582, and it has two sets of teeth:
 *
 *   * An unrecognised class throws. A popover given a new anchor the model
 *     cannot resolve must not be silently laid out as if it had none.
 *   * Inline horizontal anchoring throws — **unless** it is exactly the
 *     pinned-both-edges shape (`left: 0; right: 0; min-width: 0`), which is
 *     `AccountPopover`'s and is safe by construction: a box pinned to both
 *     edges of its containing block cannot be wider than that block. Every
 *     other inline form is the bug #582 fixed, because an inline value
 *     outranks the stylesheet and puts the popover outside the reach of
 *     every breakpoint correction.
 */
function popHorizontal(popEl, viewport) {
  [...popEl.classList].forEach((className) => {
    if (!KNOWN_POP_CLASSES.has(className)) {
      throw new Error(
        `unrecognised \`.pop\` class \`${className}\` — a popover grew an ` +
          "anchor this #582 model cannot resolve, so its conclusions no " +
          "longer describe where the box lands; teach `popHorizontal` this " +
          "class before trusting them again",
      );
    }
  });

  const popRule = bodiesForClass("pop", viewport);
  const resolved = {
    left: null,
    right: null,
    minWidth: lengthPx(requireDecl(popRule, "min-width", "`.pop`"), viewport),
    maxWidth: lengthPx(requireDecl(popRule, "max-width", "`.pop`"), viewport),
  };
  if (popEl.classList.contains("pop-compact")) {
    resolved.minWidth = lengthPx(
      requireDecl(ruleBodiesIn(css, "\\.pop\\.pop-compact"), "min-width", "`.pop.pop-compact`"),
      viewport,
    );
  }
  if (popEl.classList.contains("pop-start")) {
    resolved.left = lengthPx(
      requireDecl(ruleBodiesIn(css, "\\.pop\\.pop-start"), "left", "`.pop.pop-start`"),
      viewport,
    );
  }
  if (popEl.classList.contains("pop-end")) {
    resolved.right = lengthPx(
      requireDecl(ruleBodiesIn(css, "\\.pop\\.pop-end"), "right", "`.pop.pop-end`"),
      viewport,
    );
  }
  // The ≤640px correction: pin both edges to the composer and release the
  // floor. Read from the stylesheet so removing it moves the model.
  if (viewport <= mobileBreakpointPx() && popEl.closest(".composer-row") !== null) {
    const mobile = ruleBodiesIn(mobileBlock(), "\\.composer-row\\s+\\.pop");
    const left = effectiveDecl(mobile, "left");
    const right = effectiveDecl(mobile, "right");
    const minWidth = effectiveDecl(mobile, "min-width");
    if (left !== null) resolved.left = lengthPx(left, viewport);
    if (right !== null) resolved.right = lengthPx(right, viewport);
    if (minWidth !== null) resolved.minWidth = lengthPx(minWidth, viewport);
  }

  const inline = inlineHorizontal(popEl);
  const inlineKeys = Object.keys(inline).sort().join(",");
  if (inlineKeys === "left,min-width,right") {
    const zeroed = ["left", "right", "min-width"].every(
      (prop) => lengthPx(inline[prop], viewport) === 0,
    );
    if (zeroed) {
      // AccountPopover's shape — the one popover that never had this bug,
      // and the one #582 copies onto the other three.
      return { ...resolved, left: 0, right: 0, minWidth: 0 };
    }
  }
  if (inlineKeys !== "") {
    throw new Error(
      `\`.pop\` declares horizontal anchoring inline (${inlineKeys}) — #582 ` +
        "moved anchoring into classes precisely because an inline value " +
        "outranks the stylesheet, so no media query can correct it; the " +
        "pinned-both-edges shape is the only inline form still accepted",
    );
  }
  if (resolved.left === null && resolved.right === null) {
    throw new Error(
      "a `.pop` carries no horizontal anchor at all — it would be laid out " +
        "at its static position, which #582's assertions cannot bound",
    );
  }
  return resolved;
}

/**
 * The popover's box, given its containing block and an intrinsic content
 * width (CSS 2.1 §10.3.7 plus the `min-width` / `max-width` clamps).
 *
 * When both edges are pinned the used width is the containing block's,
 * less the offsets; a clamp that changes it is resolved by ignoring
 * `right`, which is what `direction: ltr` does. When only one edge is
 * pinned the box is shrink-to-fit, hence the swept `contentWidth`.
 */
function popBox(popEl, viewport, cb, contentWidth) {
  const { left, right, minWidth, maxWidth } = popHorizontal(popEl, viewport);
  const cbWidth = cb.right - cb.left;
  let width =
    left !== null && right !== null ? cbWidth - left - right : contentWidth;
  width = Math.min(width, maxWidth);
  width = Math.max(width, minWidth);
  const boxLeft =
    left !== null ? cb.left + left : cb.right - right - width;
  return { left: boxLeft, right: boxLeft + width };
}

// Phone-class viewports, plus the breakpoint itself. 320 is the narrowest
// display iOS still ships (SE 1st gen); 430 is a Pro Max; 393 is the device
// this was reported from.
const POP_VIEWPORTS = [320, 360, 375, 390, 393, 412, 414, 430, 540, 640];
// Stand-ins for a popover's intrinsic content width — a two-line menu at
// the low end, a long server name and tool prefix at the high end.
const POP_CONTENT_WIDTHS = [140, 220, 300, 380, 460, 560];

/** Render the real `Composer` with an MCP server registered. */
async function renderComposer() {
  // iOS Safari ships the Web Speech API, so the reporting phone renders the
  // mic button; jsdom ships nothing and `Composer` feature-detects at mount.
  vi.stubGlobal("SpeechRecognition", function FakeSpeechRecognition() {});
  let view;
  await act(async () => {
    view = render(
      createElement(Composer, {
        model: { id: "m", name: "Claude Opus 4.6", short: "Opus 4.6" },
        effort: "High",
        setModel: vi.fn(),
        setEffort: vi.fn(),
        onSend: vi.fn(),
        mcpServers: MCP_SERVERS,
        mcpSettings: { mode: "inherit", explicit_server_ids: [] },
        setMcpSettings: vi.fn(),
      }),
    );
  });
  return view;
}

/** Open a popover by clicking its real trigger; return the real `.pop`. */
async function openPopover(view, triggerSelector) {
  const trigger = view.container.querySelector(triggerSelector);
  if (trigger === null) {
    throw new Error(
      `no \`${triggerSelector}\` in the rendered tree — the popover's trigger ` +
        "was renamed or removed, and these #582 assertions need repointing",
    );
  }
  await act(async () => {
    fireEvent.click(trigger);
  });
  const pop = view.container.querySelector(".pop");
  if (pop === null) {
    throw new Error(
      `clicking \`${triggerSelector}\` opened no \`.pop\` — the popover no ` +
        "longer uses the shared popover shell (CLAUDE.md §Chat-app popovers)",
    );
  }
  return pop;
}

/** The three composer popovers, each opened in a freshly rendered Composer. */
async function composerPopovers() {
  const specs = [
    { name: "MCPPicker", trigger: ".mcp-pill" },
    { name: "ModelPicker", trigger: ".model-pick" },
    { name: "AttachMenu", trigger: 'button[aria-label="Attach files"]' },
  ];
  const opened = [];
  for (const spec of specs) {
    const view = await renderComposer();
    opened.push({ ...spec, pop: await openPopover(view, spec.trigger) });
  }
  return opened;
}

/** The sidebar's account popover, opened in a real, mobile-open Sidebar. */
async function accountPopover() {
  let view;
  await act(async () => {
    view = render(
      createElement(
        MemoryRouter,
        null,
        createElement(Sidebar, { chats: [], mobileOpen: true }),
      ),
    );
  });
  return { name: "AccountPopover", pop: await openPopover(view, "button.account") };
}

describe("popovers stay inside the viewport (#582)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("clamps `.pop` so it can never be wider than the screen", () => {
    // Necessary but not sufficient, and the rule's own comment says so: the
    // MCP picker started at -67px whatever its width was capped at. What
    // this pins is that the 280px floor YIELDS to the clamp — a floor above
    // the cap wins in CSS, and the box overflows anyway.
    const pop = topLevelRuleBody("\\.pop", ".pop");
    expect(pop).toMatch(/max-width:\s*calc\(100vw - 16px\)/);
    expect(pop).toMatch(/min-width:\s*min\(280px, calc\(100vw - 16px\)\)/);

    POP_VIEWPORTS.forEach((viewport) => {
      const bodies = bodiesForClass("pop", viewport);
      const minWidth = lengthPx(effectiveDecl(bodies, "min-width"), viewport);
      const maxWidth = lengthPx(effectiveDecl(bodies, "max-width"), viewport);
      expect(minWidth).toBeLessThanOrEqual(maxWidth);
      expect(maxWidth).toBeLessThanOrEqual(viewport);
    });
  });

  it("declares the ≤640px correction, and only inside the mobile block", () => {
    // Presence: every bound below is computed from these two rules, so if
    // one silently vanished the assertions would fail too — but failing
    // here first is what names the cause.
    const mobile = mobileBlock();
    expect(mobile).toMatch(/\.composer-row\s+\.pop-anchor\s*\{[^}]*position:\s*static/);
    expect(mobile).toMatch(/\.composer-row\s+\.pop\s*\{[^}]*left:\s*0/);
    expect(mobile).toMatch(/\.composer-row\s+\.pop\s*\{[^}]*right:\s*0/);
    expect(mobile).toMatch(/\.composer-row\s+\.pop\s*\{[^}]*min-width:\s*0/);

    // Scope: the correction exists nowhere else, so ≥641px keeps the
    // trigger-relative anchoring it has always had.
    expect([...css.matchAll(/\.composer-row\s+\.pop-anchor\s*\{/g)]).toHaveLength(1);
    expect([...css.matchAll(/\.composer-row\s+\.pop\s*\{/g)]).toHaveLength(1);
  });

  it("keeps the anchor classes byte-equivalent to the inline styles they replaced", () => {
    // The ≥641px "nothing changed" claim, made as a declaration match
    // rather than a layout: the JSX swap is a pure refactor up there, so
    // the classes must carry exactly the values that were inline.
    // `.pop-anchor` replaces each consumer's inline `position: relative`.
    expect(topLevelRuleBody("\\.pop-anchor", ".pop-anchor")).toMatch(
      /position:\s*relative/,
    );
    expect(ruleBodiesIn(css, "\\.pop\\.pop-above")[0]).toMatch(
      /bottom:\s*calc\(100% \+ 8px\)/,
    );
    expect(ruleBodiesIn(css, "\\.pop\\.pop-start")[0]).toMatch(/left:\s*0/);
    expect(ruleBodiesIn(css, "\\.pop\\.pop-end")[0]).toMatch(/right:\s*0/);
    // AttachMenu's 240px floor, which was `minWidth: 240` inline, now
    // clamped the same way the base floor is.
    expect(ruleBodiesIn(css, "\\.pop\\.pop-compact")[0]).toMatch(
      /min-width:\s*min\(240px, calc\(100vw - 16px\)\)/,
    );
  });

  it("opens the real popovers from the real triggers", async () => {
    // The #505 guard. Everything below is a claim about these four boxes,
    // so if a popover stopped using the shared shell, moved out of the
    // composer row, or grew an anchor the model cannot resolve, this is
    // where it surfaces rather than three assertions later.
    const popovers = [...(await composerPopovers()), await accountPopover()];
    expect(popovers.map((p) => p.name)).toEqual([
      "MCPPicker",
      "ModelPicker",
      "AttachMenu",
      "AccountPopover",
    ]);
    // The three composer popovers hang off `.composer-row`, which is what
    // brings them inside the ≤640px correction; the account popover does
    // not, and does not need to.
    expect(popovers.map((p) => p.pop.closest(".composer-row") !== null)).toEqual([
      true,
      true,
      true,
      false,
    ]);
    // Anchoring resolves for all four — this is the call that throws on an
    // inline anchor or an unknown class.
    popovers.forEach((p) => {
      expect(() => popHorizontal(p.pop, 393)).not.toThrow();
    });
  });

  it("keeps the MCP picker's popover on screen at 393px — the reported failure", async () => {
    // THE bug: header clipped to "VERS FOR THIS CHAT", entries to "Hub" and
    // "ls available as api_*". Its trigger sits mid-row, it is the only
    // right-anchored popover, and `.pop`'s floor is 280px, so it started
    // around -67px. Pinned first and on its own, because it is the one the
    // reporter saw.
    const view = await renderComposer();
    const pop = await openPopover(view, ".mcp-pill");
    const cb = containingBlockElement(pop, 393);
    expect([...cb.classList]).toContain("composer-wrap");

    POP_CONTENT_WIDTHS.forEach((contentWidth) => {
      const box = popBox(pop, 393, composerWrapRect(393, "bottom-composer"), contentWidth);
      expect(box.left).toBeGreaterThanOrEqual(0);
      expect(box.right).toBeLessThanOrEqual(393);
    });
  });

  it("keeps all four on screen across every phone-class viewport", async () => {
    // The other three, so a future trigger-position change cannot quietly
    // reintroduce the bug on a popover that fits only by luck today. Both
    // composer hosts are swept: the conversation strip and the empty
    // state's `.home`, which carry their gutters independently.
    const composer = await composerPopovers();
    const account = await accountPopover();
    const offscreen = [];
    let checked = 0;

    function check(label, box, viewport) {
      checked += 1;
      if (box.left < 0 || box.right > viewport) {
        offscreen.push(`${label} → [${box.left}, ${box.right}] of ${viewport}`);
      }
    }

    POP_VIEWPORTS.forEach((viewport) => {
      POP_CONTENT_WIDTHS.forEach((contentWidth) => {
        ["bottom-composer", "home"].forEach((host) => {
          composer.forEach(({ name, pop }) => {
            const cb = composerWrapRect(viewport, host);
            check(`${name} in .${host}`, popBox(pop, viewport, cb, contentWidth), viewport);
          });
        });
        const cb = accountWrapRect(viewport);
        check("AccountPopover", popBox(account.pop, viewport, cb, contentWidth), viewport);
      });
    });

    // Reported as a list rather than an early bail, so a failure names every
    // configuration that clips instead of only the first.
    expect(offscreen).toEqual([]);
    // Non-vacuity: a sweep that computed nothing would pass the line above
    // by describing nothing.
    expect(checked).toBe(
      POP_VIEWPORTS.length * POP_CONTENT_WIDTHS.length * (composer.length * 2 + 1),
    );
  });

  it("places every popover independently of its content width at ≤640px", async () => {
    // The property that makes the bound structural rather than lucky, and
    // the reason the anchor was pinned at both edges rather than flipped
    // left: a flipped anchor keeps one edge a function of content width and
    // trigger position, so a longer server name, one more control in the
    // row or a narrower device re-breaks it with no warning — the same
    // pressure that already wraps this row (#571).
    const popovers = [...(await composerPopovers()), await accountPopover()];
    POP_VIEWPORTS.forEach((viewport) => {
      popovers.forEach(({ name, pop }) => {
        const cb =
          pop.closest(".composer-row") === null
            ? accountWrapRect(viewport)
            : composerWrapRect(viewport, "bottom-composer");
        const boxes = POP_CONTENT_WIDTHS.map((w) => popBox(pop, viewport, cb, w));
        const distinct = [...new Set(boxes.map((b) => `${b.left}..${b.right}`))];
        expect(`${name}@${viewport}: ${distinct.join(" | ")}`).toBe(
          `${name}@${viewport}: ${distinct[0]}`,
        );
      });
    });
  });

  it("reproduces the reported -67px when the trigger anchor is left uncorrected", async () => {
    // The mutation check, run from inside the suite: with the ≤640px
    // correction gone the MCP popover reverts to `right: 0` against its own
    // trigger, and THAT is where the off-screen left edge comes from.
    // Without this the assertions above could pass by describing a viewport
    // that was never in trouble.
    //
    // The row is laid out by #571's model, so the trigger's x-range is the
    // one that section already established rather than a fresh invention.
    const view = await renderComposer();
    const pop = await openPopover(view, ".mcp-pill");
    const anchor = pop.closest(".pop-anchor");
    expect(anchor).not.toBeNull();

    const offscreen = [];
    MCP_WIDTHS.forEach((mcp) => {
      MODEL_WIDTHS.forEach((model) => {
        const cb = triggerRect(anchor, 393, "bottom-composer", { mcp, model });
        // `right: 0` against the trigger, at the floor `.pop` still declares.
        const popRule = bodiesForClass("pop", 393);
        const width = lengthPx(effectiveDecl(popRule, "min-width"), 393);
        const left = cb.right - width;
        if (left < 0) offscreen.push(Math.round(left));
      });
    });

    expect(offscreen.length).toBe(MCP_WIDTHS.length * MODEL_WIDTHS.length);
    // Not merely negative — negative by roughly the margin the report
    // describes. The exact figure moves with the modelled pill width; the
    // reported one sits inside this range.
    expect(Math.max(...offscreen)).toBeLessThanOrEqual(-30);
    expect(Math.min(...offscreen)).toBeGreaterThanOrEqual(-200);
  });

  it("refuses to place a popover whose anchoring it cannot resolve", () => {
    // The #505 guard's teeth, both halves.
    const unknown = document.createElement("div");
    unknown.className = "pop pop-above pop-somewhere-new";
    expect(() => popHorizontal(unknown, 393)).toThrow(
      /unrecognised `\.pop` class `pop-somewhere-new`/,
    );

    // An inline anchor is the exact shape #582 removed: it outranks the
    // stylesheet, so the ≤640px correction cannot reach it.
    const inlineAnchored = document.createElement("div");
    inlineAnchored.className = "pop pop-above";
    inlineAnchored.style.right = "0";
    expect(() => popHorizontal(inlineAnchored, 393)).toThrow(
      /declares horizontal anchoring inline/,
    );

    // …and one with no anchor at all is not silently laid out at its
    // static position.
    const unanchored = document.createElement("div");
    unanchored.className = "pop pop-above";
    expect(() => popHorizontal(unanchored, 393)).toThrow(
      /carries no horizontal anchor at all/,
    );
  });

  it("refuses to classify an ancestor app.css says nothing about", () => {
    // The containing-block walk is where a re-parented popover would change
    // meaning silently, so an ancestor the stylesheet does not describe is
    // an error rather than an assumed `static`.
    const stranger = document.createElement("div");
    stranger.className = "some-new-shell";
    expect(() => effectivePosition(stranger, 393)).toThrow(
      /no rule in app.css matches any class of/,
    );

    const classless = document.createElement("div");
    expect(() => effectivePosition(classless, 393)).toThrow(
      /has no class, so the #582 model cannot tell/,
    );
  });

  it("refuses to evaluate a length it does not implement", () => {
    // The stylesheet side of the same discipline: a bound computed from a
    // value the model guessed at would be worse than no bound.
    expect(() => lengthPx("3rem", 393)).toThrow(/cannot evaluate/);
    expect(() => lengthPx("42", 393)).toThrow(/unitless non-zero length/);
    // …and the shapes it does implement, so the guard above is not the only
    // thing exercising this function.
    expect(lengthPx("min(280px, calc(100vw - 16px))", 393)).toBe(280);
    expect(lengthPx("min(280px, calc(100vw - 16px))", 200)).toBe(184);
    expect(lengthPx("calc(12px + env(safe-area-inset-left))", 393)).toBe(12);
    expect(lengthPx("max(4px, 8px)", 393)).toBe(8);
    expect(lengthPx("min(84vw, 300px)", 393)).toBe(300);
    expect(lengthPx("0", 393)).toBe(0);
  });
});
