# Sidebar active-row highlight — design

**Date:** 2026-06-01
**Status:** Approved (ready for plan)

## Summary

The sidebar Recents list shows the active chat without any visual distinction from inactive rows — the user has to read the chat title to know which one they're in. Add a subtle background-and-text-weight highlight on the row whose `chat_id` matches the URL's `chatId` param, matching Claude Desktop's selected-row treatment.

## Goals

- The row matching `useParams().chatId` is visually distinct from inactive rows (raised background + brighter text).
- Reuses existing CSS tokens (`--raised`, `--ink`) — no new tokens.
- Inactive rows keep their current styling unchanged.

## Non-goals

- Keyboard focus-ring restyling.
- Scroll-into-view-on-active behavior.
- Group-section emphasis (the "Today" / "Yesterday" labels stay unchanged).
- Sidebar-collapse interaction.

## Architecture

```
Sidebar.jsx                               app.css
┌──────────────────────────────┐          ┌──────────────────────┐
│ const { chatId } = useParams │          │ .recent              │
│ const activeChatId = chatId  │  ←──     │   color: --ink-soft  │ (existing)
│ (already wired during        │          │ .recent.active       │
│  Phase RD Task 12)           │          │   background: --raised│
│                              │          │   color: --ink       │ (new)
│ <button                      │          └──────────────────────┘
│   className={"recent"        │
│     + (c.chat_id ===         │
│        activeChatId          │
│        ? " active"           │
│        : "")}                │
│ >                            │
└──────────────────────────────┘
```

## Component changes

### `ui/src/app/Sidebar.jsx`

The `.recent` button (inside `groups.map(...)`'s inner mapping) receives a conditional `" active"` suffix on its className when `c.chat_id === activeChatId`. `activeChatId` is already in scope (added during Phase RD's delete-modal redirect logic at line 64).

### `ui/src/styles/app.css`

Append:

```css
.recent.active {
  background: var(--raised);
  color: var(--ink);
}
```

The `.recent` base rule already exists; this layers on top.

### `ui/src/app/Sidebar.test.jsx`

One new test in the existing "per-row menu" or "Sidebar" describe block. Asserts that given `useParams` returns `{chatId: "c1"}` and chat list `[{chat_id: "c1"...}, {chat_id: "c2"...}]`, the rendered c1 button has the `active` class on its `className`, and c2 does not.

## Validation

### Layer 1 — Unit (CI gate)

`uv run inv pre-push` covers it. New test passes; 100% coverage maintained on `Sidebar.jsx` (the new branch is tested).

### Layer 2 — Visual

`uv run inv dev` and open `http://localhost:5173/app`. Click into a chat. Sidebar row for that chat shows raised background + brighter text. Click another chat — old row reverts, new row highlights.

## CHANGELOG entry (draft)

```
### Changed
- The active chat row in the sidebar Recents list now has a raised
  background and brighter title text, matching the standard
  selected-row treatment.
```

## Out of scope

- Animated highlight transition (could be added with `transition:
  background .12s` in a follow-up if the snap feels jarring).
- Active-row marker pip / accent bar on the left edge.
- Highlight color variation per chat-state (pinned, archived, etc).
