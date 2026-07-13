// Copyright (c) 2026 John Carter. All rights reserved.
import { listModels } from "../api.js";

export const EFFORTS = ["Low", "Medium", "High", "Max"];

export const QUICK_ACTIONS = [
  { id: "write",   icon: "write",     label: "Write" },
  { id: "learn",   icon: "learn",     label: "Learn" },
  { id: "code",    icon: "code",      label: "Code" },
  { id: "analyze", icon: "customize", label: "Analyze data" },
];

// ──────────────────────────────────────────────────────────────────────────
// Model allowlist + display metadata
//
// `GET /api/models` is the source of truth for which model ids the picker
// may offer. There is no client-side fallback allowlist — when the API is
// unreachable, consumers render a "Loading…" affordance rather than a
// stale hardcoded list. See issue #148.
//
// `MODEL_DISPLAY_META` is optional display chrome (short button label +
// one-line description) keyed by id. The API response provides `label`
// and `tier`; the meta map provides `short` and `desc`. Ids the API
// returns but the meta map doesn't cover fall back to `label` for both.
// Drift here is benign — the meta map is just UI sugar, not an
// allowlist.
// ──────────────────────────────────────────────────────────────────────────

export const MODEL_DISPLAY_META = {
  "claude-opus-4-6": {
    short: "Opus 4.6",
    desc: "Most capable — complex reasoning, long-horizon agentic coding, and high-autonomy work.",
  },
  "claude-sonnet-4-6": {
    short: "Sonnet 4.6",
    desc: "The best blend of speed and intelligence. The right default for most work.",
  },
  "claude-haiku-4-5": {
    short: "Haiku 4.5",
    desc: "Fastest and most cost-effective for simple, high-volume tasks.",
  },
};

// Module-level cache so every mounted consumer shares one API round-trip.
// Cold-start invalidates (page reload re-fetches). The in-flight Promise
// dedupes concurrent calls during the initial mount stampede.
let _cachedAllowlist = null;
let _inFlightFetch = null;

export function __resetModelsCacheForTest() {
  _cachedAllowlist = null;
  _inFlightFetch = null;
}

/**
 * Return the cached server allowlist, or null if not yet loaded.
 * Synchronous — consumers read this between renders without re-fetching.
 */
export function cachedModels() {
  return _cachedAllowlist;
}

/**
 * Fetch the server allowlist and cache it for the lifetime of the page.
 * Concurrent calls during the initial mount stampede share the same
 * Promise. Failures bubble up — the consumer decides whether to retry
 * or render a fallback affordance.
 */
export async function loadModels() {
  if (_cachedAllowlist) return _cachedAllowlist;
  if (_inFlightFetch) return _inFlightFetch;
  _inFlightFetch = listModels()
    .then(({ models }) => {
      const merged = models.map(mergeWithDisplayMeta);
      _cachedAllowlist = merged;
      return merged;
    })
    .finally(() => {
      _inFlightFetch = null;
    });
  return _inFlightFetch;
}

/**
 * Merge a single API model row with the optional client display meta.
 * The API row's `id` and `label` always win; the meta map supplies
 * `short` + `desc` when known.
 */
export function mergeWithDisplayMeta(apiModel) {
  const meta = MODEL_DISPLAY_META[apiModel.id] ?? {};
  return {
    ...apiModel,
    name: apiModel.label ?? apiModel.id,
    short: meta.short ?? apiModel.label ?? apiModel.id,
    desc: meta.desc ?? "",
  };
}

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

// The artifacts browse view (/app/artifacts) is now backed by the real
// asset REST surface (#328) — the former `ARTIFACTS` mock was removed
// once the view + ArtifactPanel started fetching `GET /api/assets`.

// Mock docs attached to every project (placeholder until project knowledge
// is real). Lifted from design-sources/app/views.jsx's PROJECT_DOCS const.
export const PROJECT_DOCS = [
  "product-spec-v2.pdf",
  "events-schema.sql",
  "migration-notes.md",
  "q3-targets.csv",
];
