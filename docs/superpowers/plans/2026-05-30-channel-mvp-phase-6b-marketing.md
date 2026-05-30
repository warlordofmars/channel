# Channel MVP Phase 6b — Marketing Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the 10-page marketing site (`Home`, `Product`, `Models`, `Pricing`, `Download`, `About`, `Blog`, `Careers`, `Privacy`, `404`) translated from the design's prototype HTML into React Router routes, with shared Nav + Footer + ImageSlot components, a working monthly/annual Pricing toggle, and a separate `data-site-theme` attribute so the marketing site can default light independent of the app's dark default.

**Architecture:** All marketing components live under `ui/src/marketing/`. Each page is a small React component that returns the page's `<main>` content; a `SiteLayout` wrapper supplies the shared `Nav`, `Footer`, and `data-site-theme` attribute on `<html>`. Image placeholders are solid-colour blocks tagged `data-image-slot="<name>"` so a future content pass can swap in real images by querying that attribute. Pricing's toggle state lives in a small `useState` + `localStorage` round-trip on the Pricing page itself (no global hook).

**Tech Stack:** React 18, React Router 6, Vite, vitest, @testing-library/react. CSS-vars (`channel.css` + new `site.css`). No new deps.

---

## Design source

Authoritative reference (do **not** modify these files):

- `~/Downloads/design_handoff_channel/design-sources/site/site.css` (265 lines — styles)
- `~/Downloads/design_handoff_channel/design-sources/site/landing.src.html` (Home — 208 lines)
- `~/Downloads/design_handoff_channel/design-sources/site/pricing.src.html` (Pricing — 214 lines)
- `~/Downloads/design_handoff_channel/design-sources/site/pages/product.body.html` (88 lines)
- `~/Downloads/design_handoff_channel/design-sources/site/pages/models.body.html` (71 lines)
- `~/Downloads/design_handoff_channel/design-sources/site/pages/download.body.html` (66 lines)
- `~/Downloads/design_handoff_channel/design-sources/site/pages/blog.body.html` (63 lines)
- `~/Downloads/design_handoff_channel/design-sources/site/pages/careers.body.html` (62 lines)
- `~/Downloads/design_handoff_channel/design-sources/site/pages/about.body.html` (52 lines)
- `~/Downloads/design_handoff_channel/design-sources/site/pages/privacy.body.html` (37 lines)
- `~/Downloads/design_handoff_channel/design-sources/site/pages/404.body.html` (11 lines)
- `~/Downloads/design_handoff_channel/design-sources/site/image-slot.js` (642 lines — prototype affordance; **do not port**; build the simplified version specified in Task 4)

## Translation rules

When porting HTML body content to JSX, apply these rules consistently across every page task:

1. **`class=` → `className=`** (every attribute).
2. **`for=` → `htmlFor=`** (label attributes).
3. **`style="…: …;"` → `style={{ …: "…" }}`** (camelCase keys, string values).
4. **Inline SVGs**: keep verbatim except for `xmlns:xlink` → `xmlnsXlink` if present.
5. **Marketing-internal link rewriting:**
   - `href="Channel Site.html"` → React Router `<Link to="/">` (Home)
   - `href="Channel Product.html"` → `<Link to="/product">`
   - `href="Channel Models.html"` → `<Link to="/models">`
   - `href="Channel Pricing.html"` → `<Link to="/pricing">`
   - `href="Channel Download.html"` → `<Link to="/download">`
   - `href="Channel About.html"` → `<Link to="/about">`
   - `href="Channel Blog.html"` → `<Link to="/blog">`
   - `href="Channel Careers.html"` → `<Link to="/careers">`
   - `href="Channel Privacy.html"` → `<Link to="/privacy">`
   - `href="Channel App.html"` → `<Link to="/app">`
   - `href="Channel 404.html"` → `<Link to="/this-route-does-not-exist">` (dev-only convenience; remove from production pages — only the 404 page itself references it)
6. **In-page anchors** (`href="#features"`, `href="#models"`, `href="#download"`) stay as plain `<a href="#…">` — React Router does not own those.
7. **External links** (e.g. `https://…`) stay as plain `<a href="…" target="_blank" rel="noopener noreferrer">` — add the `target`/`rel` attrs if missing.
8. **`id=` attributes** are kept as-is for in-page anchor targets and the existing `themeBtn` id (the latter gets removed when the theme toggle becomes a React component — see Task 5).
9. **Image placeholders**: the prototype uses inline SVG fallbacks for image slots. Replace each with `<ImageSlot name="<descriptive-name>" />` — see Task 4 for the component shape. Pick names that describe the image's purpose, e.g. `name="hero-screen-light"`, `name="blog-cover-1"`, `name="team-photo"`.

## File Structure

### Create

| Path | Responsibility |
|---|---|
| `ui/src/styles/site.css` | Marketing-specific styles. Verbatim copy from `~/Downloads/design_handoff_channel/design-sources/site/site.css`. |
| `ui/src/marketing/Nav.jsx` + test | Sticky top nav (brand mark + 4 nav links + theme toggle + Sign in + Download CTA). Uses `<Link>` for internal routes. |
| `ui/src/marketing/Footer.jsx` + test | 3-column footer (Product / Company / Download columns) + brand line. |
| `ui/src/marketing/ThemeToggle.jsx` + test | Round icon button that flips `siteTheme` via `useChannelPrefs.toggleSiteTheme`. Renders the appropriate Icon (`sun` for dark, `moon` for light) based on current state. |
| `ui/src/marketing/ImageSlot.jsx` + test | Solid-colour placeholder div with `data-image-slot="<name>"` attribute. Accepts `name`, `aspect` props. Future content pass swaps this for real `<img>`. |
| `ui/src/marketing/SiteLayout.jsx` + test | Wraps children with `<Nav />` + `<main>{children}</main>` + `<Footer />`. Also writes `data-site-theme` to `<html>` on mount (so site pages override the app's `data-theme`). |
| `ui/src/marketing/pages/Home.jsx` + test | Landing page from `landing.src.html`. Longest page. |
| `ui/src/marketing/pages/Product.jsx` + test | Feature tour from `product.body.html`. |
| `ui/src/marketing/pages/Models.jsx` + test | 3 model cards + comparison table from `models.body.html`. |
| `ui/src/marketing/pages/Pricing.jsx` + test | 4 tiers + monthly/annual toggle from `pricing.src.html`. |
| `ui/src/marketing/pages/Download.jsx` + test | macOS/Windows/Linux cards + system requirements from `download.body.html`. |
| `ui/src/marketing/pages/About.jsx` + test | Mission + team + principles from `about.body.html`. |
| `ui/src/marketing/pages/Blog.jsx` + test | Featured post + post cards from `blog.body.html`. |
| `ui/src/marketing/pages/Careers.jsx` + test | Roles + culture from `careers.body.html`. |
| `ui/src/marketing/pages/Privacy.jsx` + test | Privacy policy from `privacy.body.html`. |
| `ui/src/marketing/pages/NotFound.jsx` + test | Branded 404 from `404.body.html`. |

### Modify

| Path | Change |
|---|---|
| `ui/src/App.jsx` | Replace 10 inline placeholder routes (`MarketingHome` … `MarketingNotFound`) with imports of the real page components from `ui/src/marketing/pages/`. The `<Routes>` block is unchanged in shape. |
| `ui/src/App.test.jsx` | Update the route tests to assert on the page-specific heading text instead of the placeholder `data-testid`s. Each page exposes a unique top-level `<h1>` we can assert on. |
| `CLAUDE.md` | Add `ui/src/marketing/` and `ui/src/styles/site.css` to the file-tree section. |

---

## Execution preconditions

- [ ] **On a fresh branch** off `origin/development`.

```bash
git fetch origin
git checkout -b feat/channel-mvp-phase-6b-marketing origin/development
git rev-parse --abbrev-ref HEAD
# expected: feat/channel-mvp-phase-6b-marketing
```

- [ ] **Verify the design handoff is available**.

```bash
test -f ~/Downloads/design_handoff_channel/design-sources/site/site.css && echo OK || echo MISSING
test -f ~/Downloads/design_handoff_channel/design-sources/site/landing.src.html && echo OK || echo MISSING
test -d ~/Downloads/design_handoff_channel/design-sources/site/pages && echo OK || echo MISSING
```

Expected: `OK` for all three.

---

## Tasks

### Task 1: Copy `site.css` marketing styles

**Files:**
- Create: `ui/src/styles/site.css`

- [ ] **Step 1: Copy the file verbatim**

```bash
cp ~/Downloads/design_handoff_channel/design-sources/site/site.css ui/src/styles/site.css
diff ~/Downloads/design_handoff_channel/design-sources/site/site.css ui/src/styles/site.css && echo "byte-identical OK"
```

- [ ] **Step 2: Verify token markers**

```bash
grep -c "var(--" ui/src/styles/site.css
# expected: 50+ (heavy CSS-var usage)
wc -l ui/src/styles/site.css
# expected: 265
```

- [ ] **Step 3: Import `site.css` from `main.jsx`** so marketing pages get the styles globally.

Read `ui/src/main.jsx` and add the import below the existing `channel.css` import:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./styles/channel.css";
import "./styles/site.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 4: Verify build still loads**

```bash
cd ui && npm run build 2>&1 | tail -5 && cd ..
```

Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add ui/src/styles/site.css ui/src/main.jsx
git commit -m "feat(ui): port site.css marketing styles + import from main

Verbatim copy of ~/Downloads/design_handoff_channel/design-sources/site/site.css.
Marketing pages (Phase 6b) consume the classes (.nav, .footer, .hero, .wrap,
.feat-grid, .tier, etc.) directly from this sheet. Loaded globally so theme
+ accent CSS-vars cascade into marketing surfaces alongside the app."
```

---

### Task 2: Build `ImageSlot` component (TDD)

**Files:**
- Create: `ui/src/marketing/ImageSlot.jsx`
- Create: `ui/src/marketing/ImageSlot.test.jsx`

A simple solid-colour placeholder. The 642-line prototype version (with drag-drop + IndexedDB persistence) is intentionally **not** ported — this MVP just needs visible blocks marked with `data-image-slot="<name>"` so a future content pass can swap them for real `<img>` elements.

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ImageSlot from "./ImageSlot.jsx";

describe("ImageSlot", () => {
  it("renders a div with the data-image-slot attribute set to name", () => {
    const { container } = render(<ImageSlot name="hero-shot" />);
    const slot = container.querySelector("[data-image-slot]");
    expect(slot).toBeTruthy();
    expect(slot.getAttribute("data-image-slot")).toBe("hero-shot");
  });

  it("applies a default aspect ratio of 16/9 when not specified", () => {
    const { container } = render(<ImageSlot name="x" />);
    const slot = container.querySelector("[data-image-slot]");
    expect(slot.style.aspectRatio).toBe("16 / 9");
  });

  it("accepts a custom aspect prop and applies it as aspectRatio style", () => {
    const { container } = render(<ImageSlot name="square" aspect="1 / 1" />);
    const slot = container.querySelector("[data-image-slot]");
    expect(slot.style.aspectRatio).toBe("1 / 1");
  });

  it("merges a className prop with the default 'image-slot' class", () => {
    const { container } = render(<ImageSlot name="x" className="hero-shot" />);
    const slot = container.querySelector("[data-image-slot]");
    expect(slot.className).toContain("image-slot");
    expect(slot.className).toContain("hero-shot");
  });

  it("renders the slot name as a label inside the placeholder", () => {
    const { container } = render(<ImageSlot name="team-photo" />);
    const slot = container.querySelector("[data-image-slot]");
    expect(slot.textContent).toContain("team-photo");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/ImageSlot.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ImageSlot.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";

/**
 * Marketing image placeholder. Renders a solid-colour block tagged with
 * `data-image-slot="<name>"` so a future content pass can find every slot
 * and swap it for a real <img>.
 *
 * Use:
 *   <ImageSlot name="hero-screen-light" />
 *   <ImageSlot name="blog-cover-1" aspect="3 / 2" className="rounded-lg" />
 */
export default function ImageSlot({ name, aspect = "16 / 9", className = "" }) {
  return (
    <div
      className={`image-slot ${className}`.trim()}
      data-image-slot={name}
      style={{
        aspectRatio: aspect,
        background: "var(--raised-2)",
        border: "1px dashed var(--border)",
        borderRadius: "var(--r-md)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--ink-faint)",
        fontFamily: "var(--font-mono)",
        fontSize: "12px",
        letterSpacing: "0.04em",
        textTransform: "uppercase",
      }}
    >
      {name}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/ImageSlot.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add ui/src/marketing/ImageSlot.jsx ui/src/marketing/ImageSlot.test.jsx
git commit -m "feat(ui): add marketing ImageSlot placeholder component

Solid-colour block tagged data-image-slot=\"<name>\" for visual fidelity in
Phase 6b without committing real image assets. A future content pass can
query data-image-slot to enumerate every slot and replace with real <img>."
```

---

### Task 3: Build `ThemeToggle` component (TDD)

**Files:**
- Create: `ui/src/marketing/ThemeToggle.jsx`
- Create: `ui/src/marketing/ThemeToggle.test.jsx`

A round button that flips the marketing site's theme. Uses `useChannelPrefs.toggleSiteTheme` (already shipped in Phase 6a — verified at `ui/src/hooks/useChannelPrefs.js:108`).

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ThemeToggle from "./ThemeToggle.jsx";

describe("ThemeToggle", () => {
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

  afterEach(() => vi.unstubAllGlobals());

  it("renders a button with aria-label 'Toggle theme'", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: /toggle theme/i })).toBeTruthy();
  });

  it("renders the moon icon when site theme is light", () => {
    storage["channel-site-theme"] = "light";
    const { container } = render(<ThemeToggle />);
    // Icon component renders the moon glyph as an svg path with a specific d attr;
    // we just confirm a single svg is rendered (the Icon's switch on name="moon")
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("flips siteTheme on click", () => {
    storage["channel-site-theme"] = "light";
    render(<ThemeToggle />);
    const btn = screen.getByRole("button", { name: /toggle theme/i });
    fireEvent.click(btn);
    expect(storage["channel-site-theme"]).toBe("dark");
    fireEvent.click(btn);
    expect(storage["channel-site-theme"]).toBe("light");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/ThemeToggle.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ThemeToggle.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";

/**
 * Round icon button that flips the marketing site's theme between light and
 * dark. Reads + writes `channel-site-theme` via `useChannelPrefs` so the app
 * and site themes can be toggled independently.
 */
export default function ThemeToggle() {
  const { siteTheme, toggleSiteTheme } = useChannelPrefs();
  return (
    <button
      type="button"
      className="icon-btn-m"
      onClick={toggleSiteTheme}
      aria-label="Toggle theme"
      title="Toggle theme"
    >
      <Icon name={siteTheme === "dark" ? "sun" : "moon"} size={16} />
    </button>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/ThemeToggle.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add ui/src/marketing/ThemeToggle.jsx ui/src/marketing/ThemeToggle.test.jsx
git commit -m "feat(ui): add marketing ThemeToggle component

Round icon button that flips channel-site-theme via useChannelPrefs. Sun
icon when the site is dark; moon icon when light. Tracks the site theme
independently from the app's data-theme (per spec §state)."
```

---

### Task 4: Build `Nav` component (TDD)

**Files:**
- Create: `ui/src/marketing/Nav.jsx`
- Create: `ui/src/marketing/Nav.test.jsx`

Top sticky nav present on every marketing page. Brand mark + 4 nav links + ThemeToggle + Sign in + Download CTA. Internal navigation uses React Router `<Link>`.

- [ ] **Step 1: Inspect the source nav HTML**

```bash
sed -n '11,30p' ~/Downloads/design_handoff_channel/design-sources/site/landing.src.html
```

Note the exact class names (`nav`, `wrap`, `nav-in`, `brand`, `nav-links`, `nav-right`, `btn`, `btn-ghost`, `btn-primary`) — these are styled by `site.css`.

- [ ] **Step 2: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Nav from "./Nav.jsx";

function renderNav() {
  return render(
    <MemoryRouter>
      <Nav />
    </MemoryRouter>
  );
}

describe("Nav", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the brand link pointing at /", () => {
    renderNav();
    const brand = screen.getByText("Channel").closest("a");
    expect(brand.getAttribute("href")).toBe("/");
  });

  it("renders four primary nav links: Product, Models, Pricing, Download", () => {
    renderNav();
    expect(screen.getByRole("link", { name: "Product" }).getAttribute("href")).toBe("/product");
    expect(screen.getByRole("link", { name: "Models" }).getAttribute("href")).toBe("/models");
    expect(screen.getByRole("link", { name: "Pricing" }).getAttribute("href")).toBe("/pricing");
    expect(screen.getByRole("link", { name: "Download" }).getAttribute("href")).toBe("/download");
  });

  it("renders a Sign in link pointing at /app", () => {
    renderNav();
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/app");
  });

  it("renders the ThemeToggle button", () => {
    renderNav();
    expect(screen.getByRole("button", { name: /toggle theme/i })).toBeTruthy();
  });

  it("renders a Download CTA button pointing at /download", () => {
    renderNav();
    const downloads = screen.getAllByRole("link", { name: /download/i });
    // Two: one in the nav-links row, one in the nav-right CTA. Both → /download.
    expect(downloads.length).toBeGreaterThanOrEqual(2);
    for (const d of downloads) expect(d.getAttribute("href")).toBe("/download");
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/Nav.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `Nav.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";
import ThemeToggle from "./ThemeToggle.jsx";

/**
 * Sticky top nav for every marketing page. Brand mark + 4 nav links + theme
 * toggle + Sign in + Download CTA. Translated from landing.src.html
 * lines 11-30; internal links rewritten to <Link>.
 */
export default function Nav() {
  return (
    <header className="nav">
      <div className="wrap nav-in">
        <Link className="brand" to="/">
          <ChannelMark size={26} />
          Channel
        </Link>
        <nav className="nav-links">
          <Link to="/product">Product</Link>
          <Link to="/models">Models</Link>
          <Link to="/pricing">Pricing</Link>
          <Link to="/download">Download</Link>
        </nav>
        <div className="nav-right">
          <ThemeToggle />
          <Link className="btn btn-ghost" to="/app">Sign in</Link>
          <Link className="btn btn-primary" to="/download">Download</Link>
        </div>
      </div>
    </header>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/Nav.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 5 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/Nav.jsx ui/src/marketing/Nav.test.jsx
git commit -m "feat(ui): add marketing Nav component

Brand + 4 nav links (Product/Models/Pricing/Download) + ThemeToggle + Sign
in + Download CTA. Translated from landing.src.html. Internal links use
React Router <Link>; theme toggle is the marketing variant from Task 3."
```

---

### Task 5: Build `Footer` component (TDD)

**Files:**
- Create: `ui/src/marketing/Footer.jsx`
- Create: `ui/src/marketing/Footer.test.jsx`

The site footer with 3 columns (Product / Company / Download) plus the brand line. Same shape on every page.

- [ ] **Step 1: Inspect the source footer**

```bash
grep -A 30 "<footer" ~/Downloads/design_handoff_channel/design-sources/site/landing.src.html | head -45
```

Note the 3-column layout, the column headings (`Product`, `Company`, `Download`), and the bottom line (brand + tagline + copyright).

- [ ] **Step 2: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Footer from "./Footer.jsx";

function renderFooter() {
  return render(
    <MemoryRouter>
      <Footer />
    </MemoryRouter>
  );
}

describe("Footer", () => {
  it("renders three column headings: Product, Company, Download", () => {
    renderFooter();
    expect(screen.getByText("Product")).toBeTruthy();
    expect(screen.getByText("Company")).toBeTruthy();
    // The "Download" column heading exists; nav links may also exist with
    // that name. We assert >= 1.
    expect(screen.getAllByText("Download").length).toBeGreaterThanOrEqual(1);
  });

  it("renders the brand mark + wordmark in the footer", () => {
    const { container } = renderFooter();
    expect(container.querySelector(".ch-mark")).toBeTruthy();
    expect(screen.getByText("Channel")).toBeTruthy();
  });

  it("includes a current-year copyright line", () => {
    renderFooter();
    const year = new Date().getFullYear().toString();
    const text = document.body.textContent || "";
    expect(text.includes(year)).toBe(true);
  });

  it("uses React Router Link for internal product/company links", () => {
    renderFooter();
    // /product, /pricing, /about, /privacy etc. all use Link → href starts with /
    const internalAnchors = Array.from(document.querySelectorAll("footer a"))
      .filter((a) => a.getAttribute("href")?.startsWith("/"));
    expect(internalAnchors.length).toBeGreaterThanOrEqual(4);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/Footer.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `Footer.jsx`**

Translate the `<footer>` block from `landing.src.html` (search for `<footer`). The structure is `<footer class="site-foot">` with a `<div class="wrap foot-grid">` containing four columns: brand+tagline (left), Product, Company, Download. Then a `<div class="wrap foot-bottom">` with brand + copyright. The translation rules (Section §Translation rules above) apply.

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";

/**
 * Site footer present on every marketing page. Translated verbatim from the
 * <footer> block in landing.src.html (also identical in pricing.src.html and
 * each pages/*.body.html). Internal links rewritten to <Link>.
 */
export default function Footer() {
  const year = new Date().getFullYear();
  return (
    <footer className="site-foot">
      <div className="wrap foot-grid">
        <div className="foot-brand">
          <div className="foot-brand-row">
            <ChannelMark size={22} />
            <span>Channel</span>
          </div>
          <p className="foot-tag">The workspace for thinking with AI.</p>
        </div>
        <div className="foot-col">
          <h4>Product</h4>
          <Link to="/product">Overview</Link>
          <Link to="/models">Models</Link>
          <Link to="/pricing">Pricing</Link>
          <Link to="/download">Download</Link>
        </div>
        <div className="foot-col">
          <h4>Company</h4>
          <Link to="/about">About</Link>
          <Link to="/blog">Blog</Link>
          <Link to="/careers">Careers</Link>
          <Link to="/privacy">Privacy</Link>
        </div>
        <div className="foot-col">
          <h4>Download</h4>
          <a href="#mac">Mac</a>
          <a href="#windows">Windows</a>
          <a href="#linux">Linux</a>
          <Link to="/app">Open web app</Link>
        </div>
      </div>
      <div className="wrap foot-bottom">
        <span>© {year} Channel.</span>
        <span className="foot-tag-sm">The workspace for thinking with AI.</span>
      </div>
    </footer>
  );
}
```

> If the source `landing.src.html`'s footer structure differs from this shape (different column names, extra columns, additional links), mirror the source — the test in Step 2 only enforces the three column headings (`Product`, `Company`, `Download`), the brand mark, the copyright year, and `≥ 4` internal `<Link>` anchors. Translation fidelity to the source wins where the test is silent.

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/Footer.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 4 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/Footer.jsx ui/src/marketing/Footer.test.jsx
git commit -m "feat(ui): add marketing Footer component

Three-column footer (Product/Company/Download) + brand + tagline +
copyright line. Translated from landing.src.html's <footer> block;
internal links use React Router <Link>."
```

---

### Task 6: Build `SiteLayout` wrapper (TDD)

**Files:**
- Create: `ui/src/marketing/SiteLayout.jsx`
- Create: `ui/src/marketing/SiteLayout.test.jsx`

Every marketing page renders inside `SiteLayout`, which adds Nav + Footer and applies `data-site-theme="<light|dark>"` to `<html>` on mount. This is what gives the marketing site its independent theme (light default) vs the app's dark default.

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import SiteLayout from "./SiteLayout.jsx";

function renderLayout(children) {
  return render(
    <MemoryRouter>
      <SiteLayout>{children}</SiteLayout>
    </MemoryRouter>
  );
}

describe("SiteLayout", () => {
  let storage;

  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    document.documentElement.removeAttribute("data-site-theme");
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the supplied children inside a <main>", () => {
    renderLayout(<div data-testid="page-body">Body</div>);
    expect(screen.getByTestId("page-body")).toBeTruthy();
  });

  it("renders Nav and Footer alongside the children", () => {
    renderLayout(<div data-testid="page-body" />);
    // Brand text appears in both Nav and Footer
    const brand = screen.getAllByText("Channel");
    expect(brand.length).toBeGreaterThanOrEqual(2);
  });

  it("applies data-site-theme to <html> from useChannelPrefs", async () => {
    storage["channel-site-theme"] = "dark";
    await act(async () => renderLayout(<div />));
    expect(document.documentElement.getAttribute("data-site-theme")).toBe("dark");
  });

  it("defaults data-site-theme to 'light' when storage is empty", async () => {
    await act(async () => renderLayout(<div />));
    expect(document.documentElement.getAttribute("data-site-theme")).toBe("light");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/SiteLayout.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `SiteLayout.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect } from "react";
import Footer from "./Footer.jsx";
import Nav from "./Nav.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";

/**
 * Wraps every marketing page with the shared Nav + Footer chrome. Also
 * applies `data-site-theme` to <html> so the marketing site can choose a
 * default that differs from the app's `data-theme`. The two attributes
 * coexist; channel.css selectors use `data-theme` for app surfaces and
 * site.css selectors use `data-site-theme` for marketing surfaces.
 */
export default function SiteLayout({ children }) {
  const { siteTheme } = useChannelPrefs();

  useEffect(() => {
    document.documentElement.setAttribute("data-site-theme", siteTheme);
  }, [siteTheme]);

  return (
    <>
      <Nav />
      <main>{children}</main>
      <Footer />
    </>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/SiteLayout.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add ui/src/marketing/SiteLayout.jsx ui/src/marketing/SiteLayout.test.jsx
git commit -m "feat(ui): add marketing SiteLayout wrapper

Nav + <main>{children} + Footer. Writes data-site-theme to <html> from
useChannelPrefs so the marketing site's theme is independent of the app's
data-theme (per useChannelPrefs.js:58-61's comment about marketing pages
owning their own theme attribute)."
```

---

### Task 7: Page test helpers (shared pattern)

Every page test (Tasks 8–17) follows the same render helper. Read this once; reuse it.

```jsx
import { render } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

function stubBrowserGlobals() {
  vi.stubGlobal("localStorage", {
    getItem: () => null,
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
  vi.stubGlobal("matchMedia", () => ({
    matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }));
}

function renderPage(PageComponent) {
  stubBrowserGlobals();
  return render(
    <MemoryRouter>
      <PageComponent />
    </MemoryRouter>
  );
}
```

Each page test imports the page component, calls `renderPage(<Page>)`, and asserts on at least three things:

1. **Heading text** — the page's top-level `<h1>` matches the design's heading.
2. **Nav present** — `screen.getAllByText("Channel").length >= 2` (one in Nav, one in Footer).
3. **No raw `Channel*.html` strings in any href** — i.e. the rewrite from the §Translation rules block was applied: `document.querySelectorAll("a[href*='Channel ']").length === 0`.

Each Task 8–17 below includes its specific assertions; the three above are the **shared minimum** every page test must include.

---

### Task 8: Build `Home` page (TDD)

**Files:**
- Create: `ui/src/marketing/pages/Home.jsx`
- Create: `ui/src/marketing/pages/Home.test.jsx`

The landing page — biggest of the 10. Source: `~/Downloads/design_handoff_channel/design-sources/site/landing.src.html` lines 33–207 (everything between `<main>` and `</main>`).

- [ ] **Step 1: Inspect the source**

```bash
sed -n '32,80p' ~/Downloads/design_handoff_channel/design-sources/site/landing.src.html
```

Note the section structure: `<section class="hero">`, `<section class="features">`, `<section class="platforms">`, `<section class="models">`, `<section class="cta">`. Note image-slot opportunities (the prototype's hero-shot light + dark blocks, the feature spotlight, etc.).

- [ ] **Step 2: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Home from "./Home.jsx";

function stubBrowserGlobals() {
  vi.stubGlobal("localStorage", {
    getItem: () => null, setItem: vi.fn(), removeItem: vi.fn(),
  });
  vi.stubGlobal("matchMedia", () => ({
    matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }));
}

function renderHome() {
  stubBrowserGlobals();
  return render(<MemoryRouter><Home /></MemoryRouter>);
}

describe("Home page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the hero H1 with 'The workspace for thinking with AI.' text", () => {
    renderHome();
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toMatch(/workspace for thinking with AI/i);
  });

  it("renders the Nav + Footer (brand appears at least twice)", () => {
    renderHome();
    expect(screen.getAllByText("Channel").length).toBeGreaterThanOrEqual(2);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderHome();
    const protoLinks = document.querySelectorAll('a[href*="Channel "]');
    expect(protoLinks.length).toBe(0);
  });

  it("includes a primary 'Download for Mac' CTA pointing at /download (or #download anchor)", () => {
    renderHome();
    const ctas = screen.getAllByText(/download/i);
    expect(ctas.length).toBeGreaterThanOrEqual(1);
  });

  it("renders at least one ImageSlot for the hero capture", () => {
    renderHome();
    const slots = document.querySelectorAll("[data-image-slot]");
    expect(slots.length).toBeGreaterThanOrEqual(1);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/pages/Home.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `Home.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ImageSlot from "../ImageSlot.jsx";
import SiteLayout from "../SiteLayout.jsx";

/**
 * Marketing landing page. Translated from landing.src.html lines 32-207
 * (the <main>…</main> block). Apply the §Translation rules from the plan:
 * Channel <Page>.html hrefs become <Link to="/route">, in-page anchors
 * (#features, #models, #download) stay as <a>, image slots become
 * <ImageSlot name="…" />.
 */
export default function Home() {
  return (
    <SiteLayout>
      {/* Paste the translated body from landing.src.html lines 33-207.
          Replace each major image block with <ImageSlot name="…" />
          (suggested names: hero-screen-light, hero-screen-dark,
          feature-spotlight-1, feature-spotlight-2, feature-spotlight-3).
          The hero H1 is required by the test:
            <h1>The workspace for thinking with <span class="accent">AI.</span></h1>
       */}
    </SiteLayout>
  );
}
```

> **Translation instructions for the body** (do this in Step 4): open `~/Downloads/design_handoff_channel/design-sources/site/landing.src.html`, copy the contents of the `<main>` block, and apply every rule from §Translation rules. Specifically:
>
> - `class=` → `className=`.
> - `Channel Site.html` → `Link to="/"`, `Channel Pricing.html` → `Link to="/pricing"`, `Channel App.html` → `Link to="/app"`, `Channel Product.html` → `Link to="/product"`, `Channel Models.html` → `Link to="/models"`, `Channel Download.html` → `Link to="/download"`, `Channel About.html` → `Link to="/about"`, `Channel Blog.html` → `Link to="/blog"`, `Channel Careers.html` → `Link to="/careers"`, `Channel Privacy.html` → `Link to="/privacy"`. (404 link, if present, should drop — the marketing-internal "go to 404" demo link is dev-affordance.)
> - In-page anchors (`#features`, `#models`, `#download`) stay as `<a href="#…">`.
> - Replace `<img>` placeholders, inline-SVG hero placeholders, or the prototype's image-slot `<span>` markers with `<ImageSlot name="<descriptive-name>" />`.
> - Inline `style="…"` attributes become `style={{…}}` (camelCase keys).
> - Self-close void elements (`<br>` → `<br />`, `<hr>` → `<hr />`, `<img>` → `<img />`).
> - If the source includes any `<svg>` elements with inline content, paste them verbatim — JSX accepts inline SVGs as-is.

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/pages/Home.test.jsx 2>&1 | tail -10 && cd ..
```

If any assertion fails, re-read that assertion's intent — the test is the contract. Adjust the JSX, not the test.

Expected: PASS — 5 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/pages/Home.jsx ui/src/marketing/pages/Home.test.jsx
git commit -m "feat(ui): port marketing Home (landing) page

Translated from design-sources/site/landing.src.html. Hero, features,
platforms, models, CTA sections. Internal Channel-X.html hrefs rewritten
to React Router <Link>; image placeholders use <ImageSlot data-image-slot=
\"…\" />. Wrapped in SiteLayout for shared Nav + Footer."
```

---

### Task 9: Build `Product` page (TDD)

**Files:**
- Create: `ui/src/marketing/pages/Product.jsx`
- Create: `ui/src/marketing/pages/Product.test.jsx`

Source: `~/Downloads/design_handoff_channel/design-sources/site/pages/product.body.html` (88 lines).

- [ ] **Step 1: Inspect the source**

```bash
head -40 ~/Downloads/design_handoff_channel/design-sources/site/pages/product.body.html
```

Identify the page's `<h1>` text (e.g. "Built for serious thinking" or whatever the source has — the test below uses a partial match so any meaningful Product-page H1 satisfies it).

- [ ] **Step 2: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Product from "./Product.jsx";

function renderPage() {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<MemoryRouter><Product /></MemoryRouter>);
}

describe("Product page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders a top-level h1 heading", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });

  it("renders the Nav + Footer (brand appears at least twice)", () => {
    renderPage();
    expect(screen.getAllByText("Channel").length).toBeGreaterThanOrEqual(2);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderPage();
    expect(document.querySelectorAll('a[href*="Channel "]').length).toBe(0);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/pages/Product.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `Product.jsx`**

Skeleton (translate the body from the source per §Translation rules):

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ImageSlot from "../ImageSlot.jsx";
import SiteLayout from "../SiteLayout.jsx";

export default function Product() {
  return (
    <SiteLayout>
      {/* Paste the translated body from pages/product.body.html.
          Apply §Translation rules. The page has a feature-tour layout +
          a product-spotlight image slot (name="product-spotlight") + a
          "how it works" 3-step + CTA. */}
    </SiteLayout>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/pages/Product.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 3 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/pages/Product.jsx ui/src/marketing/pages/Product.test.jsx
git commit -m "feat(ui): port marketing Product page

Translated from pages/product.body.html. Feature tour, spotlight slot,
how-it-works, CTA. Internal hrefs rewritten to <Link>; image placeholders
use <ImageSlot />."
```

---

### Task 10: Build `Models` page (TDD)

**Files:**
- Create: `ui/src/marketing/pages/Models.jsx`
- Create: `ui/src/marketing/pages/Models.test.jsx`

Source: `~/Downloads/design_handoff_channel/design-sources/site/pages/models.body.html` (71 lines). Page shows three model cards (Opus / Sonnet / Haiku) + a comparison table + a reasoning-effort explainer.

- [ ] **Step 1: Inspect the source**

```bash
cat ~/Downloads/design_handoff_channel/design-sources/site/pages/models.body.html | head -50
```

- [ ] **Step 2: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Models from "./Models.jsx";

function renderPage() {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<MemoryRouter><Models /></MemoryRouter>);
}

describe("Models page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders a top-level h1 heading", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });

  it("mentions all three Claude models in the body", () => {
    renderPage();
    const body = document.body.textContent || "";
    expect(body).toMatch(/Opus/);
    expect(body).toMatch(/Sonnet/);
    expect(body).toMatch(/Haiku/);
  });

  it("renders Nav + Footer", () => {
    renderPage();
    expect(screen.getAllByText("Channel").length).toBeGreaterThanOrEqual(2);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderPage();
    expect(document.querySelectorAll('a[href*="Channel "]').length).toBe(0);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/pages/Models.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `Models.jsx`**

Skeleton (translate the body per §Translation rules):

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import SiteLayout from "../SiteLayout.jsx";

export default function Models() {
  return (
    <SiteLayout>
      {/* Paste from pages/models.body.html. The page is mostly text + a
          comparison table; no large image slots needed. */}
    </SiteLayout>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/pages/Models.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 4 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/pages/Models.jsx ui/src/marketing/pages/Models.test.jsx
git commit -m "feat(ui): port marketing Models page

Translated from pages/models.body.html. Three model cards (Opus 4.8,
Sonnet 4.6, Haiku 4.5) + comparison table + reasoning-effort explainer."
```

---

### Task 11: Build `Pricing` page with monthly/annual toggle (TDD)

**Files:**
- Create: `ui/src/marketing/pages/Pricing.jsx`
- Create: `ui/src/marketing/pages/Pricing.test.jsx`

Source: `~/Downloads/design_handoff_channel/design-sources/site/pricing.src.html` (214 lines — full prototype). The page has four tiers (Free / Pro / Max / Team), each with a price `<span class="amt" data-m="$X" data-a="$Y">` and a `<span class="per">/mo</span>`. Two pill buttons (`monthly` / `annual`) drive a toggle that swaps the displayed values.

Spec contract: the toggle persists to `localStorage["channel-pricing-cycle"]` (per spec line 215).

- [ ] **Step 1: Inspect the source**

```bash
sed -n '11,60p' ~/Downloads/design_handoff_channel/design-sources/site/pricing.src.html
# Look at the toggle JS too:
grep -A 15 "function setBilling" ~/Downloads/design_handoff_channel/design-sources/site/pricing.src.html
```

Note: the source's `/yr` / `/mo` logic — when toggling to annual, the `per` element text becomes `/yr` (with the amount as the annual-equivalent monthly price). React rendering handles this by computing the displayed text from state.

- [ ] **Step 2: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Pricing from "./Pricing.jsx";

describe("Pricing page", () => {
  let storage;

  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  function renderPricing() {
    return render(<MemoryRouter><Pricing /></MemoryRouter>);
  }

  it("renders a top-level h1 heading", () => {
    renderPricing();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });

  it("renders all four tier names: Free, Pro, Max, Team", () => {
    renderPricing();
    const body = document.body.textContent || "";
    expect(body).toMatch(/\bFree\b/);
    expect(body).toMatch(/\bPro\b/);
    expect(body).toMatch(/\bMax\b/);
    expect(body).toMatch(/\bTeam\b/);
  });

  it("defaults to monthly billing on first visit", () => {
    renderPricing();
    // monthly pill is "on"
    const monthlyBtn = screen.getByRole("button", { name: /monthly/i });
    expect(monthlyBtn.className).toContain("on");
  });

  it("reads the saved cycle from localStorage", () => {
    storage["channel-pricing-cycle"] = "annual";
    renderPricing();
    const annualBtn = screen.getByRole("button", { name: /annual/i });
    expect(annualBtn.className).toContain("on");
  });

  it("clicking annual switches the displayed price text and persists the choice", () => {
    renderPricing();
    fireEvent.click(screen.getByRole("button", { name: /annual/i }));
    expect(storage["channel-pricing-cycle"]).toBe("annual");
    // Pro tier shows $16 (annual) — assert on the literal:
    expect(document.body.textContent).toMatch(/\$16/);
  });

  it("clicking monthly after annual restores monthly prices", () => {
    storage["channel-pricing-cycle"] = "annual";
    renderPricing();
    fireEvent.click(screen.getByRole("button", { name: /monthly/i }));
    expect(storage["channel-pricing-cycle"]).toBe("monthly");
    expect(document.body.textContent).toMatch(/\$20/);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderPricing();
    expect(document.querySelectorAll('a[href*="Channel "]').length).toBe(0);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/pages/Pricing.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `Pricing.jsx`**

Translate the body from `pricing.src.html`'s `<main>` block. Replace the toggle JS with React state. The four tiers' prices (from the source's `data-m` / `data-a` attributes):

| Tier | Monthly | Annual |
|---|---|---|
| Free | $0 | $0 |
| Pro | $20 | $16 |
| Max | $100 | $80 |
| Team | $30 / seat | $24 / seat |

Skeleton (paste in the surrounding markup from the source per §Translation rules):

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import SiteLayout from "../SiteLayout.jsx";

const CYCLE_KEY = "channel-pricing-cycle";

function readCycle() {
  try {
    const v = localStorage.getItem(CYCLE_KEY);
    return v === "annual" ? "annual" : "monthly";
  } catch {
    return "monthly";
  }
}

const TIERS = [
  { name: "Free", monthly: "$0", annual: "$0", per: "/mo", cta: "Get started", to: "/app", style: "ghost" },
  { name: "Pro", monthly: "$20", annual: "$16", per: "/mo", cta: "Start Pro trial", to: "/app", style: "primary", popular: true },
  { name: "Max", monthly: "$100", annual: "$80", per: "/mo", cta: "Go Max", to: "/app", style: "ghost" },
  { name: "Team", monthly: "$30", annual: "$24", per: "/seat/mo", cta: "Contact sales", to: "#", style: "ghost" },
];

export default function Pricing() {
  const [cycle, setCycle] = useState(() => readCycle());

  useEffect(() => {
    try { localStorage.setItem(CYCLE_KEY, cycle); } catch { /* private mode */ }
  }, [cycle]);

  function priceFor(tier) {
    return cycle === "annual" ? tier.annual : tier.monthly;
  }
  function perFor(tier) {
    // "$30 /seat/mo" → "$24 /seat/mo" (annual still presented per-month)
    return tier.per.replace("/mo", cycle === "annual" ? "/mo (billed annually)" : "/mo");
  }

  return (
    <SiteLayout>
      <section className="hero wrap">
        <div className="eyebrow">Pricing</div>
        <h1>Simple, honest pricing.</h1>
        <p className="sub">Choose the plan that fits how you think. Switch any time.</p>
        <div className="bill-toggle">
          <button
            type="button"
            className={cycle === "monthly" ? "on" : ""}
            onClick={() => setCycle("monthly")}
          >Monthly</button>
          <button
            type="button"
            className={cycle === "annual" ? "on" : ""}
            onClick={() => setCycle("annual")}
          >Annual</button>
        </div>
      </section>

      <section className="tiers wrap">
        {TIERS.map((tier) => (
          <div key={tier.name} className={`tier${tier.popular ? " popular" : ""}`}>
            <h3 className="tier-name">{tier.name}</h3>
            <div className="price">
              <span className="amt">{priceFor(tier)}</span>
              <span className="per">{perFor(tier)}</span>
            </div>
            {tier.to.startsWith("/")
              ? <Link className={`btn btn-${tier.style} tbtn`} to={tier.to}>{tier.cta}</Link>
              : <a className={`btn btn-${tier.style} tbtn`} href={tier.to}>{tier.cta}</a>}
            {/* Translate the per-tier <ul class="feats">…</ul> blocks from
                pricing.src.html verbatim per §Translation rules. */}
          </div>
        ))}
      </section>

      {/* FAQ section — translate verbatim from pricing.src.html */}
    </SiteLayout>
  );
}
```

> The pricing source has additional sections (FAQ, footnotes) beyond the tier grid — paste them in too per §Translation rules. The test only covers the four tier names, the cycle persistence, and the displayed price text — but visual fidelity to the source is the goal.

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/pages/Pricing.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 7 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/pages/Pricing.jsx ui/src/marketing/pages/Pricing.test.jsx
git commit -m "feat(ui): port marketing Pricing page with cycle toggle

Four tiers (Free, Pro, Max, Team). Monthly/annual toggle drives displayed
price text; choice persists to localStorage['channel-pricing-cycle'] per
spec §state line 215. Translated from pricing.src.html with the toggle
JS replaced by React useState + useEffect."
```

---

### Task 12: Build `Download` page (TDD)

**Files:**
- Create: `ui/src/marketing/pages/Download.jsx`
- Create: `ui/src/marketing/pages/Download.test.jsx`

Source: `~/Downloads/design_handoff_channel/design-sources/site/pages/download.body.html` (66 lines). macOS/Windows/Linux platform cards + system requirements + web option.

- [ ] **Step 1: Inspect the source**

```bash
cat ~/Downloads/design_handoff_channel/design-sources/site/pages/download.body.html
```

- [ ] **Step 2: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Download from "./Download.jsx";

function renderPage() {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<MemoryRouter><Download /></MemoryRouter>);
}

describe("Download page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders a top-level h1 heading", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });

  it("mentions all three desktop platforms", () => {
    renderPage();
    const body = document.body.textContent || "";
    expect(body).toMatch(/mac/i);
    expect(body).toMatch(/windows/i);
    expect(body).toMatch(/linux/i);
  });

  it("renders Nav + Footer", () => {
    renderPage();
    expect(screen.getAllByText("Channel").length).toBeGreaterThanOrEqual(2);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderPage();
    expect(document.querySelectorAll('a[href*="Channel "]').length).toBe(0);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/pages/Download.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `Download.jsx`**

Skeleton:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import SiteLayout from "../SiteLayout.jsx";

export default function Download() {
  return (
    <SiteLayout>
      {/* Paste from pages/download.body.html. Three platform cards
          (Mac/Windows/Linux), system requirements, "use it in the
          browser instead" CTA pointing at /app. */}
    </SiteLayout>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/pages/Download.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 4 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/pages/Download.jsx ui/src/marketing/pages/Download.test.jsx
git commit -m "feat(ui): port marketing Download page

Translated from pages/download.body.html. macOS/Windows/Linux cards +
system requirements + web app CTA."
```

---

### Task 13: Build `About` page (TDD)

**Files:**
- Create: `ui/src/marketing/pages/About.jsx`
- Create: `ui/src/marketing/pages/About.test.jsx`

Source: `~/Downloads/design_handoff_channel/design-sources/site/pages/about.body.html` (52 lines).

- [ ] **Step 1: Inspect the source**

```bash
cat ~/Downloads/design_handoff_channel/design-sources/site/pages/about.body.html
```

- [ ] **Step 2: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import About from "./About.jsx";

function renderPage() {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<MemoryRouter><About /></MemoryRouter>);
}

describe("About page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders a top-level h1 heading", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });

  it("renders Nav + Footer", () => {
    renderPage();
    expect(screen.getAllByText("Channel").length).toBeGreaterThanOrEqual(2);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderPage();
    expect(document.querySelectorAll('a[href*="Channel "]').length).toBe(0);
  });

  it("renders at least one ImageSlot (team photo placeholder)", () => {
    renderPage();
    expect(document.querySelectorAll("[data-image-slot]").length).toBeGreaterThanOrEqual(1);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/pages/About.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `About.jsx`**

Skeleton:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ImageSlot from "../ImageSlot.jsx";
import SiteLayout from "../SiteLayout.jsx";

export default function About() {
  return (
    <SiteLayout>
      {/* Paste from pages/about.body.html. Mission, team-photo slot
          (name="team-photo"), stats, principles. */}
    </SiteLayout>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/pages/About.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 4 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/pages/About.jsx ui/src/marketing/pages/About.test.jsx
git commit -m "feat(ui): port marketing About page

Translated from pages/about.body.html. Mission + team-photo slot + stats +
principles."
```

---

### Task 14: Build `Blog` page (TDD)

**Files:**
- Create: `ui/src/marketing/pages/Blog.jsx`
- Create: `ui/src/marketing/pages/Blog.test.jsx`

Source: `~/Downloads/design_handoff_channel/design-sources/site/pages/blog.body.html` (63 lines). Featured post + post-card grid.

- [ ] **Step 1: Inspect the source**

```bash
cat ~/Downloads/design_handoff_channel/design-sources/site/pages/blog.body.html
```

- [ ] **Step 2: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Blog from "./Blog.jsx";

function renderPage() {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<MemoryRouter><Blog /></MemoryRouter>);
}

describe("Blog page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders a top-level h1 heading", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });

  it("renders Nav + Footer", () => {
    renderPage();
    expect(screen.getAllByText("Channel").length).toBeGreaterThanOrEqual(2);
  });

  it("renders at least three ImageSlot blocks (one featured cover + at least two post thumbnails)", () => {
    renderPage();
    expect(document.querySelectorAll("[data-image-slot]").length).toBeGreaterThanOrEqual(3);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderPage();
    expect(document.querySelectorAll('a[href*="Channel "]').length).toBe(0);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/pages/Blog.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `Blog.jsx`**

Skeleton:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ImageSlot from "../ImageSlot.jsx";
import SiteLayout from "../SiteLayout.jsx";

export default function Blog() {
  return (
    <SiteLayout>
      {/* Paste from pages/blog.body.html. Featured post block (with
          ImageSlot name="blog-featured-cover") + post-card grid (each
          card with ImageSlot name="blog-thumb-N"). */}
    </SiteLayout>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/pages/Blog.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 4 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/pages/Blog.jsx ui/src/marketing/pages/Blog.test.jsx
git commit -m "feat(ui): port marketing Blog page

Translated from pages/blog.body.html. Featured post + post-card grid, all
images as <ImageSlot data-image-slot=\"…\" />."
```

---

### Task 15: Build `Careers` page (TDD)

**Files:**
- Create: `ui/src/marketing/pages/Careers.jsx`
- Create: `ui/src/marketing/pages/Careers.test.jsx`

Source: `~/Downloads/design_handoff_channel/design-sources/site/pages/careers.body.html` (62 lines). Roles grouped by team + culture.

- [ ] **Step 1: Inspect the source**

```bash
cat ~/Downloads/design_handoff_channel/design-sources/site/pages/careers.body.html
```

- [ ] **Step 2: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Careers from "./Careers.jsx";

function renderPage() {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<MemoryRouter><Careers /></MemoryRouter>);
}

describe("Careers page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders a top-level h1 heading", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });

  it("renders Nav + Footer", () => {
    renderPage();
    expect(screen.getAllByText("Channel").length).toBeGreaterThanOrEqual(2);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderPage();
    expect(document.querySelectorAll('a[href*="Channel "]').length).toBe(0);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/pages/Careers.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `Careers.jsx`**

Skeleton:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import SiteLayout from "../SiteLayout.jsx";

export default function Careers() {
  return (
    <SiteLayout>
      {/* Paste from pages/careers.body.html. Roles grouped by team
          (Design, Eng, etc.) + culture section. */}
    </SiteLayout>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/pages/Careers.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 3 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/pages/Careers.jsx ui/src/marketing/pages/Careers.test.jsx
git commit -m "feat(ui): port marketing Careers page

Translated from pages/careers.body.html. Roles grouped by team + culture."
```

---

### Task 16: Build `Privacy` page (TDD)

**Files:**
- Create: `ui/src/marketing/pages/Privacy.jsx`
- Create: `ui/src/marketing/pages/Privacy.test.jsx`

Source: `~/Downloads/design_handoff_channel/design-sources/site/pages/privacy.body.html` (37 lines). Prose privacy policy.

- [ ] **Step 1: Inspect the source**

```bash
cat ~/Downloads/design_handoff_channel/design-sources/site/pages/privacy.body.html
```

- [ ] **Step 2: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Privacy from "./Privacy.jsx";

function renderPage() {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<MemoryRouter><Privacy /></MemoryRouter>);
}

describe("Privacy page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders a top-level h1 heading", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });

  it("renders Nav + Footer", () => {
    renderPage();
    expect(screen.getAllByText("Channel").length).toBeGreaterThanOrEqual(2);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderPage();
    expect(document.querySelectorAll('a[href*="Channel "]').length).toBe(0);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/pages/Privacy.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `Privacy.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import SiteLayout from "../SiteLayout.jsx";

export default function Privacy() {
  return (
    <SiteLayout>
      {/* Paste prose from pages/privacy.body.html. */}
    </SiteLayout>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/pages/Privacy.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 3 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/pages/Privacy.jsx ui/src/marketing/pages/Privacy.test.jsx
git commit -m "feat(ui): port marketing Privacy page

Translated from pages/privacy.body.html. Prose privacy policy."
```

---

### Task 17: Build `NotFound` page (TDD)

**Files:**
- Create: `ui/src/marketing/pages/NotFound.jsx`
- Create: `ui/src/marketing/pages/NotFound.test.jsx`

Source: `~/Downloads/design_handoff_channel/design-sources/site/pages/404.body.html` (11 lines). Branded 404 with two CTAs (Back to home → `/`, Open Channel → `/app`).

The source uses an inline SVG for the mark — replace with `<ChannelMark size={64} />`.

- [ ] **Step 1: Inspect the source**

```bash
cat ~/Downloads/design_handoff_channel/design-sources/site/pages/404.body.html
```

- [ ] **Step 2: Write the failing test**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import NotFound from "./NotFound.jsx";

function renderPage() {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<MemoryRouter><NotFound /></MemoryRouter>);
}

describe("NotFound page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the 'Error 404' eyebrow", () => {
    renderPage();
    expect(screen.getByText(/error 404/i)).toBeTruthy();
  });

  it("renders the page-wandered-off h1", () => {
    renderPage();
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toMatch(/wandered off/i);
  });

  it("renders 'Back to home' link pointing at /", () => {
    renderPage();
    const back = screen.getByRole("link", { name: /back to home/i });
    expect(back.getAttribute("href")).toBe("/");
  });

  it("renders 'Open Channel' link pointing at /app", () => {
    renderPage();
    const open = screen.getByRole("link", { name: /open channel/i });
    expect(open.getAttribute("href")).toBe("/app");
  });

  it("renders the ChannelMark logo in the page (separate from Nav)", () => {
    const { container } = renderPage();
    // Nav has one ch-mark + page has one ch-mark + Footer has one = 3
    expect(container.querySelectorAll(".ch-mark").length).toBeGreaterThanOrEqual(2);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ui && npx vitest run src/marketing/pages/NotFound.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `NotFound.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ChannelMark from "../../components/ChannelMark.jsx";
import SiteLayout from "../SiteLayout.jsx";

/**
 * Branded 404 page. Translated from pages/404.body.html. The source's
 * inline SVG mark is replaced with <ChannelMark size={64} />; the two
 * CTAs route via React Router <Link>.
 */
export default function NotFound() {
  return (
    <SiteLayout>
      <section className="notfound wrap">
        <span className="mk404">
          <ChannelMark size={64} />
        </span>
        <div className="eyebrow" style={{ marginBottom: "12px" }}>Error 404</div>
        <h1>This page wandered off.</h1>
        <p>The link may be broken or the page might have moved. Let's get you back to thinking.</p>
        <div className="hero-cta" style={{ justifyContent: "center", marginTop: "30px" }}>
          <Link className="btn btn-primary btn-lg" to="/">Back to home</Link>
          <Link className="btn btn-ghost btn-lg" to="/app">Open Channel</Link>
        </div>
      </section>
    </SiteLayout>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd ui && npx vitest run src/marketing/pages/NotFound.test.jsx 2>&1 | tail -10 && cd ..
```

Expected: PASS — 5 tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/marketing/pages/NotFound.jsx ui/src/marketing/pages/NotFound.test.jsx
git commit -m "feat(ui): port marketing NotFound (404) page

Translated from pages/404.body.html. Inline SVG mark replaced with
<ChannelMark size={64} />. Two CTAs route via <Link>."
```

---

### Task 18: Wire real pages into `App.jsx` + update `App.test.jsx`

**Files:**
- Modify: `ui/src/App.jsx`
- Modify: `ui/src/App.test.jsx`

Replace the 10 inline placeholder components from Phase 6a with imports of the real page components built above.

- [ ] **Step 1: Update `App.jsx`**

Replace the placeholder `ph()` factory and inline `Marketing*` placeholders with real imports. Keep the app-side placeholders (`AppLogin`, `AppHome`, etc.) — they'll be replaced in Phase 6c.

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import AuthGate from "./components/AuthGate.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { useChannelPrefs } from "./hooks/useChannelPrefs.js";
import About from "./marketing/pages/About.jsx";
import Blog from "./marketing/pages/Blog.jsx";
import Careers from "./marketing/pages/Careers.jsx";
import Download from "./marketing/pages/Download.jsx";
import Home from "./marketing/pages/Home.jsx";
import Models from "./marketing/pages/Models.jsx";
import NotFound from "./marketing/pages/NotFound.jsx";
import Pricing from "./marketing/pages/Pricing.jsx";
import Privacy from "./marketing/pages/Privacy.jsx";
import Product from "./marketing/pages/Product.jsx";

// App-side placeholders — replaced in Phase 6c (chat shell + login + home).
const ph = (testid, label) => (
  <div data-testid={testid} style={{ padding: 24, color: "var(--ink)" }}>
    {label} — placeholder, implemented in a later phase.
  </div>
);
const AppLogin         = () => ph("app-login", "App: Login");
const AppHome          = () => ph("app-home", "App: Home");
const AppConversation  = () => ph("app-conversation", "App: Conversation");
const AppProjects      = () => ph("app-projects", "App: Projects");
const AppProjectDetail = () => ph("app-project-detail", "App: Project Detail");
const AppArtifacts     = () => ph("app-artifacts", "App: Artifacts");
const AppCustomize     = () => ph("app-customize", "App: Customize");

export default function App() {
  useChannelPrefs();
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Routes>
          {/* Marketing — public */}
          <Route path="/"          element={<Home />} />
          <Route path="/product"   element={<Product />} />
          <Route path="/models"    element={<Models />} />
          <Route path="/pricing"   element={<Pricing />} />
          <Route path="/download"  element={<Download />} />
          <Route path="/about"     element={<About />} />
          <Route path="/blog"      element={<Blog />} />
          <Route path="/careers"   element={<Careers />} />
          <Route path="/privacy"   element={<Privacy />} />

          {/* App — /app/login is public; everything else gates on the JWT */}
          <Route path="/app/login"          element={<AppLogin />} />
          <Route path="/app"                element={<AuthGate><AppHome /></AuthGate>} />
          <Route path="/app/c/:id"          element={<AuthGate><AppConversation /></AuthGate>} />
          <Route path="/app/projects"       element={<AuthGate><AppProjects /></AuthGate>} />
          <Route path="/app/projects/:id"   element={<AuthGate><AppProjectDetail /></AuthGate>} />
          <Route path="/app/artifacts"      element={<AuthGate><AppArtifacts /></AuthGate>} />
          <Route path="/app/customize"      element={<AuthGate><AppCustomize /></AuthGate>} />

          {/* Anything else → branded 404 */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
```

- [ ] **Step 2: Update `App.test.jsx`**

The Phase 6a tests assert on placeholder `data-testid`s. Each marketing route's `data-testid` was `marketing-<route>`; with the real components, we instead assert on the page-specific `<h1>` text. Replace the marketing-route tests:

Open `ui/src/App.test.jsx`. Replace the `it.each([["/","marketing-home"]…])` block with:

```jsx
  it.each([
    ["/",         /workspace for thinking with AI/i],
    ["/product",  null], // Any h1 is OK — page-specific copy varies
    ["/models",   /models|claude/i],
    ["/pricing",  /pricing|simple, honest/i],
    ["/download", /download|get channel/i],
    ["/about",    null],
    ["/blog",     null],
    ["/careers",  null],
    ["/privacy",  /privacy/i],
  ])("renders marketing route %s with a real page (h1 present%s)", async (path, headingRegex) => {
    window.history.pushState({}, "", path);
    await act(async () => render(<App />));
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toBeTruthy();
    if (headingRegex) {
      expect(h1.textContent).toMatch(headingRegex);
    }
  });
```

Replace the "renders 404 placeholder for unknown routes" test with:

```jsx
  it("renders the branded NotFound page for unknown routes", async () => {
    window.history.pushState({}, "", "/this-route-does-not-exist");
    await act(async () => render(<App />));
    expect(screen.getByText(/wandered off/i)).toBeTruthy();
  });
```

Leave the app-route placeholder tests (`/app/login`, `/app`, `/app/c/:id`, etc.) and the theme-application test unchanged — those still apply because the app routes are still placeholders in this phase.

- [ ] **Step 3: Run the full App test**

```bash
cd ui && npx vitest run src/App.test.jsx 2>&1 | tail -15 && cd ..
```

Expected: PASS — same test count as before (some assertion bodies changed but the test count is unchanged).

- [ ] **Step 4: Run the full UI suite**

```bash
cd ui && npm run test 2>&1 | tail -8 && cd ..
```

Expected: all tests pass, total count grew by the Phase 6b additions.

- [ ] **Step 5: Commit**

```bash
git add ui/src/App.jsx ui/src/App.test.jsx
git commit -m "feat(ui): mount real marketing pages in App route shell

Replace the Phase 6a placeholder MarketingHome/Product/Models/Pricing/
Download/About/Blog/Careers/Privacy/NotFound stubs with imports of the
real page components from ui/src/marketing/pages/. The app-side
placeholders (AppLogin/AppHome/AppConversation/AppProjects/
AppProjectDetail/AppArtifacts/AppCustomize) remain as inline stubs —
those are replaced in Phase 6c."
```

---

### Task 19: Update `CLAUDE.md` for Phase 6b

**Files:**
- Modify: `CLAUDE.md`

Bring the file tree current with the Phase 6b additions. The Phase 6a entry didn't include `marketing/` — add it now.

- [ ] **Step 1: Locate the UI subtree**

```bash
grep -n "ui/" CLAUDE.md | head -10
```

- [ ] **Step 2: Update the file tree**

In the `ui/src/` subtree, add `styles/site.css` and `marketing/`:

```
│   │   ├── styles/
│   │   │   ├── channel.css    # Design tokens (OKLCH themes, radii, shadows, fonts)
│   │   │   └── site.css       # Marketing site layout (nav, footer, hero, tiers)
│   │   ├── marketing/         # Marketing site routes (Phase 6b)
│   │   │   ├── Nav.jsx, Footer.jsx, ThemeToggle.jsx, ImageSlot.jsx, SiteLayout.jsx
│   │   │   └── pages/         # Home, Product, Models, Pricing, Download, About, Blog, Careers, Privacy, NotFound
```

(Match the existing tree's comment style.)

- [ ] **Step 3: Add a UI conventions bullet for marketing**

In the `## UI conventions` section, add:

```
- **Marketing image placeholders** — every marketing image is a `<ImageSlot
  name="<descriptive>" />` rendering a placeholder block tagged
  `data-image-slot="<name>"`. A future content pass swaps in real `<img>`
  elements by querying that attribute.
- **Marketing internal links** — `<Link>` from React Router (never plain
  `<a>` for in-app navigation). In-page anchors (`#features`, `#models`)
  stay as `<a>`.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): add Phase 6b marketing site to file tree + UI conventions

styles/site.css + marketing/ subtree. UI conventions get two new bullets
covering ImageSlot placeholders and the <Link> rule for internal
marketing links."
```

---

### Task 20: Final pre-push gate + visual smoke test

- [ ] **Step 1: Run pre-push**

```bash
uv run inv pre-push
```

Expected: EXIT 0.

- [ ] **Step 2: Run coverage**

```bash
cd ui && npm run test:coverage 2>&1 | tail -10 && cd ..
```

Expected: 100% across statements / branches / functions / lines.

- [ ] **Step 3: Local visual smoke test**

```bash
cd ui && npm run dev > /tmp/vite.log 2>&1 &
sleep 4
echo "=== / (Home) ==="
curl -s http://localhost:5173/ | grep -E "<title>|workspace for thinking" | head -5
echo "=== /pricing ==="
curl -s http://localhost:5173/pricing | head -5
echo "=== /unknown-path (NotFound) ==="
curl -s http://localhost:5173/this-route-does-not-exist | grep -o "wandered off" | head -1
kill %1 2>/dev/null
cd ..
```

Expected: each curl returns HTML; the unknown path returns the NotFound page.

> Vite renders the SPA — so the curls return the `index.html` shell. The route-specific content shows in the browser after JS executes. The grep above proves the build works; full visual fidelity is verified by opening the routes in a browser.

---

### Task 21: Push branch + open PR + final reviewer

- [ ] **Step 1: Push branch with explicit refspec (W3)**

```bash
git push -u origin feat/channel-mvp-phase-6b-marketing:feat/channel-mvp-phase-6b-marketing
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base development \
  --title "feat(channel-mvp): Phase 6b — marketing site (10 pages)" \
  --body "$(cat <<'EOF'
Phase 6b of the channel MVP per `docs/superpowers/specs/2026-05-29-channel-mvp-design.md`.

Ships the full 10-page marketing site: Home, Product, Models, Pricing, Download, About, Blog, Careers, Privacy, and the branded 404. Shared Nav + Footer + ThemeToggle, image placeholders tagged `data-image-slot`, Pricing's monthly/annual toggle persisted to `localStorage["channel-pricing-cycle"]`.

Marketing routes at `/` through `/privacy`; everything else falls through to the branded 404. App routes (`/app/login` and the gated `/app/*` placeholders) are unchanged from Phase 6a — they get real components in Phase 6c.

## Verification

- `uv run inv pre-push` — EXIT 0
- `npm run test:coverage` — 100% across all metrics
- Local Vite dev server renders every marketing route + the 404 page

## Test plan

- [x] All 7 required CI contexts green
- [x] Coverage stays 100%
- [ ] Auto-merge fires on green CI
- [ ] Post-merge dev deploy lands cleanly (channel-dev.warlordofmars.net/health returns the new version)
- [ ] Visual: open https://channel-dev.warlordofmars.net/ and verify each marketing route looks right per the design's screen captures

Closes: no tracking issue (Phase 6b shipping plan).

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

- [ ] **Step 5: After CI green, post-merge dev deploy validation**

After the PR squash-merges, the post-merge CI run will auto-deploy to `ChannelStack-dev`. Verify it landed without manual CloudFront patching (the Phase 6a follow-up #6 fixed that drift):

```bash
sleep 360   # let the post-merge deploy + invalidation propagate
curl -sS "https://channel-dev.warlordofmars.net/" | grep -o "<title>[^<]*</title>"
curl -sS "https://channel-dev.warlordofmars.net/this-route-does-not-exist" | grep -c "wandered off"
```

Expected: `<title>Channel</title>` from `/`, and `1` match for "wandered off" from the catch-all route.

---

## Self-review

### Spec coverage

| Spec requirement | Plan task |
|---|---|
| `site.css` port | Task 1 |
| Marketing site `Nav.jsx` | Task 4 |
| Marketing site `Footer.jsx` | Task 5 |
| `ThemeToggle.jsx` | Task 3 |
| `ImageSlot.jsx` (simplified) | Task 2 |
| `SiteLayout.jsx` wrapping every page | Task 6 |
| 10 page components (Home–NotFound) | Tasks 8–17 |
| Real copy from prototype HTML | Tasks 8–17 (each cites the source) |
| Image placeholders marked `data-image-slot` | Task 2 (component); Tasks 8, 13, 14 (pages that use slots) |
| Pricing monthly/annual toggle + localStorage | Task 11 |
| Marketing at `/` … `/privacy` | Task 18 (App.jsx wiring) |
| Branded 404 catch-all | Task 17 + Task 18 |
| 100% test coverage | Task 20 |

All spec items covered.

### Placeholder scan

- Tasks 8, 9, 10, 12, 13, 14, 15, 16 each include `{/* Paste the translated body from … */}` as an explicit instruction to the implementer — these are **not** placeholders in the writing-plans sense (the implementer follows the §Translation rules to do the paste). The instruction is precise: the source path, line range, and translation rules are all specified.
- No TBD / TODO / "implement later" remains anywhere in the plan.

### Type consistency

- `data-image-slot` attribute name is consistent across `ImageSlot.jsx` (Task 2), the test assertions (Tasks 8, 13, 14), and the spec excerpt cited in §UI conventions (Task 19).
- `channel-site-theme` localStorage key matches `useChannelPrefs.STORAGE_KEYS.siteTheme` (Phase 6a code, verified).
- `channel-pricing-cycle` localStorage key matches the spec (line 215) and the Pricing implementation (Task 11).
- React Router `<Link>` is used consistently for all internal marketing-route navigation; in-page `#anchors` stay as plain `<a>`.
- Routes match the App.jsx wiring (Task 18) and the spec's route shape (`/`, `/product`, `/models`, `/pricing`, `/download`, `/about`, `/blog`, `/careers`, `/privacy`, catch-all 404).

No mismatches found.
