# Channel MVP — visuals-first design

**Date:** 2026-05-29
**Status:** Draft, pending user review
**Source:** Authoritative design handoff at `~/Downloads/design_handoff_channel/`
**Context:** Phase 6 of the channel fork onboarding (post-template). Replaces the
template's admin shell with the chat product designed by the handoff.

## Summary

Ship the **full marketing site (10 pages) plus a stubbed-out chat app shell
with all screens rendered** as Channel's first deploy. All visual surfaces
land. No Bedrock integration yet — the composer "sends" trigger a mock
word-by-word streamer over a canned reply. Google OAuth login stays real and
gates entry to the app.

This locks in the design's look, feel, and information architecture early so
backend work in subsequent phases can plug into a stable visual surface.

## Scope

### In scope (MVP)

**Marketing site:** Home, Product, Models, Pricing, Download, About, Blog,
Careers, Privacy, 404 — all 10 pages rendered, real copy from the design,
placeholder images marked for later replacement.

**Chat app (stubbed):** Login → Workspace shell (sidebar + main pane). All
screens render with mock data:
- Login (centered Google sign-in — real OAuth)
- Chat-Home (greeting + composer + quick actions)
- Chat-Conversation (mock streaming + inline artifact card)
- Projects (grid of cards from mock data)
- Project Detail (header + scoped composer + knowledge docs + chats list)
- Artifacts (list rows)
- Artifact viewer (right-hand slide-in panel with Code/Document/Chart/Data
  renderers)
- Customize (theme/accent/density/corners/typeface/default model/effort —
  visual toggles work, behavior toggles are UI-only)

**Visual system:** OKLCH color tokens, 5 accent hues, light/dark themes,
Figtree + JetBrains Mono typography, all radii/shadow/spacing tokens.

**Persisted user prefs (localStorage):** theme, accent hue, density, shape,
font, default model, default effort.

**Real Google OAuth login** — gates entry to `/app/*` routes. Uses the
existing `/auth/login` → `/auth/callback` → mgmt JWT flow from Phase 5.

### Out of scope (explicit deferrals)

- **Bedrock integration.** Composer's send button triggers a mock word-by-word
  streamer; no real model call. The model picker UI works (changes the label
  shown in the conversation header) but doesn't change behavior.
- **Conversation persistence.** Turns live in component state only. Refresh
  clears the conversation. Recents/Projects/Artifacts are static mock data
  copied from the prototype's `data.jsx`.
- **Server-synced user prefs.** Customize writes to localStorage only. No
  DynamoDB schema changes in this MVP.
- **Real artifact generation.** The inline artifact card is hardcoded (the
  same one the prototype shows). No markdown parsing or tool-calling.
- **Customize > Behavior toggles** ("Send on Enter", "Show reasoning trace",
  "Suggest follow-ups") render but no-op. Visual fidelity, no behavior.
- **Electron desktop app.** Web frame only. The design's `desktop` and `full`
  window frames are skipped — the prototype's web frame (browser chrome) is
  what ships. Even the chrome itself is omitted since real browsers provide
  it.
- **Update pill** ("Relaunch to update v2.4.1") — Electron-only concept,
  removed from the sidebar.
- **Pricing/billing backend.** Pricing page ships as marketing copy; all CTAs
  go to free signup. The account row's "Channel Max" plan label drops or
  becomes "Free" (TBD on what's least misleading).
- **Image content.** Marketing site uses solid-color placeholder blocks
  marked `data-image-slot` for later replacement with real images.

## Architecture

### Single Vite + React app

One `ui/` build, two route trees:

- **Marketing routes** (`/`, `/product`, `/models`, `/pricing`, `/download`,
  `/about`, `/blog`, `/careers`, `/privacy`, `*` for 404) — public, no auth.
- **App routes** (`/app/login`, `/app`, `/app/c/:id`, `/app/projects`,
  `/app/projects/:id`, `/app/artifacts`, `/app/customize`) — `/app/login` is
  public; everything else gates on a valid mgmt JWT.

CloudFront's existing SPA fallback (403/404 → `/index.html`) handles both
trees — already verified working at smoke test.

### CSS token layer

Port `design-sources/channel.css` verbatim to `ui/src/styles/channel.css`.
This file owns:
- All OKLCH custom properties (light + dark theme variants)
- Accent hue computation from `--accent-h`
- Radii / shadow / spacing tokens
- Font face loading (Figtree + JetBrains Mono via Google Fonts `<link>` in
  `index.html`, plus optional families as CSS variables exposed to Customize)
- Base resets and global element styles

Two additional sheets:
- `ui/src/styles/app.css` — app layout (sidebar, composer, conversation,
  views) — port from `design-sources/app/app.css`
- `ui/src/styles/site.css` — marketing layout — port from
  `design-sources/site/site.css`

**Tailwind decision:** uninstall Tailwind entirely as part of Phase 6a once
the existing admin-shell components that use it are deleted. Keeping two
paradigms (Tailwind + CSS-vars) doubles the styling vocabulary for no gain
when the design is fully spec'd in CSS vars. The uninstall removes
`tailwindcss`, `postcss`, `autoprefixer` deps, the `tailwind.config.js`,
`postcss.config.js`, and any `@tailwind` directives.

### Theme system

The existing `useTheme` hook is replaced by `useChannelPrefs` — a single hook
covering all seven pref dimensions (theme, accent, density, shape, font,
model, effort). Each writes to a localStorage key (`channel-{pref}`) and
applies a `data-{pref}` attribute to `<html>`. CSS selects on those
data-attrs. The marketing site uses a separate `channel-site-theme` key so
the site can default light while the app defaults dark.

## Component structure

```
ui/src/
├── App.jsx                          # router + routing tree
├── styles/
│   ├── channel.css                  # tokens, themes, fonts (from design-sources/channel.css)
│   ├── app.css                      # app layout (from design-sources/app/app.css)
│   └── site.css                     # marketing layout (from design-sources/site/site.css)
├── components/
│   ├── ChannelMark.jsx              # the logo SVG (vertical-bar mark + wordmark)
│   ├── Icon.jsx                     # the 24×24 stroke icon set (port from design-sources/app/icons.jsx)
│   └── ErrorBoundary.jsx            # kept from existing, restyled to new tokens
# Note: ConsentBanner.jsx removed in 6a — defer to whenever GA4 lands
# Note: ui/ shadcn primitives removed in 6a alongside Tailwind
├── hooks/
│   └── useChannelPrefs.js           # theme/accent/density/shape/font/model/effort persistence
├── marketing/
│   ├── Nav.jsx                      # sticky translucent nav (Product · Models · Pricing · Download)
│   ├── Footer.jsx                   # 3-column footer
│   ├── ThemeToggle.jsx              # light/dark toggle persisted to localStorage
│   ├── ImageSlot.jsx                # placeholder block with data-image-slot marker
│   └── pages/
│       ├── Home.jsx
│       ├── Product.jsx
│       ├── Models.jsx
│       ├── Pricing.jsx              # includes monthly/annual toggle
│       ├── Download.jsx
│       ├── About.jsx
│       ├── Blog.jsx
│       ├── Careers.jsx
│       ├── Privacy.jsx
│       └── NotFound.jsx
├── app/
│   ├── data.js                      # MODELS, EFFORTS, FONTS, RECENTS, PROJECTS, ARTIFACTS, QUICK_ACTIONS, SAMPLE_REPLY
│   ├── Login.jsx                    # centered Google sign-in
│   ├── Shell.jsx                    # workspace layout: <Sidebar /> + <main>{children}</main>
│   ├── Sidebar.jsx                  # 264px nav: New chat · Projects · Artifacts · Customize · Recents · account
│   ├── Composer.jsx                 # shared composer (textarea + attach + model picker + send)
│   ├── ModelPicker.jsx              # popover with model + effort
│   ├── AttachMenu.jsx               # composer's + button popover
│   ├── ChatHome.jsx                 # greeting + composer + quick actions
│   ├── Conversation.jsx             # streamed turns + inline artifact cards
│   ├── useMockStream.js             # word-by-word interval over canned reply
│   ├── views/
│   │   ├── Projects.jsx             # grid of project cards
│   │   ├── ProjectDetail.jsx        # scoped composer + knowledge docs + chats list
│   │   ├── Artifacts.jsx            # list of artifact rows
│   │   ├── ArtifactPanel.jsx        # right-hand slide-in viewer
│   │   └── Customize.jsx            # all the pref controls
│   └── account/
│       └── AccountPopover.jsx       # email + sign out
```

## Routes

```
/                    → marketing/pages/Home
/product             → marketing/pages/Product
/models              → marketing/pages/Models
/pricing             → marketing/pages/Pricing
/download            → marketing/pages/Download
/about               → marketing/pages/About
/blog                → marketing/pages/Blog
/careers             → marketing/pages/Careers
/privacy             → marketing/pages/Privacy

/app/login           → app/Login (centered Google sign-in)
/app                 → app/Shell wrapping app/ChatHome
/app/c/:id           → app/Shell wrapping app/Conversation
/app/projects        → app/Shell wrapping app/views/Projects
/app/projects/:id    → app/Shell wrapping app/views/ProjectDetail
/app/artifacts       → app/Shell wrapping app/views/Artifacts
/app/customize       → app/Shell wrapping app/views/Customize

*                    → marketing/pages/NotFound (branded 404)
```

Auth gate: visiting any `/app/*` route except `/app/login` without a valid
mgmt JWT redirects to `/app/login`. Login success redirects back to `/app`.
Sign-out clears the token and redirects to `/` (marketing home).

Invalid `/app/<unknown>` routes fall through to the marketing 404 page (same
`NotFound` component as the marketing catch-all). The app does not ship a
dedicated app-shaped 404 — the branded marketing 404 is sufficient.

## State management

**localStorage:**
- `starter_mgmt_token` — existing JWT key (kept)
- `channel-theme` — `light` | `dark`
- `channel-site-theme` — separate theme pref for marketing site (per design)
- `channel-accent` — hue value (42, 18, 150, 235, 300)
- `channel-density` — `cozy` | `compact`
- `channel-shape` — `soft` | `sharp`
- `channel-font` — font pairing key (`figtree`, `grotesk`, etc.)
- `channel-model` — default model id
- `channel-effort` — default reasoning effort
- `channel-pricing-cycle` — `monthly` | `annual` (marketing pricing toggle)

**URL state:** active route, active conversation id, active project id,
active artifact id (via search param on `/app/artifacts?artifact=:id` to open
the panel).

**In-memory (component state):** conversation turns, composer text/attaches,
open popovers (model picker, attach menu, account), sidebar collapse, mobile
nav toggle. Lost on refresh — that's the stub behavior.

**Mock data (static, imported from `app/data.js`):** MODELS, EFFORTS, FONTS,
RECENTS, PROJECTS, ARTIFACTS, QUICK_ACTIONS, SAMPLE_REPLY, SAMPLE_USER.
Lifted verbatim from the prototype.

## Mock streaming

`useMockStream` returns `{turns, send, clear, loadSample}`:
- `send(text, atts, model, effort)` appends a user turn and an empty
  assistant turn marked `streaming: true`, then begins a `setInterval` that
  appends two words every 38ms from `SAMPLE_REPLY`.
- On completion, the assistant turn flips `streaming: false` and gets an
  artifact hardcoded as `{title: 'ingestion-buffer.ts', kind: 'Code · 64
  lines'}` (matches the prototype).
- `loadSample(title)` populates a finished conversation when the user clicks
  a recent in the sidebar.

This is the prototype's `useStream` from `chat.jsx` ported verbatim. Real
Bedrock streaming swaps this hook out without touching the components that
consume it.

## Auth integration

The existing Phase 5 flow lands intact:
- `/auth/login` redirects to Google
- `/auth/callback` mints a mgmt JWT and stores it under `starter_mgmt_token`
- All `/api/*` endpoints require a valid Bearer mgmt JWT (no `/api/*`
  endpoints are called by the MVP — auth is gate-only)

New: an `AuthGate` component wraps `/app/*` routes (except `/app/login`).
It parses the token, validates `exp`, and either renders children or
redirects to `/app/login`. The existing `parseToken` / `isTokenValid` helpers
from `App.jsx` move into `lib/auth.js` for reuse.

## Phase sequencing

This MVP is ~xl per the CLAUDE.md size scale; split into 6 phases, each its
own PR:

**Phase 6a — Foundation** (size: m)
- Copy `design-sources/channel.css` verbatim to `ui/src/styles/channel.css`
- Wire fonts (Google Fonts `<link>` for Figtree + JetBrains Mono in
  `ui/index.html`)
- Add `ChannelMark.jsx` (port `ChannelMark` from `design-sources/app/icons.jsx`)
- Add `Icon.jsx` (port the 24×24 stroke icon set from the same file)
- Replace `useTheme` with `useChannelPrefs` (single hook, all seven prefs)
- Delete `UsersPanel.jsx`, `PageLayout.jsx`, `EmptyState.jsx` (and their
  tests). The template's `Dashboard.jsx` was already removed by an earlier
  scrub — verified absent at spec-write time.
- Uninstall Tailwind + PostCSS deps; remove their config files
- Rewrite `App.jsx` shell to host both route trees (marketing + app skeleton
  pointing at "not yet implemented" placeholders for each route)
- Decide on `ConsentBanner.jsx`: defer it to whenever GA4 ships (Phase 4
  deferred GA4 too) — easiest is to remove the component now and re-add when
  it actually has work to do
- Restyle `ErrorBoundary.jsx` to new tokens
- 100% test coverage on every new component

**Phase 6b — Marketing site** (size: l)
- All 10 pages: `Home`, `Product`, `Models`, `Pricing`, `Download`, `About`,
  `Blog`, `Careers`, `Privacy`, `NotFound`
- `Nav.jsx`, `Footer.jsx`, `ThemeToggle.jsx`, `ImageSlot.jsx`
- Port `site.css`
- Real copy from prototype HTML files
- Image placeholders marked `data-image-slot`
- Pricing monthly/annual toggle, persisted to localStorage

**Phase 6c — App shell + Login + ChatHome** (size: m)
- `Shell.jsx`, `Sidebar.jsx`, `AccountPopover.jsx`
- `Login.jsx` (centered Google sign-in)
- `ChatHome.jsx` (greeting + composer + quick actions)
- `Composer.jsx`, `ModelPicker.jsx`, `AttachMenu.jsx`
- `data.js` mock data
- AuthGate wrapping `/app/*` routes
- Port `app.css`

**Phase 6d — Conversation + mock streaming** (size: m)
- `Conversation.jsx` (streamed turns + inline artifact card)
- `useMockStream.js`
- Markdown rendering (paragraphs, bold, ordered lists per prototype)
- Message actions row (copy/retry/thumbs)
- Routing: `/app/c/:id` loads `loadSample` for the matching recent

**Phase 6e — Projects + Artifacts** (size: m)
- `views/Projects.jsx`, `views/ProjectDetail.jsx`
- `views/Artifacts.jsx`, `views/ArtifactPanel.jsx`
- All five artifact renderers (Code, Document, Chart, Data, Interactive)
- Routing for `/app/projects`, `/app/projects/:id`, `/app/artifacts`

**Phase 6f — Customize** (size: s)
- `views/Customize.jsx` with all six pref controls
- Behavior toggles render but no-op (clearly stubbed with `disabled` or a
  data-attr to mark TBD)

Phases 6c, 6d, 6e, 6f can ship in any order after 6a + 6b land.

## Testing strategy

CLAUDE.md mandates 100% coverage on both Python (pytest-cov) and JS (vitest
v8). For this MVP:

- Every new component gets a co-located `*.test.jsx` file
- `useChannelPrefs` and `useMockStream` hooks tested directly
- AuthGate tested with stubbed localStorage tokens (valid, expired,
  malformed, absent)
- Routing tested at `App.test.jsx` level (which route renders which
  component) using react-router's `MemoryRouter`
- Marketing pages: minimal smoke tests (`renders title`, `nav links present`)
- App screens: render + interaction tests (composer submit calls send,
  sidebar item clicks navigate, etc.)
- Theme prefs: assert `data-theme` / `data-accent` / `data-density` attrs are
  set on the root from localStorage values

Snapshots are not used (per existing convention).

Anti-flake notes (per CLAUDE.md UI conventions):
- `vi.useFakeTimers()` before `render()` for components whose `useEffect`
  schedules timers at mount (e.g. `useMockStream`)
- jsdom converts hex → `rgb()`, so assert `rgb(...)` in token-color tests
- Extract or name anonymous inline functions in `useEffect` so vitest v8
  counts them

## Deployment

No infra changes. The existing `ChannelStack-jc` distributes the SPA build
from S3 + CloudFront. New routes Just Work via the existing 403→/index.html
SPA fallback (the same one that bit us in Phase 5 smoke test). Production
deploy lands when the first 6 phases merge to `development` → `main`.

Marketing site routes need real metadata (title, og:image, og:description,
canonical URL) for SEO. Add per-route meta via React Helmet (new dep) or by
overriding `document.title` + meta tags imperatively in each page's
`useEffect`.

## Open questions / decisions

These don't block Phase 6a from starting but need answers before 6b–6f:

1. **Marketing images.** Solid-color placeholders for v1? Or commission/source
   real images for at least the Home hero and Blog post covers? The prototype
   uses image-slot placeholders the prototype owner drags-and-drops into.
2. **Pricing page copy.** Channel's tiers (Free / Pro / Max / Team) and their
   feature lists are placeholder copy from the prototype. Real product
   pricing decisions are deferred per CLAUDE.md, but the marketing page needs
   *some* text. Use prototype copy as-is, or write something honest about
   "early access, free for now"?
3. **"Channel Max" plan label** in the account row. Drop? Replace with
   "Free"? Hide the plan line entirely until billing exists?
4. **Behavior toggles in Customize.** Render them as disabled? Show with a
   "Coming soon" hint? Hide entirely?
5. **Conversation persistence for stub mode.** Zero persistence (refresh =
   gone), or localStorage stash so `/app/c/r1` resumes the mock conversation
   after refresh? Stub-mode persistence has zero backend cost and improves
   the demo feel.
6. **GA4 measurement ID.** Phase 4 deferred this. Marketing site is exactly
   where GA matters most. Set up now or defer further?

## Files to delete

Phase 6a removes these template residuals:

- `ui/src/components/UsersPanel.jsx` + `UsersPanel.test.jsx`
- `ui/src/components/EmptyState.jsx` + `EmptyState.test.jsx`
- `ui/src/components/PageLayout.jsx` + `PageLayout.test.jsx`
- `ui/src/components/LoginPage.jsx` + `LoginPage.test.jsx` (replaced by
  `app/Login.jsx`)
- `ui/src/components/NotFoundPage.jsx` + `NotFoundPage.test.jsx` (replaced by
  `marketing/pages/NotFound.jsx`)
- `ui/src/components/ConsentBanner.jsx` + `ConsentBanner.test.jsx` (defer
  alongside GA4)
- `ui/src/components/ui/` (shadcn primitives) alongside Tailwind uninstall

The CLAUDE.md file-tree section needs corresponding updates in Phase 6a.

## Out-of-scope contradictions (recorded for future resolution)

The handoff names three Anthropic API model IDs (`claude-opus-4-8`,
`claude-sonnet-4-6`, `claude-haiku-4-5`). Bedrock model IDs are different
(prefixed `anthropic.` with version suffixes) and Opus 4.8 may not exist on
Bedrock yet. The MVP sidesteps this entirely (no real model calls). Phase 7
work that wires Bedrock will:
- Verify which of the three are actually available on Bedrock in `us-east-1`
- Map display names → Bedrock model IDs
- Map Low/Medium/High/Max effort → Claude's `thinking` budget tokens

The handoff also assumes a Node/API backend; we have FastAPI/Python. This is
not a contradiction — both serve the same purpose. No translation needed.
