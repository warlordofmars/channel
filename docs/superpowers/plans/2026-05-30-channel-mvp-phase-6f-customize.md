# Channel MVP Phase 6f — Customize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `/app/customize` panel — the final view of the visual MVP. Five visual controls (theme, accent, density, default model, default effort) wired to the existing `useChannelPrefs`, plus three UI-only behavior toggles.

**Architecture:** One component (`Customize.jsx`) translated from the design source's `SettingsView` plus a small inlined `BehaviorRow` for the three no-op toggles. The `ACCENTS` array (5 hue swatches) lives at the top of `Customize.jsx` because no other view consumes it. `App.jsx` swaps the last `AppCustomize` placeholder for the real component inside `<Shell>`.

**Tech Stack:** React 18 + react-router 6 + vitest. No new deps. Every CSS class used (`.set-group`, `.set-row`, `.lbl`, `.hint`, `.ctl`, `.seg-ctl`, `.swatches`, `.swatch`, `.toggle`, `.knob`, `.view`, `.view-inner`, `.view-head`) is already in `app.css` from the Phase 6c verbatim port — no CSS changes.

**Scope note (do not second-guess):** The spec mentions "all six pref controls" in passing, but the design source's `SettingsView` ships **5** visual controls + **3** behavior toggles. The `shape` and `font` prefs in `useChannelPrefs` keep working (SiteLayout / Shell / Login already apply `data-shape` / `data-font` from the persisted values) — they just don't have a UI control in this phase. Match the design source. No shape/font controls.

---

## File structure

| File | Responsibility |
| --- | --- |
| `ui/src/app/views/Customize.jsx` (new) | The `/app/customize` view. Translates `SettingsView` (design-sources/app/views.jsx lines 75-168). Five visual controls wired to `useChannelPrefs`; three behavior toggles via a private `BehaviorRow` helper. `ACCENTS` constant lives at module scope. |
| `ui/src/App.jsx` (modify) | Drop the `AppCustomize` placeholder + import the real `Customize` view. Wrap the route in `<Shell>` like the other authed routes. |
| `CLAUDE.md` (modify) | File-tree refresh — add `Customize.jsx` to the `views/` block. |

**Scope-boundary list** (for the PR body once we open it):

- `ui/src/app/views/Customize.jsx`
- `ui/src/App.jsx`
- `CLAUDE.md`

Co-located `*.test.jsx` files are implicitly accepted by the agent-safe rules per CLAUDE.md §Special labels.

---

## Execution preconditions

- Branch off `origin/development`:
  ```bash
  git fetch origin
  git checkout -b feat/channel-mvp-phase-6f-customize origin/development
  ```
- Verify the gate is green: `uv run inv pre-push`.
- Verify the views/ directory contains the Phase 6e components but NOT `Customize.jsx`:
  ```bash
  ls ui/src/app/views/
  ```
  Expected: `Projects.jsx`, `ProjectDetail.jsx`, `Artifacts.jsx`, `ArtifactPanel.jsx`, `artifactHelpers.js` (+ their test files). No `Customize.jsx`.

---

## Task 1 — `Customize.jsx` (the view + BehaviorRow + tests)

**Files:**
- Create: `ui/src/app/views/Customize.jsx`
- Create: `ui/src/app/views/Customize.test.jsx`

The component reads from `useChannelPrefs` and calls its `setTheme` / `setAccent` / `setDensity` / `setModel` / `setEffort` setters when the user interacts. The model setter receives a model `id` string (not the full object), so when the user clicks the Sonnet button we call `prefs.setModel("claude-sonnet-4-6")`. The accent setter receives a number (the hue). `BehaviorRow` is a small private component with internal `useState` — Phase 6f is UI-only, so flipping these toggles doesn't propagate anywhere.

- [ ] **Step 1: Write the failing test**

Create `ui/src/app/views/Customize.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Customize from "./Customize.jsx";
import { MODELS, EFFORTS } from "../data.js";
import { __resetChannelPrefsForTest } from "../../hooks/useChannelPrefs.js";

function renderCustomize() {
  return render(
    <MemoryRouter initialEntries={["/app/customize"]}>
      <Customize />
    </MemoryRouter>
  );
}

describe("Customize", () => {
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
    // Reset attrs the hook sets on <html>.
    for (const a of Array.from(document.documentElement.attributes)) {
      if (a.name.startsWith("data-")) document.documentElement.removeAttribute(a.name);
    }
    __resetChannelPrefsForTest();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the 'Customize' header + tagline", () => {
    renderCustomize();
    expect(screen.getByRole("heading", { level: 2, name: "Customize" })).toBeTruthy();
    expect(screen.getByText(/Tune Channel's appearance/i)).toBeTruthy();
  });

  it("renders three section headers: Appearance / Defaults / Behavior", () => {
    renderCustomize();
    expect(screen.getByRole("heading", { level: 3, name: "Appearance" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Defaults" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Behavior" })).toBeTruthy();
  });

  it("Theme seg-ctl marks the persisted theme as .on", () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    renderCustomize();
    expect(screen.getByRole("button", { name: /Light/ }).className).toContain("on");
    expect(screen.getByRole("button", { name: /^Dark$/ }).className).not.toContain("on");
  });

  it("clicking the Dark button persists theme=dark and updates the .on class", () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    renderCustomize();
    fireEvent.click(screen.getByRole("button", { name: /^Dark$/ }));
    expect(storage["channel-theme"]).toBe("dark");
    expect(screen.getByRole("button", { name: /^Dark$/ }).className).toContain("on");
  });

  it("renders 5 accent swatches with the correct OKLCH backgrounds", () => {
    const { container } = renderCustomize();
    const swatches = container.querySelectorAll(".swatch");
    expect(swatches.length).toBe(5);
    // First swatch = hue 42 (Clay): background = oklch(0.60 0.13 42).
    expect(swatches[0].style.background).toContain("oklch(0.60 0.13 42)");
    // The title attribute carries the human label.
    expect(swatches[0].getAttribute("title")).toBe("Clay");
  });

  it("clicking an accent swatch persists the hue as the new accent", () => {
    renderCustomize();
    const fern = document.body.querySelector("[title='Fern']"); // hue 150
    expect(fern).toBeTruthy();
    fireEvent.click(fern);
    expect(storage["channel-accent"]).toBe("150");
    // The clicked swatch now has the .on class.
    expect(fern.className).toContain("on");
  });

  it("Density seg-ctl persists the chosen value", () => {
    renderCustomize();
    // Default is "cozy".
    expect(screen.getByRole("button", { name: "Cozy" }).className).toContain("on");
    fireEvent.click(screen.getByRole("button", { name: "Compact" }));
    expect(storage["channel-density"]).toBe("compact");
    expect(screen.getByRole("button", { name: "Compact" }).className).toContain("on");
  });

  it("Default model seg-ctl renders one button per MODELS entry + persists the chosen id", () => {
    renderCustomize();
    for (const m of MODELS) {
      expect(screen.getByRole("button", { name: m.short })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: "Haiku 4.5" }));
    expect(storage["channel-model"]).toBe("claude-haiku-4-5");
    expect(screen.getByRole("button", { name: "Haiku 4.5" }).className).toContain("on");
  });

  it("Reasoning effort seg-ctl renders one button per EFFORTS entry + persists the choice", () => {
    renderCustomize();
    for (const e of EFFORTS) {
      expect(screen.getByRole("button", { name: e })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: "Max" }));
    expect(storage["channel-effort"]).toBe("Max");
    expect(screen.getByRole("button", { name: "Max" }).className).toContain("on");
  });

  it("Default-model hint text follows the active model's desc string", () => {
    storage["channel-model"] = "claude-haiku-4-5";
    __resetChannelPrefsForTest();
    renderCustomize();
    const haiku = MODELS.find((m) => m.id === "claude-haiku-4-5");
    // Hint paragraph for the model row carries the active model's desc.
    expect(screen.getByText(haiku.desc)).toBeTruthy();
  });

  it("renders three Behavior toggles with the correct default states", () => {
    const { container } = renderCustomize();
    const toggles = container.querySelectorAll(".toggle");
    expect(toggles.length).toBe(3);
    // Send on Enter (true), Show reasoning trace (false), Suggest follow-ups (true).
    expect(toggles[0].className).toContain("on");
    expect(toggles[1].className).not.toContain("on");
    expect(toggles[2].className).toContain("on");
  });

  it("clicking a Behavior toggle flips its .on class (UI-only — no localStorage write)", () => {
    const { container } = renderCustomize();
    const toggles = container.querySelectorAll(".toggle");
    fireEvent.click(toggles[1]); // Show reasoning trace — starts off
    expect(toggles[1].className).toContain("on");
    // No localStorage key written — these are UI-only at Phase 6f.
    expect(Object.keys(storage).filter((k) => k.startsWith("channel-behavior"))).toEqual([]);
    fireEvent.click(toggles[1]);
    expect(toggles[1].className).not.toContain("on");
  });

  it("Behavior toggles render their label + hint text", () => {
    renderCustomize();
    expect(screen.getByText("Send on Enter")).toBeTruthy();
    expect(screen.getByText(/Press Enter to send/)).toBeTruthy();
    expect(screen.getByText("Show reasoning trace")).toBeTruthy();
    expect(screen.getByText(/model's thinking/)).toBeTruthy();
    expect(screen.getByText("Suggest follow-ups")).toBeTruthy();
    expect(screen.getByText(/related prompts/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/app/views/Customize.test.jsx
```

Expected: every test fails — `Customize.jsx` doesn't exist.

- [ ] **Step 3: Implement `ui/src/app/views/Customize.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import Icon from "../../components/Icon.jsx";
import { useChannelPrefs } from "../../hooks/useChannelPrefs.js";
import { EFFORTS, MODELS } from "../data.js";

// Five accent hues. Co-located here because nothing else in the app reads
// them; the spec mock-data list doesn't include ACCENTS. Lifted verbatim
// from design-sources/app/views.jsx.
const ACCENTS = [
  { h: 42, label: "Clay" },
  { h: 18, label: "Rust" },
  { h: 150, label: "Fern" },
  { h: 235, label: "Slate" },
  { h: 300, label: "Plum" },
];

const BEHAVIOR_ROWS = [
  ["Send on Enter", "Press Enter to send, Shift+Enter for a new line.", true],
  ["Show reasoning trace", "Display the model's thinking before each reply.", false],
  ["Suggest follow-ups", "Offer related prompts after responses.", true],
];

/**
 * `/app/customize` view. Translated from design-sources/app/views.jsx
 * `SettingsView`. Five visual controls wired to useChannelPrefs (theme,
 * accent, density, default model, default effort) — each setter
 * immediately persists to localStorage via the hook's shared store, so
 * changes apply instantly across every mounted consumer.
 *
 * Three Behavior toggles are UI-only at Phase 6f per the spec — they flip
 * a local useState but don't propagate anywhere.
 */
export default function Customize() {
  const prefs = useChannelPrefs();
  // The hook stores `model` as an id string; the seg-ctl needs the model
  // object for its `desc` hint and `short` label.
  const activeModel = MODELS.find((m) => m.id === prefs.model) ?? MODELS[0];

  return (
    <div className="view">
      <div className="view-inner" style={{ maxWidth: 720 }}>
        <div className="view-head">
          <h2>Customize</h2>
          <p>Tune Channel's appearance and defaults. Changes apply instantly.</p>
        </div>

        <div className="set-group">
          <h3>Appearance</h3>
          <div className="set-row">
            <div>
              <div className="lbl">Theme</div>
              <div className="hint">Switch between light and dark surfaces.</div>
            </div>
            <div className="ctl">
              <div className="seg-ctl">
                <button
                  type="button"
                  className={prefs.theme === "light" ? "on" : ""}
                  onClick={() => prefs.setTheme("light")}
                >
                  <Icon name="sun" size={15} /> Light
                </button>
                <button
                  type="button"
                  className={prefs.theme === "dark" ? "on" : ""}
                  onClick={() => prefs.setTheme("dark")}
                >
                  <Icon name="moon" size={15} /> Dark
                </button>
              </div>
            </div>
          </div>
          <div className="set-row">
            <div>
              <div className="lbl">Accent color</div>
              <div className="hint">Used for highlights, actions, and the mark.</div>
            </div>
            <div className="ctl">
              <div className="swatches">
                {ACCENTS.map((a) => (
                  <button
                    type="button"
                    key={a.h}
                    className={"swatch" + (Number(prefs.accent) === a.h ? " on" : "")}
                    style={{ background: `oklch(0.60 0.13 ${a.h})` }}
                    title={a.label}
                    onClick={() => prefs.setAccent(String(a.h))}
                  />
                ))}
              </div>
            </div>
          </div>
          <div className="set-row">
            <div>
              <div className="lbl">Density</div>
              <div className="hint">Comfortable spacing or a tighter layout.</div>
            </div>
            <div className="ctl">
              <div className="seg-ctl">
                <button
                  type="button"
                  className={prefs.density === "cozy" ? "on" : ""}
                  onClick={() => prefs.setDensity("cozy")}
                >
                  Cozy
                </button>
                <button
                  type="button"
                  className={prefs.density === "compact" ? "on" : ""}
                  onClick={() => prefs.setDensity("compact")}
                >
                  Compact
                </button>
              </div>
            </div>
          </div>
        </div>

        <div className="set-group">
          <h3>Defaults</h3>
          <div className="set-row">
            <div>
              <div className="lbl">Default model</div>
              <div className="hint">{activeModel.desc}</div>
            </div>
            <div className="ctl">
              <div className="seg-ctl">
                {MODELS.map((m) => (
                  <button
                    type="button"
                    key={m.id}
                    className={m.id === activeModel.id ? "on" : ""}
                    onClick={() => prefs.setModel(m.id)}
                  >
                    {m.short || m.name}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="set-row">
            <div>
              <div className="lbl">Reasoning effort</div>
              <div className="hint">Higher effort thinks longer before answering.</div>
            </div>
            <div className="ctl">
              <div className="seg-ctl">
                {EFFORTS.map((e) => (
                  <button
                    type="button"
                    key={e}
                    className={e === prefs.effort ? "on" : ""}
                    onClick={() => prefs.setEffort(e)}
                  >
                    {e}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="set-group">
          <h3>Behavior</h3>
          {BEHAVIOR_ROWS.map(([lbl, hint, def]) => (
            <BehaviorRow key={lbl} lbl={lbl} hint={hint} def={def} />
          ))}
        </div>
      </div>
    </div>
  );
}

function BehaviorRow({ lbl, hint, def }) {
  const [on, setOn] = useState(def);
  return (
    <div className="set-row">
      <div>
        <div className="lbl">{lbl}</div>
        <div className="hint">{hint}</div>
      </div>
      <div className="ctl">
        <button
          type="button"
          className={"toggle" + (on ? " on" : "")}
          onClick={() => setOn((o) => !o)}
        >
          <span className="knob" />
        </button>
      </div>
    </div>
  );
}
```

**Note on a subtle wrinkle:** `useChannelPrefs` stores everything as strings (it reads from localStorage). The `accent` swatch comparison `Number(prefs.accent) === a.h` coerces both sides to numbers — `a.h` is a number literal from the ACCENTS const, but `prefs.accent` comes back as a string like `"42"`. The setter writes back the string form (`String(a.h)`) for consistency with how `DEFAULTS.accent = "42"` is stored.

- [ ] **Step 4: Run the tests**

```bash
cd ui && npx vitest run src/app/views/Customize.test.jsx
```

Expected: 13/13 PASS.

- [ ] **Step 5: Verify per-file coverage**

```bash
cd ui && npx vitest run src/app/views/Customize.test.jsx --coverage
```

Expected: `Customize.jsx` at 100% across statements/branches/functions/lines. If anything's uncovered, report — don't add tests beyond the plan.

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/views/Customize.jsx ui/src/app/views/Customize.test.jsx
git commit -m "feat(channel-mvp): Phase 6f — Customize view (5 visual controls + 3 behavior toggles)"
```

---

## Task 2 — Wire `Customize` into App.jsx

**Files:**
- Modify: `ui/src/App.jsx`
- Modify: `ui/src/App.test.jsx`

- [ ] **Step 1: Update App.test.jsx first**

Open `ui/src/App.test.jsx`. Find the `it.each([...])` authed-route table — there's a single remaining placeholder row:

```jsx
    ["/app/customize",      "app-customize"],
```

DELETE that line. Add a new dedicated test below the existing `/app/artifacts` real-component test (which Phase 6e shipped):

```jsx
  it("/app/customize renders the Customize view (not the placeholder)", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app/customize");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 2, name: "Customize" })).toBeTruthy();
    expect(screen.queryByTestId("app-customize")).toBeNull();
  });
```

- [ ] **Step 2: Run the failing test**

```bash
cd ui && npx vitest run src/App.test.jsx -t "Customize view"
```

Expected: FAIL — App.jsx still renders the placeholder.

- [ ] **Step 3: Update App.jsx**

Add the import next to the existing `./app/views/...` imports (alphabetical with the other view imports):

```jsx
import Customize from "./app/views/Customize.jsx";
```

Find this placeholder line near the top of the file (in the `ph(...)` block):

```jsx
const AppCustomize     = () => ph("app-customize", "App: Customize");
```

DELETE it. The `ph` helper itself can stay if there are still placeholder users; if there are not, the linter will flag it as unused — handle as Step 4 if so.

Find the route declaration:

```jsx
          <Route path="/app/customize"      element={<AuthGate><AppCustomize /></AuthGate>} />
```

Replace with:

```jsx
          <Route path="/app/customize"      element={<AuthGate><Shell><Customize /></Shell></AuthGate>} />
```

- [ ] **Step 4: Check whether `ph` is now unused**

Run:

```bash
grep -n "\\bph\\b" ui/src/App.jsx
```

If the only remaining hit is the `const ph = ...` declaration itself, delete that declaration too. If there are other consumers (there shouldn't be — every authed placeholder route is now a real component), leave it alone.

- [ ] **Step 5: Run the tests**

```bash
cd ui && npx vitest run src/App.test.jsx
```

Expected: ALL pass.

- [ ] **Step 6: Verify per-file coverage**

```bash
cd ui && npx vitest run src/App.test.jsx --coverage
```

Expected: 100% on `App.jsx`.

- [ ] **Step 7: Commit**

```bash
git add ui/src/App.jsx ui/src/App.test.jsx
git commit -m "feat(channel-mvp): Phase 6f — wire /app/customize route to Customize view in Shell"
```

---

## Task 3 — Update CLAUDE.md file tree

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Edit the `views/` block**

Open `CLAUDE.md`. Use `grep -n "ArtifactPanel.jsx" CLAUDE.md` to find the views block. The current state (post Phase 6e) looks like:

```
│   │       └── views/                                        # /app/projects, /app/projects/:id, /app/artifacts
│   │           ├── Projects.jsx                              # Grid of project cards + 'New project' tile
│   │           ├── ProjectDetail.jsx                         # Project header + Composer + docs + chats list
│   │           ├── Artifacts.jsx                             # List of artifact rows + bookmarkable panel
│   │           ├── ArtifactPanel.jsx                         # Slide-in viewer with 5 renderers (Code/Chart/Data/Interactive/Document)
│   │           └── artifactHelpers.js                        # colorFor / inkFor / artIcon utilities
```

Replace with (note the `└──` on artifactHelpers becomes `├──`; the new Customize entry slots above it alphabetically; and the route list in the parent comment grows):

```
│   │       └── views/                                        # /app/projects, /app/projects/:id, /app/artifacts, /app/customize
│   │           ├── Artifacts.jsx                             # List of artifact rows + bookmarkable panel
│   │           ├── ArtifactPanel.jsx                         # Slide-in viewer with 5 renderers (Code/Chart/Data/Interactive/Document)
│   │           ├── artifactHelpers.js                        # colorFor / inkFor / artIcon utilities
│   │           ├── Customize.jsx                             # 5 visual prefs (theme/accent/density/model/effort) + 3 behavior toggles
│   │           ├── ProjectDetail.jsx                         # Project header + Composer + docs + chats list
│   │           └── Projects.jsx                              # Grid of project cards + 'New project' tile
```

(The block above is also lightly re-sorted into alphabetical order. If that re-ordering makes the diff noisier than the reviewer wants, the alternative is to keep the prior order and just slot Customize.jsx in front of `└── artifactHelpers.js` — pick whichever produces the cleaner-looking patch.)

- [ ] **Step 2: Verify the diff is minimal**

```bash
git diff CLAUDE.md
```

Expected: only the views block changed.

- [ ] **Step 3: Run the copyright-header linter**

```bash
uv run python scripts/check_copyright.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): list Phase 6f Customize.jsx in the views/ block"
```

---

## Task 4 — Final gate, smoke check, PR

This is the controller's task (the human or the parent agent) — runs `inv pre-push`, runs the dev server, verifies the controls behave, opens the PR. No code change.

- [ ] **Step 1: Run the full gate**

```bash
uv run inv pre-push
```

Expected: all stages pass.

- [ ] **Step 2: Verify global 100% coverage**

```bash
cd ui && npx vitest run src/ --coverage
```

Expected: 100% across statements/branches/functions/lines.

- [ ] **Step 3: Local smoke check (5 minutes)**

```bash
uv run inv dev --seed
```

Visit `http://localhost:5173/auth/login?test_email=you@example.com` to authenticate via bypass, then click **Customize** in the sidebar. Verify each of these in turn:

1. The page renders three sections: **Appearance**, **Defaults**, **Behavior**.
2. **Theme**: clicking Light immediately switches the page to light theme. Clicking Dark flips it back. Refreshing the page preserves the choice.
3. **Accent color**: clicking each of the 5 swatches changes the accent hue everywhere it's used (sidebar selected state, the marketing CTA button if you tab to `/`, the brand mark). Refresh preserves.
4. **Density**: clicking Compact tightens the sidebar / row heights. Cozy restores.
5. **Default model**: the seg-ctl shows 3 models with the current default highlighted. The hint text below the label changes to reflect the active model's `desc` when you click between them. Refresh preserves.
6. **Reasoning effort**: 4 buttons (Low / Medium / High / Max). Click each. Refresh preserves.
7. **Behavior toggles**: 3 toggles. Send on Enter is on by default; Show reasoning trace off; Suggest follow-ups on. Clicking each flips the knob but **does not** affect any other UI — refresh resets them (these are UI-only at Phase 6f).
8. Returning to **New chat** at `/app`: the chat-home greeting + composer still render, the Composer's model picker reflects the new default, and the Composer's effort label reflects the new default.

If any step fails, STOP and fix before opening the PR.

- [ ] **Step 4: Push + open the PR (per CLAUDE.md `Opening a PR`)**

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD   # 4 commits expected (1 plan + 3 implementation)
git push -u origin feat/channel-mvp-phase-6f-customize:feat/channel-mvp-phase-6f-customize
```

Open the PR with `gh pr create --base development …` — title `feat(channel-mvp): Phase 6f — Customize`, body referencing the spec, scope-boundary list from the §"File structure" section above, and a test plan covering the 8 smoke steps. Mention in the summary that **this is the final Phase 6 PR** of the visual MVP.

---

## Self-review against the spec

**Spec coverage:**
- `views/Customize.jsx` with all six pref controls → Task 1 ✓ (with the documented scope-note: the spec says "six" but the design source has 5 visual + 3 behavior; we match the design source exactly. Shape and font prefs still work via the hook but have no UI control.)
- Behavior toggles render but no-op → Task 1 ✓ (`BehaviorRow` uses internal `useState` only; no localStorage write — covered by an explicit test)
- App.jsx wires `/app/customize` to the real component → Task 2 ✓
- CLAUDE.md file-tree refresh → Task 3 ✓

**Placeholder scan:** no `TBD`, `TODO`, `implement later`, or `Similar to Task N` strings. Every code-changing step shows the actual code.

**Type consistency:**
- `useChannelPrefs` exposes `{ theme, setTheme, accent, setAccent, density, setDensity, model, setModel, effort, setEffort, ... }` — all consumed identically in Task 1.
- The hook stores everything as a string. `prefs.accent` is `"42"`, so the swatch comparison coerces via `Number(prefs.accent) === a.h` (where `a.h` is a number literal). The setter writes `String(a.h)` to keep storage canonical. This is consistent across all 5 swatches.
- The hook's `model` field is the id string (e.g. `"claude-opus-4-8"`); `MODELS.find((m) => m.id === prefs.model)` reconstitutes the full object for the `.desc` hint and the `.short` button label. The Phase 6e `ProjectDetail` uses the exact same resolution pattern.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-30-channel-mvp-phase-6f-customize.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task with two-stage review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
