// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/useAssetContent.js", () => ({
  useAssetContent: vi.fn(),
}));

import { useAssetContent } from "../hooks/useAssetContent.js";
import InlineData, { isInlineData } from "./InlineData.jsx";

function dataAsset(over = {}) {
  return {
    asset_id: "as-data",
    chat_id: "c1",
    msg_id: "m1",
    kind: "data",
    title: "metrics.csv",
    mime: "text/csv",
    size_bytes: 512,
    origin: "generated",
    created_at: "2026-07-18T00:00:00.000000+00:00",
    source: { msg_id: "m1", tool_use_id: "t1" },
    ...over,
  };
}

function ready(text) {
  useAssetContent.mockReturnValue({
    status: "ready",
    url: null,
    text,
    httpStatus: null,
  });
}

beforeEach(() => {
  useAssetContent.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("isInlineData", () => {
  it("is true for a kind=data asset with a text CSV mime", () => {
    expect(isInlineData(dataAsset({ mime: "text/csv" }))).toBe(true);
    expect(isInlineData(dataAsset({ mime: "text/plain" }))).toBe(true);
  });

  it("is ORIGIN-AGNOSTIC — an uploaded CSV is inline-eligible (#363)", () => {
    expect(isInlineData(dataAsset({ origin: "upload", mime: "text/csv" }))).toBe(
      true,
    );
  });

  it("is false for a binary spreadsheet upload (xlsx)", () => {
    expect(
      isInlineData(
        dataAsset({
          mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }),
      ),
    ).toBe(false);
  });

  it("is false for a non-CSV text mime (markdown / html) — allowlist, not text/* prefix", () => {
    expect(isInlineData(dataAsset({ mime: "text/markdown" }))).toBe(false);
    expect(isInlineData(dataAsset({ mime: "text/html" }))).toBe(false);
  });

  it("is false when the mime is missing or non-string", () => {
    expect(isInlineData(dataAsset({ mime: null }))).toBe(false);
    expect(isInlineData(dataAsset({ mime: undefined }))).toBe(false);
  });

  it("is true exactly at the 5 MB byte cap and false one byte over", () => {
    const MB = 1024 * 1024;
    expect(isInlineData(dataAsset({ size_bytes: 5 * MB }))).toBe(true);
    expect(isInlineData(dataAsset({ size_bytes: 5 * MB + 1 }))).toBe(false);
  });

  it("is false when size_bytes is missing or non-numeric", () => {
    expect(isInlineData(dataAsset({ size_bytes: null }))).toBe(false);
    expect(isInlineData(dataAsset({ size_bytes: undefined }))).toBe(false);
  });

  it("is false for a non-data kind", () => {
    expect(isInlineData(dataAsset({ kind: "document" }))).toBe(false);
  });

  it("is false for a null / undefined asset", () => {
    expect(isInlineData(null)).toBe(false);
    expect(isInlineData(undefined)).toBe(false);
  });
});

describe("InlineData", () => {
  it("renders a capped table from the CSV text when ready", () => {
    ready("name,score\nada,9\nlin,7");
    const { container } = render(<InlineData asset={dataAsset()} onOpen={vi.fn()} />);
    expect(container.querySelector(".convo-data-table")).toBeTruthy();
    // Header cells + two body rows.
    expect(container.querySelectorAll("thead th").length).toBe(2);
    expect(container.querySelectorAll("tbody tr").length).toBe(2);
    expect(screen.getByText("ada")).toBeTruthy();
    expect(screen.getByText("9")).toBeTruthy();
  });

  it("requests the content in text mode keyed off the descriptor ids", () => {
    ready("a,b\n1,2");
    render(<InlineData asset={dataAsset()} onOpen={vi.fn()} />);
    expect(useAssetContent).toHaveBeenCalledWith("c1", "as-data", { mode: "text" });
  });

  it("caps at 10 rows × 8 cols and shows the true N rows × M cols meta when over", () => {
    // 10 columns, 12 body rows — over both caps.
    const header = Array.from({ length: 10 }, (_, i) => `c${i}`).join(",");
    const body = Array.from({ length: 12 }, (_, r) =>
      Array.from({ length: 10 }, (_, c) => `${r}-${c}`).join(","),
    ).join("\n");
    ready(`${header}\n${body}`);
    const { container } = render(<InlineData asset={dataAsset()} onOpen={vi.fn()} />);
    // Columns clamped to 8, body rows clamped to 10.
    expect(container.querySelectorAll("thead th").length).toBe(8);
    expect(container.querySelectorAll("tbody tr").length).toBe(10);
    expect(container.querySelectorAll("tbody tr:first-child td").length).toBe(8);
    // Meta reports the FULL size, not the clamped one.
    expect(container.querySelector(".convo-open-full-meta").textContent).toBe(
      "12 rows × 10 cols",
    );
  });

  it("omits the meta when within the cap but still offers Open full table", () => {
    ready("a,b\n1,2\n3,4");
    const { container } = render(<InlineData asset={dataAsset()} onOpen={vi.fn()} />);
    expect(container.querySelector(".convo-open-full-meta")).toBeNull();
    expect(screen.getByText("Open full table")).toBeTruthy();
  });

  it("opens the panel via onOpen when Open full table is clicked", () => {
    ready("a,b\n1,2");
    const onOpen = vi.fn();
    const a = dataAsset();
    render(<InlineData asset={a} onOpen={onOpen} />);
    fireEvent.click(screen.getByText("Open full table"));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith(a);
  });

  it("does not throw when the affordance is clicked without an onOpen handler", () => {
    ready("a,b\n1,2");
    render(<InlineData asset={dataAsset()} />);
    expect(() => fireEvent.click(screen.getByText("Open full table"))).not.toThrow();
  });

  it("shows a placeholder while the text is loading", () => {
    useAssetContent.mockReturnValue({
      status: "loading",
      url: null,
      text: null,
      httpStatus: null,
    });
    const { container } = render(<InlineData asset={dataAsset()} onOpen={vi.fn()} />);
    expect(container.querySelector(".convo-data-loading")).toBeTruthy();
    expect(container.querySelector("table")).toBeNull();
  });

  it("falls back to the card when the content fetch errors", () => {
    useAssetContent.mockReturnValue({
      status: "error",
      url: null,
      text: null,
      httpStatus: 404,
    });
    const { container } = render(<InlineData asset={dataAsset()} onOpen={vi.fn()} />);
    expect(screen.getByText("metrics.csv")).toBeTruthy();
    expect(container.querySelector("table")).toBeNull();
  });

  it("falls back to the card when the hook stays idle (no fetchable ids)", () => {
    useAssetContent.mockReturnValue({
      status: "idle",
      url: null,
      text: null,
      httpStatus: null,
    });
    const { container } = render(<InlineData asset={dataAsset()} onOpen={vi.fn()} />);
    expect(screen.getByText("metrics.csv")).toBeTruthy();
    expect(container.querySelector("table")).toBeNull();
  });

  it("falls back to the card when the payload isn't parseable CSV (empty)", () => {
    ready("   ");
    const { container } = render(<InlineData asset={dataAsset()} onOpen={vi.fn()} />);
    expect(screen.getByText("metrics.csv")).toBeTruthy();
    expect(container.querySelector("table")).toBeNull();
  });

  it("falls back to the card when ready with null text (defensive || '')", () => {
    // status=ready but text=null (the hook normally hands back a string) —
    // `parseCsv(null || "")` yields an empty matrix → card. Covers the
    // falsy side of the `text || ""` guard.
    ready(null);
    const { container } = render(<InlineData asset={dataAsset()} onOpen={vi.fn()} />);
    expect(screen.getByText("metrics.csv")).toBeTruthy();
    expect(container.querySelector("table")).toBeNull();
  });

  it("passes onOpen through to the fallback card", () => {
    useAssetContent.mockReturnValue({
      status: "error",
      url: null,
      text: null,
      httpStatus: 502,
    });
    const onOpen = vi.fn();
    const a = dataAsset();
    render(<InlineData asset={a} onOpen={onOpen} />);
    fireEvent.click(screen.getByText("metrics.csv"));
    expect(onOpen).toHaveBeenCalledWith(a);
  });
});
