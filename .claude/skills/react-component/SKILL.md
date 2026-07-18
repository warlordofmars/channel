---
name: react-component
description: Conventions for adding or editing a React component in the Channel management UI — hand-rolled primitives, CSS-variable tokens, the Icon.jsx stroke set (no Tailwind, no shadcn, no lucide), co-located vitest tests, and the v8 coverage gotchas (jsdom hex→rgb, fake-timers timing, anonymous functions).
status: full
triggers:
  paths:
    - "ui/src/**/*.jsx"
    - "ui/src/**/*.css"
  areas:
    - "ui"
---

# react-component

Adding or editing a React component in the Channel management UI
follows seven conventions. Each is mechanically checkable against
the existing `ui/src/` code; the canonical examples are cited
inline by file.

The conventions exist because the UI ships through three review
gates — local pre-push, CI (vitest at 100% coverage), and the
`code-reviewer` agent — and each gate has caught the same
recurring slip more than three times: hardcoded colours, missing
co-located tests, emoji-as-icon, anonymous handlers that miss the
v8 counter, and fake-timers that block the initial render.

**Channel uses no Tailwind, no shadcn/ui, and no lucide-react.**
Styling is CSS-variable tokens plus semantic class names; icons
come from a hand-rolled stroke set. If you are porting a snippet
from another project, strip its utility classes and icon imports
first.

## 1. Components and primitives

Reusable UI lives directly under `ui/src/components/` as plain
`.jsx` files — there is no `ui/src/components/ui/` primitive layer
and no `class-variance-authority` / Radix `Slot` machinery. The
current shared set is:

- `ui/src/components/Icon.jsx` — the 24×24 stroke icon set (§3)
- `ui/src/components/Modal.jsx` — shared modal shell (backdrop +
  Esc dismissal); every destructive confirm and edit-in-place
  dialog uses it — don't reinvent the modal
- `ui/src/components/ErrorBoundary.jsx` — token-styled error
  fallback
- `ui/src/components/ChannelMark.jsx` — brand mark SVG
- `ui/src/components/AuthGate.jsx` — redirects `/app/*` to the
  login route when no JWT is present

Conventions:

- **`cn` from `@/lib/utils`** (`ui/src/lib/utils.js`) — a thin
  `clsx` wrapper. Merge class names through `cn(...)` rather than
  concatenating strings by hand. There is no `tailwind-merge`
  step; `cn` is `clsx` only.
- **Prefer an existing component over re-rolling one.** Need a
  dialog? Use `Modal.jsx`. Need an icon? Add a case to `Icon.jsx`
  (§3). Need a popover? Follow the established
  `<div className="backdrop" />` + `<div className="pop" />`
  pattern (ModelPicker / AttachMenu / AccountPopover in
  `ui/src/app/`) — the backdrop captures outside-clicks to close;
  the caller owns the `open` state.
- **User identity comes from the JWT.** Components that need the
  user's email or display name read the mgmt token from
  `localStorage[TOKEN_KEY]` via `parseToken` from
  `ui/src/lib/auth.js` — display name is the email's local-part
  unless a `name` claim is added later.

## 2. CSS variables — never hardcoded colours

All colours come from CSS custom properties (OKLCH) defined in
`ui/src/styles/channel.css`. Three relevant blocks live there:

- `:root { ... }` — global constants (e.g. `--accent-h`, the
  accent hue), radii, shadows, fonts.
- `[data-theme="light"] { ... }` — light-theme token values.
- `[data-theme="dark"] { ... }` — dark-theme overrides for the
  same names.

The core application colour tokens (the most common subset
component code reaches for):

| Token | Purpose |
| --- | --- |
| `--canvas` | Page background |
| `--raised` | Cards / panels / composer surface |
| `--raised-2` | Nested raised surface |
| `--ink` | Primary text |
| `--ink-soft` | Secondary text |
| `--ink-faint` | Tertiary / disabled text |
| `--border` | Hairline divider |
| `--border-soft` | Subtle divider |
| `--accent` | Brand accent |
| `--accent-ink` | Accent foreground / text variant |
| `--accent-soft` | Accent wash (selection, highlights) |

Consume tokens through the semantic class names defined in
`ui/src/styles/site.css` (marketing) and `ui/src/styles/app.css`
(chat app) — which reference the tokens internally — or via inline
`style` when the value is dynamic:

```jsx
// Semantic class from app.css / site.css (preferred)
<div className="composer" />

// Inline style — use when the value is computed at render time
<span style={{ color: "var(--ink-soft)" }} />
```

`code-reviewer` check 4 fails the build on any new hex / `rgb()` /
`hsl()` literal *consumed* at a use site in `*.css`, `*.jsx`, or
`*.js`. **Defining** a token's value in one of the theme blocks in
`ui/src/styles/channel.css` (or in
`docs-site/.vitepress/theme/style.css`, the docs site's
equivalent) is the one allowed shape — that is the legitimate way
a literal enters the codebase. The rule's intent is "no inline
literals at the use site".

Two slips that recur:

- **Chart series colours.** Recharts series colours are data, not
  chrome, but they should still come from the token set — add a
  `--chart-<name>` entry to the `[data-theme="light"]` /
  `[data-theme="dark"]` blocks and reference `var(--chart-<name>)`
  from the chart config rather than embedding a hex literal.
- **Hover / focus states.** Define the hovered colour as a token
  and reference it; never hardcode a hex in a `:hover` rule.

Theme, accent, density, shape, and font are centralised in
`ui/src/hooks/useChannelPrefs.js`. New components consume that hook
for preference state; they do **not** re-implement
`prefers-color-scheme` detection or the `data-theme` attribute
write — `useChannelPrefs` already owns both.

## 3. Icons — Icon.jsx, never emojis

All icons come from `ui/src/components/Icon.jsx` — a hand-rolled
24×24 stroke set that renders `currentColor` SVGs, so an icon
inherits the text colour of its surrounding context. **There is no
`lucide-react` import in the source tree.** Emoji used as a UI
element is `code-reviewer` check 5 `FAIL`.

The canonical usage shape (see any consumer, e.g.
`ui/src/app/Sidebar.jsx`):

```jsx
import Icon from "@/components/Icon.jsx";

<Icon name="search" size={18} />
```

Rules:

- **Add a glyph by adding a `case` to `Icon.jsx`.** The component
  is a `switch (name)` over stroke-path SVGs. If the glyph you
  need isn't there, add a case (keeping it in sync with the design
  source noted in the file header) rather than reaching for an
  emoji or a new icon dependency.
- **Colour via context, not a prop.** Because the stroke is
  `currentColor`, set the surrounding element's `color` (a token)
  and the icon follows. Don't pass a hardcoded colour.
- **Size via the `size` prop** (default 18); stroke weight via
  `stroke`.
- **Decorative vs. semantic.** A standalone icon button with no
  visible label needs an `aria-label="<verb>"` on the parent
  button; decorative glyphs beside a text label need no extra ARIA.

## 4. Co-located tests, 100% coverage

Every `.jsx` component under `ui/src/` ships with a
`<Component>.test.jsx` next to it. CI fails below 100% across all
four v8 metrics — `lines`, `functions`, `branches`, `statements` —
configured in `ui/vite.config.js` (`test.coverage.thresholds`, all
set to `100`).

The canonical layout:

```text
ui/src/components/Modal.jsx
ui/src/components/Modal.test.jsx
```

A component test covers, at minimum:

1. **Initial render** — assert the visible state for the default
   props branch.
2. **Conditional render branches** — assert each branch of a
   render-or-not decision (e.g. `Modal` renders `null` when
   `open` is false, its children when true).
3. **Event handlers** — drive each `onClick` / `onChange` /
   keyboard handler via `fireEvent` and assert the side effect.
4. **`useEffect` cleanups and side-channel events** — fire the
   relevant browser event and assert the re-render or teardown
   (e.g. `Modal`'s `keydown` Escape listener calls `onClose`, and
   its cleanup removes the listener on unmount).

`ui/src/components/Modal.test.jsx` (a dialog with a `useEffect`
keydown listener) and `ui/src/components/AuthGate.test.jsx` (a
redirect-on-condition component) are the two worth opening
side-by-side when scaffolding a new test — together they cover the
render-branch, event-handler, and effect-cleanup patterns the
management UI leans on.

## 5. Vitest gotchas (load-bearing)

Three vitest / jsdom behaviours have failed CI more than once
each. The fix is mechanical; the diagnosis is not. These mirror
CLAUDE.md §"UI conventions".

### 5.1 jsdom normalises hex to `rgb(...)`

jsdom converts hex literals applied via `style="..."` (or inline
`style={{ ... }}`) to the `rgb(r, g, b)` form when read back via
`element.style.<prop>`. Asserting the hex string fails; asserting
the `rgb()` form passes:

```jsx
// jsdom returns the rgb() form on read-back, not the hex you set
expect(btn.style.background).toBe("rgb(232, 160, 32)");  // not "#e8a020"
```

The same rule applies to `getComputedStyle(...).color` and any
other style read-back: convert the hex to `rgb()` (an integer
triple, no leading zeros, single space after each comma) and
assert against that. (Note this applies to literal hex only —
`var(--token)` references stay as-is in the style attribute, so
assert `.toContain("var(--accent)")` when a component sets a token.)

### 5.2 `vi.useFakeTimers()` ordering

`vi.useFakeTimers()` replaces the global `setTimeout` /
`setInterval` so anything scheduled while fake timers are active
uses the virtual clock, and anything scheduled while real timers
are active uses the real clock. The two cases that matter for
component tests:

**Case A — timer scheduled at mount (most common).** The
component calls `setTimeout` / `setInterval` inside `useEffect`
during the initial render. Fake timers must be active **before**
`render(...)` for the timer to land on the virtual clock; if you
activate them after, the timer is already pinned to the real clock
and `vi.advanceTimersByTime(...)` will not fire it.

```jsx
it("fires the mount timer", async () => {
  vi.useFakeTimers();                            // activate FIRST
  await act(async () => render(<Component />));   // then mount
  await act(async () => vi.advanceTimersByTime(60_000));
  // ...assert the timer-driven update...
  vi.useRealTimers();                             // restore
});
```

This works because vitest 1.x's default `toFake` set does **not**
include `queueMicrotask` / `Promise.resolve` — so the microtasks
that drive React's mount still resolve. Real Channel suites that
lean on this: `ui/src/app/Conversation.test.jsx`,
`ui/src/app/views/ArtifactPanel.test.jsx`, and
`ui/src/hooks/useChannelPrefs.test.js` — open any of them for the
`beforeEach` / `afterEach` fake-timer pairing.

**Case B — initial test setup needs real-clock progress.** Rare.
React function components don't await `setTimeout` during render,
so the issue isn't render itself — it's the *test* needing
real-clock progress for one of its phases:

- A mount-time `useEffect` schedules work via a real-clock helper
  (e.g. a third-party SDK that internally uses `setTimeout`) and
  the assertion needs that work to land before fake timers take
  over.
- The test uses `waitFor` / `findBy*` for an initial assertion
  before driving the timer — those helpers poll on a real
  interval, and enabling fake timers around them stalls the poll.

Pattern: render under real timers, settle the initial state, then
switch to fake timers for the timer-driven assertion:

```jsx
await act(async () => render(<RareAnimatedComponent />));
await waitFor(() => expect(screen.getByText("ready")).toBeTruthy());
vi.useFakeTimers();
await act(async () => vi.advanceTimersByTime(1_000));
// ...assert timer-driven update...
vi.useRealTimers();
```

If you're unsure which case applies, start with Case A. Always
pair `vi.useFakeTimers()` with `vi.useRealTimers()` in a matching
`afterEach` (or at the end of the same `it`) so the next test
starts on real timers — mixing them across tests is a common cause
of flaky vitest runs.

### 5.3 Anonymous functions miss the v8 counter

vitest's v8 coverage provider counts every anonymous `function` or
arrow as a separately-coverable unit. An inline
`onClick={() => doThing()}` whose body is never invoked by a test
counts as one uncovered function — and a single uncovered function
fails the 100% gate.

Naming a handler does **not** make it covered. v8 still requires
the function body to actually execute under at least one test. What
naming buys you is fewer anonymous closures to chase: each named
handler is one named function the suite must drive, instead of N
inline arrows scattered across N call sites.

The fix is to extract handlers into named `function`s in the
component body, pass references at the call site, and ensure the
suite drives each named handler's body to completion:

```jsx
// AVOID — anonymous arrow; vitest v8 counts the body as its own function
<button onClick={() => setVisible(false)}>Close</button>

// PREFER — named handler; the suite must still trigger a click that
//          executes handleClose's body for v8 to mark it covered
function handleClose() {
  setVisible(false);
}

<button onClick={handleClose}>Close</button>
```

The same rule applies to event listeners registered inside
`useEffect`: name the effect callback, the listener, and the
cleanup so each gets its own v8 counter that the suite can drive.
[`example.jsx`](./example.jsx) demonstrates the full pattern — its
`scheduleAutoDismiss` / `onAutoDismiss` / `cleanup` names are each
individually exercised by the companion test block; the anonymous
equivalents would burn three uncovered-function counts and fail
the gate even though the visible behaviour is identical.

The exception is one-line array callbacks (`array.map(x =>
<Row key={x.id} {...x} />)`) — those are typically driven by the
same render the rest of the component test exercises and don't need
extraction.

## 6. Copyright header

Every new `.jsx` and `.js` file starts with the header from
CLAUDE.md §Copyright headers:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
```

When editing an existing file in a new calendar year, append the
year to the existing line — do not duplicate the header. The
`scripts/check_copyright.py` linter runs in `inv pre-push` and CI;
a missing or malformed header fails the build.

## 7. Pre-push gate

Run the same gate CI runs before opening a PR:

```bash
uv run inv pre-push
```

This runs lint + typecheck + unit tests + frontend tests with the
100% v8 coverage threshold. Component changes that touch auth flows
or management API endpoints additionally trigger `inv e2e-local`
per CLAUDE.md §"When to run local e2e tests".

## See also

- [`example.jsx`](./example.jsx) — copy-pasteable component +
  co-located test demonstrating every convention above.
- `ui/src/components/Icon.jsx` — the 24×24 stroke icon set (§3).
- `ui/src/components/Modal.jsx` + `.test.jsx` — canonical
  component-with-effects test pattern.
- `ui/src/styles/channel.css` — the OKLCH token definitions (§2).
- CLAUDE.md §"UI conventions" — the source of truth for
  CSS-variable / Icon.jsx / vitest rules.
- `.claude/agents/code-reviewer.md` §§4–5 — review-time
  enforcement of the CSS-variable and no-emoji conventions.
- ADR-0006 — skills system contract
  (`docs/adr/0006-skills-system.md`).
