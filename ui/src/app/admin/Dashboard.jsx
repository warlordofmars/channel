// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import Icon from "../../components/Icon.jsx";
import { getAdminMetricsSummary, getAdminMetricsTimeseries } from "../../api.js";

/**
 * `/app/admin/dashboard` — usage rollups + trend charts (#239, the final
 * sub-issue of epic #233). Two sections:
 *
 *   1. Three rollup cards (Today / 7 days / 30 days) from the summary
 *      endpoint, each showing active users plus a curated set of the #236
 *      CloudWatch counters.
 *   2. Two trend charts from the timeseries endpoint, each with a window
 *      picker (24h / 7d / 30d).
 *
 * Failure is per-section: a 503 (or any error) from the summary endpoint
 * degrades only the rollup cards; a failure on one chart's timeseries fetch
 * degrades only that chart. The rest of the page always renders.
 *
 * Contract note: the issue body's second chart was "active_users over 30
 * days", but `active_users` is a summary-only aggregate — the #236
 * timeseries endpoint 422s on any metric outside the named counters — so
 * the second chart tracks `ToolCallSuccesses` (a feature-engagement proxy)
 * instead. The "recall_cache_hit_rate" card is likewise mapped onto the real
 * contract as a derived recall success rate
 * (RecallSuccesses / (RecallSuccesses + RecallFailures)).
 *
 * Hook vs tool memory (#400): the "Memory writes" tile and the derived
 * "Recall success" rate read the always-on `AgentCoreMemoryHook` /
 * `AgentCoreRecallHook` counters (`MemoryWriteSuccesses` /
 * `RecallSuccesses`+`RecallFailures`) — a hook-health signal. The
 * agent-callable `remember` / `recall` tools (#273) emit their OWN
 * `MemoryTool*` counters, surfaced by the separate "Tool saves" tile and
 * "Tool recall" rate so tool usage never masks a hook regression.
 *
 * Memory data-rights (#476 export, #477 forget; surfaced by #551): the last
 * three tiles read the export / per-record-delete / bulk-forget counters as
 * success RATES rather than raw counts, deliberately. A count tile reading 0
 * is ambiguous — "nothing happened" and "the metric name is misspelled" look
 * identical — whereas `formatRate` renders an em dash only when there were no
 * attempts at all, and a number the moment there were. On this surface the
 * failure halves are the point: a dip below 100% on "Bulk forget" means users
 * were told their data was gone when it was not.
 *
 * Request SLIs (#111): the "Requests" and "5xx responses" tiles read the
 * per-request EMF counters the API middleware emits, so the card reflects
 * real traffic rather than only agent-internal activity. They read the
 * aggregate `{Environment}` series — the same one the summary endpoint pins —
 * not the per-`Route` breakdown, so the numbers are service-wide totals.
 */

// Summary rollup windows — keys match the summary endpoint's top-level shape.
const ROLLUPS = [
  { key: "today", label: "Today" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
];

// Timeseries window options offered by each chart's picker.
const WINDOWS = [
  { key: "24h", label: "24h" },
  { key: "7d", label: "7d" },
  { key: "30d", label: "30d" },
];

// The two trend charts. Both track a single real CloudWatch counter over a
// pickable window (see the contract note above re: the ToolCallSuccesses
// substitution for active_users).
const CHARTS = [
  { metric: "MemoryWriteSuccesses", label: "Memory writes", defaultWindow: "7d" },
  { metric: "ToolCallSuccesses", label: "Tool calls", defaultWindow: "30d" },
];

const MUTED_STYLE = { fontSize: 13.5, color: "var(--ink-soft)" };

// The two multi-column grids below also carry `className="admin-grid"`, which
// exists solely so the ≤640px block in app.css can collapse them to one column
// (#468) — an inline style is otherwise unreachable from a media query. The
// inline objects stay the desktop source of truth, so wide viewports are
// untouched by that rule.
const ROLLUP_GRID_STYLE = {
  display: "grid",
  gridTemplateColumns: "repeat(3, 1fr)",
  gap: 14,
  marginBottom: 28,
};

const CARD_STYLE = {
  background: "var(--raised)",
  border: "1px solid var(--border)",
  borderRadius: "var(--r-lg)",
  padding: "16px 18px",
};

const CARD_TITLE_STYLE = {
  fontSize: 12,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.04em",
  color: "var(--ink-faint)",
  margin: "0 0 14px",
  fontFamily: "var(--font-mono)",
};

const TILE_GRID_STYLE = {
  display: "grid",
  gridTemplateColumns: "repeat(2, 1fr)",
  gap: 14,
};

const TILE_STYLE = { display: "flex", alignItems: "center", gap: 9 };
const TILE_ICON_STYLE = {
  width: 30,
  height: 30,
  borderRadius: "var(--r-sm)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  flexShrink: 0,
  background: "var(--accent-soft)",
  color: "var(--accent-ink)",
};
const TILE_VALUE_STYLE = { fontSize: 20, fontWeight: 650, color: "var(--ink)", lineHeight: 1.1 };
const TILE_LABEL_STYLE = { fontSize: 11.5, color: "var(--ink-soft)" };

const CHARTS_GRID_STYLE = {
  display: "grid",
  gridTemplateColumns: "repeat(2, 1fr)",
  gap: 14,
};

const CHART_HEAD_STYLE = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  marginBottom: 12,
};

const CHART_TITLE_STYLE = { fontSize: 14.5, fontWeight: 600, color: "var(--ink)", margin: 0 };

const WINDOW_BTN_STYLE = {
  background: "transparent",
  border: "1px solid var(--border)",
  borderRadius: "var(--r-pill)",
  padding: "3px 10px",
  fontSize: 11.5,
  color: "var(--ink-soft)",
  cursor: "pointer",
  fontFamily: "var(--font-mono)",
};

const WINDOW_BTN_ACTIVE_STYLE = {
  ...WINDOW_BTN_STYLE,
  background: "var(--accent)",
  borderColor: "var(--accent)",
  color: "var(--on-accent)",
};

const DEGRADED_STYLE = {
  display: "flex",
  alignItems: "center",
  gap: 11,
  padding: "16px 18px",
  border: "1px solid var(--border)",
  borderRadius: "var(--r-lg)",
  background: "var(--raised)",
};
const DEGRADED_ICON_STYLE = {
  width: 34,
  height: 34,
  borderRadius: "var(--r-sm)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  flexShrink: 0,
  background: "var(--danger-soft)",
  color: "var(--danger)",
};

/**
 * Hover-tooltip chrome. Themed from the design tokens so it stays readable in
 * BOTH light and dark themes — the Recharts default is a hardcoded white box
 * (unreadable on the dark canvas, #367). `--raised` bg + `--border` matches
 * the surrounding cards.
 */
const TOOLTIP_STYLE = {
  background: "var(--raised)",
  border: "1px solid var(--border)",
  borderRadius: "var(--r-md)",
  boxShadow: "var(--shadow-md)",
  padding: "8px 11px",
  fontSize: 12,
};

const TOOLTIP_LABEL_STYLE = {
  color: "var(--ink-soft)",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  marginBottom: 4,
};

const TOOLTIP_ITEM_STYLE = { color: "var(--ink)", fontWeight: 600 };

/**
 * Format a raw counter value for a stat tile. CloudWatch SUM stats arrive as
 * floats (0.0 when no data); rounding drops the ".0" and any float noise, and
 * avoiding `toLocaleString` keeps the output deterministic under any CI
 * locale (same reasoning as Users.jsx's `fmtDate`).
 */
export function formatCount(n) {
  return String(Math.round(n));
}

/**
 * Derive a success-rate percentage from a success/failure counter pair. The
 * zero-denominator branch (no attempts in the window) renders an em dash
 * rather than a misleading "0%".
 */
export function formatRate(successes, failures) {
  const total = successes + failures;
  if (total === 0) return "—";
  return `${Math.round((successes / total) * 100)}%`;
}

/**
 * Compact bucket-tick label: "2026-07-13T12:05:00+00:00" → "07-13 12:05".
 * String slicing (not `toLocaleString`) keeps it deterministic and UTC.
 */
export function fmtTick(iso) {
  return `${iso.slice(5, 10)} ${iso.slice(11, 16)}`;
}

/** Minute-precision UTC stamp for the hover tooltip's point label. */
export function fmtTooltipLabel(iso) {
  return iso.slice(0, 16).replace("T", " ");
}

/**
 * The twelve stat tiles for one rollup window. `active_users` is a top-level
 * section field; the rest read named counters from `section.metrics` (all
 * allowlisted names are always present per the #236 contract, so no
 * missing-key guards). The first four are hook-health signals; the next two
 * (#400) isolate the agent-driven `remember` / `recall` tools so their usage
 * never dilutes the hook counters above; then the #558 prompt-injection
 * signal; then two (#111) request-level SLIs, so the card answers "is the
 * service healthy" and not only "are the memory hooks firing"; and last the
 * three memory data-rights rates (#476 / #477, surfaced by #551) covering
 * export and the two forget shapes.
 *
 * "Forgeries defused" (#558) is a raw count rather than a rate, unlike the
 * data-rights tiles below it, because it has no failure half to divide by:
 * the defusal is deterministic and cannot fail, so the only question is how
 * often it fired. Its baseline genuinely is zero, and a move off zero is the
 * whole signal — someone is shaping recalled text to forge this block's
 * structure. Counting turns rather than markers is what keeps the number
 * readable: one crafted turn carrying a hundred forgeries moves it by one.
 *
 * Every name read here must be on `_METRIC_ALLOWLIST` in
 * `src/channel/api/admin.py` — the summary endpoint returns only allowlisted
 * counters, so a name that is merely emitted (or merely misspelled) renders a
 * silent zero. `tests/unit/test_admin.py` pins that both ways.
 */
function tilesFor(section) {
  const m = section.metrics;
  return [
    { key: "active", label: "Active users", icon: "cowork", value: formatCount(section.active_users) },
    { key: "memory", label: "Memory writes", icon: "database", value: formatCount(m.MemoryWriteSuccesses) },
    { key: "recall", label: "Recall success", icon: "refresh", value: formatRate(m.RecallSuccesses, m.RecallFailures) },
    { key: "titles", label: "Auto-titles", icon: "sparkle", value: formatCount(m.AutoTitleSuccesses) },
    { key: "toolSave", label: "Tool saves", icon: "pin", value: formatCount(m.MemoryToolWriteSuccesses) },
    { key: "toolRecall", label: "Tool recall", icon: "search", value: formatRate(m.MemoryToolRecallSuccesses, m.MemoryToolRecallFailures) },
    { key: "forgeries", label: "Forgeries defused", icon: "shield", value: formatCount(m.RecallForgeryDefused) },
    { key: "requests", label: "Requests", icon: "globe", value: formatCount(m.RequestCount) },
    { key: "requestErrors", label: "5xx responses", icon: "shield", value: formatCount(m.Request5xxCount) },
    { key: "export", label: "Export success", icon: "download", value: formatRate(m.MemoryExportSuccesses, m.MemoryExportFailures) },
    { key: "recordForget", label: "Record forget", icon: "trash", value: formatRate(m.MemoryRecordDeleteSuccesses, m.MemoryRecordDeleteFailures) },
    { key: "bulkForget", label: "Bulk forget", icon: "folder-x", value: formatRate(m.MemoryBulkForgetSuccesses, m.MemoryBulkForgetFailures) },
  ];
}

/** Shared degraded panel — used for the summary section and per-chart. */
function DegradedPanel({ label }) {
  return (
    <div style={DEGRADED_STYLE} data-testid="dash-degraded">
      <span style={DEGRADED_ICON_STYLE}>
        <Icon name="shield" size={18} />
      </span>
      <div>
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)" }}>
          Metrics temporarily unavailable
        </div>
        <div style={MUTED_STYLE}>
          {label} could not be loaded. This is usually transient — try again shortly.
        </div>
      </div>
    </div>
  );
}

/** One rollup card: window label + a 2×2 grid of stat tiles. */
function RollupCard({ id, label, section }) {
  return (
    <div style={CARD_STYLE} data-testid={`rollup-${id}`}>
      <h3 style={CARD_TITLE_STYLE}>{label}</h3>
      <div style={TILE_GRID_STYLE}>
        {tilesFor(section).map((t) => (
          <div style={TILE_STYLE} key={t.key}>
            <span style={TILE_ICON_STYLE}>
              <Icon name={t.icon} size={16} />
            </span>
            <div>
              <div style={TILE_VALUE_STYLE}>{t.value}</div>
              <div style={TILE_LABEL_STYLE}>{t.label}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Top section: the three rollup cards (or a loading / degraded state). */
function SummarySection() {
  const [summary, setSummary] = useState(null); // null = first load in flight
  const [degraded, setDegraded] = useState(false);

  useEffect(function loadSummary() {
    let cancelled = false;
    setSummary(null);
    setDegraded(false);
    getAdminMetricsSummary()
      .then(function onSummary(data) {
        if (cancelled) return;
        setSummary(data);
      })
      .catch(function onSummaryError() {
        if (cancelled) return;
        setDegraded(true);
      });
    return function cancelLoadSummary() {
      cancelled = true;
    };
  }, []);

  if (degraded) return <DegradedPanel label="Usage rollups" />;
  if (summary === null) return <p style={MUTED_STYLE}>Loading metrics…</p>;
  return (
    <div className="admin-grid" style={ROLLUP_GRID_STYLE} data-testid="rollup-grid">
      {ROLLUPS.map((r) => (
        <RollupCard key={r.key} id={r.key} label={r.label} section={summary[r.key]} />
      ))}
    </div>
  );
}

/**
 * Custom hover tooltip. Replaces Recharts' default white box (unreadable in
 * the dark theme, #367) with a token-themed panel, and renders the series
 * `name` ("Memory writes" / "Tool calls") instead of the raw `v` dataKey.
 * Recharts injects `active` / `payload` / `label`; render nothing until a
 * point is actually hovered.
 */
export function ChartTooltip({ active, payload, label }) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div style={TOOLTIP_STYLE} data-testid="chart-tooltip">
      <div style={TOOLTIP_LABEL_STYLE}>{fmtTooltipLabel(label)}</div>
      {payload.map((entry) => (
        <div key={entry.dataKey} style={TOOLTIP_ITEM_STYLE}>
          {`${entry.name}: ${entry.value}`}
        </div>
      ))}
    </div>
  );
}

/** The chart plot area, keyed on load / empty / degraded / data. */
function ChartBody({ degraded, points, label }) {
  if (degraded) return <DegradedPanel label={label} />;
  if (points === null) return <p style={MUTED_STYLE}>Loading chart…</p>;
  if (points.length === 0) return <p style={MUTED_STYLE}>No data in this window.</p>;
  return (
    <div style={{ width: "100%", height: 240 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 3" />
          <XAxis dataKey="t" tickFormatter={fmtTick} stroke="var(--ink-faint)" fontSize={11} minTickGap={28} />
          <YAxis allowDecimals={false} stroke="var(--ink-faint)" fontSize={11} width={36} />
          <Tooltip
            content={<ChartTooltip />}
            cursor={{ stroke: "var(--border)", strokeWidth: 1 }}
          />
          <Line
            type="monotone"
            dataKey="v"
            name={label}
            stroke="var(--accent)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** One trend chart: title + window picker + plot, self-managing its fetch. */
function MetricChart({ metric, label, defaultWindow }) {
  const [win, setWin] = useState(defaultWindow);
  const [points, setPoints] = useState(null); // null = load in flight
  const [degraded, setDegraded] = useState(false);

  useEffect(
    function loadSeries() {
      let cancelled = false;
      setPoints(null);
      setDegraded(false);
      getAdminMetricsTimeseries({ metric, window: win })
        .then(function onSeries(data) {
          if (cancelled) return;
          setPoints(data.points);
        })
        .catch(function onSeriesError() {
          if (cancelled) return;
          setDegraded(true);
        });
      return function cancelLoadSeries() {
        cancelled = true;
      };
    },
    [metric, win],
  );

  function handleWindow(event) {
    setWin(event.currentTarget.dataset.window);
  }

  return (
    <div style={CARD_STYLE} data-testid={`chart-${metric}`}>
      <div style={CHART_HEAD_STYLE}>
        <h3 style={CHART_TITLE_STYLE}>{label}</h3>
        <div style={{ display: "flex", gap: 4 }}>
          {WINDOWS.map((w) => (
            <button
              type="button"
              key={w.key}
              data-window={w.key}
              onClick={handleWindow}
              aria-pressed={w.key === win}
              style={w.key === win ? WINDOW_BTN_ACTIVE_STYLE : WINDOW_BTN_STYLE}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>
      <ChartBody degraded={degraded} points={points} label={label} />
    </div>
  );
}

export default function Dashboard() {
  return (
    <div className="view">
      <div className="view-inner">
        <div className="view-head">
          <h2>Dashboard</h2>
          <p>Usage rollups and trends across the Channel deployment.</p>
        </div>
        <SummarySection />
        <div className="admin-grid" style={CHARTS_GRID_STYLE} data-testid="charts-grid">
          {CHARTS.map((c) => (
            <MetricChart
              key={c.metric}
              metric={c.metric}
              label={c.label}
              defaultWindow={c.defaultWindow}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
