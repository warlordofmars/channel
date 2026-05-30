// Copyright (c) 2026 John Carter. All rights reserved.

export const MODELS = [
  { id: "claude-opus-4-8",   name: "Claude Opus 4.8",   short: "Opus 4.8",   tier: "Flagship", desc: "Most capable — complex reasoning, long-horizon agentic coding, and high-autonomy work." },
  { id: "claude-sonnet-4-6", name: "Claude Sonnet 4.6", short: "Sonnet 4.6", tier: "Balanced", desc: "The best blend of speed and intelligence. The right default for most work." },
  { id: "claude-haiku-4-5",  name: "Claude Haiku 4.5",  short: "Haiku 4.5",  tier: "Fast",     desc: "Fastest and most cost-effective for simple, high-volume tasks." },
];

export const EFFORTS = ["Low", "Medium", "High", "Max"];

export const RECENTS = [
  { id: "r1",  title: "Home server backup strategy", group: "Today" },
  { id: "r2",  title: "Weekend trail route near Asheville", group: "Today" },
  { id: "r3",  title: "Refactoring the auth middleware", group: "Today" },
  { id: "r4",  title: "Q3 board deck — narrative pass", group: "Yesterday" },
  { id: "r5",  title: "Sourdough hydration troubleshooting", group: "Yesterday" },
  { id: "r6",  title: "Postgres index not being used", group: "Yesterday" },
  { id: "r7",  title: "Reading list for systems design", group: "Previous 7 days" },
  { id: "r8",  title: "Naming the new analytics service", group: "Previous 7 days" },
  { id: "r9",  title: "Comparing standing desk options", group: "Previous 7 days" },
  { id: "r10", title: "Migration plan: REST → gRPC", group: "Previous 7 days" },
  { id: "r11", title: "Explaining diffusion models simply", group: "Previous 7 days" },
  { id: "r12", title: "Tax documents checklist for 2025", group: "Previous 30 days" },
  { id: "r13", title: "Garden irrigation zone layout", group: "Previous 30 days" },
  { id: "r14", title: "Rewriting the onboarding email", group: "Previous 30 days" },
  { id: "r15", title: "Debugging flaky CI test suite", group: "Previous 30 days" },
];

export const QUICK_ACTIONS = [
  { id: "write",   icon: "write",     label: "Write" },
  { id: "learn",   icon: "learn",     label: "Learn" },
  { id: "code",    icon: "code",      label: "Code" },
  { id: "analyze", icon: "customize", label: "Analyze data" },
];

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
