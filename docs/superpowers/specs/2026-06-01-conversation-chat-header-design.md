# Conversation chat header — design

**Date:** 2026-06-01
**Status:** Approved (ready for plan)

## Summary

The Conversation view (`ui/src/app/Conversation.jsx`) currently starts directly with the message thread — no header strip. Add a header at the top of the chat area containing the chat title (clickable, opens the rename/delete menu) and a disabled placeholder share icon, matching Claude Desktop's pattern.

## Goals

- Active chat's title is visible at the top of the Conversation view.
- Clicking anywhere on the title text or its caret opens the existing `ChatRowMenu` popover.
- Menu's Rename / Delete open the existing `RenameChatModal` / `DeleteChatModal` (no new modal infra).
- A disabled share icon sits on the right edge as a placeholder for future wiring (same disabled pattern as the menu's Pin / Change project placeholders).

## Non-goals

- Real share / export functionality.
- Breadcrumb / project context (Channel's projects feature isn't wired through the chat data model yet).
- Adding the header to ChatHome, Projects, Customize views — only the Conversation view gets one.
- Sticky positioning beyond what the layout naturally does (the header is OUTSIDE the `.convo` scroll container, so it doesn't scroll with messages — no `position: sticky` needed).

## Architecture

```
Conversation.jsx return tree:

<>
  <ChatHeader chat={currentChat} />     ← new, fixed at top of chat area
  <div className="convo">…</div>         ← existing scroll container
  <Composer …/>                          ← existing
</>

ChatHeader.jsx:
  ┌─────────────────────────────────────────────┐
  │ [Title text] [▼]                       [↑] │
  └─────────────────────────────────────────────┘
   ↑                                       ↑
   single button → opens ChatRowMenu       disabled share placeholder

ChatRowMenu items (reused from sidebar):
  📌 Pin (disabled)
  ✏  Rename       → opens RenameChatModal
  📁 Change project (disabled)
  📁ˣ Remove from project (disabled)
  🗑  Delete       → opens DeleteChatModal
```

The header's menu / modal state is local to `ChatHeader.jsx`; it does NOT route through Sidebar's state. Both surfaces (sidebar row menu, header menu) can be open independently — no cross-component coupling.

## Component changes

### `ui/src/app/ChatHeader.jsx` (new)

Props: `{ chat }` (the chat object from `useChats().chats.find(...)`) — may be `null` while the list loads or on a chat-not-found.

Renders `<div className="chat-hd">`:
- If `chat` is null/undefined: render the empty strip (same height, no title button) so the layout doesn't jump when the list arrives.
- Otherwise:
  - Center: `<button className="chat-hd-title" onClick={() => setMenuOpen(true)}>{chat.title} <Icon name="chevron-down" size={16}/></button>`. The whole button is the menu trigger.
  - Right: `<button className="icon-btn chat-hd-share" disabled aria-disabled="true" title="Coming soon"><Icon name="arrow-up" size={16}/></button>`.

State (component-local):
- `menuOpen: bool`, `anchorRect: DOMRect | null` — used by `ChatRowMenu`.
- `renameOpen: bool`, `deleteOpen: bool` — used by `RenameChatModal` / `DeleteChatModal`.

Behaviour:
- Click title → set `menuOpen=true`, compute `anchorRect` from `e.currentTarget.getBoundingClientRect()`.
- `ChatRowMenu`'s `onRename` → close menu, open RenameChatModal with `chatId={chat.chat_id}, currentTitle={chat.title}`.
- `onDelete` → close menu, open DeleteChatModal with `chatId={chat.chat_id}, chatTitle={chat.title}, isActive={true}` (the header IS the active chat by definition).
- Both modals' `onClose` clear their own state flag.

### `ui/src/app/Conversation.jsx` (modify)

Add `const currentChat = chats.chats.find((c) => c.chat_id === chatId) ?? null;` near the top of the body.

In the JSX return, add `<ChatHeader chat={currentChat} />` as the first child of the existing `<>` fragment, BEFORE the `<div className="convo">`.

### `ui/src/styles/app.css` (modify)

Append:

```css
/* ----- Conversation chat header ----------------------------------- */
.chat-hd {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-soft);
  min-height: 44px;
}
.chat-hd-title {
  background: transparent;
  border: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  color: var(--ink);
  cursor: pointer;
}
.chat-hd-title:hover { background: var(--raised); }
.chat-hd-share {
  margin-left: auto;
}
.chat-hd-share[disabled] {
  opacity: 0.4;
  cursor: not-allowed;
}
```

`.icon-btn` already exists (used by the sidebar's Toggle sidebar / Search). Reusing keeps visual weight consistent.

### `ui/src/app/ChatHeader.test.jsx` (new)

Tests (mocking `useChats` minimally — only needs `renameChat`, `deleteChat` for the modals):
1. Renders the empty strip when `chat` is `null` (no title button visible).
2. Renders the title text when `chat.title` is provided.
3. Click title button opens `ChatRowMenu` (asserts `role="menu"` present).
4. Click Rename → `RenameChatModal` opens with the chat's title pre-selected.
5. Click Delete → `DeleteChatModal` opens with `isActive=true` (asserted by inspecting the modal's confirmed behavior; the simplest assertion is the modal renders and contains the "Delete chat?" heading).
6. Share button has `disabled` attribute + `aria-disabled="true"`.

### `ui/src/app/Conversation.test.jsx` (modify)

One small assertion in the existing test suite: when Conversation renders with a chat in the list, the chat-hd title shows.

## Validation

### Layer 1 — Unit (CI gate)

`uv run inv pre-push` covers it. 100% coverage on `ChatHeader.jsx`.

### Layer 2 — Live verification (mandatory per memory)

Per the just-saved `feedback_ui_changes_need_live_verification` memory: spin up `inv dev`, navigate to a chat, take a screenshot via Playwright (the same `/tmp/verify_*.py` pattern used for the row-highlight fix). Confirm the header renders, the menu opens, both modals work, the share icon is disabled.

### Layer 3 — Dev verification

After merge + dev deploy: open `channel-dev.warlordofmars.net/app`, click into a chat, header should show the title; clicking it should open the menu.

## Out of scope / explicit deferrals

- Real share / export — placeholder only.
- Project breadcrumb (Channel doesn't have projects wired into the chat data model yet).
- Header on ChatHome / Projects / Customize views — only Conversation.
- Animated menu open / close — caller's `ChatRowMenu` already handles this.
- Mobile-touch sizing tweaks.

## CHANGELOG entry (draft)

```
### Added
- Conversation view now shows a header strip with the chat title and
  a menu trigger. Clicking the title opens the same Pin / Rename /
  Change project / Remove from project / Delete menu that the sidebar
  row's ⋮ button does. A placeholder share icon sits on the right for
  future wiring.
```
