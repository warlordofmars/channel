# Channel MVP Phase 6c — App shell + Login + ChatHome Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the chat app's authenticated shell — Google sign-in Login page, sidebar-wrapped layout, and the empty-state ChatHome (greeting + composer + quick-action chips). Replaces the Phase 6a `AppLogin` / `AppHome` placeholder stubs with real components.

**Architecture:** All chat-app components live under `ui/src/app/`. `Login` is unwrapped (no sidebar — pre-auth). `Shell` wraps every authenticated route with a 264px `Sidebar` plus a main pane. `Composer` composes `ModelPicker` + `AttachMenu`. `ChatHome` renders the greeting + composer + quick-action chips. Mock data (`MODELS`, `EFFORTS`, `RECENTS`, `QUICK_ACTIONS`) lives in `data.js`. The composer's `onSend` callback at 6c is a no-op — the mock streamer arrives in Phase 6d.

**Tech Stack:** React 18, React Router 6, vitest, @testing-library/react. CSS-vars only (`channel.css` + new `app.css`). No new deps.

---

## Design source

Authoritative reference (do **not** modify these files):

- `~/Downloads/design_handoff_channel/design-sources/app/app.css` (527 lines — chat-app layout + component styles)
- `~/Downloads/design_handoff_channel/design-sources/app/data.jsx` (190 lines — MODELS, EFFORTS, RECENTS, QUICK_ACTIONS + others)
- `~/Downloads/design_handoff_channel/design-sources/app/shell.jsx` (Login, Sidebar, MainTop, GoogleG, Lights)
- `~/Downloads/design_handoff_channel/design-sources/app/chat.jsx` (ModelPicker, AttachMenu, Composer, Home — `Conversation` + `useStream` are Phase 6d, NOT 6c)

## Translation rules

When porting JSX from the prototype source, apply these consistently:

1. **`'`-quoted strings → `"`-quoted strings** (project convention).
2. **`React.Fragment` → `<>...</>` shorthand** (project convention).
3. **`Object.assign(window, …)` exports at the bottom → `export default <ComponentName>;`** (one export per file).
4. **`USER`-style hardcoded constants** (the prototype defines `const USER = { name: 'John', plan: 'Channel Max', initials: 'JC' }`) — the `plan: 'Channel Max'` field is **dropped** per spec §"Channel Max label" deferral. The display name and email come from the mgmt JWT claims (`claims.email`, derived via `parseToken` from `lib/auth.js`).
5. **The `frame === 'desktop'` macOS-traffic-lights branches** in `Login` and `Sidebar` are **dropped entirely** per spec §"Window frames" deferral — Phase 6c is web-only.
6. **The `"Relaunch to update v2.4.1 ready"` pill** in the sidebar footer is **dropped entirely** — Electron-only per spec §"Update pill" deferral.
7. **The Login button's 1.2s `setTimeout(() => onAuth(), 1200)` mock** is replaced with `globalThis.location.assign("/auth/login")` — the real Google OAuth flow already shipped in Phase 5 (issues a mgmt JWT, redirects to `/app`).
8. **Composer/ModelPicker model + effort state**: the prototype passes `model` (an object from `MODELS`) and `setModel` (a setter taking another object). In our codebase the source-of-truth is `useChannelPrefs.model` (a string id) and `useChannelPrefs.setModel(id)`. `ChatHome` derives the model object by `MODELS.find((m) => m.id === prefs.model)` and supplies a wrapping `setModel` that writes the id back via `prefs.setModel(model.id)`. Same shape for `effort`.
9. **Popover backdrop pattern**: ModelPicker, AttachMenu, AccountPopover all use the same `<div className="backdrop" onClick={…} /> + <div className="pop" />` pattern. Port verbatim; `app.css` styles `.backdrop` and `.pop` already.

## File Structure

### Create

| Path | Responsibility |
|---|---|
| `ui/src/styles/app.css` | Chat-app layout + component styles. Verbatim copy from design-sources/app/app.css. |
| `ui/src/app/data.js` | Mock data exports: `MODELS`, `EFFORTS`, `RECENTS`, `QUICK_ACTIONS`. Phase 6c subset only. |
| `ui/src/app/GoogleG.jsx` + test | The Google "G" 4-colour SVG icon (single-use; lives next to Login). |
| `ui/src/app/Login.jsx` + test | Centered Google sign-in card. Redirects to `/auth/login` on click. |
| `ui/src/app/ModelPicker.jsx` + test | Popover: model selection + effort segmented control. |
| `ui/src/app/AttachMenu.jsx` + test | `+` button popover: Upload a file / Add photos or images / Connect data source. |
| `ui/src/app/Composer.jsx` + test | Auto-growing textarea + attach + model picker + mic + send. |
| `ui/src/app/AccountPopover.jsx` + test | User email + Sign out (within the sidebar's account row). |
| `ui/src/app/Sidebar.jsx` + test | 264px nav: New chat / Projects / Artifacts / Customize / time-grouped Recents / account row. |
| `ui/src/app/Shell.jsx` + test | Layout wrapper: Sidebar + `<main>` for children. |
| `ui/src/app/ChatHome.jsx` + test | Greeting + Composer + quick-action chips. |

### Modify

| Path | Change |
|---|---|
| `ui/src/main.jsx` | Add `import "./styles/app.css"` below the existing channel.css + site.css imports. |
| `ui/src/App.jsx` | Replace placeholder `AppLogin` with `Login` from `./app/Login.jsx`. Replace placeholder `AppHome` with `<Shell><ChatHome /></Shell>`. The other app-route placeholders (`AppConversation`, `AppProjects`, etc.) remain — replaced in 6d/6e/6f. |
| `ui/src/App.test.jsx` | Update the `/app/login` and `/app` assertions to assert on the real components' text instead of placeholder testids. |
| `CLAUDE.md` | Add `styles/app.css` and `app/` subtree to the file tree. |

---

## Execution preconditions

- [ ] **On a fresh branch off `origin/development`**

```bash
git fetch origin
git checkout -b feat/channel-mvp-phase-6c-app-shell origin/development
git rev-parse --abbrev-ref HEAD
# expected: feat/channel-mvp-phase-6c-app-shell
```

- [ ] **Design handoff is available**

```bash
test -f ~/Downloads/design_handoff_channel/design-sources/app/app.css && echo OK || echo MISSING
test -f ~/Downloads/design_handoff_channel/design-sources/app/data.jsx && echo OK || echo MISSING
test -f ~/Downloads/design_handoff_channel/design-sources/app/shell.jsx && echo OK || echo MISSING
test -f ~/Downloads/design_handoff_channel/design-sources/app/chat.jsx && echo OK || echo MISSING
```

Expected: `OK` x4.

---

## Tasks

### Task 1: Port `app.css` styles + import in main.jsx

**Files:**
- Create: `ui/src/styles/app.css`
- Modify: `ui/src/main.jsx`

- [ ] **Step 1: Copy verbatim**

```bash
cp ~/Downloads/design_handoff_channel/design-sources/app/app.css ui/src/styles/app.css
diff ~/Downloads/design_handoff_channel/design-sources/app/app.css ui/src/styles/app.css && echo "byte-identical OK"
wc -l ui/src/styles/app.css
# expected: 527
grep -c "var(--" ui/src/styles/app.css
# expected: 100+
```

- [ ] **Step 2: Wire import into `main.jsx`**

Read current `ui/src/main.jsx`. Add `import "./styles/app.css";` below the existing site.css import:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./styles/channel.css";
import "./styles/site.css";
import "./styles/app.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 3: Verify build**

```bash
cd ui && npm run build 2>&1 | tail -5 && cd ..
```

Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add ui/src/styles/app.css ui/src/main.jsx
git commit -m "feat(ui): port app.css chat layout styles + import from main

Verbatim copy of ~/Downloads/design_handoff_channel/design-sources/app/app.css.
Chat-app components in Phase 6c consume the classes (.sb, .composer,
.model-pick, .pop, .nav-item, .home, .greet, .quick, etc.) directly from
this sheet. Loaded globally alongside channel.css + site.css."
```

---

### Task 2: Create `data.js` mock data

**Files:**
- Create: `ui/src/app/data.js`
- Create: `ui/src/app/data.test.js`

**Step 1: Inspect the source data**

```bash
sed -n '/^const MODELS/,/^]/p' ~/Downloads/design_handoff_channel/design-sources/app/data.jsx
sed -n '/^const EFFORTS/,/^const /p' ~/Downloads/design_handoff_channel/design-sources/app/data.jsx | head -2
sed -n '/^const RECENTS/,/^]/p' ~/Downloads/design_handoff_channel/design-sources/app/data.jsx | head -25
sed -n '/^const QUICK_ACTIONS/,/^]/p' ~/Downloads/design_handoff_channel/design-sources/app/data.jsx
```

- [ ] **Step 2: Write the failing tests** (`ui/src/app/data.test.js`):

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import { EFFORTS, MODELS, QUICK_ACTIONS, RECENTS } from "./data.js";

describe("MODELS", () => {
  it("exposes exactly the three Claude models (Opus 4.8, Sonnet 4.6, Haiku 4.5)", () => {
    expect(MODELS.length).toBe(3);
    const ids = MODELS.map((m) => m.id);
    expect(ids).toEqual(["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]);
  });

  it("each model has id, name, short, tier, desc string fields", () => {
    for (const m of MODELS) {
      expect(typeof m.id).toBe("string");
      expect(typeof m.name).toBe("string");
      expect(typeof m.short).toBe("string");
      expect(typeof m.tier).toBe("string");
      expect(typeof m.desc).toBe("string");
    }
  });
});

describe("EFFORTS", () => {
  it("is the Low / Medium / High / Max segmented set", () => {
    expect(EFFORTS).toEqual(["Low", "Medium", "High", "Max"]);
  });
});

describe("RECENTS", () => {
  it("has at least 10 entries grouped by time-bucket strings", () => {
    expect(RECENTS.length).toBeGreaterThanOrEqual(10);
    for (const r of RECENTS) {
      expect(typeof r.id).toBe("string");
      expect(typeof r.title).toBe("string");
      expect(typeof r.group).toBe("string");
    }
  });

  it("has at least one entry in 'Today' and 'Yesterday' groups", () => {
    const groups = new Set(RECENTS.map((r) => r.group));
    expect(groups.has("Today")).toBe(true);
    expect(groups.has("Yesterday")).toBe(true);
  });
});

describe("QUICK_ACTIONS", () => {
  it("has four chips: Write, Learn, Code, Analyze data", () => {
    expect(QUICK_ACTIONS.length).toBe(4);
    const labels = QUICK_ACTIONS.map((q) => q.label);
    expect(labels).toEqual(["Write", "Learn", "Code", "Analyze data"]);
  });

  it("each chip has id, icon, label string fields", () => {
    for (const q of QUICK_ACTIONS) {
      expect(typeof q.id).toBe("string");
      expect(typeof q.icon).toBe("string");
      expect(typeof q.label).toBe("string");
    }
  });
});
```

- [ ] **Step 3: Run test (expect FAIL)**

```bash
mkdir -p ui/src/app
cd ui && npx vitest run src/app/data.test.js 2>&1 | tail -10 && cd ..
```

- [ ] **Step 4: Implement `ui/src/app/data.js`**

Port the subset from `design-sources/app/data.jsx`. Use named exports (the prototype attaches to `window`; we use ESM).

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.

export const MODELS = [
  { id: "claude-opus-4-8",   name: "Claude Opus 4.8",   short: "Opus 4.8",   tier: "Flagship", desc: "Most capable — complex reasoning, long-horizon agentic coding, and high-autonomy work." },
  { id: "claude-sonnet-4-6", name: "Claude Sonnet 4.6", short: "Sonnet 4.6", tier: "Balanced", desc: "The best blend of speed and intelligence. The right default for most work." },
  { id: "claude-haiku-4-5",  name: "Claude Haiku 4.5",  short: "Haiku 4.5",  tier: "Fast",     desc: "Fastest and most cost-effective for simple, high-volume tasks." },
];

export const EFFORTS = ["Low", "Medium", "High", "Max"];

export const RECENTS = [
  { id: "r1",  title: "Home server backup strategy", group: "Today" },
  { id: "r2",  title: "Weekend trail route near Asheville", group: "Today" },
  { id: "r3",  title: "Refactoring the auth middleware", group: "Today" },
  { id: "r4",  title: "Q3 board deck — narrative pass", group: "Yesterday" },
  { id: "r5",  title: "Sourdough hydration troubleshooting", group: "Yesterday" },
  { id: "r6",  title: "Postgres index not being used", group: "Yesterday" },
  { id: "r7",  title: "Reading list for systems design", group: "Previous 7 days" },
  { id: "r8",  title: "Naming the new analytics service", group: "Previous 7 days" },
  { id: "r9",  title: "Comparing standing desk options", group: "Previous 7 days" },
  { id: "r10", title: "Migration plan: REST → gRPC", group: "Previous 7 days" },
  { id: "r11", title: "Explaining diffusion models simply", group: "Previous 7 days" },
  { id: "r12", title: "Tax documents checklist for 2025", group: "Previous 30 days" },
  { id: "r13", title: "Garden irrigation zone layout", group: "Previous 30 days" },
  { id: "r14", title: "Rewriting the onboarding email", group: "Previous 30 days" },
  { id: "r15", title: "Debugging flaky CI test suite", group: "Previous 30 days" },
];

export const QUICK_ACTIONS = [
  { id: "write",   icon: "write",     label: "Write" },
  { id: "learn",   icon: "learn",     label: "Learn" },
  { id: "code",    icon: "code",      label: "Code" },
  { id: "analyze", icon: "customize", label: "Analyze data" },
];
```

- [ ] **Step 5: Run test (expect PASS)**

```bash
cd ui && npx vitest run src/app/data.test.js 2>&1 | tail -10 && cd ..
```

Expected: 7 tests pass.

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/data.js ui/src/app/data.test.js
git commit -m "feat(ui): add Phase 6c mock data (MODELS, EFFORTS, RECENTS, QUICK_ACTIONS)

Subset of design-sources/app/data.jsx needed by Phase 6c (Sidebar recents,
Composer model picker, ChatHome quick actions). Other prototype-only exports
(FONTS, PROJECTS, ARTIFACTS, SAMPLE_REPLY, SAMPLE_USER) ship later: FONTS in
6f Customize, PROJECTS/ARTIFACTS in 6e, SAMPLE_REPLY in 6d mock streamer."
```

---

### Task 3: Build `GoogleG` SVG icon (TDD)

**Files:**
- Create: `ui/src/app/GoogleG.jsx`
- Create: `ui/src/app/GoogleG.test.jsx`

The 4-colour Google "G" mark used only by the Login button. Lives next to `Login.jsx` — small enough not to merit a top-level icon library entry.

- [ ] **Step 1: Inspect the source SVG**

```bash
sed -n '/^function GoogleG/,/^}/p' ~/Downloads/design_handoff_channel/design-sources/app/shell.jsx
```

- [ ] **Step 2: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GoogleG from "./GoogleG.jsx";

describe("GoogleG", () => {
  it("renders an SVG with the four official Google brand colours", () => {
    const { container } = render(<GoogleG />);
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    const fills = Array.from(svg.querySelectorAll("path")).map((p) => p.getAttribute("fill"));
    expect(fills).toEqual(expect.arrayContaining(["#EA4335", "#4285F4", "#FBBC05", "#34A853"]));
  });

  it("applies the supplied size to width and height", () => {
    const { container } = render(<GoogleG size={24} />);
    const svg = container.querySelector("svg");
    expect(svg.getAttribute("width")).toBe("24");
    expect(svg.getAttribute("height")).toBe("24");
  });

  it("defaults to size 18 when omitted", () => {
    const { container } = render(<GoogleG />);
    expect(container.querySelector("svg").getAttribute("width")).toBe("18");
  });

  it("uses viewBox 0 0 48 48 (Google's standard)", () => {
    const { container } = render(<GoogleG />);
    expect(container.querySelector("svg").getAttribute("viewBox")).toBe("0 0 48 48");
  });
});
```

- [ ] **Step 3: Run test (expect FAIL)**

```bash
cd ui && npx vitest run src/app/GoogleG.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 4: Implement `GoogleG.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";

/**
 * Official 4-colour Google "G" mark for the OAuth sign-in button.
 * Ported verbatim from ~/Downloads/design_handoff_channel/design-sources/app/shell.jsx
 * (the `GoogleG` function). Standard 48×48 viewBox.
 */
export default function GoogleG({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" style={{ display: "block" }}>
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.6l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.4 5.4 2.5 13.3l7.8 6.1C12.2 13.2 17.6 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.6c0-1.6-.1-3.1-.4-4.6H24v9.1h12.4c-.5 2.9-2.1 5.3-4.6 6.9l7.1 5.5c4.2-3.9 6.6-9.6 6.6-16.9z" />
      <path fill="#FBBC05" d="M10.3 28.6c-.5-1.4-.8-2.9-.8-4.6s.3-3.2.8-4.6l-7.8-6.1C.9 16.5 0 20.1 0 24s.9 7.5 2.5 10.7l7.8-6.1z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.1-5.5c-2 1.3-4.5 2.1-8.8 2.1-6.4 0-11.8-3.7-13.7-9.9l-7.8 6.1C6.4 42.6 14.6 48 24 48z" />
    </svg>
  );
}
```

- [ ] **Step 5: Run test (expect PASS — 4 tests)**

```bash
cd ui && npx vitest run src/app/GoogleG.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/GoogleG.jsx ui/src/app/GoogleG.test.jsx
git commit -m "feat(ui): add GoogleG SVG icon (Google's 4-colour brand mark)

Ported from design-sources/app/shell.jsx. Used only by the Login button —
small enough to colocate with the only caller. Nominative-use of the
standard Google mark on the OAuth sign-in CTA."
```

---

### Task 4: Build `Login` component (TDD)

**Files:**
- Create: `ui/src/app/Login.jsx`
- Create: `ui/src/app/Login.test.jsx`

Centered card. The prototype mocks Google auth with a 1.2s timeout; we **replace** that with `globalThis.location.assign("/auth/login")` — Phase 5 already wired the real OAuth flow.

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Login from "./Login.jsx";

describe("Login", () => {
  let assignSpy;

  beforeEach(() => {
    assignSpy = vi.fn();
    vi.stubGlobal("location", { ...globalThis.location, assign: assignSpy });
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders the 'Sign in to Channel' heading", () => {
    render(<Login />);
    expect(screen.getByRole("heading", { name: /sign in to channel/i })).toBeTruthy();
  });

  it("renders the subtext 'Your workspace for thinking with AI.'", () => {
    render(<Login />);
    expect(screen.getByText(/workspace for thinking with AI/i)).toBeTruthy();
  });

  it("renders a 'Continue with Google' button with the Google G mark", () => {
    const { container } = render(<Login />);
    const btn = screen.getByRole("button", { name: /continue with google/i });
    expect(btn).toBeTruthy();
    // The button contains a multi-coloured SVG (the Google G)
    const fills = Array.from(btn.querySelectorAll("svg path")).map((p) => p.getAttribute("fill"));
    expect(fills).toEqual(expect.arrayContaining(["#EA4335", "#4285F4", "#FBBC05", "#34A853"]));
  });

  it("clicking 'Continue with Google' assigns location to /auth/login", () => {
    render(<Login />);
    fireEvent.click(screen.getByRole("button", { name: /continue with google/i }));
    expect(assignSpy).toHaveBeenCalledWith("/auth/login");
  });

  it("renders the fine-print legal line", () => {
    render(<Login />);
    expect(screen.getByText(/terms/i)).toBeTruthy();
    expect(screen.getByText(/privacy policy/i)).toBeTruthy();
  });

  it("renders the 'New here?' footer line", () => {
    render(<Login />);
    expect(screen.getByText(/new here\?/i)).toBeTruthy();
  });

  it("renders the ChannelMark logo (.ch-mark) at the top of the card", () => {
    const { container } = render(<Login />);
    expect(container.querySelector(".ch-mark")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd ui && npx vitest run src/app/Login.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 3: Implement `Login.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import ChannelMark from "../components/ChannelMark.jsx";
import GoogleG from "./GoogleG.jsx";

/**
 * Centered Google sign-in card. The prototype simulates auth with a 1.2s
 * timeout; we redirect to the real /auth/login endpoint (wired in Phase 5
 * — Google OAuth issues a mgmt JWT, then redirects to /app on success).
 * Translated from design-sources/app/shell.jsx `Login` function. Desktop
 * traffic-lights frame branch dropped per spec §"Window frames".
 */
export default function Login() {
  return (
    <div className="auth">
      <div className="auth-card">
        <span className="mk"><ChannelMark size={46} /></span>
        <h1>Sign in to Channel</h1>
        <p>Your workspace for thinking with AI.</p>
        <button
          type="button"
          className="google-btn"
          onClick={() => globalThis.location.assign("/auth/login")}
        >
          <GoogleG size={19} /> Continue with Google
        </button>
        <div className="auth-fine">
          By continuing, you agree to Channel&apos;s <a href="/terms">Terms</a> and <a href="/privacy">Privacy Policy</a>.
        </div>
      </div>
      <div className="auth-foot">
        New here? Your account is created automatically on first sign-in.
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test (expect PASS — 7 tests)**

```bash
cd ui && npx vitest run src/app/Login.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/Login.jsx ui/src/app/Login.test.jsx
git commit -m "feat(ui): add Login component (centered Google sign-in)

Translated from design-sources/app/shell.jsx. The prototype's 1.2s
setTimeout mock is replaced with location.assign('/auth/login') — the real
OAuth flow (Phase 5) issues a mgmt JWT and redirects to /app on success.
Desktop traffic-lights frame dropped per spec §'Window frames' (web only).
Fine-print legal anchors point at /terms and /privacy (Privacy ships in
Phase 6b; Terms is a deferred follow-up)."
```

---

### Task 5: Build `ModelPicker` component (TDD)

**Files:**
- Create: `ui/src/app/ModelPicker.jsx`
- Create: `ui/src/app/ModelPicker.test.jsx`

Trigger button + popover. Popover lists every model in `MODELS` with a check mark on the selected one, and a 4-segment effort control.

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ModelPicker from "./ModelPicker.jsx";
import { MODELS } from "./data.js";

const opus = MODELS[0];
const sonnet = MODELS[1];

describe("ModelPicker", () => {
  it("renders the trigger button with the current model's short name and effort", () => {
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    const btn = screen.getByRole("button");
    expect(btn.textContent).toContain("Opus 4.8");
    expect(btn.textContent).toContain("High");
  });

  it("opens the popover on click and shows every MODELS entry", () => {
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    fireEvent.click(screen.getByRole("button"));
    for (const m of MODELS) {
      expect(screen.getByText(m.name)).toBeTruthy();
    }
  });

  it("shows a check next to the selected model and not on others", () => {
    const { container } = render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    fireEvent.click(screen.getByRole("button"));
    // Selected entry contains the check span (".ck")
    const opusOpt = screen.getByText(opus.name).closest(".opt");
    const sonnetOpt = screen.getByText(sonnet.name).closest(".opt");
    expect(opusOpt.querySelector(".ck")).toBeTruthy();
    expect(sonnetOpt.querySelector(".ck")).toBeFalsy();
  });

  it("clicking a different model invokes onModel with that model object", () => {
    const onModel = vi.fn();
    render(<ModelPicker model={opus} effort="High" onModel={onModel} onEffort={vi.fn()} />);
    fireEvent.click(screen.getByRole("button"));
    fireEvent.click(screen.getByText(sonnet.name));
    expect(onModel).toHaveBeenCalledWith(sonnet);
  });

  it("renders the 4 effort segments (Low/Medium/High/Max) with the current highlighted", () => {
    render(<ModelPicker model={opus} effort="Medium" onModel={vi.fn()} onEffort={vi.fn()} />);
    fireEvent.click(screen.getByRole("button"));
    const low = screen.getByRole("button", { name: "Low" });
    const med = screen.getByRole("button", { name: "Medium" });
    expect(med.className).toContain("on");
    expect(low.className).not.toContain("on");
  });

  it("clicking an effort segment invokes onEffort with the new value", () => {
    const onEffort = vi.fn();
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={onEffort} />);
    fireEvent.click(screen.getByRole("button", { name: /opus/i }));
    fireEvent.click(screen.getByRole("button", { name: "Max" }));
    expect(onEffort).toHaveBeenCalledWith("Max");
  });

  it("clicking the backdrop closes the popover", () => {
    const { container } = render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /opus/i }));
    expect(container.querySelector(".pop")).toBeTruthy();
    fireEvent.click(container.querySelector(".backdrop"));
    expect(container.querySelector(".pop")).toBeFalsy();
  });
});
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd ui && npx vitest run src/app/ModelPicker.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 3: Implement `ModelPicker.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import Icon from "../components/Icon.jsx";
import { EFFORTS, MODELS } from "./data.js";

/**
 * Composer popover for picking the active model + reasoning effort.
 * Translated from design-sources/app/chat.jsx `ModelPicker` function.
 */
export default function ModelPicker({ model, effort, onModel, onEffort }) {
  const [open, setOpen] = useState(false);

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        className="model-pick"
        onClick={() => setOpen((o) => !o)}
      >
        {model.short || model.name}
        <span className="eff">{effort}</span>
        <Icon name="chevron-down" size={14} />
      </button>
      {open && (
        <>
          <div className="backdrop" onClick={() => setOpen(false)} />
          <div className="pop" style={{ bottom: "calc(100% + 8px)", left: 0 }}>
            <div className="pop-h">Model</div>
            {MODELS.map((m) => (
              <div className="opt" key={m.id} onClick={() => { onModel(m); }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="nm">{m.name}</span>
                    <span className="tg">{m.tier}</span>
                  </div>
                  <div className="ds">{m.desc}</div>
                </div>
                {m.id === model.id && (
                  <span className="ck"><Icon name="check" size={16} /></span>
                )}
              </div>
            ))}
            <div className="pop-h" style={{ marginTop: 4 }}>Reasoning effort</div>
            <div className="seg">
              {EFFORTS.map((e) => (
                <button
                  key={e}
                  type="button"
                  className={e === effort ? "on" : ""}
                  onClick={() => onEffort(e)}
                >
                  {e}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
```

> The Icon component doesn't currently have a `check` case (verified at the Phase 6a Icon.test.jsx exhaustive-list — see if needed). If `Icon name="check"` returns null, add a `case "check":` to `ui/src/components/Icon.jsx` matching the source's check glyph (`<path d="M5 12l4 4L19 7"/>`) and to its test's `it.each` list. Skip this step if `check` is already present.

- [ ] **Step 4: Verify Icon has 'check' case; add if missing**

```bash
grep "case \"check\"" ui/src/components/Icon.jsx && echo "exists" || echo "MISSING — add it"
```

If MISSING, add the case to `ui/src/components/Icon.jsx`'s switch (alphabetically near other letters):

```jsx
    case "check": return <svg {...p}><path d="M5 12l4 4L19 7"/></svg>;
```

And add `"check"` to the `it.each([...])` list in `ui/src/components/Icon.test.jsx`. Run the Icon tests to confirm:

```bash
cd ui && npx vitest run src/components/Icon.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 5: Run ModelPicker tests (expect PASS — 7 tests)**

```bash
cd ui && npx vitest run src/app/ModelPicker.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/ModelPicker.jsx ui/src/app/ModelPicker.test.jsx ui/src/components/Icon.jsx ui/src/components/Icon.test.jsx
git commit -m "feat(ui): add ModelPicker popover (model + effort selection)

Composer trigger button + popover. Translated from design-sources/app/
chat.jsx. The popover lists MODELS with a check on the selected one;
EFFORTS render as a 4-segment control. If Icon didn't already have a
'check' case, this commit adds it (with matching test list update)."
```

---

### Task 6: Build `AttachMenu` component (TDD)

**Files:**
- Create: `ui/src/app/AttachMenu.jsx`
- Create: `ui/src/app/AttachMenu.test.jsx`

The `+` button on the composer that opens a popover with three options. Each option, when clicked, calls `onAdd(sample)` and closes the menu.

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AttachMenu from "./AttachMenu.jsx";

describe("AttachMenu", () => {
  it("renders a round + button that triggers the menu", () => {
    const { container } = render(<AttachMenu onAdd={vi.fn()} />);
    expect(container.querySelector(".cbtn.round")).toBeTruthy();
  });

  it("opens the popover on click and shows the three options", () => {
    render(<AttachMenu onAdd={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Add attachment"));
    expect(screen.getByText("Upload a file")).toBeTruthy();
    expect(screen.getByText("Add photos or images")).toBeTruthy();
    expect(screen.getByText("Connect data source")).toBeTruthy();
  });

  it("clicking 'Upload a file' invokes onAdd with a file-kind sample and closes the menu", () => {
    const onAdd = vi.fn();
    const { container } = render(<AttachMenu onAdd={onAdd} />);
    fireEvent.click(screen.getByTitle("Add attachment"));
    fireEvent.click(screen.getByText("Upload a file"));
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ kind: "file" }));
    expect(container.querySelector(".pop")).toBeFalsy();
  });

  it("clicking the backdrop closes the popover", () => {
    const { container } = render(<AttachMenu onAdd={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Add attachment"));
    expect(container.querySelector(".pop")).toBeTruthy();
    fireEvent.click(container.querySelector(".backdrop"));
    expect(container.querySelector(".pop")).toBeFalsy();
  });
});
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd ui && npx vitest run src/app/AttachMenu.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 3: Implement `AttachMenu.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import Icon from "../components/Icon.jsx";

const ITEMS = [
  { ic: "file",     label: "Upload a file",           sample: { kind: "file",  name: "spec-v2.pdf",       ic: "doc" } },
  { ic: "image",    label: "Add photos or images",    sample: { kind: "image", name: "diagram.png",       ic: "image" } },
  { ic: "database", label: "Connect data source",     sample: { kind: "data",  name: "events.parquet",    ic: "database" } },
];

/**
 * Composer + button popover. Three mock sample attachments — clicking one
 * appends it to the composer's attachment chips list via `onAdd`. Translated
 * from design-sources/app/chat.jsx `AttachMenu` function.
 */
export default function AttachMenu({ onAdd }) {
  const [open, setOpen] = useState(false);

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        className="cbtn round"
        onClick={() => setOpen((o) => !o)}
        title="Add attachment"
      >
        <Icon name="plus" size={19} />
      </button>
      {open && (
        <>
          <div className="backdrop" onClick={() => setOpen(false)} />
          <div className="pop" style={{ bottom: "calc(100% + 8px)", left: 0, minWidth: 240 }}>
            {ITEMS.map((it, i) => (
              <div
                className="opt"
                key={i}
                onClick={() => { onAdd(it.sample); setOpen(false); }}
              >
                <span style={{ color: "var(--ink-soft)" }}>
                  <Icon name={it.ic} size={18} />
                </span>
                <span className="nm" style={{ fontWeight: 500 }}>{it.label}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
```

> The Icon component may need `file`, `image`, `database`, `doc` cases. Check:
>
> ```bash
> for i in file image database doc; do grep "case \"$i\"" ui/src/components/Icon.jsx > /dev/null && echo "$i: present" || echo "$i: MISSING"; done
> ```
>
> For any MISSING cases, copy the matching `case 'X': return <svg {...p}>…</svg>;` from `design-sources/app/icons.jsx` into the switch in `ui/src/components/Icon.jsx` and add the name to the `it.each([…])` list in `Icon.test.jsx`. Re-run the Icon test to confirm.

- [ ] **Step 4: Run AttachMenu tests (expect PASS — 4 tests)**

```bash
cd ui && npx vitest run src/app/AttachMenu.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/AttachMenu.jsx ui/src/app/AttachMenu.test.jsx ui/src/components/Icon.jsx ui/src/components/Icon.test.jsx
git commit -m "feat(ui): add AttachMenu popover (composer + button)

Three sample-attachment options. Translated from design-sources/app/chat.jsx
'AttachMenu' function. Any missing Icon cases (file/image/database/doc)
backfilled from design-sources/app/icons.jsx with matching test entries."
```

---

### Task 7: Build `Composer` component (TDD)

**Files:**
- Create: `ui/src/app/Composer.jsx`
- Create: `ui/src/app/Composer.test.jsx`

Auto-growing textarea + AttachMenu + ModelPicker + mic + send. The `onSend(text, atts)` callback is the integration point for Phase 6d's mock streamer; for now it's a no-op.

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Composer from "./Composer.jsx";
import { MODELS } from "./data.js";

const opus = MODELS[0];

function defaultProps(overrides = {}) {
  return {
    model: opus,
    effort: "High",
    setModel: vi.fn(),
    setEffort: vi.fn(),
    onSend: vi.fn(),
    placeholder: undefined,
    autofocus: false,
    ...overrides,
  };
}

describe("Composer", () => {
  it("renders a textarea with the supplied placeholder", () => {
    render(<Composer {...defaultProps({ placeholder: "How can I help?" })} />);
    expect(screen.getByPlaceholderText("How can I help?")).toBeTruthy();
  });

  it("defaults the placeholder when none supplied", () => {
    render(<Composer {...defaultProps()} />);
    expect(screen.getByPlaceholderText(/how can i help/i)).toBeTruthy();
  });

  it("disables the send button when the input is empty", () => {
    render(<Composer {...defaultProps()} />);
    expect(screen.getByTitle("Send").disabled).toBe(true);
  });

  it("enables the send button once the user types text", () => {
    render(<Composer {...defaultProps()} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "hello" } });
    expect(screen.getByTitle("Send").disabled).toBe(false);
  });

  it("clicking Send invokes onSend with the typed text and clears the input", () => {
    const onSend = vi.fn();
    render(<Composer {...defaultProps({ onSend })} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "hello world" } });
    fireEvent.click(screen.getByTitle("Send"));
    expect(onSend).toHaveBeenCalledWith("hello world", []);
    expect(ta.value).toBe("");
  });

  it("Enter (no shift) submits; Shift+Enter inserts a newline", () => {
    const onSend = vi.fn();
    render(<Composer {...defaultProps({ onSend })} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "x" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledTimes(1);
    onSend.mockClear();
    fireEvent.change(ta, { target: { value: "x" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSend).toHaveBeenCalledTimes(0);
  });

  it("renders the ModelPicker trigger with current model short name", () => {
    render(<Composer {...defaultProps()} />);
    expect(screen.getByText(opus.short)).toBeTruthy();
  });

  it("renders the mic and attach buttons", () => {
    render(<Composer {...defaultProps()} />);
    expect(screen.getByTitle("Add attachment")).toBeTruthy();
    expect(screen.getByTitle("Dictate")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd ui && npx vitest run src/app/Composer.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 3: Implement `Composer.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef, useState } from "react";
import Icon from "../components/Icon.jsx";
import AttachMenu from "./AttachMenu.jsx";
import ModelPicker from "./ModelPicker.jsx";

/**
 * Auto-growing textarea + attach + model picker + mic + send. Translated
 * from design-sources/app/chat.jsx `Composer` function.
 *
 * Props:
 *   - model, effort, setModel, setEffort — for ModelPicker
 *   - onSend(text, atts) — invoked on submit; clears the input afterward
 *   - placeholder — defaults to "How can I help you today?"
 *   - autofocus — focuses the textarea on mount when true
 */
export default function Composer({ model, effort, setModel, setEffort, onSend, autofocus, placeholder }) {
  const [text, setText] = useState("");
  const [atts, setAtts] = useState([]);
  const taRef = useRef(null);

  function grow() {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  }

  useEffect(() => {
    if (autofocus && taRef.current) taRef.current.focus();
  }, [autofocus]);

  function submit() {
    if (!text.trim() && atts.length === 0) return;
    onSend(text.trim() || "Take a look at the attached files.", atts);
    setText("");
    setAtts([]);
    requestAnimationFrame(grow);
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="composer-wrap">
      <div className="composer">
        {atts.length > 0 && (
          <div className="attaches">
            {atts.map((a, i) => (
              <div className="chip" key={i}>
                <span className="tile"><Icon name={a.ic} size={15} /></span>
                <span>{a.name}</span>
                <span className="x" onClick={() => setAtts(atts.filter((_, j) => j !== i))}>
                  <Icon name="close" size={14} />
                </span>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={taRef}
          rows={1}
          placeholder={placeholder || "How can I help you today?"}
          value={text}
          onChange={(e) => { setText(e.target.value); grow(); }}
          onKeyDown={onKeyDown}
        />
        <div className="composer-row">
          <AttachMenu onAdd={(a) => setAtts((p) => [...p, a])} />
          <div className="spacer" />
          <ModelPicker model={model} effort={effort} onModel={setModel} onEffort={setEffort} />
          <button type="button" className="cbtn round" title="Dictate">
            <Icon name="mic" size={18} />
          </button>
          <button
            type="button"
            className="send"
            disabled={!text.trim() && atts.length === 0}
            onClick={submit}
            title="Send"
          >
            <Icon name="arrow-up" size={18} stroke={2} />
          </button>
        </div>
      </div>
    </div>
  );
}
```

> Icon may need a `close` case. Check + backfill per Task 5/6 pattern if missing.

- [ ] **Step 4: Run Composer tests (expect PASS — 8 tests)**

```bash
cd ui && npx vitest run src/app/Composer.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/Composer.jsx ui/src/app/Composer.test.jsx ui/src/components/Icon.jsx ui/src/components/Icon.test.jsx
git commit -m "feat(ui): add Composer (textarea + attach + model picker + send)

Translated from design-sources/app/chat.jsx 'Composer' function. Wraps
AttachMenu + ModelPicker; auto-grows textarea up to 220px; Enter submits,
Shift+Enter newlines. Phase 6c's onSend is supplied by parents but at
this stage is wired to a no-op (mock streamer arrives in 6d)."
```

---

### Task 8: Build `AccountPopover` component (TDD)

**Files:**
- Create: `ui/src/app/AccountPopover.jsx`
- Create: `ui/src/app/AccountPopover.test.jsx`

The popover that opens above the sidebar account row. Shows the user's display name + email + a Sign out option.

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AccountPopover from "./AccountPopover.jsx";

describe("AccountPopover", () => {
  it("renders the user name and email", () => {
    render(<AccountPopover userName="Ada Lovelace" email="ada@example.com" onSignOut={vi.fn()} />);
    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
    expect(screen.getByText("ada@example.com")).toBeTruthy();
  });

  it("renders a Sign out row", () => {
    render(<AccountPopover userName="x" email="x@example.com" onSignOut={vi.fn()} />);
    expect(screen.getByText("Sign out")).toBeTruthy();
  });

  it("clicking Sign out invokes onSignOut", () => {
    const onSignOut = vi.fn();
    render(<AccountPopover userName="x" email="x@example.com" onSignOut={onSignOut} />);
    fireEvent.click(screen.getByText("Sign out"));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd ui && npx vitest run src/app/AccountPopover.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 3: Implement `AccountPopover.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Icon from "../components/Icon.jsx";

/**
 * The popover that opens above the sidebar account row. Shows the
 * authenticated user's display name + email and a Sign out option.
 * Translated from the popover JSX inside design-sources/app/shell.jsx
 * `Sidebar`. Caller controls open/close.
 */
export default function AccountPopover({ userName, email, onSignOut }) {
  return (
    <div className="pop" style={{ bottom: "calc(100% + 6px)", left: 0, right: 0, minWidth: 0 }}>
      <div style={{ padding: "8px 10px 6px" }}>
        <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>{userName}</div>
        <div className="mono" style={{ fontSize: 11.5, color: "var(--ink-faint)", marginTop: 1 }}>{email}</div>
      </div>
      <div style={{ height: 1, background: "var(--border-soft)", margin: "4px 0" }} />
      <div className="opt" onClick={onSignOut}>
        <span style={{ color: "var(--ink-soft)" }}><Icon name="arrow-right" size={16} /></span>
        <span className="nm" style={{ fontWeight: 500 }}>Sign out</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests (expect PASS — 3 tests)**

```bash
cd ui && npx vitest run src/app/AccountPopover.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/AccountPopover.jsx ui/src/app/AccountPopover.test.jsx
git commit -m "feat(ui): add AccountPopover (sidebar account-row popover)

Email + Sign out. Extracted from the popover JSX in design-sources/app/
shell.jsx Sidebar. Caller (Sidebar) controls open/close — same shape as
ModelPicker and AttachMenu, so the backdrop logic stays with the caller."
```

---

### Task 9: Build `Sidebar` component (TDD)

**Files:**
- Create: `ui/src/app/Sidebar.jsx`
- Create: `ui/src/app/Sidebar.test.jsx`

The 264px left nav. The biggest 6c component. Per spec deferrals: drop the desktop traffic lights, drop the "Relaunch to update v2.4.1 ready" pill, drop the "Channel Max" plan label.

User name + email come from the mgmt JWT via `lib/auth.js`:`parseToken(token).email` — the token is read from `localStorage["starter_mgmt_token"]`.

Sign out clears the token and redirects to `/` (the marketing site).

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Sidebar from "./Sidebar.jsx";
import { TOKEN_KEY } from "../lib/auth.js";

function makeToken({ email = "ada@example.com" } = {}) {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("Sidebar", () => {
  let storage;
  let assignSpy;

  beforeEach(() => {
    storage = { [TOKEN_KEY]: makeToken() };
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    assignSpy = vi.fn();
    vi.stubGlobal("location", { ...globalThis.location, assign: assignSpy });
  });
  afterEach(() => vi.unstubAllGlobals());

  function renderSidebar() {
    return render(<MemoryRouter><Sidebar /></MemoryRouter>);
  }

  it("renders four primary nav items (New chat, Projects, Artifacts, Customize)", () => {
    renderSidebar();
    expect(screen.getByRole("button", { name: /new chat/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^projects/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^artifacts/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^customize/i })).toBeTruthy();
  });

  it("renders time-grouped recents (Today, Yesterday, …)", () => {
    renderSidebar();
    expect(screen.getByText("Today")).toBeTruthy();
    expect(screen.getByText("Yesterday")).toBeTruthy();
  });

  it("renders the account row with the email's local-part initial", () => {
    renderSidebar();
    // Email "ada@example.com" → initials "A" (one letter is OK for short names)
    expect(screen.getByText("ada@example.com")).toBeTruthy();
  });

  it("clicking the account row opens the popover", () => {
    renderSidebar();
    const account = screen.getByText("ada@example.com").closest("button");
    fireEvent.click(account);
    expect(screen.getByText("Sign out")).toBeTruthy();
  });

  it("Sign out clears the mgmt token and assigns location to /", () => {
    renderSidebar();
    fireEvent.click(screen.getByText("ada@example.com").closest("button"));
    fireEvent.click(screen.getByText("Sign out"));
    expect(storage[TOKEN_KEY]).toBeUndefined();
    expect(assignSpy).toHaveBeenCalledWith("/");
  });

  it("clicking the search button reveals the search input", () => {
    renderSidebar();
    fireEvent.click(screen.getByTitle("Search"));
    expect(screen.getByPlaceholderText("Search chats")).toBeTruthy();
  });

  it("typing in the search input filters recents to matching titles", () => {
    renderSidebar();
    fireEvent.click(screen.getByTitle("Search"));
    fireEvent.change(screen.getByPlaceholderText("Search chats"), { target: { value: "Postgres" } });
    expect(screen.getByText(/Postgres index not being used/i)).toBeTruthy();
    expect(screen.queryByText(/Weekend trail route/i)).toBeNull();
  });

  it("does NOT render the 'Relaunch to update' pill (Electron-only)", () => {
    renderSidebar();
    expect(screen.queryByText(/relaunch to update/i)).toBeNull();
  });

  it("does NOT render the 'Channel Max' plan label (deferred)", () => {
    renderSidebar();
    expect(screen.queryByText(/channel max/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd ui && npx vitest run src/app/Sidebar.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 3: Implement `Sidebar.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import { parseToken, TOKEN_KEY } from "../lib/auth.js";
import Icon from "../components/Icon.jsx";
import AccountPopover from "./AccountPopover.jsx";
import { RECENTS } from "./data.js";

/**
 * 264px left nav. Primary items (New chat / Projects / Artifacts / Customize)
 * + a Search input that filters RECENTS + time-grouped Recents + an account
 * row at the bottom.
 *
 * Translated from design-sources/app/shell.jsx `Sidebar` function. Per
 * spec deferrals: desktop traffic-lights branch dropped (web only),
 * "Relaunch to update" pill dropped (Electron only), "Channel Max" plan
 * label dropped (billing deferred). User name + email derive from the
 * mgmt JWT.
 *
 * Sign out clears the JWT and sends the user back to the marketing site at /.
 */
export default function Sidebar() {
  const [searching, setSearching] = useState(false);
  const [search, setSearch] = useState("");
  const [acctOpen, setAcctOpen] = useState(false);

  const token = localStorage.getItem(TOKEN_KEY) ?? "";
  const claims = parseToken(token) ?? {};
  const email = claims.email ?? "you@example.com";
  const userName = email.split("@")[0] || "You";
  const initials = userName.slice(0, 2).toUpperCase();

  const filtered = RECENTS.filter((r) =>
    r.title.toLowerCase().includes(search.toLowerCase())
  );
  const groups = [];
  filtered.forEach((r) => {
    let g = groups.find((x) => x.name === r.group);
    if (!g) { g = { name: r.group, items: [] }; groups.push(g); }
    g.items.push(r);
  });

  function signOut() {
    localStorage.removeItem(TOKEN_KEY);
    globalThis.location.assign("/");
  }

  return (
    <div className="sb">
      <div className="sb-top">
        <div className="sb-actions">
          <button type="button" className="icon-btn" title="Toggle sidebar"><Icon name="sidebar" size={18} /></button>
          <button type="button" className="icon-btn" title="Search" onClick={() => setSearching((s) => !s)}>
            <Icon name="search" size={18} />
          </button>
        </div>
      </div>

      <div className="sb-scroll">
        {searching && (
          <div style={{ padding: "0 2px 8px" }}>
            <div className="composer" style={{ padding: "7px 10px", boxShadow: "none" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--ink-faint)" }}>
                <Icon name="search" size={16} />
                <input
                  autoFocus
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search chats"
                  style={{
                    border: "none",
                    outline: "none",
                    background: "transparent",
                    color: "var(--ink)",
                    fontFamily: "var(--font-sans)",
                    fontSize: 13.5,
                    width: "100%",
                  }}
                />
              </div>
            </div>
          </div>
        )}

        <button type="button" className="nav-item primary">
          <span className="ic"><Icon name="plus" size={18} /></span> New chat
        </button>
        <button type="button" className="nav-item">
          <span className="ic"><Icon name="projects" size={18} /></span> Projects
        </button>
        <button type="button" className="nav-item">
          <span className="ic"><Icon name="artifacts" size={18} /></span> Artifacts
        </button>
        <button type="button" className="nav-item">
          <span className="ic"><Icon name="customize" size={18} /></span> Customize
        </button>

        {groups.map((g) => (
          <div key={g.name}>
            <div className="sb-section">{g.name}</div>
            {g.items.map((r) => (
              <button type="button" key={r.id} className="recent">{r.title}</button>
            ))}
          </div>
        ))}
        {filtered.length === 0 && (
          <div style={{ padding: "20px 10px", fontSize: 13, color: "var(--ink-faint)" }}>
            No chats match &ldquo;{search}&rdquo;.
          </div>
        )}
      </div>

      <div className="sb-foot">
        <div className="account-wrap">
          {acctOpen && (
            <>
              <div className="backdrop" onClick={() => setAcctOpen(false)} />
              <AccountPopover userName={userName} email={email} onSignOut={signOut} />
            </>
          )}
          <button type="button" className="account" onClick={() => setAcctOpen((o) => !o)}>
            <span className="avatar">{initials}</span>
            <div style={{ flex: 1, textAlign: "left" }}>
              <div className="nm">{userName}</div>
              <div className="pl" style={{ fontSize: 11.5, color: "var(--ink-faint)" }}>{email}</div>
            </div>
            <Icon name="chevron-down" size={15} style={{ color: "var(--ink-faint)" }} />
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run Sidebar tests (expect PASS — 9 tests)**

```bash
cd ui && npx vitest run src/app/Sidebar.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/Sidebar.jsx ui/src/app/Sidebar.test.jsx
git commit -m "feat(ui): add Sidebar (left nav + recents + account row)

264px nav: New chat / Projects / Artifacts / Customize + search-filtered
time-grouped Recents + account row at the bottom. User name + email come
from the mgmt JWT (parseToken on localStorage[starter_mgmt_token]). Sign
out clears the token and assigns location to /. Per spec deferrals: drops
the desktop traffic lights (web only), the Relaunch-to-update pill
(Electron only), and the 'Channel Max' plan label (billing deferred)."
```

---

### Task 10: Build `Shell` layout wrapper (TDD)

**Files:**
- Create: `ui/src/app/Shell.jsx`
- Create: `ui/src/app/Shell.test.jsx`

The chat-app layout: Sidebar on the left + `<main>` holding the routed child on the right.

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Shell from "./Shell.jsx";
import { TOKEN_KEY } from "../lib/auth.js";

function makeToken() {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email: "a@b.com" }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("Shell", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: (k) => (k === TOKEN_KEY ? makeToken() : null),
      setItem: vi.fn(), removeItem: vi.fn(),
    });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the supplied children inside a <main>", () => {
    render(
      <MemoryRouter>
        <Shell>
          <div data-testid="child" />
        </Shell>
      </MemoryRouter>
    );
    expect(screen.getByTestId("child")).toBeTruthy();
  });

  it("renders the Sidebar alongside the children", () => {
    render(
      <MemoryRouter>
        <Shell>
          <div />
        </Shell>
      </MemoryRouter>
    );
    expect(screen.getByRole("button", { name: /new chat/i })).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd ui && npx vitest run src/app/Shell.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 3: Implement `Shell.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Sidebar from "./Sidebar.jsx";

/**
 * Wraps every authenticated chat-app route with the persistent Sidebar.
 * Translated from the `ChannelApp` top-level orchestrator in
 * design-sources/app/shell.jsx. Phase 6c keeps it minimal: Sidebar + main;
 * Phase 6e adds the MainTop bar for share/etc.
 */
export default function Shell({ children }) {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="app-main">{children}</main>
    </div>
  );
}
```

- [ ] **Step 4: Run Shell tests (expect PASS — 2 tests)**

```bash
cd ui && npx vitest run src/app/Shell.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/Shell.jsx ui/src/app/Shell.test.jsx
git commit -m "feat(ui): add Shell layout wrapper (Sidebar + main)

Two-column chat-app shell. Phase 6e adds a top bar (share + sidebar
collapse toggle) once the Conversation view exists. Translated from
the ChannelApp orchestrator in design-sources/app/shell.jsx."
```

---

### Task 11: Build `ChatHome` component (TDD)

**Files:**
- Create: `ui/src/app/ChatHome.jsx`
- Create: `ui/src/app/ChatHome.test.jsx`

The empty-state shown when the user lands on `/app` without an active conversation. Greeting + Composer + quick-action chips. The composer's `onSend` at Phase 6c is wired to a no-op (mock streamer arrives in 6d).

- [ ] **Step 1: Write the failing tests**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatHome from "./ChatHome.jsx";
import { TOKEN_KEY } from "../lib/auth.js";
import { QUICK_ACTIONS } from "./data.js";

function makeToken({ email = "ada@example.com" } = {}) {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const payload = btoa(JSON.stringify({ exp, sub: "u1", role: "user", email }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("ChatHome", () => {
  let storage;

  beforeEach(() => {
    storage = { [TOKEN_KEY]: makeToken() };
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the 'Back at it, <name>' greeting derived from the JWT email", () => {
    render(<ChatHome />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it, ada/i);
  });

  it("renders the composer (textarea)", () => {
    render(<ChatHome />);
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("renders all four quick-action chips", () => {
    render(<ChatHome />);
    for (const q of QUICK_ACTIONS) {
      expect(screen.getByRole("button", { name: q.label })).toBeTruthy();
    }
  });

  it("clicking a quick-action does not throw (no-op send at Phase 6c)", () => {
    render(<ChatHome />);
    expect(() => fireEvent.click(screen.getByRole("button", { name: "Write" }))).not.toThrow();
  });

  it("clicking Send on a typed message does not throw (no-op send at Phase 6c)", () => {
    render(<ChatHome />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "hi" } });
    expect(() => fireEvent.click(screen.getByTitle("Send"))).not.toThrow();
  });
});
```

- [ ] **Step 2: Run test (expect FAIL)**

```bash
cd ui && npx vitest run src/app/ChatHome.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 3: Implement `ChatHome.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { parseToken, TOKEN_KEY } from "../lib/auth.js";
import ChannelMark from "../components/ChannelMark.jsx";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
import Composer from "./Composer.jsx";
import { MODELS, QUICK_ACTIONS } from "./data.js";

/**
 * Empty-state shown at /app when there's no active conversation.
 * Greeting + Composer + quick-action chips. Translated from
 * design-sources/app/chat.jsx `Home` function. The composer's onSend is
 * wired to a Phase-6c no-op; mock streaming arrives in 6d.
 */
export default function ChatHome() {
  const prefs = useChannelPrefs();
  const token = localStorage.getItem(TOKEN_KEY) ?? "";
  const claims = parseToken(token) ?? {};
  const userName = (claims.email ?? "you@example.com").split("@")[0] || "You";

  // Derive the model object from the prefs string id.
  const modelObj = MODELS.find((m) => m.id === prefs.model) ?? MODELS[0];
  const setModelObj = (m) => prefs.setModel(m.id);

  // Phase 6c no-op send. Phase 6d swaps this for the mock streamer hook.
  function noOpSend(/* text, atts */) {
    /* no-op until Phase 6d */
  }

  return (
    <div className="home">
      <div className="greet">
        <span className="gm"><ChannelMark size={34} /></span>
        <h1>Back at it, {userName}</h1>
      </div>
      <Composer
        model={modelObj}
        effort={prefs.effort}
        setModel={setModelObj}
        setEffort={prefs.setEffort}
        onSend={noOpSend}
        autofocus
      />
      <div className="quick">
        {QUICK_ACTIONS.map((q) => (
          <button
            type="button"
            className="qa"
            key={q.id}
            onClick={() => noOpSend(`Help me ${q.label.toLowerCase()} something.`, [])}
          >
            <span className="ic"><Icon name={q.icon} size={17} /></span>
            {q.label}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run ChatHome tests (expect PASS — 5 tests)**

```bash
cd ui && npx vitest run src/app/ChatHome.test.jsx 2>&1 | tail -10 && cd ..
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/ChatHome.jsx ui/src/app/ChatHome.test.jsx
git commit -m "feat(ui): add ChatHome (greeting + composer + quick actions)

Empty-state at /app. Greeting derives the first name from the JWT email's
local-part. Composer's model object derives from useChannelPrefs.model
(string id); setModel wraps prefs.setModel to write the id back. onSend
is a no-op at Phase 6c — Phase 6d swaps it for the mock streamer."
```

---

### Task 12: Wire `Login` + Shell-wrapped `ChatHome` into `App.jsx`

**Files:**
- Modify: `ui/src/App.jsx`
- Modify: `ui/src/App.test.jsx`

Replace the Phase 6a `AppLogin` and `AppHome` placeholders with the real components. Leave the other app placeholders (`AppConversation`, `AppProjects`, etc.) — Phase 6d/6e/6f replaces them.

- [ ] **Step 1: Edit `App.jsx`**

Replace the imports + the two route mounts. Concretely:

Add at the top with the other imports:

```jsx
import ChatHome from "./app/ChatHome.jsx";
import Login from "./app/Login.jsx";
import Shell from "./app/Shell.jsx";
```

Remove these two inline placeholders:

```jsx
const AppLogin = () => ph("app-login", "App: Login");
const AppHome  = () => ph("app-home", "App: Home");
```

Update the two routes from:

```jsx
<Route path="/app/login"          element={<AppLogin />} />
<Route path="/app"                element={<AuthGate><AppHome /></AuthGate>} />
```

to:

```jsx
<Route path="/app/login"          element={<Login />} />
<Route path="/app"                element={<AuthGate><Shell><ChatHome /></Shell></AuthGate>} />
```

Leave the other 5 app routes unchanged (still wrapped in `<AuthGate>` with their placeholder components — those become Shell-wrapped real components in 6d/6e/6f).

- [ ] **Step 2: Update `App.test.jsx`**

Find the test `"renders the app-login placeholder at /app/login (no auth required)"` and replace its assertion body with:

```jsx
  it("renders the Login page at /app/login (no auth required)", async () => {
    window.history.pushState({}, "", "/app/login");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { name: /sign in to channel/i })).toBeTruthy();
  });
```

Find the test `"renders the app-home placeholder at /app with a valid token"` and replace with:

```jsx
  it("renders the ChatHome at /app with a valid token", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/back at it/i);
  });
```

Find the `it("redirects /app to /app/login when no token")` and update its assertion to:

```jsx
  it("redirects /app to /app/login when no token", async () => {
    window.history.pushState({}, "", "/app");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { name: /sign in to channel/i })).toBeTruthy();
  });
```

The other app-route placeholder tests (`/app/c/:id`, `/app/projects`, `/app/projects/:id`, `/app/artifacts`, `/app/customize`) remain unchanged — those routes still render their `data-testid` placeholders.

- [ ] **Step 3: Run App tests**

```bash
cd ui && npx vitest run src/App.test.jsx 2>&1 | tail -15 && cd ..
```

- [ ] **Step 4: Run full UI test suite**

```bash
cd ui && npm run test 2>&1 | tail -8 && cd ..
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ui/src/App.jsx ui/src/App.test.jsx
git commit -m "feat(ui): mount real Login + Shell-wrapped ChatHome in App

Replace Phase 6a placeholder AppLogin/AppHome with real components from
ui/src/app/. The other app-side placeholders (AppConversation, AppProjects,
AppProjectDetail, AppArtifacts, AppCustomize) remain — replaced by
6d/6e/6f. App.test.jsx assertions updated to query the real h1 text
('Sign in to Channel', 'Back at it, …') instead of placeholder testids."
```

---

### Task 13: Update `CLAUDE.md` for Phase 6c

**File:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Locate the UI subtree in `## Structure`**

```bash
grep -n "ui/src\|marketing/\|styles/" CLAUDE.md | head -10
```

- [ ] **Step 2: Update the file tree**

In the `ui/src/styles/` block, add `app.css`:

```
│   │   ├── styles/
│   │   │   ├── channel.css    # Design tokens (OKLCH themes, radii, shadows, fonts)
│   │   │   ├── site.css       # Marketing site layout (nav, footer, hero, tiers)
│   │   │   └── app.css        # Chat-app layout (sidebar, composer, popovers, greet)
```

Add the `app/` subtree (Phase 6c) alongside `marketing/`:

```
│   │   ├── app/               # Chat app routes (Phase 6c onward)
│   │   │   ├── data.js                                       # MODELS, EFFORTS, RECENTS, QUICK_ACTIONS
│   │   │   ├── Login.jsx, GoogleG.jsx                        # Centered Google sign-in
│   │   │   ├── Shell.jsx, Sidebar.jsx, AccountPopover.jsx    # Layout chrome
│   │   │   ├── Composer.jsx, ModelPicker.jsx, AttachMenu.jsx # Composer + its popovers
│   │   │   └── ChatHome.jsx                                  # Empty-state greeting
```

(Match the existing tree's comment-column style.)

- [ ] **Step 3: Add a UI conventions bullet**

In the `## UI conventions` section, after the marketing bullets added in Phase 6b, add:

```
- **Chat-app popovers** — ModelPicker, AttachMenu, AccountPopover all follow
  the same `<div className="backdrop" />` + `<div className="pop" />` pattern.
  The backdrop captures outside-clicks to close the popover; the caller
  controls `open` state.
- **User identity from JWT** — chat-app components that need the user's
  email or display name read the mgmt JWT from `localStorage[TOKEN_KEY]`
  via `parseToken` from `lib/auth.js`. Display name = email's local-part
  unless we later add a `name` claim.
```

- [ ] **Step 4: Verify the doc parses**

```bash
head -100 CLAUDE.md
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): add Phase 6c app shell to file tree + UI conventions

styles/app.css + app/ subtree. UI conventions get two new bullets: the
shared popover-backdrop pattern (ModelPicker/AttachMenu/AccountPopover)
and the JWT-derived user identity convention."
```

---

### Task 14: Final pre-push gate + coverage check

- [ ] **Step 1: Run pre-push**

```bash
uv run inv pre-push
```

Expected: EXIT 0.

- [ ] **Step 2: Run coverage**

```bash
cd ui && npm run test:coverage 2>&1 | tail -10 && cd ..
```

Expected: 100% statements / branches / functions / lines.

- [ ] **Step 3: Local visual smoke test**

```bash
cd ui && npm run dev > /tmp/vite.log 2>&1 &
sleep 4
echo "=== / (marketing home) ==="
curl -s http://localhost:5173/ | grep -o "<title>[^<]*</title>" | head -1
echo "=== /app/login ==="
curl -s http://localhost:5173/app/login | grep -o "<title>[^<]*</title>" | head -1
kill %1 2>/dev/null
cd ..
```

Both return `<title>Channel</title>` (SPA shell — full route render happens client-side; visual smoke happens in the browser after merge).

---

### Task 15: Push branch + open PR + final reviewer

- [ ] **Step 1: Push branch with explicit refspec (W3)**

```bash
git push -u origin feat/channel-mvp-phase-6c-app-shell:feat/channel-mvp-phase-6c-app-shell
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base development \
  --title "feat(channel-mvp): Phase 6c — app shell + Login + ChatHome" \
  --body "$(cat <<'EOF'
Phase 6c of the channel MVP per docs/superpowers/specs/2026-05-29-channel-mvp-design.md and docs/superpowers/plans/2026-05-30-channel-mvp-phase-6c-app-shell.md.

Replaces the Phase 6a placeholder \`AppLogin\` and \`AppHome\` stubs with real components. The other app-route placeholders (\`/app/c/:id\`, \`/app/projects\`, \`/app/projects/:id\`, \`/app/artifacts\`, \`/app/customize\`) remain — those become Shell-wrapped real components in Phase 6d/6e/6f.

## What ships

### New chat-app components (\`ui/src/app/\`)
- \`Login.jsx\` — centered Google sign-in; clicking 'Continue with Google' redirects to the existing \`/auth/login\` backend
- \`Shell.jsx\` — Sidebar + \`<main>\` layout wrapper for every authed route
- \`Sidebar.jsx\` — 264px nav: New chat / Projects / Artifacts / Customize + time-grouped Recents + account row; Sign out clears the mgmt JWT and returns to \`/\`
- \`AccountPopover.jsx\` — email + Sign out
- \`Composer.jsx\` — auto-growing textarea + attach + model picker + mic + send; \`onSend\` no-ops at 6c (mock streamer arrives in 6d)
- \`ModelPicker.jsx\` — popover (models + 4-segment effort)
- \`AttachMenu.jsx\` — + button popover (three sample attachments)
- \`ChatHome.jsx\` — greeting + composer + quick-action chips
- \`GoogleG.jsx\` — 4-colour Google brand mark (only the Login button uses it)
- \`data.js\` — mock data: MODELS, EFFORTS, RECENTS, QUICK_ACTIONS

### Per spec deferrals (deliberately NOT shipped)
- Desktop traffic-lights frame (web only)
- 'Relaunch to update v2.4.1' pill (Electron only)
- 'Channel Max' plan label (billing deferred)
- Real Bedrock model invocation (mock streamer in 6d, real Bedrock later)

### Updated
- \`ui/src/styles/app.css\` — verbatim port from design-sources/app/app.css (527 lines)
- \`ui/src/main.jsx\` — imports channel.css + site.css + app.css
- \`ui/src/App.jsx\` — real Login + Shell-wrapped ChatHome at \`/app/login\` and \`/app\`
- \`ui/src/App.test.jsx\` — assertions updated to query real h1 text
- \`ui/src/components/Icon.jsx\` — backfilled any new icon cases needed (check, file, image, database, doc, close) with matching test entries
- \`CLAUDE.md\` — adds app.css + app/ subtree + two new UI-conventions bullets

## Verification

- \`uv run inv pre-push\` — EXIT 0
- \`npm run test:coverage\` — 100% across statements / branches / functions / lines

## Test plan

- [x] All 7 required CI contexts green
- [x] Coverage stays 100%
- [ ] Auto-merge fires on green CI
- [ ] Post-merge dev deploy lands cleanly
- [ ] Visual: open https://channel-dev.warlordofmars.net/app/login in a fresh-incognito browser and confirm the Google sign-in card renders per design; sign in; confirm Sidebar + ChatHome render per design

Closes: no tracking issue (Phase 6c shipping plan).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Enable auto-merge**

```bash
gh pr merge --auto --squash --delete-branch
```

- [ ] **Step 4: Watch CI**

```bash
gh pr checks
```

Wait for green. Auto-merge fires.

- [ ] **Step 5: After merge — post-deploy validation**

```bash
sleep 360
curl -sS "https://channel-dev.warlordofmars.net/app/login" | grep -o "<title>[^<]*</title>" | head -1
# Real visual check is in the browser.
```

---

## Self-review

### Spec coverage

| Spec requirement | Plan task |
|---|---|
| `app.css` port | Task 1 |
| `data.js` mock data (Phase 6c subset) | Task 2 |
| `Login.jsx` (centered Google sign-in) | Tasks 3, 4 (GoogleG + Login) |
| `ModelPicker.jsx` | Task 5 |
| `AttachMenu.jsx` | Task 6 |
| `Composer.jsx` | Task 7 |
| `AccountPopover.jsx` | Task 8 |
| `Sidebar.jsx` | Task 9 |
| `Shell.jsx` | Task 10 |
| `ChatHome.jsx` | Task 11 |
| Wire into App.jsx | Task 12 |
| 100% coverage | Task 14 |

All 11 spec deliverables covered.

### Placeholder scan

- Task 5/6/7 cite the conditional "if MISSING add the case" pattern for Icon backfills. This is a concrete decision rule (check, then add or skip) rather than a placeholder.
- No "TBD" / "implement later" / "similar to Task N" anywhere.

### Type consistency

- `MODELS`, `EFFORTS`, `RECENTS`, `QUICK_ACTIONS` exported from `data.js` (Task 2) and consumed by Tasks 5 (ModelPicker), 6 (AttachMenu doesn't use), 9 (Sidebar — RECENTS), 11 (ChatHome — MODELS, QUICK_ACTIONS).
- `TOKEN_KEY` and `parseToken` imported from `lib/auth.js` (Phase 6a) and used by Sidebar (Task 9) and ChatHome (Task 11).
- Composer's props (`model`, `effort`, `setModel`, `setEffort`, `onSend`, `autofocus`, `placeholder`) (Task 7) match what ChatHome passes (Task 11): model object from `MODELS.find()`, setModel wrapped to pass through the id to `prefs.setModel`.
- ModelPicker's props (`model`, `effort`, `onModel`, `onEffort`) (Task 5) match what Composer passes (Task 7): the `setModel` → `onModel` rename and `setEffort` → `onEffort` rename are consistent with the source.
- AccountPopover's props (`userName`, `email`, `onSignOut`) (Task 8) match Sidebar's invocation (Task 9).
- `Sign out` flow: `localStorage.removeItem(TOKEN_KEY)` then `globalThis.location.assign("/")` — consistent across Sidebar (Task 9) and its tests.
- Test test-ids and accessible-name queries are stable: `Title="Add attachment"`, `Title="Dictate"`, `Title="Send"`, `Title="Search"`, `Title="Toggle sidebar"`, `aria-label="Toggle theme"` (marketing only).

No mismatches.
