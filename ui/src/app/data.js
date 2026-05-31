// Copyright (c) 2026 John Carter. All rights reserved.

export const MODELS = [
  { id: "claude-opus-4-8",   name: "Claude Opus 4.8",   short: "Opus 4.8",   tier: "Flagship", desc: "Most capable — complex reasoning, long-horizon agentic coding, and high-autonomy work." },
  { id: "claude-sonnet-4-6", name: "Claude Sonnet 4.6", short: "Sonnet 4.6", tier: "Balanced", desc: "The best blend of speed and intelligence. The right default for most work." },
  { id: "claude-haiku-4-5",  name: "Claude Haiku 4.5",  short: "Haiku 4.5",  tier: "Fast",     desc: "Fastest and most cost-effective for simple, high-volume tasks." },
];

export const EFFORTS = ["Low", "Medium", "High", "Max"];

export const QUICK_ACTIONS = [
  { id: "write",   icon: "write",     label: "Write" },
  { id: "learn",   icon: "learn",     label: "Learn" },
  { id: "code",    icon: "code",      label: "Code" },
  { id: "analyze", icon: "customize", label: "Analyze data" },
];

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

// Mock artifacts — used by /app/artifacts (list) and the ArtifactPanel
// overlay. `kind` drives the renderer switch in ArtifactPanel.
// `project` is null when the artifact isn't scoped to one. Lifted
// verbatim from design-sources/app/data.jsx.
export const ARTIFACTS = [
  { id: "a1", title: "Pricing tier comparison", kind: "Document",    updated: "2h ago",     lines: "—",         project: "Q3 Planning" },
  { id: "a2", title: "rate-limiter.ts",         kind: "Code",        updated: "5h ago",     lines: "142 lines", project: "Analytics Rewrite" },
  { id: "a3", title: "Onboarding flow mockup",  kind: "Interactive", updated: "Yesterday",  lines: "React",     project: null },
  { id: "a4", title: "Trail elevation chart",   kind: "Chart",       updated: "Yesterday",  lines: "SVG",       project: "Field Guide" },
  { id: "a5", title: "Migration runbook",       kind: "Document",    updated: "3d ago",     lines: "—",         project: "Analytics Rewrite" },
  { id: "a6", title: "budget-summary.csv",      kind: "Data",        updated: "4d ago",     lines: "380 rows",  project: "Side Project: Tally" },
];

// Mock docs attached to every project (placeholder until project knowledge
// is real). Lifted from design-sources/app/views.jsx's PROJECT_DOCS const.
export const PROJECT_DOCS = [
  "product-spec-v2.pdf",
  "events-schema.sql",
  "migration-notes.md",
  "q3-targets.csv",
];
