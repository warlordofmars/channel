# Channel MVP Phase 6a — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the foundation for the Channel chat product's visuals-first MVP: port the design token system, the icon set, the brand mark, and a multi-pref hook; rewrite `App.jsx` as a marketing + app route shell with auth-gated stubs; remove all template residue (admin components, Tailwind, shadcn primitives, ConsentBanner).

**Architecture:** Single Vite + React app. Marketing routes at `/`, app routes at `/app` (gated by a mgmt-JWT check), 404 catch-all. CSS-variable token system (OKLCH) loaded from `ui/src/styles/channel.css`, with `useChannelPrefs` writing `data-{pref}` attributes to `<html>` from `localStorage`. Phase 6a ships an empty shell — every route renders a one-line placeholder; subsequent phases (6b–6f) replace those placeholders.

**Tech Stack:** Vite, React 18, React Router 6, vitest, @testing-library/react. No Tailwind. No shadcn. CSS-vars only.

---

## File Structure

### Create

| Path | Responsibility |
|---|---|
| `ui/src/styles/channel.css` | Design tokens (OKLCH colors, radii, shadows, fonts), themed via `[data-theme="light"\|"dark"]`. Verbatim copy from `~/Downloads/design_handoff_channel/design-sources/channel.css`. |
| `ui/src/components/ChannelMark.jsx` | Brand mark SVG (rounded square with two vertical bars). Single component, `size` prop. |
| `ui/src/components/ChannelMark.test.jsx` | Renders + size prop. |
| `ui/src/components/Icon.jsx` | 24×24 stroke icon set (`currentColor`). `name` + `size` + `stroke` props; switch on `name`. Port from `~/Downloads/design_handoff_channel/design-sources/app/icons.jsx`. |
| `ui/src/components/Icon.test.jsx` | Each icon name renders an SVG; unknown name returns null. |
| `ui/src/components/AuthGate.jsx` | Wraps children; redirects to `/app/login` when no valid mgmt JWT. |
| `ui/src/components/AuthGate.test.jsx` | Valid token renders children; missing/expired/malformed token redirects. |
| `ui/src/hooks/useChannelPrefs.js` | Reads/writes seven prefs (theme, accent, density, shape, font, model, effort) plus a separate `siteTheme`. Applies `data-{pref}` to `<html>`; persists to `localStorage`. |
| `ui/src/hooks/useChannelPrefs.test.js` | Each pref: default value, persistence, attribute application. |
| `ui/src/lib/auth.js` | `parseToken(token)`, `isTokenValid(token)`, `TOKEN_KEY` constant. Extracted from current `App.jsx`. |
| `ui/src/lib/auth.test.js` | Valid/invalid/expired/malformed cases. |

### Modify

| Path | Change |
|---|---|
| `ui/src/main.jsx` | Replace `import "./index.css"` with `import "./styles/channel.css"`. |
| `ui/src/App.jsx` | Full rewrite: marketing + app route trees with placeholder route components; uses `useChannelPrefs` for theme + AuthGate for `/app/*`. |
| `ui/src/App.test.jsx` | Full rewrite: tests for routing, auth gate, placeholders. |
| `ui/src/components/ErrorBoundary.jsx` | Restyle to use channel tokens (`var(--canvas)`, `var(--ink)`, etc.) instead of hardcoded styles. |
| `ui/src/components/ErrorBoundary.test.jsx` | Update colour assertions if any. |
| `ui/package.json` | Remove deps: `tailwindcss`, `@tailwindcss/vite`, `@radix-ui/react-dialog`, `@radix-ui/react-slot`, `class-variance-authority`, `tailwind-merge`, `sonner`. Keep: `clsx`, `lucide-react`, `react-router-dom`, etc. |
| `ui/vite.config.js` | Remove `@tailwindcss/vite` plugin import + usage. |
| `CLAUDE.md` | Update file-tree structure map; remove references to deleted components; document `useChannelPrefs` + the new routes. |

### Delete

| Path | Reason |
|---|---|
| `ui/src/index.css` | Replaced by `ui/src/styles/channel.css` (Phase 6a). |
| `ui/src/hooks/useTheme.js` + `useTheme.test.js` | Superseded by `useChannelPrefs`. |
| `ui/src/components/UsersPanel.jsx` + test | Template admin residue; not in design. |
| `ui/src/components/PageLayout.jsx` + test | Template residue; design has its own layout. |
| `ui/src/components/EmptyState.jsx` + test | Template residue; design has its own empty states. |
| `ui/src/components/LoginPage.jsx` + test | Replaced by future `app/Login.jsx` (Phase 6c); Phase 6a stubs it with a placeholder. |
| `ui/src/components/NotFoundPage.jsx` + test | Replaced by future `marketing/pages/NotFound.jsx` (Phase 6b); Phase 6a stubs it. |
| `ui/src/components/ConsentBanner.jsx` + test | Deferred alongside GA4 (Phase 4 deferred). |
| `ui/src/components/ui/` (entire directory) | shadcn primitives; replaced by CSS-vars styling. |

---

## Execution preconditions

- [ ] **You're on branch `chore/channel-mvp-design-spec`** (the spec branch). The Phase 6a plan PR will include both the plan doc and the foundation implementation.

```bash
git branch --show-current
# expected: chore/channel-mvp-design-spec
```

If not, branch off from there:

```bash
git fetch origin
git checkout chore/channel-mvp-design-spec
```

- [ ] **Verify the design handoff is available** at the expected path.

```bash
test -f ~/Downloads/design_handoff_channel/design-sources/channel.css && echo OK || echo MISSING
test -f ~/Downloads/design_handoff_channel/design-sources/app/icons.jsx && echo OK || echo MISSING
```

Expected: `OK` for both.

---

## Tasks

### Task 1: Copy `channel.css` design tokens

**Files:**
- Create: `ui/src/styles/channel.css`

- [ ] **Step 1: Create the styles directory and copy the file**

```bash
mkdir -p ui/src/styles
cp ~/Downloads/design_handoff_channel/design-sources/channel.css ui/src/styles/channel.css
```

- [ ] **Step 2: Verify the copy is byte-identical and has expected token markers**

```bash
diff ~/Downloads/design_handoff_channel/design-sources/channel.css ui/src/styles/channel.css && echo "byte-identical OK"
grep -c "^\[data-theme=" ui/src/styles/channel.css
# expected: 2 (one for light, one for dark)
grep -c "oklch" ui/src/styles/channel.css
# expected: 30+ (every colour token uses oklch)
```

- [ ] **Step 3: Commit**

```bash
git add ui/src/styles/channel.css
git commit -m "feat(ui): add channel.css design tokens (OKLCH + themes)

Verbatim copy of ~/Downloads/design_handoff_channel/design-sources/channel.css.
Provides --canvas/--surface/--raised/--ink/--accent/--border tokens for both
light and dark themes, radii (--r-xs through --r-pill), shadow tokens, and
Google Fonts @import for the 10 face options. Phase 6a foundation per
docs/superpowers/specs/2026-05-29-channel-mvp-design.md."
```

---

### Task 2: Switch `main.jsx` CSS entry to `channel.css`

**Files:**
- Modify: `ui/src/main.jsx`
- Delete: `ui/src/index.css`

- [ ] **Step 1: Replace the CSS import in `main.jsx`**

Current content of `ui/src/main.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

Change `import "./index.css"` to `import "./styles/channel.css"`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./styles/channel.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 2: Delete the old CSS entry**

```bash
git rm ui/src/index.css
```

- [ ] **Step 3: Verify the build still loads**

```bash
cd ui && npm run build 2>&1 | tail -10 && cd ..
```

Expected: build succeeds. (May still have Tailwind warnings if `@tailwind` directives are referenced anywhere — addressed in Task 13.)

- [ ] **Step 4: Commit**

```bash
git add ui/src/main.jsx
git commit -m "feat(ui): point main.jsx at the channel.css token entry

Removes ui/src/index.css (Tailwind directives only) and replaces it with
ui/src/styles/channel.css from Task 1."
```

---

### Task 3: Add `ChannelMark` component (TDD)

**Files:**
- Create: `ui/src/components/ChannelMark.jsx`
- Create: `ui/src/components/ChannelMark.test.jsx`

- [ ] **Step 1: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ChannelMark from "./ChannelMark.jsx";

describe("ChannelMark", () => {
  it("renders an SVG element with the supplied size", () => {
    const { container } = render(<ChannelMark size={34} />);
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg.getAttribute("width")).toBe("34");
    expect(svg.getAttribute("height")).toBe("34");
    expect(svg.getAttribute("viewBox")).toBe("0 0 24 24");
  });

  it("defaults to size 22 when omitted", () => {
    const { container } = render(<ChannelMark />);
    expect(container.querySelector("svg").getAttribute("width")).toBe("22");
  });

  it("wraps the SVG in a .ch-mark span (channel.css styles this)", () => {
    const { container } = render(<ChannelMark size={24} />);
    const wrapper = container.querySelector("span.ch-mark");
    expect(wrapper).toBeTruthy();
    expect(wrapper.querySelector("svg")).toBeTruthy();
  });

  it("contains two vertical bars between the rounded square", () => {
    const { container } = render(<ChannelMark size={24} />);
    // Outer rounded square + two vertical bars = 3 rects
    const rects = container.querySelectorAll("svg rect");
    expect(rects.length).toBe(3);
  });

  it("accepts a color prop overriding the default accent fill", () => {
    const { container } = render(<ChannelMark size={24} color="hotpink" />);
    const outerRect = container.querySelector("svg rect");
    expect(outerRect.getAttribute("fill")).toBe("hotpink");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ui && npx vitest run src/components/ChannelMark.test.jsx 2>&1 | tail -15 && cd ..
```

Expected: FAIL — "Cannot find module './ChannelMark.jsx'"

- [ ] **Step 3: Read the source ChannelMark from the design**

```bash
grep -A 12 "function ChannelMark" ~/Downloads/design_handoff_channel/design-sources/app/icons.jsx
```

Source uses viewBox `"0 0 24 24"`, default `size=22`, a `color` prop defaulting to `var(--accent)`, an outer rounded square with `rx = size * 0.26` (dynamic), and two `<rect>` bars at `x="6.6"` / `x="14.3"` (width 3.1 each). The SVG is wrapped in `<span className="ch-mark">` — `channel.css` styles that class for inline-flex alignment, so the wrapper is load-bearing.

- [ ] **Step 4: Implement `ChannelMark.jsx`**

Port the source verbatim:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";

/**
 * Channel brand mark: rounded square (--accent fill) with two vertical bars
 * (--on-accent fill) framing a central gap — a literal "channel".
 *
 * Ported verbatim from ~/Downloads/design_handoff_channel/design-sources/app/icons.jsx
 * (the `ChannelMark` export). The .ch-mark span wrapper is styled by
 * channel.css for inline-flex alignment; do not drop it.
 */
export default function ChannelMark({ size = 22, color = "var(--accent)" }) {
  const r = size * 0.26;
  return (
    <span className="ch-mark" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 24 24">
        <rect x="0.5" y="0.5" width="23" height="23" rx={r} fill={color} />
        <rect x="6.6" y="6" width="3.1" height="12" rx="1.2" fill="var(--on-accent)" />
        <rect x="14.3" y="6" width="3.1" height="12" rx="1.2" fill="var(--on-accent)" />
      </svg>
    </span>
  );
}
```

The structure mirrors the source line-for-line — same viewBox, same coordinates, same `size * 0.26` corner-radius computation, same `.ch-mark` wrapper, same `color` prop.

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/components/ChannelMark.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add ui/src/components/ChannelMark.jsx ui/src/components/ChannelMark.test.jsx
git commit -m "feat(ui): add ChannelMark brand SVG component

Two vertical bars in a rounded square — the literal 'channel' mark. Uses
--accent + --on-accent CSS vars so it tracks the user's accent hue + theme.
Ported from design-sources/app/icons.jsx."
```

---

### Task 4: Add `Icon` component (TDD)

**Files:**
- Create: `ui/src/components/Icon.jsx`
- Create: `ui/src/components/Icon.test.jsx`

- [ ] **Step 1: Inspect the full icon set in the design source**

```bash
grep -c "^    case '" ~/Downloads/design_handoff_channel/design-sources/app/icons.jsx
# count of icon names — expected: 30+
grep "^    case '" ~/Downloads/design_handoff_channel/design-sources/app/icons.jsx | sed -E "s/.*case '([^']+)'.*/\1/"
# list of all icon names
```

- [ ] **Step 2: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Icon from "./Icon.jsx";

describe("Icon", () => {
  it("renders an SVG for the 'plus' name", () => {
    const { container } = render(<Icon name="plus" />);
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg.getAttribute("viewBox")).toBe("0 0 24 24");
  });

  it("applies the supplied size to width and height", () => {
    const { container } = render(<Icon name="plus" size={28} />);
    const svg = container.querySelector("svg");
    expect(svg.getAttribute("width")).toBe("28");
    expect(svg.getAttribute("height")).toBe("28");
  });

  it("applies the supplied stroke-width", () => {
    const { container } = render(<Icon name="plus" stroke={2.4} />);
    const svg = container.querySelector("svg");
    expect(svg.getAttribute("stroke-width")).toBe("2.4");
  });

  it("uses currentColor for stroke", () => {
    const { container } = render(<Icon name="plus" />);
    expect(container.querySelector("svg").getAttribute("stroke")).toBe("currentColor");
  });

  it("returns null for an unknown name", () => {
    const { container } = render(<Icon name="not-a-real-icon" />);
    expect(container.querySelector("svg")).toBeFalsy();
  });

  // Every icon name in the set must render an SVG. Names listed in the design
  // source — see Step 1 grep. If you add a name here, also add a case to
  // Icon.jsx; if you remove a case, also remove it here.
  it.each([
    "plus", "chat", "code", "projects", "artifacts", "customize", "search",
    "sidebar", "mic", "arrow-up", "arrow-right", "chevron-down", "chevron-right",
    "attach", "image", "write", "learn", "settings", "sun", "moon", "download",
    "leaf", "copy", "refresh",
  ])("renders an SVG for the '%s' name", (name) => {
    const { container } = render(<Icon name={name} />);
    expect(container.querySelector("svg")).toBeTruthy();
  });
});
```

> **Update the `it.each` list to match the full set** from Step 1's grep. The list above is the minimum that should be present per the design's sidebar + composer + customize UI; if the source has more cases, add them. If the source has fewer of the listed names, remove the missing ones (don't fake them — the test will fail honestly).

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/components/Icon.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `Icon.jsx`**

Port the full `Icon` function from `~/Downloads/design_handoff_channel/design-sources/app/icons.jsx`. The signature in the source is:

```js
function Icon({ name, size = 18, stroke = 1.6, style = {} }) {
  const p = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
              stroke: 'currentColor', strokeWidth: stroke,
              strokeLinecap: 'round', strokeLinejoin: 'round',
              style: { display: 'block', ...style } };
  switch (name) {
    case 'plus': return <svg {...p}><path d="M12 5v14M5 12h14"/></svg>;
    // ... [many more cases]
    default: return null;
  }
}
```

Translate to module-default export with the copyright header:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";

/**
 * 24×24 stroke icon set. `currentColor` stroke so icons inherit the text
 * colour of the surrounding context.
 *
 * Ported from ~/Downloads/design_handoff_channel/design-sources/app/icons.jsx.
 * Keep cases in sync with that file if the design adds/removes glyphs.
 */
export default function Icon({ name, size = 18, stroke = 1.6, style = {} }) {
  const p = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: stroke,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    style: { display: "block", ...style },
  };
  switch (name) {
    // Paste every case from the source file verbatim here.
    // Use double-quoted strings (existing project convention) instead of
    // single-quoted. Otherwise the SVG content is identical.
    default:
      return null;
  }
}
```

> **Paste every case** from the source verbatim (with single→double quote translation). Do not abbreviate the switch with "… etc". The implementer must enumerate each glyph; coverage requires every branch be hit by the `it.each` test in Step 2.

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/components/Icon.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — all icon-name cases render SVGs.

- [ ] **Step 6: Commit**

```bash
git add ui/src/components/Icon.jsx ui/src/components/Icon.test.jsx
git commit -m "feat(ui): add Icon component (24×24 stroke set)

Ported from design-sources/app/icons.jsx. Each case is a tested branch; the
it.each test fails honestly if a case is removed without updating the test."
```

---

### Task 5: Add `useChannelPrefs` hook (TDD)

**Files:**
- Create: `ui/src/hooks/useChannelPrefs.js`
- Create: `ui/src/hooks/useChannelPrefs.test.js`

- [ ] **Step 1: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useChannelPrefs, DEFAULTS, STORAGE_KEYS } from "./useChannelPrefs.js";

const allKeys = Object.values(STORAGE_KEYS);

describe("useChannelPrefs", () => {
  let storage;

  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    // matchMedia is used for the dark-mode default
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    // Reset documentElement dataset between tests
    for (const a of Array.from(document.documentElement.attributes)) {
      if (a.name.startsWith("data-")) document.documentElement.removeAttribute(a.name);
    }
  });

  afterEach(() => vi.unstubAllGlobals());

  it("returns the default value for each pref when storage is empty", () => {
    const { result } = renderHook(() => useChannelPrefs());
    expect(result.current.theme).toBe(DEFAULTS.theme);
    expect(result.current.accent).toBe(DEFAULTS.accent);
    expect(result.current.density).toBe(DEFAULTS.density);
    expect(result.current.shape).toBe(DEFAULTS.shape);
    expect(result.current.font).toBe(DEFAULTS.font);
    expect(result.current.model).toBe(DEFAULTS.model);
    expect(result.current.effort).toBe(DEFAULTS.effort);
    expect(result.current.siteTheme).toBe(DEFAULTS.siteTheme);
  });

  it("reads each pref from localStorage when present", () => {
    storage[STORAGE_KEYS.theme] = "light";
    storage[STORAGE_KEYS.accent] = "150";
    storage[STORAGE_KEYS.density] = "compact";
    storage[STORAGE_KEYS.shape] = "sharp";
    storage[STORAGE_KEYS.font] = "space";
    storage[STORAGE_KEYS.model] = "claude-haiku-4-5";
    storage[STORAGE_KEYS.effort] = "Low";
    storage[STORAGE_KEYS.siteTheme] = "dark";
    const { result } = renderHook(() => useChannelPrefs());
    expect(result.current.theme).toBe("light");
    expect(result.current.accent).toBe("150");
    expect(result.current.density).toBe("compact");
    expect(result.current.shape).toBe("sharp");
    expect(result.current.font).toBe("space");
    expect(result.current.model).toBe("claude-haiku-4-5");
    expect(result.current.effort).toBe("Low");
    expect(result.current.siteTheme).toBe("dark");
  });

  it("writes pref to localStorage and applies data-{pref} on <html>", () => {
    const { result } = renderHook(() => useChannelPrefs());
    act(() => result.current.setTheme("light"));
    expect(storage[STORAGE_KEYS.theme]).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");

    act(() => result.current.setAccent("300"));
    expect(storage[STORAGE_KEYS.accent]).toBe("300");
    expect(document.documentElement.getAttribute("data-accent")).toBe("300");

    act(() => result.current.setDensity("compact"));
    expect(document.documentElement.getAttribute("data-density")).toBe("compact");

    act(() => result.current.setShape("sharp"));
    expect(document.documentElement.getAttribute("data-shape")).toBe("sharp");

    act(() => result.current.setFont("space"));
    expect(document.documentElement.getAttribute("data-font")).toBe("space");

    act(() => result.current.setModel("claude-haiku-4-5"));
    expect(document.documentElement.getAttribute("data-model")).toBe("claude-haiku-4-5");

    act(() => result.current.setEffort("Max"));
    expect(document.documentElement.getAttribute("data-effort")).toBe("Max");
  });

  it("toggleTheme flips dark ↔ light", () => {
    storage[STORAGE_KEYS.theme] = "dark";
    const { result } = renderHook(() => useChannelPrefs());
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("light");
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("dark");
  });

  it("toggleSiteTheme flips dark ↔ light independently of theme", () => {
    storage[STORAGE_KEYS.theme] = "dark";
    storage[STORAGE_KEYS.siteTheme] = "light";
    const { result } = renderHook(() => useChannelPrefs());
    act(() => result.current.toggleSiteTheme());
    expect(result.current.siteTheme).toBe("dark");
    expect(result.current.theme).toBe("dark"); // unchanged
  });

  it("STORAGE_KEYS are all distinct and channel-prefixed", () => {
    const keys = Object.values(STORAGE_KEYS);
    expect(new Set(keys).size).toBe(keys.length); // all distinct
    for (const k of keys) expect(k).toMatch(/^channel-/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ui && npx vitest run src/hooks/useChannelPrefs.test.js 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `useChannelPrefs.js`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useState } from "react";

export const STORAGE_KEYS = Object.freeze({
  theme:     "channel-theme",
  siteTheme: "channel-site-theme",
  accent:    "channel-accent",
  density:   "channel-density",
  shape:     "channel-shape",
  font:      "channel-font",
  model:     "channel-model",
  effort:    "channel-effort",
});

export const DEFAULTS = Object.freeze({
  theme:     "dark",       // app defaults dark per design
  siteTheme: "light",      // marketing defaults light per design
  accent:    "42",         // Clay
  density:   "cozy",
  shape:     "soft",
  font:      "figtree",    // ship Figtree per design README
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

function applyAttr(name, value) {
  document.documentElement.setAttribute("data-" + name, value);
}

function writePref(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* localStorage may throw in private mode; degrade silently */
  }
}

export function useChannelPrefs() {
  const [theme, _setTheme] = useState(() => readPref(STORAGE_KEYS.theme, DEFAULTS.theme));
  const [siteTheme, _setSiteTheme] = useState(() => readPref(STORAGE_KEYS.siteTheme, DEFAULTS.siteTheme));
  const [accent, _setAccent] = useState(() => readPref(STORAGE_KEYS.accent, DEFAULTS.accent));
  const [density, _setDensity] = useState(() => readPref(STORAGE_KEYS.density, DEFAULTS.density));
  const [shape, _setShape] = useState(() => readPref(STORAGE_KEYS.shape, DEFAULTS.shape));
  const [font, _setFont] = useState(() => readPref(STORAGE_KEYS.font, DEFAULTS.font));
  const [model, _setModel] = useState(() => readPref(STORAGE_KEYS.model, DEFAULTS.model));
  const [effort, _setEffort] = useState(() => readPref(STORAGE_KEYS.effort, DEFAULTS.effort));

  useEffect(() => { applyAttr("theme", theme);     writePref(STORAGE_KEYS.theme, theme); }, [theme]);
  useEffect(() => {                                writePref(STORAGE_KEYS.siteTheme, siteTheme); }, [siteTheme]);
  useEffect(() => { applyAttr("accent", accent);   writePref(STORAGE_KEYS.accent, accent); }, [accent]);
  useEffect(() => { applyAttr("density", density); writePref(STORAGE_KEYS.density, density); }, [density]);
  useEffect(() => { applyAttr("shape", shape);     writePref(STORAGE_KEYS.shape, shape); }, [shape]);
  useEffect(() => { applyAttr("font", font);       writePref(STORAGE_KEYS.font, font); }, [font]);
  useEffect(() => { applyAttr("model", model);     writePref(STORAGE_KEYS.model, model); }, [model]);
  useEffect(() => { applyAttr("effort", effort);   writePref(STORAGE_KEYS.effort, effort); }, [effort]);

  // Apply --accent-h as an inline CSS custom property so channel.css's
  // hue-driven --accent / --accent-ink / --accent-soft track the user's hue.
  useEffect(() => {
    document.documentElement.style.setProperty("--accent-h", accent);
  }, [accent]);

  const toggleTheme = useCallback(() => _setTheme((t) => (t === "dark" ? "light" : "dark")), []);
  const toggleSiteTheme = useCallback(() => _setSiteTheme((t) => (t === "dark" ? "light" : "dark")), []);

  return {
    theme, setTheme: _setTheme, toggleTheme,
    siteTheme, setSiteTheme: _setSiteTheme, toggleSiteTheme,
    accent, setAccent: _setAccent,
    density, setDensity: _setDensity,
    shape, setShape: _setShape,
    font, setFont: _setFont,
    model, setModel: _setModel,
    effort, setEffort: _setEffort,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ui && npx vitest run src/hooks/useChannelPrefs.test.js 2>&1 | tail -10 && cd ..
```

Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add ui/src/hooks/useChannelPrefs.js ui/src/hooks/useChannelPrefs.test.js
git commit -m "feat(ui): add useChannelPrefs hook (7 prefs + siteTheme)

Single hook for theme/accent/density/shape/font/model/effort + siteTheme.
Writes data-{pref} to <html> + persists each to localStorage under
channel-{pref} keys. Replaces the simpler useTheme hook in the next task."
```

---

### Task 6: Remove the old `useTheme` hook

**Files:**
- Delete: `ui/src/hooks/useTheme.js`
- Delete: `ui/src/hooks/useTheme.test.js`

(`App.jsx` still imports `useTheme` — that import is replaced in Task 10. Tests for App.jsx will be rewritten in Task 10. For now, delete the hook; the test suite will fail until Task 10 lands. This is intentional — we want the failure to track the actual removal.)

- [ ] **Step 1: Delete the files**

```bash
git rm ui/src/hooks/useTheme.js ui/src/hooks/useTheme.test.js
```

- [ ] **Step 2: Confirm grep finds no other consumers (outside `App.jsx`)**

```bash
grep -rn "useTheme" ui/src/ --include="*.jsx" --include="*.js" 2>/dev/null | grep -v "useTheme.js\|useTheme.test.js\|useChannelPrefs"
# expected: only App.jsx and App.test.jsx (handled in Task 10)
```

- [ ] **Step 3: Commit**

```bash
git commit -m "refactor(ui): remove useTheme hook (replaced by useChannelPrefs)

App.jsx still imports useTheme — it gets rewritten in the App.jsx task and
will pick up useChannelPrefs there. Test suite will fail on this commit
alone; passes again after the App.jsx rewrite."
```

---

### Task 7: Extract auth helpers to `lib/auth.js` (TDD)

**Files:**
- Create: `ui/src/lib/auth.js`
- Create: `ui/src/lib/auth.test.js`

- [ ] **Step 1: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import { TOKEN_KEY, isTokenValid, parseToken } from "./auth.js";

function makeToken({ expOffsetSeconds = 3600, role = "user", email = "u@example.com", sub = "user-1" } = {}) {
  const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
  const payload = btoa(JSON.stringify({ exp, sub, role, email }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("TOKEN_KEY", () => {
  it("matches the legacy starter_mgmt_token localStorage key", () => {
    expect(TOKEN_KEY).toBe("starter_mgmt_token");
  });
});

describe("parseToken", () => {
  it("parses a valid token into its claims object", () => {
    const t = makeToken({ role: "admin" });
    const claims = parseToken(t);
    expect(claims.role).toBe("admin");
    expect(claims.exp).toBeGreaterThan(Math.floor(Date.now() / 1000));
  });

  it("returns null for null/undefined/empty inputs", () => {
    expect(parseToken(null)).toBeNull();
    expect(parseToken(undefined)).toBeNull();
    expect(parseToken("")).toBeNull();
  });

  it("returns null for a malformed token", () => {
    expect(parseToken("not.a.jwt")).toBeNull();
    expect(parseToken("only-one-segment")).toBeNull();
  });
});

describe("isTokenValid", () => {
  it("returns true for a future-exp token", () => {
    expect(isTokenValid(makeToken({ expOffsetSeconds: 3600 }))).toBe(true);
  });
  it("returns false for a past-exp token", () => {
    expect(isTokenValid(makeToken({ expOffsetSeconds: -3600 }))).toBe(false);
  });
  it("returns false for null/undefined/empty/malformed inputs", () => {
    expect(isTokenValid(null)).toBe(false);
    expect(isTokenValid(undefined)).toBe(false);
    expect(isTokenValid("")).toBe(false);
    expect(isTokenValid("not.a.jwt")).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ui && npx vitest run src/lib/auth.test.js 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `lib/auth.js`** (extracted verbatim from current `App.jsx:15-34`)

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.

export const TOKEN_KEY = "starter_mgmt_token";

export function parseToken(token) {
  if (!token) return null;
  try {
    return JSON.parse(atob(token.split(".")[1].replaceAll("-", "+").replaceAll("_", "/")));
  } catch {
    return null;
  }
}

export function isTokenValid(token) {
  const payload = parseToken(token);
  return payload ? payload.exp * 1000 > Date.now() : false;
}
```

- [ ] **Step 4: Create `ui/src/lib/` directory if it does not exist**

```bash
mkdir -p ui/src/lib
```

(May already exist — `ui/src/lib/utils.js` is mentioned in `CLAUDE.md`. Check with `ls ui/src/lib/`.)

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/lib/auth.test.js 2>&1 | tail -10 && cd ..
```

Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add ui/src/lib/auth.js ui/src/lib/auth.test.js
git commit -m "feat(ui): extract auth helpers (parseToken, isTokenValid) to lib/auth

Moved verbatim from App.jsx. AuthGate (next task) and the rewritten App.jsx
both import from here, so the helpers need their own module."
```

---

### Task 8: Add `AuthGate` component (TDD)

**Files:**
- Create: `ui/src/components/AuthGate.jsx`
- Create: `ui/src/components/AuthGate.test.jsx`

- [ ] **Step 1: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AuthGate from "./AuthGate.jsx";
import { TOKEN_KEY } from "../lib/auth.js";

function makeToken({ expOffsetSeconds = 3600 } = {}) {
  const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email: "u@ex.com" }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

function renderAt(path, storage) {
  vi.stubGlobal("localStorage", {
    getItem: (k) => storage[k] ?? null,
    setItem: (k, v) => { storage[k] = String(v); },
    removeItem: (k) => { delete storage[k]; },
  });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/login" element={<div data-testid="login" />} />
        <Route path="/app" element={<AuthGate><div data-testid="app-home" /></AuthGate>} />
        <Route path="/app/projects" element={<AuthGate><div data-testid="projects" /></AuthGate>} />
      </Routes>
    </MemoryRouter>
  );
}

describe("AuthGate", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders children when the token is valid", () => {
    renderAt("/app", { [TOKEN_KEY]: makeToken() });
    expect(screen.getByTestId("app-home")).toBeTruthy();
    expect(screen.queryByTestId("login")).toBeNull();
  });

  it("redirects to /app/login when no token is stored", () => {
    renderAt("/app", {});
    expect(screen.getByTestId("login")).toBeTruthy();
    expect(screen.queryByTestId("app-home")).toBeNull();
  });

  it("redirects to /app/login when token is expired", () => {
    renderAt("/app", { [TOKEN_KEY]: makeToken({ expOffsetSeconds: -3600 }) });
    expect(screen.getByTestId("login")).toBeTruthy();
  });

  it("redirects to /app/login when token is malformed", () => {
    renderAt("/app", { [TOKEN_KEY]: "not.a.jwt" });
    expect(screen.getByTestId("login")).toBeTruthy();
  });

  it("gates other /app/* routes too", () => {
    renderAt("/app/projects", {});
    expect(screen.getByTestId("login")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ui && npx vitest run src/components/AuthGate.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `AuthGate.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Navigate } from "react-router-dom";
import { TOKEN_KEY, isTokenValid } from "../lib/auth.js";

/**
 * Wraps any /app/* route. Reads the mgmt JWT from localStorage and either
 * renders the children or redirects to /app/login.
 *
 * NB: this is a render-time gate only. The mgmt JWT itself is verified by
 * the backend on every /api/* request; the frontend gate is UX, not
 * security.
 */
export default function AuthGate({ children }) {
  const token = localStorage.getItem(TOKEN_KEY) ?? "";
  if (!isTokenValid(token)) {
    return <Navigate to="/app/login" replace />;
  }
  return children;
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ui && npx vitest run src/components/AuthGate.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/AuthGate.jsx ui/src/components/AuthGate.test.jsx
git commit -m "feat(ui): add AuthGate component for /app/* route protection

Reads starter_mgmt_token from localStorage, validates exp client-side, and
redirects unauthenticated visits to /app/login. The actual security boundary
is server-side on every /api/* request — this gate is for UX (don't render
the app shell behind a sign-in)."
```

---

### Task 9: Restyle `ErrorBoundary` to channel tokens

**Files:**
- Modify: `ui/src/components/ErrorBoundary.jsx`
- Modify: `ui/src/components/ErrorBoundary.test.jsx` (if it asserts on old style values)

- [ ] **Step 1: Inspect existing styling**

```bash
cat ui/src/components/ErrorBoundary.jsx
```

Identify any hardcoded Tailwind classnames or inline styles that reference template colours.

- [ ] **Step 2: Replace with channel tokens**

The render method should produce a centered card on `var(--canvas)` with `var(--ink)` text, an accent-colored "Reload" button, and use `var(--raised)` for any inner panel surface. Concretely, the JSX should look like (adapt to the existing structure — preserve the `componentDidCatch` + `getDerivedStateFromError` logic):

```jsx
render() {
  if (this.state.hasError) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--canvas)",
          color: "var(--ink)",
          fontFamily: "var(--font-sans)",
          padding: "24px",
        }}
      >
        <div
          style={{
            background: "var(--raised)",
            border: "1px solid var(--border)",
            borderRadius: "var(--r-lg)",
            boxShadow: "var(--shadow-md)",
            padding: "32px",
            maxWidth: "480px",
            width: "100%",
            textAlign: "center",
          }}
        >
          <h1 style={{ fontSize: "26px", fontWeight: 600, marginBottom: "12px" }}>
            Something went wrong
          </h1>
          <p style={{ color: "var(--ink-soft)", marginBottom: "20px" }}>
            {this.state.message || "An unexpected error occurred."}
          </p>
          <button
            onClick={this.handleReload}
            style={{
              background: "var(--accent)",
              color: "var(--on-accent)",
              border: "none",
              borderRadius: "var(--r-md)",
              padding: "10px 18px",
              fontSize: "14px",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
  return this.props.children;
}
```

- [ ] **Step 3: Update test if it asserts on old colours/classes**

If the existing `ErrorBoundary.test.jsx` asserts on `bg-white` or specific hex colours, update the assertions to use the new token-based styles (use `getComputedStyle` against the rendered element's style attribute, or simply assert structural elements: heading text, Reload button presence).

```bash
cd ui && npx vitest run src/components/ErrorBoundary.test.jsx 2>&1 | tail -10 && cd ..
```

Iterate until it passes.

- [ ] **Step 4: Commit**

```bash
git add ui/src/components/ErrorBoundary.jsx ui/src/components/ErrorBoundary.test.jsx
git commit -m "style(ui): restyle ErrorBoundary with channel tokens

Replaces Tailwind classnames + template colours with --canvas/--ink/--raised/
--accent CSS-var styles. Component logic (getDerivedStateFromError,
componentDidCatch) unchanged."
```

---

### Task 10: Rewrite `App.jsx` as the marketing + app route shell

**Files:**
- Modify: `ui/src/App.jsx`
- Modify: `ui/src/App.test.jsx`

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import { TOKEN_KEY } from "./lib/auth.js";

function makeToken({ expOffsetSeconds = 3600 } = {}) {
  const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email: "u@ex.com" }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("App routing", () => {
  let storage;

  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
  });

  afterEach(() => { vi.unstubAllGlobals(); window.history.pushState({}, "", "/"); });

  it.each([
    ["/", "marketing-home"],
    ["/product", "marketing-product"],
    ["/models", "marketing-models"],
    ["/pricing", "marketing-pricing"],
    ["/download", "marketing-download"],
    ["/about", "marketing-about"],
    ["/blog", "marketing-blog"],
    ["/careers", "marketing-careers"],
    ["/privacy", "marketing-privacy"],
  ])("renders marketing route %s with placeholder", async (path, testId) => {
    window.history.pushState({}, "", path);
    await act(async () => render(<App />));
    expect(screen.getByTestId(testId)).toBeTruthy();
  });

  it("renders 404 placeholder for unknown routes", async () => {
    window.history.pushState({}, "", "/this-route-does-not-exist");
    await act(async () => render(<App />));
    expect(screen.getByTestId("marketing-notfound")).toBeTruthy();
  });

  it("renders the app-login placeholder at /app/login (no auth required)", async () => {
    window.history.pushState({}, "", "/app/login");
    await act(async () => render(<App />));
    expect(screen.getByTestId("app-login")).toBeTruthy();
  });

  it("redirects /app to /app/login when no token", async () => {
    window.history.pushState({}, "", "/app");
    await act(async () => render(<App />));
    expect(screen.getByTestId("app-login")).toBeTruthy();
  });

  it("renders the app-home placeholder at /app with a valid token", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app");
    await act(async () => render(<App />));
    expect(screen.getByTestId("app-home")).toBeTruthy();
  });

  it.each([
    ["/app/c/r1", "app-conversation"],
    ["/app/projects", "app-projects"],
    ["/app/projects/p1", "app-project-detail"],
    ["/app/artifacts", "app-artifacts"],
    ["/app/customize", "app-customize"],
  ])("renders the %s placeholder when authed", async (path, testId) => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", path);
    await act(async () => render(<App />));
    expect(screen.getByTestId(testId)).toBeTruthy();
  });

  it("applies the saved theme to <html> on mount", async () => {
    storage["channel-theme"] = "light";
    await act(async () => render(<App />));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ui && npx vitest run src/App.test.jsx 2>&1 | tail -15 && cd ..
```

Expected: FAIL — multiple test cases find no matching test ID.

- [ ] **Step 3: Rewrite `App.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import AuthGate from "./components/AuthGate.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { useChannelPrefs } from "./hooks/useChannelPrefs.js";

// Placeholders — each subsequent phase (6b/6c/6d/6e/6f) replaces the
// matching placeholder with a real component.
const ph = (testid, label) => (
  <div data-testid={testid} style={{ padding: 24, color: "var(--ink)" }}>
    {label} — placeholder, implemented in a later phase.
  </div>
);

const MarketingHome      = () => ph("marketing-home", "Marketing: Home");
const MarketingProduct   = () => ph("marketing-product", "Marketing: Product");
const MarketingModels    = () => ph("marketing-models", "Marketing: Models");
const MarketingPricing   = () => ph("marketing-pricing", "Marketing: Pricing");
const MarketingDownload  = () => ph("marketing-download", "Marketing: Download");
const MarketingAbout     = () => ph("marketing-about", "Marketing: About");
const MarketingBlog      = () => ph("marketing-blog", "Marketing: Blog");
const MarketingCareers   = () => ph("marketing-careers", "Marketing: Careers");
const MarketingPrivacy   = () => ph("marketing-privacy", "Marketing: Privacy");
const MarketingNotFound  = () => ph("marketing-notfound", "404");

const AppLogin           = () => ph("app-login", "App: Login");
const AppHome            = () => ph("app-home", "App: Home");
const AppConversation    = () => ph("app-conversation", "App: Conversation");
const AppProjects        = () => ph("app-projects", "App: Projects");
const AppProjectDetail   = () => ph("app-project-detail", "App: Project Detail");
const AppArtifacts       = () => ph("app-artifacts", "App: Artifacts");
const AppCustomize       = () => ph("app-customize", "App: Customize");

export default function App() {
  // Apply theme/accent/density/etc. to <html> on every mount.
  useChannelPrefs();

  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Routes>
          {/* Marketing — public */}
          <Route path="/"          element={<MarketingHome />} />
          <Route path="/product"   element={<MarketingProduct />} />
          <Route path="/models"    element={<MarketingModels />} />
          <Route path="/pricing"   element={<MarketingPricing />} />
          <Route path="/download"  element={<MarketingDownload />} />
          <Route path="/about"     element={<MarketingAbout />} />
          <Route path="/blog"      element={<MarketingBlog />} />
          <Route path="/careers"   element={<MarketingCareers />} />
          <Route path="/privacy"   element={<MarketingPrivacy />} />

          {/* App — /app/login is public; everything else gates on the JWT */}
          <Route path="/app/login"          element={<AppLogin />} />
          <Route path="/app"                element={<AuthGate><AppHome /></AuthGate>} />
          <Route path="/app/c/:id"          element={<AuthGate><AppConversation /></AuthGate>} />
          <Route path="/app/projects"       element={<AuthGate><AppProjects /></AuthGate>} />
          <Route path="/app/projects/:id"   element={<AuthGate><AppProjectDetail /></AuthGate>} />
          <Route path="/app/artifacts"      element={<AuthGate><AppArtifacts /></AuthGate>} />
          <Route path="/app/customize"      element={<AuthGate><AppCustomize /></AuthGate>} />

          {/* Anything else (marketing or app catch-all) → branded 404 */}
          <Route path="*" element={<MarketingNotFound />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ui && npx vitest run src/App.test.jsx 2>&1 | tail -15 && cd ..
```

Expected: PASS (all routing + auth + theme tests).

- [ ] **Step 5: Commit**

```bash
git add ui/src/App.jsx ui/src/App.test.jsx
git commit -m "feat(ui): rewrite App as marketing + app route shell

Single route tree: 10 public marketing routes, 7 app routes (one public
/app/login, six gated by AuthGate), and a wildcard 404. Every route renders
a placeholder testid'd \"<surface>-<route>\" — subsequent phases (6b/6c/6d/
6e/6f) replace placeholders with real components."
```

---

### Task 11: Delete obsolete template components

**Files:**
- Delete (with tests):
  - `ui/src/components/UsersPanel.jsx`
  - `ui/src/components/PageLayout.jsx`
  - `ui/src/components/EmptyState.jsx`
  - `ui/src/components/LoginPage.jsx`
  - `ui/src/components/NotFoundPage.jsx`
  - `ui/src/components/ConsentBanner.jsx`

- [ ] **Step 1: Confirm no remaining imports**

```bash
grep -rn "UsersPanel\|PageLayout\|EmptyState\|LoginPage\|NotFoundPage\|ConsentBanner" ui/src/ --include="*.jsx" --include="*.js" 2>/dev/null | grep -v ".test.jsx:"
```

Expected: empty output (App.jsx no longer imports these after Task 10).

If anything remains, fix that file first — don't leave dangling imports.

- [ ] **Step 2: Delete the files**

```bash
git rm \
  ui/src/components/UsersPanel.jsx \
  ui/src/components/UsersPanel.test.jsx \
  ui/src/components/PageLayout.jsx \
  ui/src/components/PageLayout.test.jsx \
  ui/src/components/EmptyState.jsx \
  ui/src/components/EmptyState.test.jsx \
  ui/src/components/LoginPage.jsx \
  ui/src/components/LoginPage.test.jsx \
  ui/src/components/NotFoundPage.jsx \
  ui/src/components/NotFoundPage.test.jsx \
  ui/src/components/ConsentBanner.jsx \
  ui/src/components/ConsentBanner.test.jsx
```

- [ ] **Step 3: Run full UI test suite**

```bash
cd ui && npm run test 2>&1 | tail -15 && cd ..
```

Expected: PASS — no test failures from deleted modules.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(ui): remove template residual components

Phase 6a removes the admin / scaffolding components inherited from the
template that have no place in the channel design:

- UsersPanel, PageLayout, EmptyState — admin scaffolding, replaced by the
  app's sidebar + main pane in Phase 6c
- LoginPage — replaced by app/Login.jsx in Phase 6c
- NotFoundPage — replaced by marketing/pages/NotFound.jsx in Phase 6b
- ConsentBanner — deferred alongside GA4 (re-added when GA4 lands)

App.jsx no longer imports any of these after the previous task's rewrite."
```

---

### Task 12: Remove shadcn primitives directory

**Files:**
- Delete: `ui/src/components/ui/` (entire directory)

- [ ] **Step 1: Inspect what's there**

```bash
ls ui/src/components/ui/
```

- [ ] **Step 2: Confirm no remaining imports**

```bash
grep -rn "components/ui/" ui/src/ --include="*.jsx" --include="*.js" 2>/dev/null
```

Expected: empty.

- [ ] **Step 3: Delete the directory**

```bash
git rm -r ui/src/components/ui/
```

- [ ] **Step 4: Run tests**

```bash
cd ui && npm run test 2>&1 | tail -10 && cd ..
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "chore(ui): remove shadcn primitives (replaced by CSS-vars styling)

Phase 6a moves to a CSS-var token system. Button/Dialog/Toaster shadcn
primitives have no consumers after the App.jsx rewrite + the template
component sweep. Drop the directory; subsequent phases style components
directly with --canvas/--raised/--accent/etc."
```

---

### Task 13: Uninstall Tailwind + supporting deps

**Files:**
- Modify: `ui/package.json`
- Modify: `ui/vite.config.js`

- [ ] **Step 1: Inspect the current vite.config.js**

```bash
cat ui/vite.config.js
```

Look for `@tailwindcss/vite` import + usage in the `plugins` array.

- [ ] **Step 2: Remove the Tailwind plugin from `vite.config.js`**

If the file looks like:

```js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // ...
});
```

Remove the tailwind import + the `tailwindcss()` call from `plugins`. The result should look like:

```js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // ... preserve the rest of the config
});
```

- [ ] **Step 3: Uninstall the Tailwind + shadcn deps from `ui/`**

```bash
cd ui && npm uninstall \
  tailwindcss \
  @tailwindcss/vite \
  @radix-ui/react-dialog \
  @radix-ui/react-slot \
  class-variance-authority \
  tailwind-merge \
  sonner \
  2>&1 | tail -5 && cd ..
```

(`clsx` and `lucide-react` stay — they are framework-agnostic and may be used by surviving components. Re-evaluate in a later phase if they're truly unused.)

- [ ] **Step 4: Verify package.json is clean**

```bash
grep -E "tailwindcss|@tailwindcss|radix|class-variance-authority|tailwind-merge|sonner" ui/package.json
```

Expected: no matches (one possible exception: `@vitest/coverage-v8` includes `v8` which won't match these patterns).

- [ ] **Step 5: Build + test to confirm nothing broke**

```bash
cd ui && npm run build 2>&1 | tail -10 && npm run test 2>&1 | tail -10 && cd ..
```

Expected: both succeed.

- [ ] **Step 6: Commit**

```bash
git add ui/package.json ui/package-lock.json ui/vite.config.js
git commit -m "chore(ui): uninstall Tailwind + shadcn deps

After the App.jsx rewrite + template component sweep, nothing imports
tailwindcss, @tailwindcss/vite, @radix-ui/react-dialog, @radix-ui/react-slot,
class-variance-authority, tailwind-merge, or sonner. Drop the deps.

Tailwind didn't have a config file (v4 + the vite plugin handled defaults),
so only the vite-plugin import + plugins[] entry needed removing. clsx and
lucide-react are kept — they're framework-agnostic and may serve future
phases."
```

---

### Task 14: Update `CLAUDE.md` file-tree + UI section

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Read the current file-tree section**

```bash
grep -n "ui/" CLAUDE.md | head -20
```

Identify the file-tree block that lists `ui/src/components/...`. The current entries probably include UsersPanel, EmptyState, LoginPage — those need removing. New entries: `ChannelMark.jsx`, `Icon.jsx`, `AuthGate.jsx`, plus `styles/channel.css` and `lib/auth.js`.

- [ ] **Step 2: Update the file-tree block**

Replace the UI section's component list to match the post-Phase-6a state. For example, change:

```
│   │   └── components/
│   │       ├── ui/
│   │       │   └── button.jsx # shadcn/ui Button primitive
│   │       ├── Dashboard.jsx  # Admin: CloudWatch metrics + cost data
│   │       ├── UsersPanel.jsx # Admin: user list + management
│   │       ├── EmptyState.jsx # Shared empty-state illustrations
│   │       ├── PageLayout.jsx # Shared page layout + navbar
│   │       └── LoginPage.jsx
```

…to:

```
│   ├── styles/
│   │   └── channel.css        # Design tokens (OKLCH themes, radii, shadows, fonts)
│   ├── lib/
│   │   ├── auth.js            # parseToken, isTokenValid, TOKEN_KEY
│   │   └── utils.js           # Shared utility functions (cn, etc.)
│   ├── hooks/
│   │   ├── useChannelPrefs.js # theme/accent/density/shape/font/model/effort + siteTheme
│   │   └── useRelativeTime.js
│   └── components/
│       ├── AuthGate.jsx       # Redirects /app/* visits to /app/login when no JWT
│       ├── ChannelMark.jsx    # Brand mark SVG
│       ├── ErrorBoundary.jsx  # Token-styled error fallback
│       └── Icon.jsx           # 24×24 stroke icon set
```

(Adapt to the existing comment style in CLAUDE.md.)

- [ ] **Step 3: Update the `Management UI` and `UI conventions` sections**

Find each and remove references to deleted components/features. For example:

- Drop the "Tab set: Users, Dashboard (admin only)" bullet — there are no admin tabs in Phase 6a.
- Drop the "shadcn/ui primitives" bullet — Tailwind/shadcn is gone.
- Drop the "Anonymous inline functions" v8 caveat *only if* you've validated it's no longer relevant — leave it for now.
- Keep the `vi.useFakeTimers()` caveat; still relevant for any timer-using component.
- Add a bullet: "**CSS-vars only** — token system in `ui/src/styles/channel.css`; no Tailwind."

- [ ] **Step 4: Verify the doc parses + isn't broken**

```bash
head -100 CLAUDE.md
```

Visually confirm the file-tree is still valid Markdown.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): update file tree + UI conventions for Phase 6a foundation

Remove references to deleted template components (UsersPanel, Dashboard,
PageLayout, EmptyState, LoginPage, NotFoundPage, ConsentBanner) and shadcn
primitives. Add ChannelMark, Icon, AuthGate, useChannelPrefs, lib/auth, and
styles/channel.css to the structure map. Drop the Tailwind / shadcn bullets
from UI conventions; note CSS-vars + channel.css as the token system."
```

---

### Task 15: Final pre-push gate

**Files:** (none modified)

- [ ] **Step 1: Run the full pre-push gate**

```bash
uv run inv pre-push
```

Expected: EXIT 0. All Python tests + frontend tests pass, lint + typecheck clean.

- [ ] **Step 2: Visual smoke-test the dev server**

```bash
cd ui && npm run dev &
sleep 3
curl -s http://localhost:5173/ | head -20
curl -s http://localhost:5173/app/login | head -20
kill %1 2>/dev/null
cd ..
```

Expected: both routes return HTML containing the App skeleton.

- [ ] **Step 3: Confirm the design tokens reach the browser**

```bash
cd ui && npm run dev > /tmp/vite.log 2>&1 &
sleep 3
curl -s http://localhost:5173/src/styles/channel.css | grep -c "data-theme=\"dark\""
# expected: 1 — channel.css is being served
kill %1 2>/dev/null
cd ..
```

- [ ] **Step 4: Coverage check**

```bash
cd ui && npm run test:coverage 2>&1 | tail -20 && cd ..
```

Expected: 100% coverage on every new component, hook, and lib module added in this phase.

---

### Task 16: Push branch + open the PR

- [ ] **Step 1: Push branch with explicit refspec (W3 rule)**

```bash
git push -u origin chore/channel-mvp-design-spec:chore/channel-mvp-design-spec
```

- [ ] **Step 2: Open PR with auto-merge enabled**

```bash
gh pr create --base development --title "feat(channel-mvp): Phase 6a — design-token foundation + route shell" --body "$(cat <<'EOF'
Phase 6a of the channel MVP per docs/superpowers/specs/2026-05-29-channel-mvp-design.md.

Lays the visual foundation: design tokens (OKLCH themes, fonts, radii, shadows),
ChannelMark + Icon + AuthGate components, useChannelPrefs hook for the seven
prefs + siteTheme, rewritten App.jsx as a marketing + app route tree with
placeholder route components, ErrorBoundary restyled to the new tokens.

Removes the template residue that has no place in the channel design:
UsersPanel, PageLayout, EmptyState, LoginPage, NotFoundPage, ConsentBanner,
shadcn primitives, Tailwind + supporting deps.

The plan doc (docs/superpowers/plans/2026-05-29-channel-mvp-phase-6a-foundation.md)
ships in the same PR — the spec + plan are the same artifact for an MVP this
size; subsequent phases will ship plans in their own PRs.

## Test plan

- [x] uv run inv pre-push (242 + N tests)
- [x] dev server loads / and /app/login
- [x] data-theme attribute applied on mount
- [x] AuthGate redirects unauthenticated /app visits to /app/login
- [ ] CI green on all 7 required contexts

Closes: no tracking issue (Phase 6a bootstrap — the issue queue picks up at 6b).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Enable auto-merge (squash)**

```bash
gh pr merge --auto --squash --delete-branch
```

- [ ] **Step 4: Watch CI**

```bash
gh pr checks
```

Wait for all 7 required contexts to green. Auto-merge fires when they do.

---

## Self-review

### Spec coverage

Checked the spec at `docs/superpowers/specs/2026-05-29-channel-mvp-design.md` for Phase 6a's explicit deliverables:

| Spec requirement | Plan task |
|---|---|
| Copy `channel.css` verbatim | Task 1 |
| Wire fonts | Task 1 (channel.css `@import url(...)` from Google Fonts inline; no separate `<link>` needed) |
| Add `ChannelMark.jsx` + `Icon.jsx` | Tasks 3, 4 |
| Replace `useTheme` with `useChannelPrefs` | Tasks 5, 6 |
| Delete `UsersPanel`, `PageLayout`, `EmptyState` | Task 11 |
| Uninstall Tailwind + PostCSS deps + shadcn primitives | Tasks 12, 13 |
| Rewrite `App.jsx` shell | Task 10 |
| Drop `ConsentBanner` | Task 11 |
| Restyle `ErrorBoundary` | Task 9 |
| 100% test coverage | Task 15 step 4 |

All 10 spec requirements covered.

### Placeholder scan

- No "TBD" / "implement later" / "similar to Task N" leftovers.
- Step 3 of Task 4 (Icon implementation) deliberately says "paste every case verbatim" — this is the correct guidance because the icon set is large and not all cases were in scope to enumerate in this plan. The test in Task 4 Step 1 enumerates the names the design requires; the implementer is asked to mirror the source. This is **explicit guidance**, not a placeholder.
- Step 2 of Task 9 deliberately says "adapt to the existing structure" because the existing ErrorBoundary's pre-error render path isn't shown in this plan. The implementer reads the existing file and preserves its non-error structure. This is **explicit guidance**, not a placeholder.

### Type consistency

- `TOKEN_KEY` used in `lib/auth.js`, `AuthGate.jsx`, `App.test.jsx`, `AuthGate.test.jsx` — same string `"starter_mgmt_token"` everywhere.
- `STORAGE_KEYS` and `DEFAULTS` exported from `useChannelPrefs.js` and consumed by `useChannelPrefs.test.js` — names align.
- Route paths consistent across `App.jsx` and `App.test.jsx`: `/`, `/product`, `/models`, `/pricing`, `/download`, `/about`, `/blog`, `/careers`, `/privacy`, `/app/login`, `/app`, `/app/c/:id`, `/app/projects`, `/app/projects/:id`, `/app/artifacts`, `/app/customize`.
- `testid` strings consistent across plan tasks: `marketing-{name}` for marketing routes, `app-{name}` for app routes, `marketing-notfound` for the catch-all 404.

No mismatches found.
