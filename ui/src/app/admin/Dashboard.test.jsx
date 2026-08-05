// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Dashboard, {
  ChartTooltip,
  fmtTick,
  fmtTooltipLabel,
  formatCount,
  formatRate,
} from "./Dashboard.jsx";
import { getAdminMetricsSummary, getAdminMetricsTimeseries } from "../../api.js";

// Only the two metrics wrappers are consumed by Dashboard; stub them so the
// component's fetch behaviour is fully controllable per test.
vi.mock("../../api.js", () => ({
  getAdminMetricsSummary: vi.fn(),
  getAdminMetricsTimeseries: vi.fn(),
}));

// Mock Recharts at the module level so jsdom never tries to lay out SVG (which
// would need a ResizeObserver stub for ResponsiveContainer). The mocks are
// thin passthroughs that expose the point count so tests can assert the data
// reached the chart. These arrows live in the test file, which vitest excludes
// from coverage.
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }) => (
    <div data-testid="rc-container">{children}</div>
  ),
  LineChart: ({ children, data }) => (
    <div data-testid="rc-linechart" data-points={data.length}>
      {children}
    </div>
  ),
  Line: () => <div data-testid="rc-line" />,
  XAxis: () => <div data-testid="rc-xaxis" />,
  YAxis: () => <div data-testid="rc-yaxis" />,
  Tooltip: () => <div data-testid="rc-tooltip" />,
  CartesianGrid: () => <div data-testid="rc-grid" />,
}));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// All named counters, defaulting to 0 (the #236 contract guarantees every
// name is always present), with per-test overrides.
function metricsBlock(overrides = {}) {
  const names = [
    "MemoryWriteSuccesses",
    "MemoryWriteFailures",
    "RecallSuccesses",
    "RecallFailures",
    // Tool-driven memory (#400) — separate from the hook counters above.
    "MemoryToolWriteSuccesses",
    "MemoryToolWriteFailures",
    "MemoryToolRecallSuccesses",
    "MemoryToolRecallFailures",
    // Memory data-rights counters (#476 export, #477 forget) — surfaced on
    // the dashboard by #551. The Python side of the name agreement is pinned
    // by tests/unit/test_admin.py, which derives both sides from the real
    // artefacts rather than restating them.
    "MemoryExportSuccesses",
    "MemoryExportFailures",
    "MemoryRecordDeleteSuccesses",
    "MemoryRecordDeleteFailures",
    "MemoryBulkForgetSuccesses",
    "MemoryBulkForgetFailures",
    "AutoTitleSuccesses",
    "AutoTitleFailures",
    "ChatDeleteMemoryWipeSuccesses",
    "ChatDeleteMemoryWipeFailures",
    "ChatDeleteAttachmentWipeSuccesses",
    "ChatDeleteAttachmentWipeFailures",
    "ChatDeleteAssetWipeSuccesses",
    "ChatDeleteAssetWipeFailures",
    "AssetLazyExpiryReaps",
    "AssetLazyExpiryReapFailures",
    "ToolCallSuccesses",
    "ToolCallFailures",
    // Request-level SLIs (#111).
    "RequestCount",
    "Request4xxCount",
    "Request5xxCount",
    "BedrockTokensIn",
    "BedrockTokensOut",
    "BedrockErrors",
    "BedrockThrottles",
    "FollowupGenSuccesses",
    "FollowupGenFailures",
    "CSPViolations",
  ];
  const metrics = {};
  for (const name of names) metrics[name] = 0;
  return { ...metrics, ...overrides };
}

function summaryFixture() {
  return {
    today: {
      active_users: 12,
      // recall 90/10 → 90% exercises the rate branch; tool save 15 + tool
      // recall 8/2 → 80% exercise the separate #400 tiles.
      metrics: metricsBlock({
        MemoryWriteSuccesses: 340,
        RecallSuccesses: 90,
        RecallFailures: 10,
        AutoTitleSuccesses: 22,
        MemoryToolWriteSuccesses: 15,
        MemoryToolRecallSuccesses: 8,
        MemoryToolRecallFailures: 2,
        RequestCount: 4821,
        Request5xxCount: 7,
        // #551 data-rights rates, chosen distinct from each other and from
        // the 90% / 80% above so each getByText below matches exactly one
        // tile. The bulk-forget pair is deliberately half-failing — that is
        // the shape the tile exists to make visible.
        MemoryExportSuccesses: 3,
        MemoryExportFailures: 1,
        MemoryRecordDeleteSuccesses: 3,
        MemoryRecordDeleteFailures: 2,
        MemoryBulkForgetSuccesses: 1,
        MemoryBulkForgetFailures: 1,
      }),
    },
    "7d": {
      active_users: 48,
      metrics: metricsBlock({
        MemoryWriteSuccesses: 2100,
        RecallSuccesses: 600,
        RecallFailures: 0,
        AutoTitleSuccesses: 130,
        MemoryToolWriteSuccesses: 90,
        MemoryToolRecallSuccesses: 30,
        MemoryToolRecallFailures: 0,
      }),
    },
    "30d": {
      active_users: 120,
      // ...and hook recall 0/0 → "—" exercises the zero-denominator branch.
      // Tool recall AND all three #551 data-rights pairs are non-zero here so
      // the card has a single em dash (the hook tile), keeping the
      // getByText("—") assertion unambiguous.
      metrics: metricsBlock({
        MemoryWriteSuccesses: 9000,
        RecallSuccesses: 0,
        RecallFailures: 0,
        AutoTitleSuccesses: 500,
        MemoryToolWriteSuccesses: 200,
        MemoryToolRecallSuccesses: 40,
        MemoryToolRecallFailures: 10,
        MemoryExportSuccesses: 12,
        MemoryRecordDeleteSuccesses: 30,
        MemoryBulkForgetSuccesses: 6,
      }),
    },
  };
}

function tsFixture(overrides = {}) {
  return {
    metric: "MemoryWriteSuccesses",
    window: "7d",
    bucket: "1h",
    start: "2026-07-06T00:00:00+00:00",
    end: "2026-07-13T00:00:00+00:00",
    points: [
      { t: "2026-07-06T00:00:00+00:00", v: 10 },
      { t: "2026-07-07T00:00:00+00:00", v: 20 },
      { t: "2026-07-08T00:00:00+00:00", v: 15 },
    ],
    ...overrides,
  };
}

describe("Dashboard", () => {
  beforeEach(() => {
    getAdminMetricsSummary.mockReset();
    getAdminMetricsTimeseries.mockReset();
    // Sensible defaults; individual tests override the wrapper under test.
    getAdminMetricsSummary.mockResolvedValue(summaryFixture());
    getAdminMetricsTimeseries.mockResolvedValue(tsFixture());
  });

  // #468 — both grids are multi-column and neither collapsed at 390px, which
  // sliced the "7 days" card and the "Tool calls" chart off the right edge.
  // The className is the only handle app.css's ≤640px block has on an inline
  // style; the inline templates below are what desktop still renders from.
  it("marks both multi-column grids for the mobile single-column collapse", async () => {
    await act(async () => render(<Dashboard />));

    const rollups = screen.getByTestId("rollup-grid");
    const charts = screen.getByTestId("charts-grid");
    expect(rollups.className).toBe("admin-grid");
    expect(charts.className).toBe("admin-grid");
    expect(rollups.style.gridTemplateColumns).toBe("repeat(3, 1fr)");
    expect(charts.style.gridTemplateColumns).toBe("repeat(2, 1fr)");
  });

  it("renders the three rollup cards with active users and a derived recall rate", async () => {
    await act(async () => render(<Dashboard />));

    expect(screen.getByTestId("rollup-today")).toBeTruthy();
    expect(screen.getByTestId("rollup-7d")).toBeTruthy();
    expect(screen.getByTestId("rollup-30d")).toBeTruthy();

    // active_users per window.
    const today = within(screen.getByTestId("rollup-today"));
    expect(today.getByText("12")).toBeTruthy();
    expect(today.getByText("340")).toBeTruthy(); // MemoryWriteSuccesses (hook)
    expect(today.getByText("90%")).toBeTruthy(); // hook recall: 90 / (90 + 10)
    expect(today.getByText("22")).toBeTruthy(); // AutoTitleSuccesses
    // #400 tool tiles are separate from the hook tiles above.
    expect(today.getByText("15")).toBeTruthy(); // MemoryToolWriteSuccesses
    expect(today.getByText("80%")).toBeTruthy(); // tool recall: 8 / (8 + 2)
    // #111 request SLIs — the card is no longer memory-hooks-only.
    expect(today.getByText("4821")).toBeTruthy(); // RequestCount
    expect(today.getByText("7")).toBeTruthy(); // Request5xxCount

    // Zero-denominator recall renders an em dash rather than "0%".
    const thirty = within(screen.getByTestId("rollup-30d"));
    expect(thirty.getByText("120")).toBeTruthy();
    expect(thirty.getByText("—")).toBeTruthy();

    // The stat-tile labels are present, including the isolated tool tiles.
    expect(today.getByText("Active users")).toBeTruthy();
    expect(today.getByText("Memory writes")).toBeTruthy();
    expect(today.getByText("Recall success")).toBeTruthy();
    expect(today.getByText("Auto-titles")).toBeTruthy();
    expect(today.getByText("Tool saves")).toBeTruthy();
    expect(today.getByText("Tool recall")).toBeTruthy();
    expect(today.getByText("Requests")).toBeTruthy();
    expect(today.getByText("5xx responses")).toBeTruthy();
  });

  // #551 — the six #476/#477 counters were emitted but absent from the admin
  // allowlist, so nothing rendered them. Rates rather than counts, because a
  // count tile reading 0 cannot be told apart from a misspelled metric name.
  it("renders the memory data-rights tiles as export / forget success rates", async () => {
    await act(async () => render(<Dashboard />));
    const today = within(screen.getByTestId("rollup-today"));

    expect(today.getByText("Export success")).toBeTruthy();
    expect(today.getByText("75%")).toBeTruthy(); // export: 3 / (3 + 1)
    expect(today.getByText("Record forget")).toBeTruthy();
    expect(today.getByText("60%")).toBeTruthy(); // record delete: 3 / (3 + 2)
    // The one that matters most: a half-failing bulk forget means users were
    // told their data was gone when it was not, and it must not read clean.
    expect(today.getByText("Bulk forget")).toBeTruthy();
    expect(today.getByText("50%")).toBeTruthy(); // bulk forget: 1 / (1 + 1)
  });

  // A window with no export/forget attempts must render an em dash, not
  // "0%" — "nobody exercised the endpoint" and "the endpoint is failing" are
  // different operational statements and the tile has to distinguish them.
  it("renders an em dash for a data-rights window with no attempts", async () => {
    getAdminMetricsSummary.mockResolvedValue({
      ...summaryFixture(),
      today: { active_users: 0, metrics: metricsBlock() },
    });
    await act(async () => render(<Dashboard />));

    const today = within(screen.getByTestId("rollup-today"));
    // Every rate tile has a 0 denominator here: hook recall, tool recall,
    // and the three data-rights tiles.
    expect(today.getAllByText("—")).toHaveLength(5);
  });

  it("shows the loading state while the summary fetch is in flight", async () => {
    const d = deferred();
    getAdminMetricsSummary.mockReturnValue(d.promise);
    render(<Dashboard />);
    expect(screen.getByText("Loading metrics…")).toBeTruthy();
    await act(async () => {
      d.resolve(summaryFixture());
    });
  });

  it("renders a degraded panel when the summary endpoint fails, keeping the charts", async () => {
    getAdminMetricsSummary.mockRejectedValue(new Error("metrics_unavailable"));
    await act(async () => render(<Dashboard />));

    expect(screen.getByText("Metrics temporarily unavailable")).toBeTruthy();
    expect(screen.getByText(/Usage rollups could not be loaded/)).toBeTruthy();
    // Cards are gone but the charts still rendered — per-section failure.
    expect(screen.queryByTestId("rollup-today")).toBeNull();
    expect(screen.getByTestId("chart-MemoryWriteSuccesses")).toBeTruthy();
  });

  it("renders both trend charts with their point data", async () => {
    await act(async () => render(<Dashboard />));

    expect(screen.getByTestId("chart-MemoryWriteSuccesses")).toBeTruthy();
    expect(screen.getByTestId("chart-ToolCallSuccesses")).toBeTruthy();

    const charts = screen.getAllByTestId("rc-linechart");
    expect(charts).toHaveLength(2);
    charts.forEach((c) => expect(c.dataset.points).toBe("3"));

    // Two charts fired on mount with their configured defaults.
    expect(getAdminMetricsTimeseries).toHaveBeenCalledWith({
      metric: "MemoryWriteSuccesses",
      window: "7d",
    });
    expect(getAdminMetricsTimeseries).toHaveBeenCalledWith({
      metric: "ToolCallSuccesses",
      window: "30d",
    });
  });

  it("shows a per-chart empty state when the window has no data", async () => {
    getAdminMetricsTimeseries.mockResolvedValue(tsFixture({ points: [] }));
    await act(async () => render(<Dashboard />));
    expect(screen.getAllByText("No data in this window.")).toHaveLength(2);
  });

  it("shows the chart loading state while the timeseries fetch is in flight", async () => {
    const d = deferred();
    getAdminMetricsTimeseries.mockReturnValue(d.promise);
    render(<Dashboard />);
    expect(screen.getAllByText("Loading chart…")).toHaveLength(2);
    await act(async () => {
      d.resolve(tsFixture());
    });
  });

  it("degrades only the charts when their timeseries fetch fails", async () => {
    getAdminMetricsTimeseries.mockRejectedValue(new Error("metrics_unavailable"));
    await act(async () => render(<Dashboard />));
    // Summary cards still render; each chart shows its own degraded panel.
    expect(screen.getByTestId("rollup-today")).toBeTruthy();
    expect(screen.getAllByTestId("dash-degraded")).toHaveLength(2);
  });

  it("refetches the timeseries for the window a picker click selects", async () => {
    await act(async () => render(<Dashboard />));

    const memoryChart = within(screen.getByTestId("chart-MemoryWriteSuccesses"));
    const btn24h = memoryChart.getByText("24h");
    expect(btn24h.getAttribute("aria-pressed")).toBe("false");

    await act(async () => fireEvent.click(btn24h));

    expect(getAdminMetricsTimeseries).toHaveBeenCalledWith({
      metric: "MemoryWriteSuccesses",
      window: "24h",
    });
    expect(
      within(screen.getByTestId("chart-MemoryWriteSuccesses"))
        .getByText("24h")
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("discards a summary response that lands after unmount", async () => {
    const d = deferred();
    getAdminMetricsSummary.mockReturnValue(d.promise);
    const { unmount } = render(<Dashboard />);
    unmount();
    await act(async () => {
      d.resolve(summaryFixture());
    });
    expect(screen.queryByTestId("rollup-today")).toBeNull();
  });

  it("discards a summary failure that lands after unmount", async () => {
    const d = deferred();
    getAdminMetricsSummary.mockReturnValue(d.promise);
    const { unmount } = render(<Dashboard />);
    unmount();
    await act(async () => {
      d.reject(new Error("late failure"));
    });
    expect(screen.queryByText("Metrics temporarily unavailable")).toBeNull();
  });

  it("discards a timeseries response that lands after unmount", async () => {
    const d = deferred();
    getAdminMetricsTimeseries.mockReturnValue(d.promise);
    const { unmount } = render(<Dashboard />);
    unmount();
    await act(async () => {
      d.resolve(tsFixture());
    });
    expect(screen.queryByTestId("rc-linechart")).toBeNull();
  });

  it("discards a timeseries failure that lands after unmount", async () => {
    const d = deferred();
    getAdminMetricsTimeseries.mockReturnValue(d.promise);
    const { unmount } = render(<Dashboard />);
    unmount();
    await act(async () => {
      d.reject(new Error("late failure"));
    });
    expect(screen.queryByTestId("dash-degraded")).toBeNull();
  });
});

describe("Dashboard formatting helpers", () => {
  it("formatCount rounds floats and drops the decimal", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(1234)).toBe("1234");
    expect(formatCount(2.7)).toBe("3");
  });

  it("formatRate returns a rounded percentage or an em dash for no attempts", () => {
    expect(formatRate(90, 10)).toBe("90%");
    expect(formatRate(1, 2)).toBe("33%");
    expect(formatRate(0, 0)).toBe("—");
  });

  it("fmtTick compacts an ISO bucket timestamp to MM-DD HH:MM", () => {
    expect(fmtTick("2026-07-13T12:05:00+00:00")).toBe("07-13 12:05");
  });

  it("fmtTooltipLabel gives a minute-precision UTC stamp", () => {
    expect(fmtTooltipLabel("2026-07-13T12:05:00+00:00")).toBe("2026-07-13 12:05");
  });
});

// Recharts is mocked at the module level (the visual hover box can't be
// exercised through the stubbed <Tooltip>), so the custom content component
// is unit-tested directly with the props Recharts would inject. This covers
// the #367 fix: a token-themed panel (readable in dark AND light) whose value
// line reads the series name ("Tool calls: 6"), not the raw "v" dataKey.
describe("ChartTooltip", () => {
  const point = [{ dataKey: "v", name: "Tool calls", value: 6 }];
  const label = "2026-07-08T00:00:00+00:00";

  it("renders nothing until a point is hovered (inactive)", () => {
    render(<ChartTooltip active={false} payload={point} label={label} />);
    expect(screen.queryByTestId("chart-tooltip")).toBeNull();
  });

  it("renders nothing when active but the payload is absent", () => {
    render(<ChartTooltip active payload={undefined} label={label} />);
    expect(screen.queryByTestId("chart-tooltip")).toBeNull();
  });

  it("renders nothing when active but the payload is empty", () => {
    render(<ChartTooltip active payload={[]} label={label} />);
    expect(screen.queryByTestId("chart-tooltip")).toBeNull();
  });

  it("renders a token-themed panel with the metric name and formatted date on hover", () => {
    render(<ChartTooltip active payload={point} label={label} />);

    const box = screen.getByTestId("chart-tooltip");
    // The core #367 fix: background comes from the theme token, not Recharts'
    // hardcoded white — readable in both light and dark. CSS vars are stored
    // verbatim by jsdom (no hex→rgb normalisation applies to a var()).
    expect(box.style.background).toBe("var(--raised)");
    expect(box.style.borderRadius).toBe("var(--r-md)");
    expect(box.style.boxShadow).toBe("var(--shadow-md)");

    // The value line reads the series name, not the raw "v" dataKey.
    const value = screen.getByText("Tool calls: 6");
    expect(value.style.color).toBe("var(--ink)");
    expect(value.style.fontWeight).toBe("600");
    expect(screen.queryByText(/^v\b/)).toBeNull();

    // The date label keeps the existing minute-precision UTC format.
    const dateLabel = screen.getByText("2026-07-08 00:00");
    expect(dateLabel.style.color).toBe("var(--ink-soft)");
  });
});
