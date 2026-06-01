# Chat rename + delete — design

**Date:** 2026-06-01
**Status:** Approved (ready for plan)

## Summary

Add user-facing rename and delete actions to chats in the sidebar.
Each row gets a "more" button (`⋮`) that appears on hover; clicking it
opens a small popover with five menu items matching the reference UX
(Pin, Rename, Change project, Remove from project, Delete). Only
Rename and Delete are wired in this phase — the other three render as
disabled placeholders with `Coming soon` tooltips so the menu shape is
established and future features slot in without churn.

Rename opens a modal with a pre-filled, pre-selected title input
(Enter confirms, Esc cancels). Delete opens a confirmation modal,
focus-defaulted to Cancel for safety, that wipes the chat from
DynamoDB AND from AgentCore Memory so the agent's cross-chat recall
stops surfacing the deleted conversation.

## Goals

- Users can rename a chat from the sidebar without typing the new
  title into the URL bar or a debug endpoint.
- Users can delete a chat permanently and have it disappear from both
  the sidebar AND the AI's memory (i.e. it stops appearing in
  cross-session recall context in future chats).
- Establish a reusable popover-menu pattern and a reusable modal
  primitive that the placeholder items (Pin / Change project / Remove
  from project) and future destructive confirms can consume.
- Delete is irreversible by design — UX makes that clear; no undo.

## Non-goals

- Wiring Pin / Change project / Remove from project (placeholders
  only).
- Bulk delete, "delete all chats", or "clear memory" controls.
- Restore / undo / trash-bin for deleted chats.
- Mobile-touch alternative to hover-to-reveal (touch users will see
  the menu button via the same hover state once that surface is
  audited as part of a general UX pass).
- Keyboard shortcut to trigger the menu from a row (e.g. context-menu
  key). Tab-to-button + Enter is sufficient.

## Architecture

```
                          Sidebar.jsx (per row)
                          ┌──────────────────┐
                          │ title link       │
                          │ ⋮  ◀── on hover  │
                          └──────────────────┘
                                  │ click
                                  ▼
                          ChatRowMenu.jsx
                          ┌──────────────────┐
                          │ 📌 Pin    (disabled)
                          │ ✏  Rename       │───┐
                          │ 📁 Change …  (d)│   │
                          │ 📁ˣ Remove … (d)│   │
                          │ 🗑  Delete      │   │
                          └──────────────────┘   │
                              │                  │
              ┌───────────────┘                  │
              ▼                                  ▼
      RenameChatModal.jsx              DeleteChatModal.jsx
      ┌───────────────────┐            ┌───────────────────────┐
      │ Rename chat       │            │ Delete chat?          │
      │ [<title>____]     │            │ This will permanently │
      │ Cancel  Save      │            │ delete "<title>" and  │
      └───────────────────┘            │ remove it from the    │
                                       │ AI's memory…          │
                                       │ Cancel  Delete (red)  │
                                       └───────────────────────┘
              │                                  │
              ▼                                  ▼
      api.patchChat(id,title)         api.deleteChat(id)
              │                                  │
              ▼                                  ▼
       PATCH /api/chats/{id}         DELETE /api/chats/{id}
       (existing)                    (new)
                                              │
                                              ├─► DDB: delete msg rows + chat-index row
                                              └─► AgentCore: list_events + delete_event (best-effort)
```

All five popover items share the same `Modal.jsx` primitive (Esc + backdrop close, focus trap).
The popover itself follows the existing `<div className="backdrop"/> + <div className="pop"/>` pattern used by `ModelPicker`, `AttachMenu`, and `AccountPopover` per CLAUDE.md §UI conventions.

## Component changes

### Backend

- `src/channel/api/chats.py`
  - New `DELETE /api/chats/{chat_id}` → 204 No Content.
  - Ownership guard via existing `_load_owned_chat(chat_id, jwt_sub)` — returns 404 (not 403) on mismatch, matching the existing convention to avoid leaking chat existence.
  - Flow:
    1. Load chat with ownership check.
    2. `storage.delete_chat(user_id, chat)` — handles all DDB writes.
    3. Best-effort AgentCore wipe: page through `bedrock-agentcore.list_events(memoryId, actorId, sessionId=chat_id)` and call `delete_event` per event. Wrap in `try / except → log + EMF counter (`ChatDeleteMemoryWipeFailures`) + swallow`. DynamoDB is the source of truth for chat existence; an AgentCore failure must not fail the user's delete.
    4. Return 204.
  - No new env vars. No new config.

- `src/channel/storage.py`
  - New `delete_chat(user_id, chat) -> None`:
    - Query all message rows (`PK=CHAT#{chat_id}, SK begins_with MSG#`) with pagination, batch-delete in chunks of 25 (DDB batch limit).
    - Delete the chat-index row (`PK=USER#{user_id}, SK=CHAT#{created_at}#{chat_id}`).
    - Plain DDB writes — no condition expressions; if the chat is concurrently being written to we accept the race (the SSE stream's writes for an already-deleted chat will succeed against a now-orphaned `PK=CHAT#{chat_id}` partition; harmless).

- `src/channel/metrics.py`
  - New `record_chat_delete_memory_wipe_outcome(success: bool)` — counter, namespace `Channel`, no per-actor/per-chat dimensions (cardinality discipline, same as Phase 7c/7d/8a metrics).

- IAM: `bedrock-agentcore:DeleteEvent` is already in the policy (granted by Phase 7c per the existing `infra/stacks/channel_stack.py:381-387` block — verified). No new IAM action required. The CDK assertion test already asserts `DeleteEvent` is granted.

### Frontend

- `ui/src/components/Icon.jsx` — add four new icons:
  - `pencil` — Rename
  - `trash` — Delete
  - `more-vertical` — the per-row dots button (existing `dots` is horizontal; the menu reference uses vertical)
  - `folder-x` — "Remove from project" placeholder
  - `pin` (Pin) and `projects` (Change project) reused as-is.

- `ui/src/api.js` — new `deleteChat(chatId)` calling `DELETE /api/chats/{chat_id}` with the bearer JWT.

- `ui/src/hooks/useChatList.js` — new `deleteChat(chatId)`:
  - Optimistic local removal (`setChats(prev => prev.filter(c => c.chat_id !== chatId))`).
  - Call `api.deleteChat(chatId)`.
  - On failure, re-fetch the list to revert (same shape as existing `archiveChat`).

- `ui/src/hooks/ChatsContext.jsx` — re-export `deleteChat` from the context value (already exports `renameChat` / `archiveChat`).

- `ui/src/components/Modal.jsx` — **new** thin shared primitive (~30 LOC):
  - Backdrop + centered panel.
  - Owns Esc-to-close + backdrop-click-to-close.
  - Focus trap inside the panel while open (Tab cycles through panel focusable elements).
  - API: `<Modal open onClose>{children}</Modal>`.
  - Co-located test: `Modal.test.jsx`.

- `ui/src/app/ChatRowMenu.jsx` — **new** popover anchored to a sidebar row:
  - Props: `chatId`, `chatTitle`, `anchorRect` (DOMRect from `getBoundingClientRect()`), `onClose`, `onRename`, `onDelete`.
  - Renders `<div className="backdrop" />` (captures outside clicks) + `<div className="pop chat-row-menu" />` positioned by `anchorRect`.
  - Items in order: Pin (disabled), Rename, Change project (disabled), Remove from project (disabled), Delete (danger).
  - Disabled items: `aria-disabled="true"`, faded, `title="Coming soon"`, click handler is a no-op.
  - Esc key handler closes the popover.
  - Co-located test: `ChatRowMenu.test.jsx`.

- `ui/src/app/RenameChatModal.jsx` — **new**:
  - Props: `open`, `chatId`, `currentTitle`, `onClose`, `onSaved`.
  - Heading "Rename chat", text input pre-filled with `currentTitle`, **all text selected on mount**.
  - Save button disabled when input is empty or equals `currentTitle`.
  - Enter → submit (if valid). Esc/backdrop → cancel.
  - Save calls `chats.renameChat(chatId, newTitle)` from `useChats()`; modal closes on success. On API error, shows inline error line and leaves the modal open.
  - Co-located test: `RenameChatModal.test.jsx`.

- `ui/src/app/DeleteChatModal.jsx` — **new**:
  - Props: `open`, `chatId`, `chatTitle`, `isActive`, `onClose`.
  - Heading "Delete chat?", body `This will permanently delete "{chatTitle}" and remove it from the AI's memory. This action cannot be undone.`.
  - Buttons: Cancel (secondary, default focus), Delete (primary, **danger-red**).
  - Enter → confirm (but focus is on Cancel by default, so a stray Enter dismisses; user must Tab to Delete first). Esc/backdrop → cancel.
  - On confirm: if `isActive`, `navigate("/app")` BEFORE calling delete (prevents Conversation.jsx flashing 404). Then `chats.deleteChat(chatId)`. Modal closes.
  - Co-located test: `DeleteChatModal.test.jsx`.

- `ui/src/app/Sidebar.jsx`:
  - Each `.recent` row gets a `<button className="recent-menu-btn">` with `<Icon name="more-vertical" />`.
  - Sidebar owns `{ openChatId, anchorRect }` state; click on a menu button sets it; `ChatRowMenu` consumes it.
  - `onRename` opens `RenameChatModal` with the row's `chatId` and current title.
  - `onDelete` opens `DeleteChatModal` with the row's `chatId`, title, and `isActive = (activeChatId === chatId)`.
  - Co-located test additions in `Sidebar.test.jsx`.

- `ui/src/styles/app.css`:
  - `.recent-menu-btn { opacity: 0; transition: opacity .12s ease; }`
  - `.recent:hover .recent-menu-btn, .recent-menu-btn:focus-visible { opacity: 1; }`
  - `.chat-row-menu` — positioning and panel styling reusing the existing `--raised` / `--border` / `--shadow-pop` tokens.
  - `.chat-row-menu-item.disabled { opacity: .5; cursor: not-allowed; }`
  - `.chat-row-menu-item.danger { color: var(--danger); }`.
  - `.modal-backdrop` + `.modal` — first appearance in the codebase; tokens-driven styling.
  - `.btn-danger` — red-filled button style for the Delete confirm.

- `ui/src/styles/channel.css` — add a new `--danger` token (the codebase has no destructive-action colour yet; verified by grep). Light theme: `oklch(0.55 0.18 25)` (a muted brick red, harmonises with the existing OKLCH palette). Dark theme: `oklch(0.70 0.16 25)` (lifted for legibility on dark backgrounds, matches the existing dark-theme lift pattern used by `--accent`).

## Behavior + edge cases

- **Active chat deletion** — `DeleteChatModal` navigates to `/app` before invoking the API so the user never sees a flash of 404. The optimistic removal in `useChatList.deleteChat` immediately drops the row from the sidebar.
- **Concurrent SSE stream into a deleted chat** — the SSE POST writes to `PK=CHAT#{chat_id}` are unaware of the delete and will succeed against an orphaned partition. The chat won't reappear in the sidebar (no chat-index row). The orphaned message rows are not referenced by anything and will be cleaned up if the user retries delete (which they won't, because the chat is gone) — i.e. they leak until the AgentCore memory-events TTL eventually doesn't apply (DDB has no TTL on message rows). Acceptable for now; a follow-up could add a sweeper if it ever matters.
- **AgentCore wipe failure** — logged + counted via `record_chat_delete_memory_wipe_outcome(success=False)`. User sees a successful delete (chat is gone from app). Agent's recall will continue to surface those events until they age out or another mechanism cleans them. Trade-off explicitly chosen: a hung AgentCore must not block a user's delete.
- **Rename to empty / unchanged title** — Save is disabled; cannot submit.
- **Rename API failure** — modal stays open with inline error; optimistic update in `useChatList` already reverts on failure.
- **Popover off-screen** — `anchorRect` positioning clamps to viewport. Edge case: short sidebar with the bottom-most row's menu would open below the viewport; positioning logic checks `anchorRect.bottom + menuHeight > window.innerHeight` and flips to opening upward.

## Validation

### Layer 1 — Unit + integration (CI gate)

`uv run inv pre-push` covers:
- `tests/unit/test_chats_api.py` — DELETE returns 204; ownership 404; all message rows deleted; chat-index row deleted; AgentCore `delete_event` called per event; AgentCore failure swallowed + metric increments; no leak of failure to the user.
- `tests/unit/test_storage.py` — `delete_chat` paginates message-row deletion correctly (test with 26+ messages crosses the batch boundary).
- `tests/unit/test_metrics.py` — `record_chat_delete_memory_wipe_outcome` signature is locked (no per-actor / per-chat dimensions).
- Frontend (`vitest`): `Modal.test.jsx`, `ChatRowMenu.test.jsx`, `RenameChatModal.test.jsx`, `DeleteChatModal.test.jsx`, `Icon.test.jsx`, `Sidebar.test.jsx` additions, `useChatList.test.js` additions.
- 100% coverage on the new files (project-wide gate).

### Layer 2 — Local end-to-end (Playwright)

`tests/e2e/test_chat_management.py` (new):
1. Create a chat by sending a message; assert the row appears in the sidebar.
2. Hover the row; assert `.recent-menu-btn` has `opacity: 1`.
3. Click the menu button; assert the popover renders all five items in the expected order with Pin, Change project, and Remove from project disabled.
4. Click Rename; assert the modal opens with the input pre-filled and selected; type a new title; press Enter; assert the sidebar row text updates AND `GET /api/chats/{id}` persists the new title.
5. Re-open the menu; click Delete; the confirm modal opens; click Delete; assert the row disappears AND `GET /api/chats/{id}` returns 404 AND the AgentCore session for `chat_id` has no events (`list_events` returns empty).

### Layer 3 — Dev sanity (after deploy)

Manual: create a chat on dev, rename it, then delete it. Verify in the AgentCore console that the session has no events. Out-of-band: no automated assertion — local Layer-2 is the rigorous gate.

## CHANGELOG entry (draft)

```
### Added

- Chat rename + delete from the sidebar. Hovering a chat in the
  Recents list reveals a ⋮ menu with Rename (pencil) and Delete
  (trash) actions. Delete is permanent — it wipes the chat from
  DynamoDB AND from AgentCore Memory so the agent stops recalling
  the deleted conversation in future chats. Pin, Change project,
  and Remove from project appear in the menu as disabled
  placeholders.
```

## CLAUDE.md updates

- `## Structure` block — add `Modal.jsx`, `ChatRowMenu.jsx`, `RenameChatModal.jsx`, `DeleteChatModal.jsx` to the `ui/src/components/` and `ui/src/app/` listings.
- `## UI conventions` — note the new `Modal` primitive: "All modal dialogs use `Modal.jsx` for backdrop + Esc + focus trap. Don't reinvent the modal shell."
- `## AgentCore Memory` `### Recall (Phase 8a; pivoted from 7d)` block — note that chat deletion now removes the chat's session events, so recall context shrinks accordingly.

## Deferred / out of scope

- Pin / Change project / Remove from project wiring (placeholders
  remain disabled until their respective features land).
- Bulk delete / "delete all chats" / "clear memory" buttons.
- Restore / undo / trash-bin for deleted chats — explicit product
  decision: delete is permanent and the modal makes that clear.
- Mobile / touch alternative to hover-to-reveal — defer to a general
  UX pass.
- Sweeper for orphaned message rows from concurrent SSE writes into
  a deleted chat — only worth building if observed in practice.
