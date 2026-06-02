# Conversation chat header — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ChatHeader` strip at the top of `Conversation.jsx` containing the chat title (clickable, opens `ChatRowMenu`) and a disabled placeholder share icon — matches Claude Desktop.

**Architecture:** New `ChatHeader.jsx` component reuses the existing `ChatRowMenu` / `RenameChatModal` / `DeleteChatModal` from Phase RD. Local state. Conversation passes the matched chat object. CSS appended to `app.css`.

**Tech Stack:** React (Vite), `react-router-dom`, Vitest + RTL.

**Spec:** `docs/superpowers/specs/2026-06-01-conversation-chat-header-design.md` — read first. The "title button is the whole menu trigger" and "share is disabled placeholder" decisions are locked.

---

## File map

**Create:**
- `ui/src/app/ChatHeader.jsx`
- `ui/src/app/ChatHeader.test.jsx`

**Modify:**
- `ui/src/app/Conversation.jsx` — look up `currentChat`, render `<ChatHeader />` above `.convo`.
- `ui/src/app/Conversation.test.jsx` — assert header renders with the title.
- `ui/src/styles/app.css` — append `.chat-hd*` rules.
- `CHANGELOG.md` — one `### Added` bullet.

---

## Task 1: `ChatHeader.jsx` component (TDD)

**Files:**
- Create: `ui/src/app/ChatHeader.jsx`
- Create: `ui/src/app/ChatHeader.test.jsx`

- [ ] **Step 1: Write the failing tests**

Create `ui/src/app/ChatHeader.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import ChatHeader from "./ChatHeader.jsx";

vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => ({
    renameChat: vi.fn(),
    deleteChat: vi.fn(),
    renameChatLocal: vi.fn(),
  }),
}));

function renderHeader(props) {
  return render(
    <MemoryRouter>
      <ChatHeader {...props} />
    </MemoryRouter>,
  );
}

describe("ChatHeader", () => {
  it("renders empty strip when chat is null", () => {
    const { container } = renderHeader({ chat: null });
    // Strip is rendered (so layout doesn't jump) but no title button.
    expect(container.querySelector(".chat-hd")).toBeTruthy();
    expect(container.querySelector(".chat-hd-title")).toBeNull();
  });

  it("renders the title text when chat is provided", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    expect(screen.getByRole("button", { name: /alpha/i })).toBeTruthy();
  });

  it("click on title opens ChatRowMenu", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    fireEvent.click(screen.getByRole("button", { name: /alpha/i }));
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /rename/i })).toBeTruthy();
  });

  it("click Rename opens RenameChatModal with the chat's title", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    fireEvent.click(screen.getByRole("button", { name: /alpha/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /rename/i }));
    expect(screen.getByText(/rename chat/i)).toBeTruthy();
    const input = screen.getByLabelText(/title/i);
    expect(input.value).toBe("alpha");
  });

  it("click Delete opens DeleteChatModal", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    fireEvent.click(screen.getByRole("button", { name: /alpha/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
    expect(screen.getByText(/delete chat\?/i)).toBeTruthy();
  });

  it("share button is disabled placeholder", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    const share = screen.getByLabelText(/share/i);
    expect(share.disabled).toBe(true);
    expect(share.getAttribute("aria-disabled")).toBe("true");
  });
});
```

- [ ] **Step 2: Verify failure**

```bash
cd ui && npx vitest run src/app/ChatHeader.test.jsx
```

Expected: import error — `ChatHeader.jsx` doesn't exist.

- [ ] **Step 3: Implement `ChatHeader.jsx`**

Create `ui/src/app/ChatHeader.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import Icon from "../components/Icon.jsx";
import ChatRowMenu from "./ChatRowMenu.jsx";
import RenameChatModal from "./RenameChatModal.jsx";
import DeleteChatModal from "./DeleteChatModal.jsx";

/**
 * Header strip at the top of the Conversation view. Shows the active
 * chat's title as a single big button — clicking anywhere on it opens
 * the same ChatRowMenu (Pin / Rename / Change project / Remove from
 * project / Delete) used by the sidebar row's ⋮ button.
 *
 * Renders an empty strip (no title button) when ``chat`` is null so
 * the layout doesn't jump while the chat list loads.
 *
 * Local state — does NOT route through Sidebar. The header's menu
 * and the sidebar row's menu are independent.
 */
export default function ChatHeader({ chat }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [anchorRect, setAnchorRect] = useState(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  if (!chat) {
    return (
      <div className="chat-hd">
        <div className="chat-hd-spacer" />
        <button
          type="button"
          className="icon-btn chat-hd-share"
          disabled
          aria-disabled="true"
          aria-label="Share (coming soon)"
          title="Coming soon"
        >
          <Icon name="arrow-up" size={16} />
        </button>
      </div>
    );
  }

  function openMenu(e) {
    setAnchorRect(e.currentTarget.getBoundingClientRect());
    setMenuOpen(true);
  }

  return (
    <div className="chat-hd">
      <button
        type="button"
        className="chat-hd-title"
        onClick={openMenu}
      >
        <span>{chat.title}</span>
        <Icon name="chevron-down" size={16} />
      </button>
      <button
        type="button"
        className="icon-btn chat-hd-share"
        disabled
        aria-disabled="true"
        aria-label="Share (coming soon)"
        title="Coming soon"
      >
        <Icon name="arrow-up" size={16} />
      </button>
      {menuOpen && (
        <ChatRowMenu
          open
          anchorRect={anchorRect}
          onClose={() => setMenuOpen(false)}
          onRename={() => setRenameOpen(true)}
          onDelete={() => setDeleteOpen(true)}
        />
      )}
      {renameOpen && (
        <RenameChatModal
          open
          chatId={chat.chat_id}
          currentTitle={chat.title}
          onClose={() => setRenameOpen(false)}
        />
      )}
      {deleteOpen && (
        <DeleteChatModal
          open
          chatId={chat.chat_id}
          chatTitle={chat.title}
          isActive={true}
          onClose={() => setDeleteOpen(false)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 4: Verify tests pass**

```bash
cd ui && npx vitest run src/app/ChatHeader.test.jsx --coverage
```

Expected: 6 PASS, 100% coverage on `ChatHeader.jsx`.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/ChatHeader.jsx ui/src/app/ChatHeader.test.jsx
git commit -m "feat(channel-rd): ChatHeader component — title button + disabled share"
```

---

## Task 2: Wire `ChatHeader` into `Conversation.jsx`

**Files:**
- Modify: `ui/src/app/Conversation.jsx`
- Modify: `ui/src/app/Conversation.test.jsx`

- [ ] **Step 1: Write the failing test**

In `ui/src/app/Conversation.test.jsx`, find the existing test setup. Add ONE new assertion to whichever test renders Conversation with a populated chat list, or write a new dedicated test:

```jsx
it("renders the chat header with the active chat's title", () => {
  // Use whatever the existing mock-useChats pattern is — read the
  // file first to match its shape. The test must arrange:
  //   - useChats returns { chats: [{chat_id: 'c1', title: 'Alpha'}, ...], ...stubs }
  //   - the route puts chatId=c1 in useParams
  // Then assert the title 'Alpha' shows in a .chat-hd-title button.
  // ...arrange...
  expect(screen.getByRole("button", { name: /Alpha/i })).toBeTruthy();
});
```

(Translate the arrange step to whatever the existing test harness uses — likely a `<MemoryRouter initialEntries=["/app/c/c1"]>` with `<Route path="/app/c/:id" element={<Conversation />} />` and a `useChats` mock.)

- [ ] **Step 2: Verify failure**

```bash
cd ui && npx vitest run src/app/Conversation.test.jsx -t "chat header"
```

Expected: FAIL — Conversation doesn't render a header.

- [ ] **Step 3: Modify `Conversation.jsx`**

Add imports at the top:
```jsx
import ChatHeader from "./ChatHeader.jsx";
```

In the body, near where `chats` / `chatId` are destructured, add:
```jsx
const currentChat = chats.chats.find((c) => c.chat_id === chatId) ?? null;
```

In the JSX return, the existing fragment starts:
```jsx
return (
  <>
    <div className="convo" ref={ref}>
      ...
    </div>
    <Composer ... />
  </>
);
```

Change to put `<ChatHeader />` first inside the fragment:
```jsx
return (
  <>
    <ChatHeader chat={currentChat} />
    <div className="convo" ref={ref}>
      ...
    </div>
    <Composer ... />
  </>
);
```

- [ ] **Step 4: Verify tests pass**

```bash
cd ui && npx vitest run src/app/Conversation.test.jsx --coverage
```

Expected: all PASS, 100% coverage on `Conversation.jsx`.

Also run the full Sidebar suite to make sure header / sidebar menu state stays independent:
```bash
cd ui && npx vitest run src/app/Sidebar.test.jsx
```

Expected: all PASS (no regression).

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/Conversation.jsx ui/src/app/Conversation.test.jsx
git commit -m "feat(channel-rd): mount ChatHeader at top of Conversation view"
```

---

## Task 3: CSS

**Files:**
- Modify: `ui/src/styles/app.css`

- [ ] **Step 1: Append rules**

Append to `ui/src/styles/app.css`:

```css
/* ----- Conversation chat header ----------------------------------- */
.chat-hd {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-soft);
  min-height: 44px;
}
.chat-hd-spacer { flex: 1; }
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

- [ ] **Step 2: Smoke-build**

```bash
cd ui && npx vite build --mode development 2>&1 | tail -3
```

Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add ui/src/styles/app.css
git commit -m "style(channel-rd): chat header layout + hover"
```

---

## Task 4: Layer-2 live verification (mandatory)

**Files:** none (verification step).

Per saved memory (`feedback_ui_changes_need_live_verification`), every CSS / className / layout change MUST be driven through the running app before pushing.

- [ ] **Step 1: Start the dev stack**

```bash
uv run inv dev
```

Wait for "Stack ready" (Uvicorn + Vite both up).

- [ ] **Step 2: Drive a Playwright probe**

Write `/tmp/verify_chatheader.py`:

```python
#!/usr/bin/env python3
"""Visual verification — Conversation chat header renders with title."""
from __future__ import annotations
import asyncio, html as html_lib, os, re, sys, time, uuid
import httpx
from playwright.async_api import async_playwright

UI = os.environ.get("STARTER_UI_URL", "http://localhost:5173")
API = os.environ.get("STARTER_API_URL", "http://localhost:8001")


def mint_jwt(email):
    r = httpx.get(f"{API}/auth/login", params={"test_email": email}, follow_redirects=False, timeout=15.0)
    r.raise_for_status()
    m = re.search(r"localStorage\.setItem\('starter_mgmt_token',\s*'([^']+)'\)", r.text)
    if not m:
        sys.exit("bypass not active")
    return html_lib.unescape(m.group(1))


async def main():
    tag = f"hdrverify-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    jwt = mint_jwt(f"{tag}@example.com")
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{API}/api/chats",
            headers={"Authorization": f"Bearer {jwt}"},
            json={"title": "Verification chat", "model_default": "claude-sonnet-4-6"},
        )
        r.raise_for_status()
        chat_id = r.json()["chat_id"]
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        await page.goto(f"{UI}/app")
        await page.evaluate("(t) => window.localStorage.setItem('starter_mgmt_token', t)", jwt)
        await page.goto(f"{UI}/app/c/{chat_id}")
        await page.wait_for_url(lambda url: f"/app/c/{chat_id}" in url, timeout=10_000)
        await page.wait_for_selector(".chat-hd-title", timeout=10_000)
        title_text = await page.locator(".chat-hd-title").inner_text()
        print(f"chat-hd-title text: {title_text!r}")
        await page.locator(".chat-hd-title").click()
        await page.wait_for_selector('[role="menu"]', timeout=5_000)
        print("menu opened OK")
        await page.screenshot(path="/tmp/chatheader-verify.png", full_page=False)
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
STARTER_UI_URL=http://localhost:5173 STARTER_API_URL=http://localhost:8001 uv run python /tmp/verify_chatheader.py
```

Expected stdout:
```
chat-hd-title text: 'Verification chat\n▼'   (or similar — title + chevron)
menu opened OK
```

Expected screenshot at `/tmp/chatheader-verify.png` showing the header rendered.

- [ ] **Step 3: Inspect the screenshot**

```bash
open /tmp/chatheader-verify.png
```

Confirm: header strip at the top with "Verification chat" + caret, share icon dimmed on the right, menu visible.

- [ ] **Step 4: Tear down**

Stop `inv dev` (Ctrl-C in its terminal).

No commit — verification only.

---

## Task 5: CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Append entry**

Add to `[Unreleased]` `### Added` (at the top of the subsection):

```markdown
- Conversation view now shows a header strip with the chat title and
  a menu trigger. Clicking the title opens the same Pin / Rename /
  Change project / Remove from project / Delete menu that the sidebar
  row's `⋮` button does. A placeholder share icon sits on the right
  for future wiring.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(channel-rd): CHANGELOG entry for chat header"
```

---

## Task 6: Pre-push + PR

- [ ] **Step 1: Pre-push gate**

```bash
uv run inv pre-push
```

Expected: `All checks passed!`. If ruff format complains on any new file, apply + recommit.

- [ ] **Step 2: File issue**

```bash
gh issue create \
  --title "feat(channel-rd): chat header (title + menu) on Conversation view" \
  --label "status:ready,priority:p2,size:s,enhancement,agent-safe" \
  --body "$(cat <<'EOF'
Add a chat header strip at the top of the Conversation view containing the chat title (clickable, opens the existing ChatRowMenu) and a disabled placeholder share icon. Matches Claude Desktop.

Design: docs/superpowers/specs/2026-06-01-conversation-chat-header-design.md
Plan: docs/superpowers/plans/2026-06-01-conversation-chat-header.md

## Files to touch

- ui/src/app/ChatHeader.jsx
- ui/src/app/ChatHeader.test.jsx
- ui/src/app/Conversation.jsx
- ui/src/app/Conversation.test.jsx
- ui/src/styles/app.css
- CHANGELOG.md
- docs/superpowers/specs/2026-06-01-conversation-chat-header-design.md
- docs/superpowers/plans/2026-06-01-conversation-chat-header.md
EOF
)"
```

- [ ] **Step 3: Push + PR + auto-merge**

```bash
git fetch origin
git rebase origin/development
git push -u origin feat/conversation-chat-header:feat/conversation-chat-header

gh pr create --base development \
  --title "feat(channel-rd): chat header on Conversation view" \
  --body "$(cat <<'EOF'
## Summary

New ``ChatHeader.jsx`` rendered at the top of the Conversation view. Clicking the chat title opens the same ``ChatRowMenu`` the sidebar row's ⋮ button uses; Rename / Delete reuse the existing modals. A placeholder share icon sits on the right (disabled, ``Coming soon`` tooltip).

Closes #<NNN-from-step-2>

## Test plan

- [x] Layer 1 — ``inv pre-push`` green. 6 new ChatHeader unit tests + Conversation assertion. 100% coverage on ``ChatHeader.jsx``.
- [x] Layer 2 — Playwright probe against local stack confirms ``.chat-hd-title`` renders with the correct title and clicking opens ``[role=menu]``. Screenshot attached.
- [ ] Layer 3 — after merge + dev deploy: open a chat on https://channel-dev.warlordofmars.net/app, confirm the header shows the title + caret + dimmed share icon.

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

- ✅ `ChatHeader.jsx` exists; renders empty strip when chat is null; renders title button + share placeholder when chat present.
- ✅ Click title opens ChatRowMenu; Rename / Delete open their modals.
- ✅ Conversation.jsx mounts ChatHeader above `.convo`.
- ✅ CSS appended; build succeeds.
- ✅ Layer-2 Playwright verification: header renders, menu opens, screenshot saved.
- ✅ 100% coverage on `ChatHeader.jsx`.
- ✅ CHANGELOG entry added.
- ✅ Dev verification post-deploy: header visible on a real chat.
