# Channel MVP Phase 6d — Conversation + Mock Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the chat conversation view with a mock word-by-word streamer so the chat-app at `/app/c/:id` renders a realistic-feeling streamed reply plus an inline artifact card — without any real model call.

**Architecture:** Port the prototype's `useStream` hook to `useMockStream.js` (returns `{ turns, send, clear, loadSample }`, uses `setInterval` over `SAMPLE_REPLY` words). Build `Conversation.jsx` that renders user/assistant turns with a tiny markdown renderer (paragraphs, bold, ordered lists) and a per-assistant-turn message-actions row. Wire ChatHome's `onSend` to navigate to `/app/c/new` and Sidebar's recents to navigate to `/app/c/<id>`; the Conversation route reads the URL id, looks it up in `RECENTS`, and either calls `loadSample(title)` or — for the `new` id — relies on the previous-page send to have queued a pending message via `sessionStorage`.

**Tech Stack:** React 18 + React Router 6 (already wired), vitest, `@testing-library/react`. No new deps.

---

## File structure

| File | Responsibility |
| --- | --- |
| `ui/src/app/data.js` (modify) | Add `SAMPLE_REPLY` (long canned reply, exact prototype copy) + `SAMPLE_USER` (canned user prompt). |
| `ui/src/hooks/useMockStream.js` (new) | The mock streamer hook — owns turns array, `send`, `clear`, `loadSample`. |
| `ui/src/hooks/useMockStream.test.js` (new) | Hook tests (fake timers). |
| `ui/src/app/renderMarkdown.js` (new) | Pure helper: `renderMarkdown(text, streaming)` → array of `<p>` / `<ol>` elements, with a trailing `.cursor` span when `streaming` and on the last block. Also exports `renderInline` used internally. |
| `ui/src/app/renderMarkdown.test.jsx` (new) | Coverage for paragraphs, bold, ordered lists, streaming cursor. |
| `ui/src/app/Conversation.jsx` (new) | Renders the streamed turns. Reads `:id` from the URL; looks up `RECENTS` and calls `loadSample` for known ids, or consumes `sessionStorage["channel-pending-send"]` JSON for a brand-new conversation arriving from ChatHome. |
| `ui/src/app/Conversation.test.jsx` (new) | Renders user + assistant turns, asserts message-actions row appears only when not streaming. |
| `ui/src/app/ChatHome.jsx` (modify) | Replace `noOpSend` with a real send: serialise `{ text, atts, modelId, effort }` into `sessionStorage["channel-pending-send"]` and `useNavigate("/app/c/new")`. |
| `ui/src/app/ChatHome.test.jsx` (modify) | Update the existing `noOpSend` tests to assert the navigation + sessionStorage instead. |
| `ui/src/app/Sidebar.jsx` (modify) | Wire each recent button to `useNavigate(\`/app/c/${r.id}\`)`. |
| `ui/src/app/Sidebar.test.jsx` (modify) | Test that clicking a recent navigates. |
| `ui/src/App.jsx` (modify) | Swap the `AppConversation` placeholder for the real component: `<AuthGate><Shell><Conversation /></Shell></AuthGate>`. |
| `ui/src/App.test.jsx` (modify) | Drop the conversation-placeholder testid assertion (or replace with a real render check). |
| `CLAUDE.md` (modify) | Add `Conversation.jsx`, `useMockStream.js`, `renderMarkdown.js` to the file-tree comment in the Structure section. |

**Scope-boundary list** (for the `## Files to touch` block in the PR body once we open it):

- `ui/src/app/data.js`
- `ui/src/hooks/useMockStream.js`
- `ui/src/app/renderMarkdown.js`
- `ui/src/app/Conversation.jsx`
- `ui/src/app/ChatHome.jsx`
- `ui/src/app/Sidebar.jsx`
- `ui/src/App.jsx`
- `CLAUDE.md`

(Co-located `*.test.{js,jsx}` files are implicitly accepted by the agent-safe rules per CLAUDE.md §Special labels — no need to enumerate them.)

---

## Execution preconditions

- Branch off `origin/development`:
  ```bash
  git fetch origin
  git checkout -b feat/channel-mvp-phase-6d-conversation origin/development
  ```
- Verify the gate is green before starting any task: `uv run inv pre-push`. It should already pass on a fresh branch — if it doesn't, fix that *first*.
- Verify no `ui/src/app/Conversation.jsx` exists yet (would conflict): `ls ui/src/app/Conversation.jsx 2>&1 | grep -q "No such" && echo OK`.

---

## Task 1 — Add SAMPLE_REPLY + SAMPLE_USER to data.js

**Files:**
- Modify: `ui/src/app/data.js`
- Test: `ui/src/app/data.test.js` (new)

- [ ] **Step 1: Write the failing test**

Create `ui/src/app/data.test.js`:

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import { MODELS, EFFORTS, RECENTS, QUICK_ACTIONS, SAMPLE_REPLY, SAMPLE_USER } from "./data.js";

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
    // Has paragraphs (\n\n), bold (**...**), and at least one ordered-list line.
    expect(SAMPLE_REPLY).toMatch(/\n\n/);
    expect(SAMPLE_REPLY).toMatch(/\*\*[^*]+\*\*/);
    expect(SAMPLE_REPLY).toMatch(/^\d+\.\s/m);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/app/data.test.js
```
Expected: 2/3 fail — the two `SAMPLE_*` assertions fail (`SAMPLE_USER` and `SAMPLE_REPLY` are undefined). The "re-exports" test passes.

- [ ] **Step 3: Implement — append to `ui/src/app/data.js`**

Append these two exports to `ui/src/app/data.js` (after the existing `QUICK_ACTIONS` block, preserving the existing copyright header at the top):

```js
// A canned assistant reply used by the mock-streaming engine
// (ui/src/hooks/useMockStream.js). Lifted verbatim from
// design-sources/app/data.jsx. The streamer splits this on whitespace
// and appends two words every 38ms to simulate token streaming.
export const SAMPLE_REPLY = `Good question — here's how I'd think about it.

The core trade-off is between **read latency** and **write amplification**. A columnar store wins big on analytical scans because it only touches the columns you query, but you pay for that on ingest.

A few concrete recommendations:

1. **Batch your writes.** Buffer events for 5–10 seconds and flush in bulk. Columnar formats hate row-at-a-time inserts.
2. **Partition by time, then by tenant.** Most of your queries are time-bounded, so this prunes the search space dramatically before any column is read.
3. **Keep a hot row-store tail.** Serve the last few minutes from the existing row store and merge at query time — users never notice the seam.

Want me to sketch the ingestion buffer as a small artifact you can drop into the pipeline?`;

// The canned user prompt that pairs with SAMPLE_REPLY when `loadSample`
// rehydrates a recent conversation.
export const SAMPLE_USER =
  "We are moving our events pipeline to a columnar store. What should I watch out for on the ingest side?";
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ui && npx vitest run src/app/data.test.js
```
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/data.js ui/src/app/data.test.js
git commit -m "feat(channel-mvp): Phase 6d — add SAMPLE_REPLY + SAMPLE_USER mock data"
```

---

## Task 2 — `renderMarkdown.js` helper

**Files:**
- Create: `ui/src/app/renderMarkdown.js`
- Create: `ui/src/app/renderMarkdown.test.jsx`

- [ ] **Step 1: Write the failing test**

Create `ui/src/app/renderMarkdown.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./renderMarkdown.js";

function wrap(nodes) {
  // renderMarkdown returns an array of <p>/<ol> elements — wrap them in a
  // <div> so testing-library can render the array.
  return render(<div>{nodes}</div>);
}

describe("renderMarkdown", () => {
  it("renders a single paragraph as a <p>", () => {
    const { container } = wrap(renderMarkdown("hello world", false));
    expect(container.querySelectorAll("p").length).toBe(1);
    expect(container.querySelector("p").textContent).toBe("hello world");
  });

  it("splits paragraphs on \\n\\n", () => {
    const { container } = wrap(renderMarkdown("one\n\ntwo\n\nthree", false));
    const ps = container.querySelectorAll("p");
    expect(ps.length).toBe(3);
    expect([ps[0].textContent, ps[1].textContent, ps[2].textContent]).toEqual(["one", "two", "three"]);
  });

  it("renders **bold** inline as <strong>", () => {
    const { container } = wrap(renderMarkdown("hi **there** friend", false));
    const strong = container.querySelector("strong");
    expect(strong).toBeTruthy();
    expect(strong.textContent).toBe("there");
    expect(container.querySelector("p").textContent).toBe("hi there friend");
  });

  it("renders an ordered-list block as <ol><li>", () => {
    const md = "1. first\n2. second\n3. third";
    const { container } = wrap(renderMarkdown(md, false));
    const ol = container.querySelector("ol");
    expect(ol).toBeTruthy();
    const lis = ol.querySelectorAll("li");
    expect(lis.length).toBe(3);
    expect(lis[0].textContent).toBe("first");
    expect(lis[2].textContent).toBe("third");
  });

  it("renders bold inside list items", () => {
    const { container } = wrap(renderMarkdown("1. **batch** writes", false));
    expect(container.querySelector("ol li strong").textContent).toBe("batch");
  });

  it("appends a blinking .cursor span to the last paragraph only when streaming", () => {
    const { container: streaming } = wrap(renderMarkdown("one\n\ntwo", true));
    const ps = streaming.querySelectorAll("p");
    expect(ps[0].querySelector(".cursor")).toBeNull();
    expect(ps[1].querySelector(".cursor")).toBeTruthy();

    const { container: done } = wrap(renderMarkdown("one\n\ntwo", false));
    expect(done.querySelector(".cursor")).toBeNull();
  });

  it("does not append the cursor inside an ordered-list block", () => {
    // The last block is an OL — streaming cursor only attaches to <p>.
    const { container } = wrap(renderMarkdown("intro\n\n1. one\n2. two", true));
    expect(container.querySelector(".cursor")).toBeNull();
  });

  it("renders an empty string as a single empty paragraph", () => {
    const { container } = wrap(renderMarkdown("", false));
    expect(container.querySelectorAll("p").length).toBe(1);
    expect(container.querySelector("p").textContent).toBe("");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/app/renderMarkdown.test.jsx
```
Expected: ALL fail — file doesn't exist.

- [ ] **Step 3: Implement `ui/src/app/renderMarkdown.js`**

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";

/**
 * Tiny markdown renderer for the mock-stream replies — paragraphs (split on
 * blank lines), **bold** spans, and 1. / 2. / 3. ordered lists. Ported
 * verbatim from design-sources/app/chat.jsx (`renderInline` + `renderMarkdown`).
 *
 * When `streaming` is true, a blinking `.cursor` span is appended to the
 * last block IFF that block is a paragraph — so the cursor doesn't appear
 * inside an OL where the design has no styling for it.
 */
export function renderInline(text, keyPrefix) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((seg, i) => {
    if (seg.startsWith("**") && seg.endsWith("**")) {
      return <strong key={`${keyPrefix}-${i}`}>{seg.slice(2, -2)}</strong>;
    }
    return <React.Fragment key={`${keyPrefix}-${i}`}>{seg}</React.Fragment>;
  });
}

export function renderMarkdown(text, streaming) {
  const blocks = text.split(/\n\n+/);
  const out = [];
  blocks.forEach((blk, bi) => {
    const lines = blk.split("\n");
    const isOl =
      lines.every((l) => /^\d+\.\s/.test(l.trim()) || l.trim() === "") &&
      lines.some((l) => /^\d+\.\s/.test(l.trim()));
    if (isOl) {
      out.push(
        <ol key={`b${bi}`}>
          {lines
            .filter((l) => l.trim())
            .map((l, li) => (
              <li key={li}>
                {renderInline(l.replace(/^\d+\.\s/, ""), `b${bi}l${li}`)}
              </li>
            ))}
        </ol>
      );
    } else {
      const last = bi === blocks.length - 1;
      out.push(
        <p key={`b${bi}`}>
          {renderInline(blk, `b${bi}`)}
          {streaming && last && <span className="cursor" />}
        </p>
      );
    }
  });
  return out;
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ui && npx vitest run src/app/renderMarkdown.test.jsx
```
Expected: 8/8 PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/renderMarkdown.js ui/src/app/renderMarkdown.test.jsx
git commit -m "feat(channel-mvp): Phase 6d — renderMarkdown helper (paragraphs/bold/OL/cursor)"
```

---

## Task 3 — `useMockStream` hook

**Files:**
- Create: `ui/src/hooks/useMockStream.js`
- Create: `ui/src/hooks/useMockStream.test.js`

- [ ] **Step 1: Write the failing test**

Create `ui/src/hooks/useMockStream.test.js`:

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMockStream } from "./useMockStream.js";
import { MODELS, SAMPLE_REPLY, SAMPLE_USER } from "../app/data.js";

const OPUS = MODELS[0]; // claude-opus-4-8
const EFFORT = "High";

describe("useMockStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts with an empty turns array", () => {
    const { result } = renderHook(() => useMockStream());
    expect(result.current.turns).toEqual([]);
  });

  it("send() appends a user turn + an empty streaming assistant turn immediately", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", [], OPUS, EFFORT));
    expect(result.current.turns.length).toBe(2);
    expect(result.current.turns[0]).toMatchObject({ role: "user", text: "hi", atts: [] });
    expect(result.current.turns[1]).toMatchObject({
      role: "assistant",
      text: "",
      streaming: true,
      model: `${OPUS.name} · ${EFFORT}`,
    });
  });

  it("send() streams 2 words every 38ms until SAMPLE_REPLY is fully emitted", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", [], OPUS, EFFORT));
    // One tick → 2 words appended.
    act(() => vi.advanceTimersByTime(38));
    const partial = result.current.turns[1].text;
    expect(partial.length).toBeGreaterThan(0);
    expect(SAMPLE_REPLY.startsWith(partial)).toBe(true);
    expect(result.current.turns[1].streaming).toBe(true);

    // Drain the rest.
    act(() => vi.advanceTimersByTime(38 * 200));
    expect(result.current.turns[1].text).toBe(SAMPLE_REPLY);
    expect(result.current.turns[1].streaming).toBe(false);
    expect(result.current.turns[1].artifact).toEqual({
      title: "ingestion-buffer.ts",
      kind: "Code · 64 lines",
      ic: "code",
    });
  });

  it("clear() drops all turns and stops any in-flight stream", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", [], OPUS, EFFORT));
    act(() => vi.advanceTimersByTime(38)); // partway through
    act(() => result.current.clear());
    expect(result.current.turns).toEqual([]);
    // Advancing more time should NOT bring turns back.
    act(() => vi.advanceTimersByTime(38 * 200));
    expect(result.current.turns).toEqual([]);
  });

  it("loadSample(title, model) populates a finished conversation in one tick", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.loadSample("Some recent", OPUS));
    expect(result.current.turns.length).toBe(2);
    expect(result.current.turns[0]).toMatchObject({ role: "user", text: SAMPLE_USER, atts: [] });
    expect(result.current.turns[1]).toMatchObject({
      role: "assistant",
      text: SAMPLE_REPLY,
      streaming: false,
      model: `${OPUS.name} · High`,
      artifact: { title: "ingestion-buffer.ts", kind: "Code · 64 lines", ic: "code" },
    });
  });

  it("loadSample() defaults the model label to 'Claude Opus 4.8 · High' when no model is passed", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.loadSample("Some recent"));
    expect(result.current.turns[1].model).toBe("Claude Opus 4.8 · High");
  });

  it("loadSample() interrupts an in-flight stream", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", [], OPUS, EFFORT));
    act(() => vi.advanceTimersByTime(38));
    act(() => result.current.loadSample("Some recent", OPUS));
    // Should be the canned conversation now, with no streaming flag.
    expect(result.current.turns[1].streaming).toBe(false);
    expect(result.current.turns[0].text).toBe(SAMPLE_USER);
    // And advancing time should not re-trigger the old stream.
    act(() => vi.advanceTimersByTime(38 * 200));
    expect(result.current.turns[1].text).toBe(SAMPLE_REPLY);
  });

  it("unmounting clears any active timer (no stray state updates)", () => {
    const { result, unmount } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", [], OPUS, EFFORT));
    act(() => vi.advanceTimersByTime(38));
    unmount();
    // If the timer leaked we'd see a React act() warning. As a positive
    // assertion, we just check no error is thrown when the clock moves.
    expect(() => vi.advanceTimersByTime(38 * 200)).not.toThrow();
  });

  it("send() defaults atts to [] when undefined is passed", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", undefined, OPUS, EFFORT));
    expect(result.current.turns[0].atts).toEqual([]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/hooks/useMockStream.test.js
```
Expected: ALL fail — file doesn't exist.

- [ ] **Step 3: Implement `ui/src/hooks/useMockStream.js`**

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useRef, useState } from "react";
import { SAMPLE_REPLY, SAMPLE_USER } from "../app/data.js";

const HARDCODED_ARTIFACT = {
  title: "ingestion-buffer.ts",
  kind: "Code · 64 lines",
  ic: "code",
};

/**
 * Mock streaming engine for the chat conversation. Ported verbatim from
 * design-sources/app/chat.jsx `useStream`. Real Bedrock streaming will
 * swap this hook for one with the same `{ turns, send, clear, loadSample }`
 * shape so the components that consume it don't have to change.
 *
 * `send(text, atts, model, effort)` pushes a user turn + an empty streaming
 * assistant turn, then advances 2 words every 38ms from SAMPLE_REPLY until
 * fully drained. The assistant turn gets a hardcoded artifact when done.
 *
 * `loadSample(title, model)` populates a finished conversation immediately
 * — used when the user clicks a Recent in the sidebar.
 */
export function useMockStream() {
  const [turns, setTurns] = useState([]);
  const timer = useRef(null);

  const stop = useCallback(() => {
    if (timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
  }, []);

  const send = useCallback(
    (text, atts, model, effort) => {
      setTurns((prev) => [
        ...prev,
        { role: "user", text, atts: atts || [] },
        {
          role: "assistant",
          text: "",
          model: `${model.name} · ${effort}`,
          streaming: true,
        },
      ]);
      const words = SAMPLE_REPLY.split(/(\s+)/);
      let i = 0;
      stop();
      timer.current = setInterval(() => {
        i += 2;
        const chunk = words.slice(0, i).join("");
        setTurns((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === "assistant") {
            next[next.length - 1] = { ...last, text: chunk };
          }
          return next;
        });
        if (i >= words.length) {
          stop();
          setTurns((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = {
              ...last,
              streaming: false,
              artifact: HARDCODED_ARTIFACT,
            };
            return next;
          });
        }
      }, 38);
    },
    [stop]
  );

  const clear = useCallback(() => {
    stop();
    setTurns([]);
  }, [stop]);

  const loadSample = useCallback(
    (_title, model) => {
      stop();
      const modelLabel = `${model ? model.name : "Claude Opus 4.8"} · High`;
      setTurns([
        { role: "user", text: SAMPLE_USER, atts: [] },
        {
          role: "assistant",
          text: SAMPLE_REPLY,
          model: modelLabel,
          streaming: false,
          artifact: HARDCODED_ARTIFACT,
        },
      ]);
    },
    [stop]
  );

  useEffect(() => stop, [stop]);

  return { turns, send, clear, loadSample };
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ui && npx vitest run src/hooks/useMockStream.test.js
```
Expected: 9/9 PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/hooks/useMockStream.js ui/src/hooks/useMockStream.test.js
git commit -m "feat(channel-mvp): Phase 6d — useMockStream hook (verbatim port of useStream)"
```

---

## Task 4 — `Conversation.jsx` component

**Files:**
- Create: `ui/src/app/Conversation.jsx`
- Create: `ui/src/app/Conversation.test.jsx`

The component reads `:id` from the URL via `useParams`. If the id matches a `RECENTS` entry, it calls `loadSample(title, model)` on mount. If the id is `"new"`, it reads the JSON-serialised pending payload from `sessionStorage["channel-pending-send"]`, calls `send(...)` once, and removes the key. Anything else (unknown id) renders an empty conversation.

- [ ] **Step 1: Write the failing test**

Create `ui/src/app/Conversation.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import Conversation from "./Conversation.jsx";
import { MODELS, SAMPLE_REPLY, SAMPLE_USER, RECENTS } from "./data.js";
import { __resetChannelPrefsForTest } from "../hooks/useChannelPrefs.js";

const REAL_RECENT_ID = RECENTS[0].id; // "r1"

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/c/:id" element={<Conversation />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("Conversation", () => {
  let storage;
  beforeEach(() => {
    vi.useFakeTimers();
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
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("renders the SAMPLE_USER + SAMPLE_REPLY pair when the URL :id matches a Recent", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(screen.getByText(SAMPLE_USER)).toBeTruthy();
    // Reply is split across multiple <p>/<li> elements — assert one of the
    // unique bold-list phrases is present.
    expect(screen.getByText(/Batch your writes/)).toBeTruthy();
  });

  it("shows the assistant-head with 'Channel' + model label when a recent is loaded", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(screen.getByText("Channel")).toBeTruthy();
    expect(screen.getByText(/Claude Opus 4.8 · High/)).toBeTruthy();
  });

  it("shows the inline artifact card on assistant turns once streaming is done", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(screen.getByText("ingestion-buffer.ts")).toBeTruthy();
    expect(screen.getByText("Code · 64 lines")).toBeTruthy();
  });

  it("shows the message-actions row (copy/retry/thumbs) on completed assistant turns", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(screen.getByTitle("Copy")).toBeTruthy();
    expect(screen.getByTitle("Retry")).toBeTruthy();
    expect(screen.getByTitle("Good")).toBeTruthy();
    expect(screen.getByTitle("Bad")).toBeTruthy();
  });

  it("clicking the message-actions buttons does not throw (no-op at Phase 6d)", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    for (const title of ["Copy", "Retry", "Good", "Bad"]) {
      expect(() => fireEvent.click(screen.getByTitle(title))).not.toThrow();
    }
  });

  it("clicking the inline artifact does not throw (no-op at Phase 6d; opens panel in 6e)", () => {
    renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(() => fireEvent.click(screen.getByText("ingestion-buffer.ts"))).not.toThrow();
  });

  it("/app/c/new consumes a pending send from sessionStorage and starts streaming", () => {
    storage["s:channel-pending-send"] = JSON.stringify({
      text: "hello",
      atts: [],
      modelId: MODELS[1].id, // sonnet
      effort: "Low",
    });
    renderAt("/app/c/new");
    // The user turn appears immediately.
    expect(screen.getByText("hello")).toBeTruthy();
    // The assistant header shows the picked model + effort.
    expect(screen.getByText(/Claude Sonnet 4.6 · Low/)).toBeTruthy();
    // The pending-send key is consumed.
    expect(storage["s:channel-pending-send"]).toBeUndefined();
    // The streaming cursor is present before time advances.
    const container = document.body;
    expect(container.querySelector(".cursor")).toBeTruthy();
    // After draining the timer, the cursor + streaming flag are gone and
    // the artifact card appears.
    act(() => vi.advanceTimersByTime(38 * 400));
    expect(container.querySelector(".cursor")).toBeNull();
    expect(screen.getByText("ingestion-buffer.ts")).toBeTruthy();
  });

  it("/app/c/new with no pending payload renders an empty conversation (no user/assistant turns)", () => {
    renderAt("/app/c/new");
    expect(screen.queryByText(SAMPLE_USER)).toBeNull();
    expect(document.body.querySelector(".turn")).toBeNull();
  });

  it("/app/c/<unknown-id> renders an empty conversation", () => {
    renderAt("/app/c/does-not-exist");
    expect(document.body.querySelector(".turn")).toBeNull();
  });

  it("/app/c/new with a malformed JSON payload renders empty (and clears the key)", () => {
    storage["s:channel-pending-send"] = "not-json{";
    renderAt("/app/c/new");
    expect(document.body.querySelector(".turn")).toBeNull();
    expect(storage["s:channel-pending-send"]).toBeUndefined();
  });

  it("/app/c/new with a payload referencing an unknown modelId falls back to the first MODELS entry", () => {
    storage["s:channel-pending-send"] = JSON.stringify({
      text: "hi",
      atts: [],
      modelId: "no-such-model",
      effort: "Max",
    });
    renderAt("/app/c/new");
    expect(screen.getByText(/Claude Opus 4.8 · Max/)).toBeTruthy();
  });

  it("renders a per-turn attachment chip when the pending payload includes atts", () => {
    storage["s:channel-pending-send"] = JSON.stringify({
      text: "look at this",
      atts: [{ kind: "file", name: "spec.pdf", ic: "doc" }],
      modelId: MODELS[0].id,
      effort: "High",
    });
    renderAt("/app/c/new");
    expect(screen.getByText("spec.pdf")).toBeTruthy();
  });

  it("uses the .convo > .convo-inner structure expected by app.css", () => {
    const { container } = renderAt(`/app/c/${REAL_RECENT_ID}`);
    expect(container.querySelector(".convo > .convo-inner")).toBeTruthy();
  });

  it("does NOT show the message-actions row while a turn is streaming", () => {
    storage["s:channel-pending-send"] = JSON.stringify({
      text: "hi",
      atts: [],
      modelId: MODELS[0].id,
      effort: "High",
    });
    renderAt("/app/c/new");
    // Mid-stream: actions row should be absent.
    expect(screen.queryByTitle("Copy")).toBeNull();
    act(() => vi.advanceTimersByTime(38 * 400));
    expect(screen.getByTitle("Copy")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/app/Conversation.test.jsx
```
Expected: ALL fail — file doesn't exist.

- [ ] **Step 3: Implement `ui/src/app/Conversation.jsx`**

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef } from "react";
import { useParams } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";
import Icon from "../components/Icon.jsx";
import { useMockStream } from "../hooks/useMockStream.js";
import { renderMarkdown } from "./renderMarkdown.js";
import { MODELS, RECENTS } from "./data.js";

const PENDING_KEY = "channel-pending-send";

function resolveModel(modelId) {
  return MODELS.find((m) => m.id === modelId) ?? MODELS[0];
}

function consumePendingSend() {
  let raw;
  try {
    raw = sessionStorage.getItem(PENDING_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* private mode etc — best-effort */
  }
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Streaming conversation pane. Translated from
 * design-sources/app/chat.jsx `Conversation`. The URL `:id` decides what
 * to render on mount:
 *
 *   - id === "new"  → consume sessionStorage[channel-pending-send] and
 *                     kick off send() once (ChatHome handed us a draft).
 *   - id ∈ RECENTS  → loadSample(title, model) — canned SAMPLE_USER +
 *                     SAMPLE_REPLY pair instantly.
 *   - otherwise     → empty conversation.
 *
 * Message-actions row (copy/retry/thumbs) is rendered per assistant turn
 * once that turn is no longer streaming. Buttons are no-ops at Phase 6d.
 */
export default function Conversation() {
  const { id } = useParams();
  const { turns, send, loadSample } = useMockStream();
  const ref = useRef(null);
  const kickedRef = useRef(false);

  // Auto-scroll to the bottom on any turns change (catches each stream tick).
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  useEffect(() => {
    if (kickedRef.current) return;
    kickedRef.current = true;

    if (id === "new") {
      const pending = consumePendingSend();
      if (!pending) return;
      const model = resolveModel(pending.modelId);
      send(pending.text, pending.atts || [], model, pending.effort);
      return;
    }
    const recent = RECENTS.find((r) => r.id === id);
    if (recent) {
      loadSample(recent.title, MODELS[0]);
    }
  }, [id, send, loadSample]);

  const noop = () => {};

  return (
    <div className="convo" ref={ref}>
      <div className="convo-inner">
        {turns.map((t, i) =>
          t.role === "user" ? (
            <div className="turn user" key={i}>
              {t.atts && t.atts.length > 0 && (
                <div
                  className="attaches"
                  style={{ justifyContent: "flex-end", marginBottom: 0 }}
                >
                  {t.atts.map((a, j) => (
                    <div className="chip" key={j}>
                      <span className="tile"><Icon name={a.ic} size={15} /></span>
                      <span>{a.name}</span>
                    </div>
                  ))}
                </div>
              )}
              <div className="bubble">{t.text}</div>
            </div>
          ) : (
            <div className="turn" key={i}>
              <div className="assistant-head">
                <ChannelMark size={20} />
                <span className="nm">Channel</span>
                <span className="mdl">{t.model}</span>
              </div>
              <div className="msg">{renderMarkdown(t.text, t.streaming)}</div>
              {t.artifact && (
                <div className="art-inline" onClick={noop}>
                  <div className="ah">
                    <span className="ic"><Icon name={t.artifact.ic} size={18} /></span>
                    <div>
                      <div className="at">{t.artifact.title}</div>
                      <div className="as">{t.artifact.kind}</div>
                    </div>
                    <span style={{ marginLeft: "auto", color: "var(--ink-faint)" }}>
                      <Icon name="expand" size={16} />
                    </span>
                  </div>
                </div>
              )}
              {!t.streaming && (
                <div className="msg-actions">
                  <button type="button" className="icon-btn" title="Copy" onClick={noop}>
                    <Icon name="copy" size={16} />
                  </button>
                  <button type="button" className="icon-btn" title="Retry" onClick={noop}>
                    <Icon name="refresh" size={16} />
                  </button>
                  <button type="button" className="icon-btn" title="Good" onClick={noop}>
                    <Icon name="thumb-up" size={16} />
                  </button>
                  <button type="button" className="icon-btn" title="Bad" onClick={noop}>
                    <Icon name="thumb-down" size={16} />
                  </button>
                </div>
              )}
            </div>
          )
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ui && npx vitest run src/app/Conversation.test.jsx
```
Expected: 13/13 PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/Conversation.jsx ui/src/app/Conversation.test.jsx
git commit -m "feat(channel-mvp): Phase 6d — Conversation component with streamed turns + artifact card"
```

---

## Task 5 — Wire ChatHome's Composer to navigate + queue the pending send

**Files:**
- Modify: `ui/src/app/ChatHome.jsx`
- Modify: `ui/src/app/ChatHome.test.jsx`

- [ ] **Step 1: Update tests first**

Open `ui/src/app/ChatHome.test.jsx`. Replace the "no-op send" tests with navigation + sessionStorage assertions. Find these two existing tests:

```jsx
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
```

Replace BOTH with:

```jsx
  it("sending a typed message stashes the payload in sessionStorage and navigates to /app/c/new", () => {
    render(<ChatHome />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "hi there" } });
    fireEvent.click(screen.getByTitle("Send"));
    const raw = sessionStorage.getItem("channel-pending-send");
    expect(raw).toBeTruthy();
    const payload = JSON.parse(raw);
    expect(payload.text).toBe("hi there");
    expect(payload.atts).toEqual([]);
    expect(payload.modelId).toBe("claude-opus-4-8");
    expect(payload.effort).toBe("High");
    expect(window.location.pathname).toBe("/app/c/new");
  });

  it("clicking a quick-action stashes the prefilled prompt and navigates to /app/c/new", () => {
    render(<ChatHome />);
    fireEvent.click(screen.getByRole("button", { name: "Write" }));
    const payload = JSON.parse(sessionStorage.getItem("channel-pending-send"));
    expect(payload.text).toMatch(/help me write something/i);
    expect(window.location.pathname).toBe("/app/c/new");
  });
```

Also add at the top of the existing `beforeEach`, after the matchMedia stub:

```jsx
    // ChatHome now writes to sessionStorage on send — stub it.
    vi.stubGlobal("sessionStorage", {
      getItem: (k) => storage[`s:${k}`] ?? null,
      setItem: (k, v) => { storage[`s:${k}`] = String(v); },
      removeItem: (k) => { delete storage[`s:${k}`]; },
    });
    // useNavigate needs a real-ish history; reset before each test.
    window.history.pushState({}, "", "/app");
```

ChatHome is currently rendered without a Router in the existing tests. Wrap the renders in a `<MemoryRouter initialEntries={["/app"]}>` to give `useNavigate` a context. Add this helper near the top of the describe block (above the `it` blocks):

```jsx
import { MemoryRouter } from "react-router-dom";

function renderChatHome() {
  return render(
    <MemoryRouter initialEntries={["/app"]}>
      <ChatHome />
    </MemoryRouter>
  );
}
```

And replace `render(<ChatHome />)` with `renderChatHome()` throughout the file. (The bare `render` import can stay — `renderChatHome` uses it.)

In the navigation assertions, `window.location.pathname` won't change with `MemoryRouter` — use `useLocation` injection instead. Replace the two new tests' navigation assertions with a route-listener approach:

```jsx
import { Routes, Route } from "react-router-dom";

function renderChatHomeWithLocationCatcher() {
  let lastPath = null;
  function PathCatcher() {
    const { pathname } = useLocation();
    lastPath = pathname;
    return null;
  }
  const result = render(
    <MemoryRouter initialEntries={["/app"]}>
      <Routes>
        <Route path="/app" element={<><ChatHome /><PathCatcher /></>} />
        <Route path="/app/c/:id" element={<PathCatcher />} />
      </Routes>
    </MemoryRouter>
  );
  return { ...result, getLastPath: () => lastPath };
}
```

(Import `useLocation`, `Routes`, `Route` from `react-router-dom` at the top of the file.)

Then both new tests use `getLastPath()` instead of `window.location.pathname`:

```jsx
  it("sending a typed message stashes the payload in sessionStorage and navigates to /app/c/new", () => {
    const { getLastPath } = renderChatHomeWithLocationCatcher();
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "hi there" } });
    fireEvent.click(screen.getByTitle("Send"));
    const raw = sessionStorage.getItem("channel-pending-send");
    expect(raw).toBeTruthy();
    const payload = JSON.parse(raw);
    expect(payload.text).toBe("hi there");
    expect(payload.atts).toEqual([]);
    expect(payload.modelId).toBe("claude-opus-4-8");
    expect(payload.effort).toBe("High");
    expect(getLastPath()).toBe("/app/c/new");
  });

  it("clicking a quick-action stashes the prefilled prompt and navigates to /app/c/new", () => {
    const { getLastPath } = renderChatHomeWithLocationCatcher();
    fireEvent.click(screen.getByRole("button", { name: "Write" }));
    const payload = JSON.parse(sessionStorage.getItem("channel-pending-send"));
    expect(payload.text).toMatch(/help me write something/i);
    expect(getLastPath()).toBe("/app/c/new");
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd ui && npx vitest run src/app/ChatHome.test.jsx
```
Expected: the two new tests FAIL (Composer still calls `noOpSend`). Other tests should pass.

- [ ] **Step 3: Implement the Composer wiring**

Open `ui/src/app/ChatHome.jsx`. Replace this:

```jsx
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
```

with:

```jsx
import { useNavigate } from "react-router-dom";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
```

Replace the `noOpSend` block:

```jsx
  // Phase 6c no-op send. Phase 6d swaps this for the mock streamer hook.
  function noOpSend(/* text, atts */) {
    /* no-op until Phase 6d */
  }
```

with:

```jsx
  const navigate = useNavigate();

  // Hand the draft off to Conversation via sessionStorage and route to
  // /app/c/new. Conversation reads the payload on mount, calls
  // useMockStream.send(...), and clears the key.
  function send(text, atts) {
    try {
      sessionStorage.setItem(
        "channel-pending-send",
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
```

Then update the two `onSend` usages from `noOpSend` to `send`:

```jsx
        onSend={send}
```

and

```jsx
            onClick={() => send(`Help me ${q.label.toLowerCase()} something.`, [])}
```

Also update the JSDoc near the top of the file. Replace this paragraph:

```jsx
 * Empty-state shown at /app when there's no active conversation.
 * Greeting + Composer + quick-action chips. Translated from
 * design-sources/app/chat.jsx `Home` function. The composer's onSend is
 * wired to a Phase-6c no-op; mock streaming arrives in 6d.
```

with:

```jsx
 * Empty-state shown at /app when there's no active conversation.
 * Greeting + Composer + quick-action chips. Translated from
 * design-sources/app/chat.jsx `Home` function. Sending stashes the
 * draft in sessionStorage and navigates to /app/c/new, where
 * Conversation consumes it and kicks off the mock streamer.
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd ui && npx vitest run src/app/ChatHome.test.jsx
```
Expected: ALL pass (the existing tests + 2 new ones).

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/ChatHome.jsx ui/src/app/ChatHome.test.jsx
git commit -m "feat(channel-mvp): Phase 6d — wire ChatHome composer to /app/c/new via sessionStorage"
```

---

## Task 6 — Wire Sidebar recents to navigate

**Files:**
- Modify: `ui/src/app/Sidebar.jsx`
- Modify: `ui/src/app/Sidebar.test.jsx`

- [ ] **Step 1: Add the failing test**

Append to `ui/src/app/Sidebar.test.jsx`, inside the existing `describe("Sidebar", …)`:

```jsx
  it("clicking a recent navigates to /app/c/<that-id>", () => {
    let lastPath = null;
    function PathCatcher() {
      const { pathname } = useLocation();
      lastPath = pathname;
      return null;
    }
    render(
      <MemoryRouter initialEntries={["/app"]}>
        <Routes>
          <Route path="/app" element={<><Sidebar /><PathCatcher /></>} />
          <Route path="/app/c/:id" element={<PathCatcher />} />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(screen.getByText("Home server backup strategy"));
    expect(lastPath).toBe("/app/c/r1");
  });
```

Make sure these imports are at the top of `Sidebar.test.jsx`:

```jsx
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
```

(`MemoryRouter` may already be imported; ensure `Route`, `Routes`, and `useLocation` are added.)

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/app/Sidebar.test.jsx -t "navigates to /app/c"
```
Expected: FAIL — the recent button currently has no `onClick`.

- [ ] **Step 3: Implement the navigation**

Open `ui/src/app/Sidebar.jsx`. Replace this:

```jsx
import { parseToken, TOKEN_KEY } from "../lib/auth.js";
```

with:

```jsx
import { useNavigate } from "react-router-dom";
import { parseToken, TOKEN_KEY } from "../lib/auth.js";
```

Just inside `export default function Sidebar(...)`, add `const navigate = useNavigate();` near the top of the function body (above the `const token = localStorage.getItem...` line):

```jsx
export default function Sidebar({ collapsed = false, onToggle = () => {} }) {
  const navigate = useNavigate();
  const [searching, setSearching] = useState(false);
```

Find the recent-row render:

```jsx
              <button type="button" key={r.id} className="recent">{r.title}</button>
```

Replace with:

```jsx
              <button
                type="button"
                key={r.id}
                className="recent"
                onClick={() => navigate(`/app/c/${r.id}`)}
              >
                {r.title}
              </button>
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ui && npx vitest run src/app/Sidebar.test.jsx
```
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/Sidebar.jsx ui/src/app/Sidebar.test.jsx
git commit -m "feat(channel-mvp): Phase 6d — Sidebar recents navigate to /app/c/<id>"
```

---

## Task 7 — Swap the App.jsx Conversation placeholder for the real component

**Files:**
- Modify: `ui/src/App.jsx`
- Modify: `ui/src/App.test.jsx`

- [ ] **Step 1: Update the App.test.jsx assertion first**

Open `ui/src/App.test.jsx`. Find the table-driven authed-route test that asserts the placeholder testid. There's an entry like:

```jsx
    ["/app/c/some-id",     "app-conversation"],
```

Remove that line (it's an authed-route placeholder check). Add a new dedicated test below the existing authed-route table:

```jsx
  it("/app/c/r1 renders the Conversation component (not the placeholder)", async () => {
    storage[TOKEN_KEY] = makeToken();
    window.history.pushState({}, "", "/app/c/r1");
    await act(async () => render(<App />));
    // The real component renders the canned SAMPLE_USER prompt, which the
    // placeholder never did.
    expect(screen.getByText(/columnar store/i)).toBeTruthy();
    // And the placeholder testid is no longer in the tree.
    expect(screen.queryByTestId("app-conversation")).toBeNull();
  });
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ui && npx vitest run src/App.test.jsx -t "Conversation component"
```
Expected: FAIL — App.jsx still renders the placeholder.

- [ ] **Step 3: Update App.jsx**

Open `ui/src/App.jsx`. At the top of the file (with the other app-side imports), add:

```jsx
import Conversation from "./app/Conversation.jsx";
```

Find this placeholder line:

```jsx
const AppConversation  = () => ph("app-conversation", "App: Conversation");
```

Delete it.

Then find the route declaration:

```jsx
          <Route path="/app/c/:id"          element={<AuthGate><AppConversation /></AuthGate>} />
```

Replace with:

```jsx
          <Route path="/app/c/:id"          element={<AuthGate><Shell><Conversation /></Shell></AuthGate>} />
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd ui && npx vitest run src/App.test.jsx
```
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add ui/src/App.jsx ui/src/App.test.jsx
git commit -m "feat(channel-mvp): Phase 6d — wire /app/c/:id route to Conversation in Shell"
```

---

## Task 8 — Update CLAUDE.md file tree

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the new files to the structure comment**

Open `CLAUDE.md`. Find the `ui/` block in the `## Structure` section. Locate this region (the `src/app/` listing):

```
│   │   ├── app/                   # Chat app routes (Phase 6c onward)
│   │   │   ├── data.js                                       # MODELS, EFFORTS, RECENTS, QUICK_ACTIONS
│   │   │   ├── Login.jsx, GoogleG.jsx                        # Centered Google sign-in
│   │   │   ├── Shell.jsx, Sidebar.jsx, AccountPopover.jsx    # Layout chrome
│   │   │   ├── Composer.jsx, ModelPicker.jsx, AttachMenu.jsx # Composer + its popovers
│   │   │   └── ChatHome.jsx                                  # Empty-state greeting
```

Replace it with:

```
│   │   ├── app/                   # Chat app routes (Phase 6c onward)
│   │   │   ├── data.js                                       # MODELS, EFFORTS, RECENTS, QUICK_ACTIONS, SAMPLE_REPLY, SAMPLE_USER
│   │   │   ├── Login.jsx, GoogleG.jsx                        # Centered Google sign-in
│   │   │   ├── Shell.jsx, Sidebar.jsx, AccountPopover.jsx    # Layout chrome
│   │   │   ├── Composer.jsx, ModelPicker.jsx, AttachMenu.jsx # Composer + its popovers
│   │   │   ├── ChatHome.jsx                                  # Empty-state greeting
│   │   │   ├── Conversation.jsx                              # Streamed-turns view + inline artifact card
│   │   │   └── renderMarkdown.js                             # Tiny markdown helper (paragraphs/bold/OL/cursor)
```

Find the `hooks/` block. Add `useMockStream.js` to the listing:

```
│   │   ├── hooks/
│   │   │   ├── useChannelPrefs.js  # theme/accent/density/shape/font/model/effort + siteTheme
│   │   │   ├── useMockStream.js    # Mock chat streamer (Phase 6d; swapped for real Bedrock later)
│   │   │   └── useRelativeTime.js
```

(Note the existing `useChannelPrefs.js` comment still mentions `siteTheme` — leave that string alone, it's outside this PR's scope. A separate doc-sync sweep can clean it.)

- [ ] **Step 2: Run the copyright-header linter**

```bash
uv run python scripts/check_copyright.py
```

Expected: PASS (no header errors).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): list Phase 6d files (Conversation, useMockStream, renderMarkdown)"
```

---

## Task 9 — Run the full gate and self-review

- [ ] **Step 1: Run pre-push**

```bash
uv run inv pre-push
```

Expected: all stages pass — ruff/mypy/copyright/pytest-unit + integration + vitest. 100% coverage on both Python and JS.

- [ ] **Step 2: Local smoke check (5 minutes — UI conventions §2)**

```bash
uv run inv dev --seed
```

Visit `http://localhost:5173/app/login?test_email=you@example.com` (bypass), then:

1. From `/app`, type "hi" in the composer and hit Enter. You should land on `/app/c/new` and see a user bubble + Channel streaming a reply word-by-word with a blinking cursor. After ~5 seconds, the reply finishes and the `ingestion-buffer.ts` artifact card + the copy/retry/thumbs row appear.
2. Click a recent in the sidebar (e.g. "Home server backup strategy"). The URL becomes `/app/c/r1` and the canned SAMPLE_USER/SAMPLE_REPLY pair renders instantly.
3. Click "New chat" — the URL goes back to `/app` and ChatHome appears empty.

If any of these don't behave as described, stop and fix before the PR.

- [ ] **Step 3: If smoke passed, prep the PR (per CLAUDE.md Opening a PR)**

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD   # must show ONLY your commits
git push -u origin feat/channel-mvp-phase-6d-conversation:feat/channel-mvp-phase-6d-conversation
```

Then `gh pr create --base development …` with `Closes` + the `## Files to touch` block listing exactly the 8 production files above.

---

## Self-review against the spec

**Spec coverage** (lines 236-250 and 307-313):
- `useMockStream` returns `{turns, send, clear, loadSample}` → Task 3 ✓
- `send()` appends a user turn + empty streaming assistant turn, then `setInterval` 2 words / 38ms over SAMPLE_REPLY → Task 3 ✓
- On completion the assistant turn gets the hardcoded artifact `{title: 'ingestion-buffer.ts', kind: 'Code · 64 lines'}` → Task 3 ✓ (also includes the `ic: 'code'` field used by the inline artifact card)
- `loadSample(title)` populates a finished conversation when a recent is clicked → Task 3 + Task 6 ✓
- `Conversation.jsx` renders streamed turns + inline artifact card → Task 4 ✓
- Markdown rendering (paragraphs, bold, ordered lists per prototype) → Task 2 ✓
- Message actions row (copy/retry/thumbs) → Task 4 ✓
- Routing: `/app/c/:id` loads `loadSample` for the matching recent → Task 4 ✓
- Replacing the Phase 6c no-op send → Task 5 ✓
- Sidebar recents wired (already exists in 6c, just navigation added) → Task 6 ✓

**Placeholder scan:** every code-changing step contains the actual content. No "TODO" / "fill in" / "implement later" leaks.

**Type consistency:**
- `useMockStream` exposes `{ turns, send, clear, loadSample }` — `Conversation.jsx` consumes only `{ turns, send, loadSample }` (correct — `clear` is unused at Phase 6d, exposed for Phase 6e onward).
- Artifact shape is `{ title, kind, ic }` everywhere: Task 3's hook return, Task 4's `t.artifact.title / kind / ic` reads, the Task 4 test's `toEqual` assertion. Consistent.
- Pending-send payload shape is `{ text, atts, modelId, effort }` everywhere: Task 4 reads, Task 5 writes, Task 5 test asserts. Consistent.
- `sessionStorage` key is `"channel-pending-send"` everywhere. Consistent.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-30-channel-mvp-phase-6d-conversation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, two-stage review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
