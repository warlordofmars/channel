# Sidebar active-row highlight — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Recents row whose chat_id matches `useParams().chatId` gets a raised background and brighter title text — matches Claude Desktop's selected-row treatment.

**Architecture:** `activeChatId` is already in scope at `Sidebar.jsx:64` (added during Phase RD Task 12 for the delete modal's redirect logic). One className conditional + one CSS rule + one unit test. Single TDD pass.

**Tech Stack:** React (Vite), `react-router-dom`, Vitest + React Testing Library.

**Spec:** `docs/superpowers/specs/2026-06-01-sidebar-active-row-highlight-design.md` — reuses `--raised` / `--ink` tokens; no new tokens.

---

## File map

**Modify:**
- `ui/src/app/Sidebar.jsx` — 1-line className conditional on the chat-row `<button>`.
- `ui/src/styles/app.css` — one new `.recent.active` rule.
- `ui/src/app/Sidebar.test.jsx` — one new test asserting the active class.
- `CHANGELOG.md` — one bullet.

---

## Task 1: TDD the active-row class

**Files:**
- Modify: `ui/src/app/Sidebar.jsx`
- Modify: `ui/src/app/Sidebar.test.jsx`

Single-pass TDD: write the failing test, add the className conditional, watch it pass.

- [ ] **Step 1: Write the failing test**

Open `ui/src/app/Sidebar.test.jsx`. Find the existing `describe("Sidebar — per-row menu", ...)` block (added in Phase RD). Append a new test inside it (the block already mocks `useChats` and renders with `<MemoryRouter>`).

Currently the file uses default `useParams` (no chatId, since `<MemoryRouter>` doesn't match `/app/c/:chatId`). To test the active state, we need to mount on a route that DOES set the param. Use the route-specifying form of `MemoryRouter`:

```jsx
// Imports may need extending — check the file's current imports first.
// At minimum, the test needs <Routes>, <Route> from react-router-dom in addition
// to MemoryRouter.

import { MemoryRouter, Route, Routes } from "react-router-dom";

// ...inside the existing `describe("Sidebar — per-row menu", () => {` block:
it("marks the active row with the 'active' class", () => {
  render(
    <MemoryRouter initialEntries={["/app/c/c1"]}>
      <Routes>
        <Route
          path="/app/c/:chatId"
          element={
            <Sidebar
              chats={[
                { chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString() },
                { chat_id: "c2", title: "beta", last_message_at: new Date().toISOString() },
              ]}
            />
          }
        />
      </Routes>
    </MemoryRouter>,
  );
  const alpha = screen.getByRole("button", { name: /^alpha$/i });
  const beta = screen.getByRole("button", { name: /^beta$/i });
  expect(alpha.className).toMatch(/\bactive\b/);
  expect(beta.className).not.toMatch(/\bactive\b/);
});
```

If `screen` / `render` aren't imported at the top of the file, add them from `@testing-library/react` (the existing tests already do this; check first).

If `Route` / `Routes` aren't imported, add them alongside `MemoryRouter` from `react-router-dom`.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/app/Sidebar.test.jsx -t "marks the active row"
```

Expected: FAIL — both rows render with `className="recent"` and the regex assertion `/\bactive\b/` doesn't match.

- [ ] **Step 3: Add the className conditional in `ui/src/app/Sidebar.jsx`**

Find the chat-row mapping. It currently looks like:

```jsx
<button
  type="button"
  className="recent"
  onClick={() => navigate(`/app/c/${c.chat_id}`)}
>
  {c.title}
</button>
```

Change `className="recent"` to:

```jsx
className={"recent" + (c.chat_id === activeChatId ? " active" : "")}
```

`activeChatId` is already in scope at `Sidebar.jsx:64` (from the Phase RD delete-modal redirect logic).

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ui && npx vitest run src/app/Sidebar.test.jsx -t "marks the active row"
```

Expected: PASS. Also run the full Sidebar test file to confirm no regression:

```bash
cd ui && npx vitest run src/app/Sidebar.test.jsx --coverage
```

Expected: all PASS, 100% coverage on `Sidebar.jsx`.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/Sidebar.jsx ui/src/app/Sidebar.test.jsx
git commit -m "feat(channel-rd): mark active chat row with .active className"
```

---

## Task 2: CSS rule

**Files:**
- Modify: `ui/src/styles/app.css`

- [ ] **Step 1: Append the rule**

Open `ui/src/styles/app.css`. Find the existing `.recent` rule (it sets the default color / padding). Add this rule immediately after it (keeps related rules adjacent for future readability):

```css
.recent.active {
  background: var(--raised);
  color: var(--ink);
}
```

- [ ] **Step 2: Smoke-build the UI**

```bash
cd ui && npx vite build --mode development 2>&1 | tail -3
```

Expected: build succeeds.

- [ ] **Step 3: Visual smoke check (optional, manual)**

If `inv dev` is already running, hard-refresh the SPA tab. Click a chat; its row should now have a raised background and brighter text. Click another chat; old row reverts, new row highlights.

- [ ] **Step 4: Commit**

```bash
git add ui/src/styles/app.css
git commit -m "style(channel-rd): highlight active chat row in sidebar"
```

---

## Task 3: CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Append entry**

Open `CHANGELOG.md`. Find `[Unreleased]` `### Changed`. Add this bullet at the top of the subsection:

```markdown
- The active chat row in the sidebar Recents list now has a raised
  background and brighter title text, matching the standard
  selected-row treatment.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(channel-rd): CHANGELOG entry for sidebar active-row highlight"
```

---

## Task 4: Pre-push + PR

- [ ] **Step 1: Pre-push gate**

```bash
uv run inv pre-push
```

Expected: `All checks passed!`. If ruff format catches anything, apply + recommit as `style(channel-rd): ruff format`.

- [ ] **Step 2: File the issue**

```bash
gh issue create \
  --title "feat(channel-rd): highlight active chat row in sidebar" \
  --label "status:ready,priority:p2,size:xs,enhancement,agent-safe" \
  --body "$(cat <<'EOF'
The sidebar Recents list currently shows no visual distinction for the row matching the active chat. Add a raised background + brighter title text on that row, matching Claude Desktop's selected-row treatment.

Design: docs/superpowers/specs/2026-06-01-sidebar-active-row-highlight-design.md
Plan: docs/superpowers/plans/2026-06-01-sidebar-active-row-highlight.md

## Files to touch

- ui/src/app/Sidebar.jsx
- ui/src/app/Sidebar.test.jsx
- ui/src/styles/app.css
- CHANGELOG.md
- docs/superpowers/specs/2026-06-01-sidebar-active-row-highlight-design.md
- docs/superpowers/plans/2026-06-01-sidebar-active-row-highlight.md
EOF
)"
```

- [ ] **Step 3: Push + open PR + auto-merge**

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD  # confirm only your commits

git push -u origin feat/sidebar-active-row-highlight:feat/sidebar-active-row-highlight

gh pr create --base development \
  --title "feat(channel-rd): highlight active chat row in sidebar" \
  --body "$(cat <<'EOF'
## Summary

The Recents row whose ``chat_id`` matches ``useParams().chatId`` gets a raised background (``var(--raised)``) and brighter text (``var(--ink)``). Matches Claude Desktop's selected-row treatment. One-line className conditional in ``Sidebar.jsx`` + one CSS rule. ``activeChatId`` was already in scope from Phase RD's delete-modal redirect logic.

Closes #<NNN-from-step-2>

## Test plan

- [x] Layer 1 — ``inv pre-push`` green. New unit test in ``Sidebar.test.jsx`` asserts the active row has the ``.active`` class and inactive rows don't. 100% coverage on ``Sidebar.jsx``.
- [ ] Layer 2 — after merge + dev deploy: click into a chat on https://channel-dev.warlordofmars.net/app; the sidebar row for that chat highlights; click another chat; the highlight moves.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"

gh pr merge --auto --squash --delete-branch
```

- [ ] **Step 4: Watch CI**

```bash
gh run watch
```

---

## Done criteria

- ✅ Active chat row in the sidebar has a raised background + brighter text.
- ✅ Inactive rows render unchanged.
- ✅ Unit test asserts both cases.
- ✅ ``inv pre-push`` green; 100% coverage on ``Sidebar.jsx``.
- ✅ CHANGELOG ``[Unreleased]`` ``### Changed`` has the bullet.
- ✅ Live verification on dev: clicking between chats updates the highlight.
