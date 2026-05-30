# Channel MVP Phase 6e — Projects + Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three `/app/projects`, `/app/projects/:id`, `/app/artifacts` placeholder routes with the real Projects list, Project Detail, Artifacts list, and the bookmarkable Artifact Panel overlay — all backed by static mock data per the prototype.

**Architecture:** Five small files under `ui/src/app/views/` (Projects, ProjectDetail, Artifacts, ArtifactPanel, plus an `artifactHelpers.js` for the shared `colorFor`/`inkFor`/`artIcon` utilities). Mock data (`PROJECTS`, `ARTIFACTS`, `PROJECT_DOCS`) is appended to the existing `ui/src/app/data.js`. The Artifact Panel renders inside the Artifacts route based on a `?artifact=:id` search param so refreshing keeps the panel open. App.jsx swaps three placeholders for real components.

**Tech Stack:** React 18 + react-router 6 (`useParams`, `useNavigate`, `useSearchParams`), vitest + `@testing-library/react`. No new deps. All CSS classes already exist in `app.css` from the Phase 6c verbatim port.

---

## File structure

| File | Responsibility |
| --- | --- |
| `ui/src/app/data.js` (modify) | Append `PROJECTS`, `ARTIFACTS`, `PROJECT_DOCS` arrays. |
| `ui/src/app/views/artifactHelpers.js` (new) | Three pure helpers — `colorFor(h)`, `inkFor(h)`, `artIcon(kind)`. Used by Projects, ProjectDetail, Artifacts, and ArtifactPanel. |
| `ui/src/app/views/Projects.jsx` (new) | The `/app/projects` view: header + grid of project cards + "New project" tile. Card click → `navigate(\`/app/projects/${p.id}\`)`. "New project" tile is a no-op at this phase. |
| `ui/src/app/views/ProjectDetail.jsx` (new) | The `/app/projects/:id` view: back button + project header + Composer (stashes to sessionStorage + navigates to `/app/c/new`, just like ChatHome) + "Project knowledge" doc chip row + "Chats in this project" list. Unknown id → empty state. |
| `ui/src/app/views/Artifacts.jsx` (new) | The `/app/artifacts` view: header + list-row per artifact + the ArtifactPanel overlay (gated on `?artifact=:id`). Click a row → set `?artifact=<id>`. |
| `ui/src/app/views/ArtifactPanel.jsx` (new) | Panel overlay (backdrop + sliding panel) + Code/Chart/Data/Interactive/Document renderers via a private `renderArtifactBody(artifact)` switch. Copy/Download/Close buttons (Copy + Download are no-op at Phase 6e; Close clears the search param). |
| `ui/src/App.jsx` (modify) | Drop three placeholders; wire the three real views inside `<AuthGate><Shell>...</Shell></AuthGate>`. |
| `CLAUDE.md` (modify) | File-tree refresh listing the new `views/` directory, the helpers file, and the expanded data.js exports. |

**Scope-boundary list** (for the PR body once we open it):

- `ui/src/app/data.js`
- `ui/src/app/views/artifactHelpers.js`
- `ui/src/app/views/Projects.jsx`
- `ui/src/app/views/ProjectDetail.jsx`
- `ui/src/app/views/Artifacts.jsx`
- `ui/src/app/views/ArtifactPanel.jsx`
- `ui/src/App.jsx`
- `CLAUDE.md`

(Co-located `*.test.{js,jsx}` files are implicitly accepted by the agent-safe rules per CLAUDE.md §Special labels — no need to enumerate.)

---

## Execution preconditions

- Branch off `origin/development`:
  ```bash
  git fetch origin
  git checkout -b feat/channel-mvp-phase-6e-projects-artifacts origin/development
  ```
- Verify the gate is green: `uv run inv pre-push` (should already pass).
- Verify the views/ directory doesn't exist yet: `ls ui/src/app/views/ 2>&1 | grep -q "No such" && echo OK`.

---

## Task 1 — Add `PROJECTS`, `ARTIFACTS`, `PROJECT_DOCS` to data.js

**Files:**
- Modify: `ui/src/app/data.js`
- Test: `ui/src/app/data.test.js` (existing — extend)

- [ ] **Step 1: Extend the failing test**

Open `ui/src/app/data.test.js`. Find the existing `describe("data mocks", ...)` block and add the imports of the new exports + two new test cases. The updated file should look exactly like this:

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import {
  MODELS,
  EFFORTS,
  RECENTS,
  QUICK_ACTIONS,
  SAMPLE_REPLY,
  SAMPLE_USER,
  PROJECTS,
  ARTIFACTS,
  PROJECT_DOCS,
} from "./data.js";

describe("data mocks", () => {
  it("re-exports the Phase 6c mock arrays unchanged", () => {
    expect(MODELS.length).toBeGreaterThanOrEqual(3);
    expect(EFFORTS).toEqual(["Low", "Medium", "High", "Max"]);
    expect(RECENTS.length).toBeGreaterThanOrEqual(15);
    expect(QUICK_ACTIONS.length).toBe(4);
  });

  it("exports SAMPLE_USER as the canned question that pairs with SAMPLE_REPLY", () => {
    expect(SAMPLE_USER).toMatch(/columnar store/i);
    expect(SAMPLE_USER).toMatch(/ingest/i);
  });

  it("exports SAMPLE_REPLY with markdown features the renderer must handle", () => {
    expect(SAMPLE_REPLY).toMatch(/\n\n/);
    expect(SAMPLE_REPLY).toMatch(/\*\*[^*]+\*\*/);
    expect(SAMPLE_REPLY).toMatch(/^\d+\.\s/m);
  });

  it("exports PROJECTS with the verbatim 6-entry mock array", () => {
    expect(PROJECTS).toHaveLength(6);
    // Spot-check a known entry — id, name, color all matter for routing + theming.
    expect(PROJECTS[0]).toMatchObject({
      id: "p1",
      name: "Analytics Rewrite",
      chats: 24,
      docs: 8,
      color: 42,
    });
    // Every entry needs the fields Projects.jsx + ProjectDetail.jsx read.
    for (const p of PROJECTS) {
      expect(typeof p.id).toBe("string");
      expect(typeof p.name).toBe("string");
      expect(typeof p.desc).toBe("string");
      expect(typeof p.chats).toBe("number");
      expect(typeof p.docs).toBe("number");
      expect(typeof p.color).toBe("number");
    }
  });

  it("exports ARTIFACTS with the verbatim 6-entry mock array", () => {
    expect(ARTIFACTS).toHaveLength(6);
    // Every renderer kind that ArtifactPanel switches on must be represented.
    const kinds = new Set(ARTIFACTS.map((a) => a.kind));
    expect(kinds).toEqual(new Set(["Document", "Code", "Interactive", "Chart", "Data"]));
    // project can be null (a3 has no project) — keep that intentional null.
    expect(ARTIFACTS.find((a) => a.id === "a3").project).toBeNull();
  });

  it("exports PROJECT_DOCS as a 4-string array used by ProjectDetail", () => {
    expect(PROJECT_DOCS).toEqual([
      "product-spec-v2.pdf",
      "events-schema.sql",
      "migration-notes.md",
      "q3-targets.csv",
    ]);
  });
});
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

```bash
cd ui && npx vitest run src/app/data.test.js
```

Expected: the 3 existing tests still pass; the 3 new ones fail because `PROJECTS`, `ARTIFACTS`, `PROJECT_DOCS` are undefined.

- [ ] **Step 3: Append to `ui/src/app/data.js`**

Open `ui/src/app/data.js` and append (do NOT touch existing exports!) these three new exports after `SAMPLE_USER`:

```js
// Mock projects — used by /app/projects (grid) and /app/projects/:id
// (detail page). Lifted verbatim from design-sources/app/data.jsx.
// `color` is an OKLCH hue (0-360) — Projects.jsx and ProjectDetail.jsx
// pipe it through artifactHelpers.colorFor()/inkFor() for the badge.
export const PROJECTS = [
  { id: "p1", name: "Analytics Rewrite",   desc: "Migrating the events pipeline to a columnar store.", chats: 24, docs: 8,  color: 42 },
  { id: "p2", name: "Field Guide",         desc: "Long-form writing project — Appalachian flora.",     chats: 11, docs: 31, color: 150 },
  { id: "p3", name: "Home Lab",            desc: "Self-hosted services, networking, automation notes.", chats: 38, docs: 5,  color: 250 },
  { id: "p4", name: "Q3 Planning",         desc: "Roadmap, OKRs, and the board narrative.",            chats: 9,  docs: 14, color: 12 },
  { id: "p5", name: "Recipe Development",  desc: "Bread, ferments, and the great pizza experiment.",   chats: 17, docs: 6,  color: 90 },
  { id: "p6", name: "Side Project: Tally", desc: "A tiny budgeting app. Specs, copy, and code.",        chats: 22, docs: 19, color: 310 },
];

// Mock artifacts — used by /app/artifacts (list) and the ArtifactPanel
// overlay. `kind` drives the renderer switch in ArtifactPanel.
// `project` is null when the artifact isn't scoped to one. Lifted
// verbatim from design-sources/app/data.jsx.
export const ARTIFACTS = [
  { id: "a1", title: "Pricing tier comparison", kind: "Document",    updated: "2h ago",     lines: "—",         project: "Q3 Planning" },
  { id: "a2", title: "rate-limiter.ts",         kind: "Code",        updated: "5h ago",     lines: "142 lines", project: "Analytics Rewrite" },
  { id: "a3", title: "Onboarding flow mockup",  kind: "Interactive", updated: "Yesterday",  lines: "React",     project: null },
  { id: "a4", title: "Trail elevation chart",   kind: "Chart",       updated: "Yesterday",  lines: "SVG",       project: "Field Guide" },
  { id: "a5", title: "Migration runbook",       kind: "Document",    updated: "3d ago",     lines: "—",         project: "Analytics Rewrite" },
  { id: "a6", title: "budget-summary.csv",      kind: "Data",        updated: "4d ago",     lines: "380 rows",  project: "Side Project: Tally" },
];

// Mock docs attached to every project (placeholder until project knowledge
// is real). Lifted from design-sources/app/views.jsx's PROJECT_DOCS const.
export const PROJECT_DOCS = [
  "product-spec-v2.pdf",
  "events-schema.sql",
  "migration-notes.md",
  "q3-targets.csv",
];
```

- [ ] **Step 4: Run the tests to confirm pass**

```bash
cd ui && npx vitest run src/app/data.test.js
```

Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/data.js ui/src/app/data.test.js
git commit -m "feat(channel-mvp): Phase 6e — add PROJECTS / ARTIFACTS / PROJECT_DOCS mock data"
```

---

## Task 2 — `artifactHelpers.js` (colorFor + inkFor + artIcon)

**Files:**
- Create: `ui/src/app/views/artifactHelpers.js`
- Test: `ui/src/app/views/artifactHelpers.test.js`

- [ ] **Step 1: Write the failing test**

Create `ui/src/app/views/artifactHelpers.test.js`:

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import { colorFor, inkFor, artIcon } from "./artifactHelpers.js";

describe("artifactHelpers", () => {
  it("colorFor returns an OKLCH light-band swatch for the given hue", () => {
    expect(colorFor(42)).toBe("oklch(0.92 0.05 42)");
    expect(colorFor(310)).toBe("oklch(0.92 0.05 310)");
  });

  it("inkFor returns a darker OKLCH ink colour for legible foreground text", () => {
    expect(inkFor(42)).toBe("oklch(0.45 0.12 42)");
    expect(inkFor(310)).toBe("oklch(0.45 0.12 310)");
  });

  it("artIcon maps each known artifact kind to the right Icon name", () => {
    expect(artIcon("Code")).toBe("code");
    expect(artIcon("Chart")).toBe("customize");
    expect(artIcon("Interactive")).toBe("sparkle");
    expect(artIcon("Data")).toBe("database");
  });

  it("artIcon falls back to 'doc' for the default (Document) and any unknown kind", () => {
    expect(artIcon("Document")).toBe("doc");
    expect(artIcon("Unknown")).toBe("doc");
    expect(artIcon(undefined)).toBe("doc");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/app/views/artifactHelpers.test.js
```

Expected: ALL fail (file doesn't exist).

- [ ] **Step 3: Implement `ui/src/app/views/artifactHelpers.js`**

```js
// Copyright (c) 2026 John Carter. All rights reserved.

/**
 * Shared utilities for the Phase 6e views. Ported verbatim from
 * design-sources/app/views.jsx (top of file): `colorFor`, `inkFor`, and
 * `artIcon`. Pure helpers — no React, no DOM — so they live in a `.js`
 * sibling instead of a `.jsx` component.
 */

/** OKLCH light-band swatch (project card badge background). */
export function colorFor(h) {
  return `oklch(0.92 0.05 ${h})`;
}

/** Darker OKLCH ink for legible text on a `colorFor(h)` background. */
export function inkFor(h) {
  return `oklch(0.45 0.12 ${h})`;
}

/** Map an artifact kind to the Icon-set name used as its badge glyph. */
export function artIcon(kind) {
  return kind === "Code" ? "code"
       : kind === "Chart" ? "customize"
       : kind === "Interactive" ? "sparkle"
       : kind === "Data" ? "database"
       : "doc";
}
```

- [ ] **Step 4: Run the tests**

```bash
cd ui && npx vitest run src/app/views/artifactHelpers.test.js
```

Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/views/artifactHelpers.js ui/src/app/views/artifactHelpers.test.js
git commit -m "feat(channel-mvp): Phase 6e — artifactHelpers (colorFor / inkFor / artIcon)"
```

---

## Task 3 — `Projects.jsx` (grid of project cards)

**Files:**
- Create: `ui/src/app/views/Projects.jsx`
- Create: `ui/src/app/views/Projects.test.jsx`

- [ ] **Step 1: Write the failing test**

Create `ui/src/app/views/Projects.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import Projects from "./Projects.jsx";
import { PROJECTS } from "../data.js";

function renderAtProjects() {
  let lastPath = null;
  function PathCatcher() {
    const { pathname } = useLocation();
    lastPath = pathname;
    return null;
  }
  const result = render(
    <MemoryRouter initialEntries={["/app/projects"]}>
      <Routes>
        <Route path="/app/projects" element={<><Projects /><PathCatcher /></>} />
        <Route path="/app/projects/:id" element={<PathCatcher />} />
      </Routes>
    </MemoryRouter>
  );
  return { ...result, getLastPath: () => lastPath };
}

describe("Projects", () => {
  it("renders the 'Projects' header + tagline", () => {
    renderAtProjects();
    expect(screen.getByRole("heading", { level: 2, name: "Projects" })).toBeTruthy();
    expect(screen.getByText(/Group related chats/i)).toBeTruthy();
  });

  it("renders one card per PROJECTS entry plus a 'New project' tile", () => {
    const { container } = renderAtProjects();
    const cards = container.querySelectorAll(".card");
    // PROJECTS.length tiles + 1 "new" tile.
    expect(cards.length).toBe(PROJECTS.length + 1);
    expect(screen.getByText(/New project/i)).toBeTruthy();
    // Each project name is present in the tree.
    for (const p of PROJECTS) {
      expect(screen.getByText(p.name)).toBeTruthy();
    }
  });

  it("clicking a project card navigates to /app/projects/<id>", () => {
    const { getLastPath } = renderAtProjects();
    fireEvent.click(screen.getByText("Analytics Rewrite"));
    expect(getLastPath()).toBe("/app/projects/p1");
  });

  it("clicking the 'New project' tile does not throw (no-op at Phase 6e)", () => {
    renderAtProjects();
    expect(() => fireEvent.click(screen.getByText(/New project/i))).not.toThrow();
  });

  it("each project card's badge is themed with the project's color hue", () => {
    const { container } = renderAtProjects();
    // The first PROJECTS entry (Analytics Rewrite) has color 42 → background = oklch(0.92 0.05 42).
    // The first .card.badge with that exact OKLCH proves the theming wiring.
    const badges = Array.from(container.querySelectorAll(".card .badge"));
    expect(badges.length).toBe(PROJECTS.length);
    expect(badges[0].style.background).toContain("oklch(0.92 0.05 42)");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/app/views/Projects.test.jsx
```

Expected: every test fails (`Projects.jsx` doesn't exist).

- [ ] **Step 3: Implement `ui/src/app/views/Projects.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { useNavigate } from "react-router-dom";
import Icon from "../../components/Icon.jsx";
import { PROJECTS } from "../data.js";
import { colorFor, inkFor } from "./artifactHelpers.js";

/**
 * `/app/projects` view. Translated from design-sources/app/views.jsx
 * `ProjectsView`. Grid of project cards plus a "New project" tile (the
 * tile is a Phase 6e no-op; later phases wire the create-project modal).
 */
export default function Projects() {
  const navigate = useNavigate();
  return (
    <div className="view">
      <div className="view-inner">
        <div className="view-head">
          <h2>Projects</h2>
          <p>Group related chats, share knowledge, and keep context in one place.</p>
        </div>
        <div className="grid">
          <button type="button" className="card new-card">
            <Icon name="plus" size={20} /> New project
          </button>
          {PROJECTS.map((p) => (
            <div
              className="card"
              key={p.id}
              onClick={() => navigate(`/app/projects/${p.id}`)}
            >
              <div className="ct">
                <span
                  className="badge"
                  style={{ background: colorFor(p.color), color: inkFor(p.color) }}
                >
                  <Icon name="projects" size={19} />
                </span>
                <h3>{p.name}</h3>
              </div>
              <p className="desc">{p.desc}</p>
              <div className="meta">
                <span><Icon name="chat" size={14} /> {p.chats} chats</span>
                <span><Icon name="doc" size={14} /> {p.docs} docs</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run the tests**

```bash
cd ui && npx vitest run src/app/views/Projects.test.jsx
```

Expected: 5/5 PASS.

- [ ] **Step 5: Verify per-file 100% coverage**

```bash
cd ui && npx vitest run src/app/views/Projects.test.jsx --coverage
```

Expected: `Projects.jsx` at 100% across statements/branches/functions/lines. If anything's uncovered, report — don't invent extra tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/views/Projects.jsx ui/src/app/views/Projects.test.jsx
git commit -m "feat(channel-mvp): Phase 6e — Projects view (grid of cards + New project tile)"
```

---

## Task 4 — `ProjectDetail.jsx`

**Files:**
- Create: `ui/src/app/views/ProjectDetail.jsx`
- Create: `ui/src/app/views/ProjectDetail.test.jsx`

The component looks up the project by `:id` from `PROJECTS`. If found, it renders the back button + project header + a Composer wired to the same `channel-pending-send` sessionStorage payload as ChatHome + "Project knowledge" doc chips + "Chats in this project" list (first 6 RECENTS, click → `/app/c/<recent.id>`). If not found, renders a small empty state with a link back to `/app/projects`.

- [ ] **Step 1: Write the failing test**

Create `ui/src/app/views/ProjectDetail.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import ProjectDetail from "./ProjectDetail.jsx";
import { PROJECTS, PROJECT_DOCS, RECENTS } from "../data.js";
import { __resetChannelPrefsForTest } from "../../hooks/useChannelPrefs.js";

function renderAt(path) {
  let lastPath = null;
  function PathCatcher() {
    const { pathname } = useLocation();
    lastPath = pathname;
    return null;
  }
  const result = render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/projects/:id" element={<><ProjectDetail /><PathCatcher /></>} />
        <Route path="/app/projects" element={<PathCatcher />} />
        <Route path="/app/c/:id" element={<PathCatcher />} />
      </Routes>
    </MemoryRouter>
  );
  return { ...result, getLastPath: () => lastPath };
}

describe("ProjectDetail", () => {
  let storage;
  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("sessionStorage", {
      getItem: (k) => storage[`s:${k}`] ?? null,
      setItem: (k, v) => { storage[`s:${k}`] = String(v); },
      removeItem: (k) => { delete storage[`s:${k}`]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    __resetChannelPrefsForTest();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the project's name + description in the header when :id matches PROJECTS", () => {
    renderAt("/app/projects/p1");
    const p1 = PROJECTS.find((p) => p.id === "p1");
    expect(screen.getByRole("heading", { level: 2, name: p1.name })).toBeTruthy();
    expect(screen.getByText(p1.desc)).toBeTruthy();
  });

  it("renders all PROJECT_DOCS chips", () => {
    renderAt("/app/projects/p1");
    for (const d of PROJECT_DOCS) {
      expect(screen.getByText(d)).toBeTruthy();
    }
  });

  it("renders the first 6 RECENTS as 'Chats in this project' entries", () => {
    const { container } = renderAt("/app/projects/p1");
    const rows = container.querySelectorAll(".proj-chat");
    expect(rows.length).toBe(6);
    expect(rows[0].textContent).toContain(RECENTS[0].title);
  });

  it("clicking a chat row navigates to /app/c/<that-recent-id>", () => {
    const { getLastPath } = renderAt("/app/projects/p1");
    fireEvent.click(screen.getByText(RECENTS[0].title));
    expect(getLastPath()).toBe(`/app/c/${RECENTS[0].id}`);
  });

  it("clicking the back button navigates to /app/projects", () => {
    const { getLastPath } = renderAt("/app/projects/p1");
    fireEvent.click(screen.getByRole("button", { name: /^Projects$/i }));
    expect(getLastPath()).toBe("/app/projects");
  });

  it("typing in the Composer + Send stashes the pending payload and navigates to /app/c/new", () => {
    const { getLastPath } = renderAt("/app/projects/p1");
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "kick off a new chat" } });
    fireEvent.click(screen.getByTitle("Send"));
    const raw = sessionStorage.getItem("channel-pending-send");
    expect(raw).toBeTruthy();
    const payload = JSON.parse(raw);
    expect(payload.text).toBe("kick off a new chat");
    expect(payload.modelId).toBe("claude-opus-4-8");
    expect(payload.effort).toBe("High");
    expect(getLastPath()).toBe("/app/c/new");
  });

  it("Composer placeholder names the active project", () => {
    renderAt("/app/projects/p1");
    expect(screen.getByPlaceholderText(/New chat in Analytics Rewrite/)).toBeTruthy();
  });

  it("unknown :id renders an empty state with a link back to /app/projects", () => {
    const { getLastPath } = renderAt("/app/projects/does-not-exist");
    expect(screen.getByText(/Project not found/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Back to projects/i }));
    expect(getLastPath()).toBe("/app/projects");
  });

  it("Composer falls through silently when sessionStorage.setItem throws (private mode)", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: () => null,
      setItem: () => { throw new Error("private mode"); },
      removeItem: () => {},
    });
    const { getLastPath } = renderAt("/app/projects/p1");
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "still works" } });
    expect(() => fireEvent.click(screen.getByTitle("Send"))).not.toThrow();
    expect(getLastPath()).toBe("/app/c/new");
  });

  it("clicking 'Add' in the Project knowledge section does not throw (Phase 6e no-op)", () => {
    renderAt("/app/projects/p1");
    expect(() => fireEvent.click(screen.getByRole("button", { name: /^Add$/i }))).not.toThrow();
  });

  it("project badge background is themed by the project's color hue", () => {
    const { container } = renderAt("/app/projects/p1");
    const badge = container.querySelector(".proj-head .badge");
    expect(badge).toBeTruthy();
    expect(badge.style.background).toContain("oklch(0.92 0.05 42)");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/app/views/ProjectDetail.test.jsx
```

Expected: every test fails (file doesn't exist).

- [ ] **Step 3: Implement `ui/src/app/views/ProjectDetail.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { useNavigate, useParams } from "react-router-dom";
import ChannelMark from "../../components/ChannelMark.jsx";
import Composer from "../Composer.jsx";
import Icon from "../../components/Icon.jsx";
import { useChannelPrefs } from "../../hooks/useChannelPrefs.js";
import { MODELS, PROJECTS, PROJECT_DOCS, RECENTS } from "../data.js";
import { colorFor, inkFor } from "./artifactHelpers.js";

const PENDING_KEY = "channel-pending-send";

/**
 * `/app/projects/:id` view. Translated from design-sources/app/views.jsx
 * `ProjectDetail`. Composer follows the same stash-and-navigate pattern
 * as ChatHome — Phase 6e doesn't scope the conversation to the project
 * (no backend persistence yet); we just kick off /app/c/new with the
 * user's draft. Recents are the first 6 mock entries — clicking one
 * opens its canned conversation.
 */
export default function ProjectDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const prefs = useChannelPrefs();
  const project = PROJECTS.find((p) => p.id === id);

  if (!project) {
    return (
      <div className="view">
        <div className="view-inner">
          <div className="view-head">
            <h2>Project not found</h2>
            <p>The link may be stale or the project was renamed.</p>
          </div>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => navigate("/app/projects")}
          >
            Back to projects
          </button>
        </div>
      </div>
    );
  }

  const chats = RECENTS.slice(0, 6);
  const modelObj = MODELS.find((m) => m.id === prefs.model) ?? MODELS[0];
  const setModelObj = (m) => prefs.setModel(m.id);

  function startChat(text, atts) {
    try {
      sessionStorage.setItem(
        PENDING_KEY,
        JSON.stringify({
          text,
          atts: atts || [],
          modelId: modelObj.id,
          effort: prefs.effort,
        })
      );
    } catch {
      /* private mode etc — Conversation just renders empty */
    }
    navigate("/app/c/new");
  }

  return (
    <div className="view">
      <div className="view-inner" style={{ maxWidth: 820 }}>
        <button
          type="button"
          className="back-btn"
          onClick={() => navigate("/app/projects")}
        >
          <span style={{ transform: "rotate(180deg)", display: "inline-flex" }}>
            <Icon name="chevron-right" size={16} />
          </span>
          Projects
        </button>
        <div className="proj-head">
          <span
            className="badge"
            style={{ background: colorFor(project.color), color: inkFor(project.color) }}
          >
            <Icon name="projects" size={24} />
          </span>
          <div style={{ flex: 1 }}>
            <h2>{project.name}</h2>
            <p>{project.desc}</p>
          </div>
        </div>

        <Composer
          model={modelObj}
          effort={prefs.effort}
          setModel={setModelObj}
          setEffort={prefs.setEffort}
          onSend={startChat}
          placeholder={`New chat in ${project.name}…`}
        />

        <div className="proj-section">
          <div className="proj-section-h">
            <h3>Project knowledge</h3>
            <button type="button" className="ghost-sm">
              <Icon name="plus" size={15} /> Add
            </button>
          </div>
          <div className="doc-row">
            {PROJECT_DOCS.map((d) => (
              <div className="doc-chip" key={d}>
                <span className="dc-ic"><Icon name="doc" size={15} /></span>{d}
              </div>
            ))}
          </div>
        </div>

        <div className="proj-section">
          <div className="proj-section-h">
            <h3>Chats in this project</h3>
          </div>
          {chats.map((c) => (
            <button
              type="button"
              className="proj-chat"
              key={c.id}
              onClick={() => navigate(`/app/c/${c.id}`)}
            >
              <span className="pc-ic"><Icon name="chat" size={16} /></span>
              <span className="pc-t">{c.title}</span>
              <span className="pc-go"><Icon name="chevron-right" size={16} /></span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
```

Note: `ChannelMark` is imported but unused — delete that line if your editor lints unused imports. (It's not used by the body — only kept if your IDE auto-adds it.)

- [ ] **Step 4: Run the tests**

```bash
cd ui && npx vitest run src/app/views/ProjectDetail.test.jsx
```

Expected: 11/11 PASS. If any fails, read it carefully — most likely a misspelled `data-testid`, a missing `<MemoryRouter>` wrap, or the unused-import lint described above.

- [ ] **Step 5: Verify per-file 100% coverage**

```bash
cd ui && npx vitest run src/app/views/ProjectDetail.test.jsx --coverage
```

Expected: `ProjectDetail.jsx` at 100%. If anything's uncovered, report — don't add extra tests.

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/views/ProjectDetail.jsx ui/src/app/views/ProjectDetail.test.jsx
git commit -m "feat(channel-mvp): Phase 6e — ProjectDetail (back/header/Composer/docs/chats)"
```

---

## Task 5 — `ArtifactPanel.jsx`

**Files:**
- Create: `ui/src/app/views/ArtifactPanel.jsx`
- Create: `ui/src/app/views/ArtifactPanel.test.jsx`

The panel renders nothing when its `artifact` prop is null. When non-null, it renders the overlay (`.art-panel-wrap` + `.art-backdrop` + `.art-panel`) with a header (icon badge + title + kind/lines + Copy + Download + Close) and a body that depends on `artifact.kind`. Click on the backdrop OR the Close button → `onClose()`. Copy + Download are no-op at Phase 6e.

- [ ] **Step 1: Write the failing test**

Create `ui/src/app/views/ArtifactPanel.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ArtifactPanel from "./ArtifactPanel.jsx";
import { ARTIFACTS } from "../data.js";

function byKind(kind) {
  return ARTIFACTS.find((a) => a.kind === kind);
}

describe("ArtifactPanel", () => {
  it("renders nothing when artifact is null", () => {
    const { container } = render(<ArtifactPanel artifact={null} onClose={() => {}} />);
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });

  it("renders title + kind in the header for a Code artifact", () => {
    const a = byKind("Code");
    render(<ArtifactPanel artifact={a} onClose={() => {}} />);
    expect(screen.getByText(a.title)).toBeTruthy();
    expect(screen.getByText(/Code/)).toBeTruthy();
    expect(screen.getByText(/142 lines/)).toBeTruthy();
  });

  it("omits the trailing ' · <lines>' when artifact.lines is the placeholder '—'", () => {
    const a = byKind("Document");
    render(<ArtifactPanel artifact={a} onClose={() => {}} />);
    // The kind text should be present, but no " · —" suffix.
    const sub = document.body.querySelector(".art-phead .s");
    expect(sub).toBeTruthy();
    expect(sub.textContent).toBe("Document");
  });

  it("renders <pre><code> for kind=Code", () => {
    render(<ArtifactPanel artifact={byKind("Code")} onClose={() => {}} />);
    expect(document.body.querySelector("pre.art-code code")).toBeTruthy();
  });

  it("renders an SVG chart for kind=Chart", () => {
    render(<ArtifactPanel artifact={byKind("Chart")} onClose={() => {}} />);
    const svg = document.body.querySelector(".art-chart svg");
    expect(svg).toBeTruthy();
    // 7 bars + one baseline line = 8 SVG children.
    expect(svg.querySelectorAll("rect").length).toBe(7);
    expect(svg.querySelectorAll("line").length).toBe(1);
  });

  it("renders an art-table for kind=Data with a header row + 4 data rows", () => {
    render(<ArtifactPanel artifact={byKind("Data")} onClose={() => {}} />);
    const table = document.body.querySelector("table.art-table");
    expect(table).toBeTruthy();
    expect(table.querySelectorAll("thead th").length).toBe(4);
    expect(table.querySelectorAll("tbody tr").length).toBe(4);
  });

  it("renders the interactive preview block for kind=Interactive", () => {
    render(<ArtifactPanel artifact={byKind("Interactive")} onClose={() => {}} />);
    expect(document.body.querySelector(".art-interactive")).toBeTruthy();
    expect(screen.getByText(/Interactive preview/i)).toBeTruthy();
  });

  it("renders the document body for kind=Document (default branch)", () => {
    render(<ArtifactPanel artifact={byKind("Document")} onClose={() => {}} />);
    expect(document.body.querySelector(".art-doc")).toBeTruthy();
    expect(screen.getByText(/Summary/i)).toBeTruthy();
  });

  it("clicking the close button fires onClose", () => {
    const onClose = vi.fn();
    render(<ArtifactPanel artifact={byKind("Code")} onClose={onClose} />);
    fireEvent.click(screen.getByTitle("Close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("clicking the backdrop fires onClose", () => {
    const onClose = vi.fn();
    const { container } = render(<ArtifactPanel artifact={byKind("Code")} onClose={onClose} />);
    fireEvent.click(container.querySelector(".art-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Copy + Download buttons render but are Phase-6e no-ops", () => {
    render(<ArtifactPanel artifact={byKind("Code")} onClose={() => {}} />);
    for (const title of ["Copy", "Download"]) {
      expect(() => fireEvent.click(screen.getByTitle(title))).not.toThrow();
    }
  });

  it("falls through to the Document renderer for an unknown kind", () => {
    render(
      <ArtifactPanel
        artifact={{ id: "x", title: "Mystery", kind: "Mystery", updated: "", lines: "—", project: null }}
        onClose={() => {}}
      />
    );
    expect(document.body.querySelector(".art-doc")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/app/views/ArtifactPanel.test.jsx
```

Expected: every test fails (file doesn't exist).

- [ ] **Step 3: Implement `ui/src/app/views/ArtifactPanel.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Icon from "../../components/Icon.jsx";
import { artIcon } from "./artifactHelpers.js";

/**
 * Slide-in artifact viewer panel. Translated from
 * design-sources/app/views.jsx `ArtifactPanel` + `renderArtifactBody`.
 * Five renderers (Code, Chart, Data, Interactive, Document/default) —
 * each lifted verbatim from the design source. Copy + Download are
 * Phase-6e no-ops; Close fires `onClose`.
 *
 * When `artifact` is null the component returns nothing, so callers can
 * mount it unconditionally and just flip the prop to show/hide.
 */
export default function ArtifactPanel({ artifact, onClose }) {
  if (!artifact) return null;
  const lines = artifact.lines && artifact.lines !== "—" ? " · " + artifact.lines : "";
  const noop = () => {};
  return (
    <div className="art-panel-wrap">
      <div className="art-backdrop" onClick={onClose} />
      <div className="art-panel">
        <div className="art-phead">
          <span className="ic"><Icon name={artIcon(artifact.kind)} size={18} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="t">{artifact.title}</div>
            <div className="s mono">{artifact.kind}{lines}</div>
          </div>
          <button type="button" className="icon-btn" title="Copy" onClick={noop}>
            <Icon name="copy" size={17} />
          </button>
          <button type="button" className="icon-btn" title="Download" onClick={noop}>
            <Icon name="download" size={17} />
          </button>
          <button type="button" className="icon-btn" title="Close" onClick={onClose}>
            <Icon name="close" size={18} />
          </button>
        </div>
        <div className="art-pbody">{renderArtifactBody(artifact)}</div>
      </div>
    </div>
  );
}

function renderArtifactBody(a) {
  switch (a.kind) {
    case "Code":
      return (
        <pre className="art-code"><code>{`export function createIngestionBuffer(opts: BufferOpts) {
  const queue: Event[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;

  function flush() {
    if (queue.length === 0) return;
    const batch = queue.splice(0, queue.length);
    opts.sink.writeColumnar(batch);   // bulk insert
    timer = null;
  }

  return {
    push(ev: Event) {
      queue.push(ev);
      if (queue.length >= opts.maxBatch) return flush();
      timer ??= setTimeout(flush, opts.flushMs);
    },
    drain: flush,
  };
}`}</code></pre>
      );
    case "Chart": {
      const bars = [42, 68, 55, 80, 61, 73, 90];
      const max = 100;
      return (
        <div className="art-chart">
          <svg viewBox="0 0 320 160" width="100%" height="220">
            {bars.map((v, i) => (
              <rect
                key={i}
                x={12 + i * 44}
                y={150 - (v / max) * 130}
                width="28"
                height={(v / max) * 130}
                rx="3"
                fill="var(--accent)"
                opacity={0.55 + i * 0.06}
              />
            ))}
            <line x1="8" y1="150" x2="316" y2="150" stroke="var(--border)" strokeWidth="1" />
          </svg>
          <div className="art-cap mono">Elevation gain by segment (m)</div>
        </div>
      );
    }
    case "Data":
      return (
        <table className="art-table">
          <thead>
            <tr><th>Month</th><th>Spend</th><th>Budget</th><th>Δ</th></tr>
          </thead>
          <tbody>
            {[
              ["Jan", "$4,210", "$4,500", "−6%"],
              ["Feb", "$4,880", "$4,500", "+8%"],
              ["Mar", "$3,940", "$4,500", "−12%"],
              ["Apr", "$5,120", "$5,000", "+2%"],
            ].map((r, i) => (
              <tr key={i}>
                {r.map((c, j) => (
                  <td key={j} className={j > 0 ? "mono" : ""}>{c}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
    case "Interactive":
      return (
        <div className="art-interactive">
          <div className="art-ph">
            <Icon name="play" size={22} />
            <div>Interactive preview</div>
            <div className="mono">React · runs in a sandbox</div>
          </div>
        </div>
      );
    default:
      return (
        <div className="art-doc">
          <h4>Summary</h4>
          <p>This runbook covers the cut-over from the row store to the columnar pipeline, including the dual-write window, verification queries, and rollback triggers.</p>
          <h4>Steps</h4>
          <p>1. Enable dual-write and let both stores receive events for 24 hours.<br />2. Backfill historical partitions oldest-first, verifying row counts per day.<br />3. Flip reads to the columnar store behind a feature flag, starting at 5%.</p>
          <p>Keep the row store warm for one full billing cycle before decommissioning.</p>
        </div>
      );
  }
}
```

- [ ] **Step 4: Run the tests**

```bash
cd ui && npx vitest run src/app/views/ArtifactPanel.test.jsx
```

Expected: 12/12 PASS.

- [ ] **Step 5: Verify per-file 100% coverage**

```bash
cd ui && npx vitest run src/app/views/ArtifactPanel.test.jsx --coverage
```

Expected: `ArtifactPanel.jsx` at 100% across all axes.

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/views/ArtifactPanel.jsx ui/src/app/views/ArtifactPanel.test.jsx
git commit -m "feat(channel-mvp): Phase 6e — ArtifactPanel + 5 renderers (Code/Chart/Data/Interactive/Document)"
```

---

## Task 6 — `Artifacts.jsx` (list + bookmarkable panel via `?artifact=:id`)

**Files:**
- Create: `ui/src/app/views/Artifacts.jsx`
- Create: `ui/src/app/views/Artifacts.test.jsx`

The view renders a list of `.list-row` per `ARTIFACTS` entry. Click → set search param `?artifact=<id>`. The component then renders `<ArtifactPanel artifact={...} onClose={...} />`. `onClose` clears the search param. Refreshing the URL `/app/artifacts?artifact=a2` should re-open the panel for `rate-limiter.ts`.

- [ ] **Step 1: Write the failing test**

Create `ui/src/app/views/Artifacts.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import Artifacts from "./Artifacts.jsx";
import { ARTIFACTS } from "../data.js";

function renderAt(path) {
  let lastSearch = null;
  function SearchCatcher() {
    const { search } = useLocation();
    lastSearch = search;
    return null;
  }
  const result = render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/artifacts" element={<><Artifacts /><SearchCatcher /></>} />
      </Routes>
    </MemoryRouter>
  );
  return { ...result, getLastSearch: () => lastSearch };
}

describe("Artifacts", () => {
  it("renders the 'Artifacts' header + tagline", () => {
    renderAt("/app/artifacts");
    expect(screen.getByRole("heading", { level: 2, name: "Artifacts" })).toBeTruthy();
    expect(screen.getByText(/Documents, code/i)).toBeTruthy();
  });

  it("renders one .list-row per ARTIFACTS entry", () => {
    const { container } = renderAt("/app/artifacts");
    const rows = container.querySelectorAll(".list-row");
    expect(rows.length).toBe(ARTIFACTS.length);
  });

  it("each row shows the artifact title + a sub-line with lines + updated time", () => {
    renderAt("/app/artifacts");
    for (const a of ARTIFACTS) {
      expect(screen.getByText(a.title)).toBeTruthy();
    }
  });

  it("a row whose artifact has a project shows the project name in the sub-line", () => {
    renderAt("/app/artifacts");
    // a2 (rate-limiter.ts) has project "Analytics Rewrite".
    expect(screen.getByText(/Analytics Rewrite/)).toBeTruthy();
  });

  it("no ArtifactPanel overlay is rendered by default (no ?artifact=)", () => {
    const { container } = renderAt("/app/artifacts");
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });

  it("clicking a row sets the ?artifact=<id> search param + opens the panel", () => {
    const { container, getLastSearch } = renderAt("/app/artifacts");
    fireEvent.click(screen.getByText("rate-limiter.ts"));
    expect(getLastSearch()).toBe("?artifact=a2");
    // The panel renders the Code artifact body.
    expect(container.querySelector(".art-panel-wrap")).toBeTruthy();
    expect(container.querySelector(".art-code")).toBeTruthy();
  });

  it("mounting directly at /app/artifacts?artifact=a4 opens the panel for that artifact", () => {
    const { container } = renderAt("/app/artifacts?artifact=a4");
    expect(container.querySelector(".art-panel-wrap")).toBeTruthy();
    // a4 is a Chart.
    expect(container.querySelector(".art-chart svg")).toBeTruthy();
  });

  it("clicking the panel's Close button clears the ?artifact= search param", () => {
    const { container, getLastSearch } = renderAt("/app/artifacts?artifact=a2");
    fireEvent.click(screen.getByTitle("Close"));
    expect(getLastSearch()).toBe("");
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });

  it("mounting with an unknown ?artifact= id renders no panel (silent ignore)", () => {
    const { container } = renderAt("/app/artifacts?artifact=nope");
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/app/views/Artifacts.test.jsx
```

Expected: every test fails.

- [ ] **Step 3: Implement `ui/src/app/views/Artifacts.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { useSearchParams } from "react-router-dom";
import Icon from "../../components/Icon.jsx";
import ArtifactPanel from "./ArtifactPanel.jsx";
import { ARTIFACTS } from "../data.js";
import { artIcon } from "./artifactHelpers.js";

const ARTIFACT_PARAM = "artifact";

/**
 * `/app/artifacts` view. Translated from design-sources/app/views.jsx
 * `ArtifactsView`. The detail panel is gated on a `?artifact=<id>`
 * search param so the URL is bookmarkable — refresh keeps it open.
 * Per the design spec §URL state, this is the only stateful piece of
 * the route.
 */
export default function Artifacts() {
  const [params, setParams] = useSearchParams();
  const openId = params.get(ARTIFACT_PARAM);
  const openArtifact = openId ? ARTIFACTS.find((a) => a.id === openId) ?? null : null;

  function openRow(a) {
    const next = new URLSearchParams(params);
    next.set(ARTIFACT_PARAM, a.id);
    setParams(next);
  }
  function closePanel() {
    const next = new URLSearchParams(params);
    next.delete(ARTIFACT_PARAM);
    setParams(next);
  }

  return (
    <div className="view">
      <div className="view-inner">
        <div className="view-head">
          <h2>Artifacts</h2>
          <p>Documents, code, and interactive pieces Channel has built with you.</p>
        </div>
        {ARTIFACTS.map((a) => (
          <div className="list-row" key={a.id} onClick={() => openRow(a)}>
            <span className="badge"><Icon name={artIcon(a.kind)} size={18} /></span>
            <div className="info">
              <div className="ti">{a.title}</div>
              <div className="sub">
                {a.lines} · updated {a.updated}{a.project ? " · " + a.project : ""}
              </div>
            </div>
            <span className="kind">{a.kind}</span>
          </div>
        ))}
      </div>
      <ArtifactPanel artifact={openArtifact} onClose={closePanel} />
    </div>
  );
}
```

- [ ] **Step 4: Run the tests**

```bash
cd ui && npx vitest run src/app/views/Artifacts.test.jsx
```

Expected: 9/9 PASS.

- [ ] **Step 5: Verify per-file 100% coverage**

```bash
cd ui && npx vitest run src/app/views/Artifacts.test.jsx --coverage
```

Expected: 100%.

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/views/Artifacts.jsx ui/src/app/views/Artifacts.test.jsx
git commit -m "feat(channel-mvp): Phase 6e — Artifacts view (list + bookmarkable panel via ?artifact=)"
```

---

## Task 7 — Wire the three new views into App.jsx

**Files:**
- Modify: `ui/src/App.jsx`
- Modify: `ui/src/App.test.jsx`

- [ ] **Step 1: Update App.test.jsx first**

Open `ui/src/App.test.jsx`. Find the `it.each([...])` authed-route table — it has three entries for the placeholders we're replacing:

```jsx
    ["/app/projects",       "app-projects"],
    ["/app/projects/p1",    "app-project-detail"],
    ["/app/artifacts",      "app-artifacts"],
```

DELETE those three rows. Then add three new tests immediately below the existing `it("/app/c/r1 renders the Conversation component (not the placeholder)", ...)` test:

```jsx
  it("/app/projects renders the Projects view (not the placeholder)", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app/projects");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 2, name: "Projects" })).toBeTruthy();
    expect(screen.queryByTestId("app-projects")).toBeNull();
  });

  it("/app/projects/p1 renders the ProjectDetail view (not the placeholder)", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app/projects/p1");
    await act(async () => render(<App />));
    // p1 = "Analytics Rewrite" per PROJECTS[0].
    expect(screen.getByRole("heading", { level: 2, name: "Analytics Rewrite" })).toBeTruthy();
    expect(screen.queryByTestId("app-project-detail")).toBeNull();
  });

  it("/app/artifacts renders the Artifacts view (not the placeholder)", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app/artifacts");
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { level: 2, name: "Artifacts" })).toBeTruthy();
    expect(screen.queryByTestId("app-artifacts")).toBeNull();
  });
```

- [ ] **Step 2: Run the failing tests**

```bash
cd ui && npx vitest run src/App.test.jsx
```

Expected: the three new tests fail — App.jsx still renders the placeholders.

- [ ] **Step 3: Update App.jsx**

Open `ui/src/App.jsx`. Add three new imports next to the existing `./app/...` imports (alphabetical order):

```jsx
import Artifacts from "./app/views/Artifacts.jsx";
import ProjectDetail from "./app/views/ProjectDetail.jsx";
import Projects from "./app/views/Projects.jsx";
```

Find these three placeholder lines (in the `ph(...)` block):

```jsx
const AppProjects      = () => ph("app-projects", "App: Projects");
const AppProjectDetail = () => ph("app-project-detail", "App: Project Detail");
const AppArtifacts     = () => ph("app-artifacts", "App: Artifacts");
```

DELETE all three.

Find the route declarations:

```jsx
          <Route path="/app/projects"       element={<AuthGate><AppProjects /></AuthGate>} />
          <Route path="/app/projects/:id"   element={<AuthGate><AppProjectDetail /></AuthGate>} />
          <Route path="/app/artifacts"      element={<AuthGate><AppArtifacts /></AuthGate>} />
```

Replace all three (wrap in `<Shell>` like the other authed routes do):

```jsx
          <Route path="/app/projects"       element={<AuthGate><Shell><Projects /></Shell></AuthGate>} />
          <Route path="/app/projects/:id"   element={<AuthGate><Shell><ProjectDetail /></Shell></AuthGate>} />
          <Route path="/app/artifacts"      element={<AuthGate><Shell><Artifacts /></Shell></AuthGate>} />
```

- [ ] **Step 4: Run the tests**

```bash
cd ui && npx vitest run src/App.test.jsx
```

Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add ui/src/App.jsx ui/src/App.test.jsx
git commit -m "feat(channel-mvp): Phase 6e — wire Projects / ProjectDetail / Artifacts into App.jsx"
```

---

## Task 8 — Update CLAUDE.md file tree

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Edit the `src/app/` block**

Open `CLAUDE.md`. Find this region (use `grep -n "data.js" CLAUDE.md` to locate):

```
│   │   └── app/                   # Chat app routes (Phase 6c onward)
│   │       ├── data.js                                       # MODELS, EFFORTS, RECENTS, QUICK_ACTIONS, SAMPLE_REPLY, SAMPLE_USER
│   │       ├── Login.jsx, GoogleG.jsx                        # Centered Google sign-in
│   │       ├── Shell.jsx, Sidebar.jsx, AccountPopover.jsx    # Layout chrome
│   │       ├── Composer.jsx, ModelPicker.jsx, AttachMenu.jsx # Composer + its popovers
│   │       ├── ChatHome.jsx                                  # Empty-state greeting
│   │       ├── Conversation.jsx                              # Streamed-turns view + inline artifact card
│   │       └── renderMarkdown.jsx                            # Tiny markdown helper (paragraphs/bold/OL/cursor)
```

Replace with:

```
│   │   └── app/                   # Chat app routes (Phase 6c onward)
│   │       ├── data.js                                       # MODELS, EFFORTS, RECENTS, QUICK_ACTIONS, SAMPLE_REPLY, SAMPLE_USER, PROJECTS, ARTIFACTS, PROJECT_DOCS
│   │       ├── Login.jsx, GoogleG.jsx                        # Centered Google sign-in
│   │       ├── Shell.jsx, Sidebar.jsx, AccountPopover.jsx    # Layout chrome
│   │       ├── Composer.jsx, ModelPicker.jsx, AttachMenu.jsx # Composer + its popovers
│   │       ├── ChatHome.jsx                                  # Empty-state greeting
│   │       ├── Conversation.jsx                              # Streamed-turns view + inline artifact card
│   │       ├── renderMarkdown.jsx                            # Tiny markdown helper (paragraphs/bold/OL/cursor)
│   │       └── views/                                        # /app/projects, /app/projects/:id, /app/artifacts
│   │           ├── Projects.jsx                              # Grid of project cards + 'New project' tile
│   │           ├── ProjectDetail.jsx                         # Project header + Composer + docs + chats list
│   │           ├── Artifacts.jsx                             # List of artifact rows + bookmarkable panel
│   │           ├── ArtifactPanel.jsx                         # Slide-in viewer with 5 renderers (Code/Chart/Data/Interactive/Document)
│   │           └── artifactHelpers.js                        # colorFor / inkFor / artIcon utilities
```

- [ ] **Step 2: Verify the diff is minimal**

```bash
git diff CLAUDE.md
```

Expected: only the above region changed.

- [ ] **Step 3: Run the copyright-header linter (sanity check)**

```bash
uv run python scripts/check_copyright.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): list Phase 6e views/ files in the structure tree"
```

---

## Task 9 — Final gate, smoke check, PR

The implementer hands back to the controller for this — runs `inv pre-push`, runs the dev server, verifies the four flows, opens the PR. No code change.

- [ ] **Step 1: Run the full gate**

```bash
uv run inv pre-push
```

Expected: all stages pass. Note total test count (should be ~376 + ~50 new = ~420 tests).

- [ ] **Step 2: Verify global 100% coverage**

```bash
cd ui && npx vitest run src/ --coverage
```

Expected: 100% across statements/branches/functions/lines. If any per-file gap, report — don't add tests beyond the plan.

- [ ] **Step 3: Local smoke check (5 minutes)**

```bash
uv run inv dev --seed
```

Visit `http://localhost:5173/auth/login?test_email=you@example.com` to authenticate via bypass, then:

1. Click **Projects** in the sidebar. The grid of 6 project cards + "New project" tile renders. Each card's badge background colour varies by hue.
2. Click **Analytics Rewrite**. The detail page renders with the back button, header, Composer (placeholder "New chat in Analytics Rewrite…"), 4 doc chips, and 6 chats. Typing + Send lands on `/app/c/new` with the canned streaming reply.
3. Click the back button. Returns to `/app/projects`.
4. Click **Artifacts** in the sidebar. The list of 6 artifact rows renders.
5. Click **rate-limiter.ts**. The slide-in panel opens with the Code body. The URL shows `?artifact=a2`. Refresh the page — the panel reopens.
6. Click the **Close** button (×). The panel closes, URL clears.
7. Click each of the other rows (one per kind). The body switches: Chart shows the SVG bars, Data shows the table, Interactive shows the play-button placeholder, Document shows the runbook prose.

If any step fails, STOP and fix before opening the PR.

- [ ] **Step 4: Push + open the PR (per CLAUDE.md `Opening a PR`)**

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD   # 9 commits expected (1 plan + 8 implementation)
git push -u origin feat/channel-mvp-phase-6e-projects-artifacts:feat/channel-mvp-phase-6e-projects-artifacts
```

Open the PR with `gh pr create --base development …` — title `feat(channel-mvp): Phase 6e — Projects + Artifacts`, body referencing the spec, scope-boundary list from the §"File structure" section above, and a test plan covering the 7 smoke steps.

---

## Self-review against the spec

**Spec coverage** (lines 224-226 + 314-318):
- `views/Projects.jsx` → Task 3 ✓
- `views/ProjectDetail.jsx` → Task 4 ✓
- `views/Artifacts.jsx` → Task 6 ✓
- `views/ArtifactPanel.jsx` → Task 5 ✓
- All five artifact renderers (Code, Document, Chart, Data, Interactive) → Task 5 ✓
- Routing for `/app/projects`, `/app/projects/:id`, `/app/artifacts` → Task 7 ✓
- `?artifact=:id` URL state convention → Task 6 ✓ (the implementation uses `useSearchParams`, the bookmarkable mounting test is explicit)
- Mock data lifted verbatim (PROJECTS, ARTIFACTS, PROJECT_DOCS) → Task 1 ✓
- Helpers (colorFor, inkFor, artIcon) → Task 2 ✓ (used by Tasks 3, 4, 5, 6)

**Placeholder scan:** no `TBD`, `TODO`, `implement later`, or `Similar to Task N` strings. Every step block contains actual code or actual commands.

**Type consistency:**
- `colorFor(h: number) => string` — Task 2 defines, Tasks 3 + 4 consume identically.
- `inkFor(h: number) => string` — same.
- `artIcon(kind: string) => string` — same; matches the 5 cases in `renderArtifactBody`.
- `ARTIFACTS[i].kind` is one of `"Code"`, `"Chart"`, `"Data"`, `"Interactive"`, `"Document"` — used consistently in Tasks 1, 2, 5, 6.
- The `channel-pending-send` JSON payload shape `{ text, atts, modelId, effort }` matches what ChatHome already writes (Phase 6d) and what Conversation reads — Task 4 follows the same shape.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-30-channel-mvp-phase-6e-projects-artifacts.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
