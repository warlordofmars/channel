# Server-side user preferences (Customize.jsx becomes real)

**Scope chosen 2026-06-02:** Foundation + Send on Enter + Suggest follow-ups
behavior toggles. Show reasoning trace is deferred to its own follow-up
issue — the underlying capability (extended-thinking on `BedrockModel` +
SSE event type for thinking deltas) isn't built yet.

## Goal

Make `/app/customize` actually do what it claims: persist visual prefs
across devices, and make two of the three Behavior toggles affect runtime
behavior.

## Architecture

### Storage

New DDB row pattern: `PK=USER#{user_id}, SK=PREFS`. Body carries a single
JSON blob of all preference keys. JSON-blob rationale: prefs are written
together, read together, never queried-by-attribute. Schema evolution
just adds keys; older rows missing the new keys get defaults at hydration.

Field set (all optional in storage; defaults applied on read):

| Key | Type | Default | Notes |
|---|---|---|---|
| `theme` | str | `"dark"` | existing localStorage pref |
| `accent` | str | `"42"` | OKLCH hue (existing) |
| `density` | str | `"cozy"` | existing |
| `shape` | str | `"soft"` | existing |
| `font` | str | `"figtree"` | existing |
| `model` | str | `"claude-opus-4-6"` | existing |
| `effort` | str | `"High"` | existing — backend wiring is its own issue |
| `send_on_enter` | bool | `true` | NEW; behavior toggle |
| `show_reasoning` | bool | `false` | NEW; persisted but not wired (Show-reasoning is a separate feature; the toggle persists so users can pre-opt-in) |
| `suggest_followups` | bool | `true` | NEW; behavior toggle |

Last-writer-wins for conflicts (web vs desktop racing on the same user).
v1 doesn't need versioning; the prefs surface is small enough that the
race window is uninteresting.

### API

New module `src/channel/api/prefs.py`:

- `GET /api/me/prefs` → 200 `{prefs: {...}}` with defaults merged in.
  Returns defaults-only when no row exists. No 404 for "first time" —
  defaults are the truth.
- `PUT /api/me/prefs` → 204. Body is `{prefs: {...}}`. Partial updates
  allowed: server merges body into stored row (or initializes if absent).
  Unknown keys are rejected (422) to prevent silent client-version drift.

Both routes gated by `require_mgmt_user` (same pattern as `chats.py`).
Mount in `src/channel/api/main.py`.

### Frontend hook

`useChannelPrefs.js` gains server-sync behavior, but keeps its existing
shared-snapshot store unchanged. New flow:

1. **Module init**: snapshot is built from localStorage + DEFAULTS (today's
   behavior). Server fetch hasn't happened yet, so the very first render
   is local-only.
2. **On first hook mount in the tree**: a module-level `hydrateFromServer()`
   fires once. It calls `GET /api/me/prefs`, merges the result into the
   snapshot (server wins over localStorage), and writes the server values
   back to localStorage so a refresh after first paint still shows them
   without an API call.
3. **On each `setPref`**: updates the snapshot + localStorage (existing
   behavior), then enqueues a debounced PUT (200ms) of the changed key.
   PUT errors are swallowed — localStorage stays canonical; we don't
   block the UI on network.

The debounce key is per-pref-name so e.g. rapid accent toggling
collapses to one PUT, but a theme + accent change in the same window
still PUTs both keys.

`hydrateFromServer()` is gated to only run when a mgmt JWT is present —
the marketing site mounts `useChannelPrefs` (for site theme) and must
not call /api endpoints there.

### Send on Enter wire-up

`Composer.jsx` keydown handler reads `prefs.sendOnEnter`:

- **`true`** (default): Enter → submit; Shift+Enter → insert newline.
- **`false`**: Enter → insert newline; Cmd/Ctrl+Enter → submit.

Mirrors Slack / Discord / GitHub PR comment convention. No platform-
detection branching beyond Cmd vs Ctrl (use `e.metaKey || e.ctrlKey`).

### Suggest follow-ups

**Backend.** In `src/channel/api/chats.py`'s SSE stream, after the
`done` event fires for an assistant turn, check the user's
`suggest_followups` pref (fetched once at stream start to avoid a second
DDB read per turn). If true, invoke a Haiku one-shot `Agent` (same
pattern as the auto-titler in `build_titler_agent`) prompting it to
generate 2-3 plain-text follow-up prompts. Emit a new SSE event:

```
event: follow_ups_suggested
data: {"chat_id": "...", "message_id": "...", "suggestions": ["...", "...", "..."]}
```

Kill-switch: `STARTER_FOLLOWUPS_ENABLED=0` skips the whole block.

Failure mode: log + EMF counter `FollowupGenFailures` + swallow. No
visible regression to the chat reply when generation fails.

**Frontend.** `useChatStream` handles the new event, exposing
`followUps` on the last assistant turn. `Conversation.jsx` renders a chip
row below the message (only the last turn, only when chips exist).
Clicking a chip drops its text into the composer (does NOT auto-send) —
gives the user a chance to edit before sending.

### Customize.jsx

Replace `BehaviorRow`'s local `useState` with three hook fields:

```jsx
const prefs = useChannelPrefs();
const BEHAVIOR_ROWS = [
  ["Send on Enter", "...", "sendOnEnter"],
  ["Show reasoning trace", "...", "showReasoning"],   // persists but not wired yet
  ["Suggest follow-ups", "...", "suggestFollowups"],
];
```

Show reasoning trace stays in the UI as a persistable toggle so users
can opt-in ahead of the feature landing — but a small `disabled` or
"coming soon" affordance signals it isn't live. (Decided: leave the
toggle live; persisting a not-yet-wired pref costs nothing.)

## Out of scope (for this PR)

- **Effort plumbing** — accepted as a separate follow-up issue.
- **Show reasoning trace runtime behavior** — separate issue (needs
  extended-thinking on `BedrockModel` + new SSE event type).
- **Per-workspace prefs override** — workspaces are a future epic.
- **Conflict resolution beyond LWW** — not needed at this scale.

## Testing

- Unit: `Prefs` model defaults, `get_prefs` / `put_prefs` roundtrip,
  GET defaults-only, GET with row, PUT partial update, PUT unknown-key
  rejection, follow-up generator failure swallowed.
- Frontend: hydrate-from-server flow, debounced PUT, server-wins on
  conflict, Composer Enter / Cmd+Enter both keydown paths, Conversation
  chip rendering + click-drops-into-composer, Customize toggle round-
  trip.
- Hook tests stub fetch with mocked `/api/me/prefs` responses; no real
  HTTP.

## Acceptance

- Toggle theme in web → refresh desktop → desktop reflects the change.
- Toggle Send on Enter off → Enter inserts newline; Cmd+Enter submits.
- Toggle Suggest follow-ups on → after each reply, 3 chips appear
  beneath; clicking populates composer.
- Toggle Show reasoning trace on → persists across refresh; no other
  visible effect yet.
