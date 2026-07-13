// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AssetCard, { formatBytes } from "./AssetCard.jsx";

function asset(overrides = {}) {
  return {
    asset_id: "as-1",
    chat_id: "c1",
    msg_id: "m1",
    kind: "code",
    title: "fib.py",
    mime: "text/x-python",
    size_bytes: 2048,
    origin: "generated",
    created_at: "2026-07-13T00:00:00.000000+00:00",
    source: { msg_id: "m1", fence_index: 0, lang: "python" },
    ...overrides,
  };
}

describe("formatBytes", () => {
  it("returns '' for a non-numeric size", () => {
    expect(formatBytes(undefined)).toBe("");
    expect(formatBytes(null)).toBe("");
    expect(formatBytes(Number.NaN)).toBe("");
  });

  it("formats bytes under 1 KB as B", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(0)).toBe("0 B");
  });

  it("formats KB with one decimal under 10, rounded at/over 10", () => {
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(15_360)).toBe("15 KB");
  });

  it("formats MB with one decimal under 10, rounded at/over 10", () => {
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
    expect(formatBytes(12 * 1024 * 1024)).toBe("12 MB");
  });
});

describe("AssetCard", () => {
  it("renders the title and a kind · size meta line", () => {
    render(<AssetCard asset={asset()} onOpen={vi.fn()} />);
    expect(screen.getByText("fib.py")).toBeTruthy();
    expect(screen.getByText("code · 2.0 KB")).toBeTruthy();
  });

  it("drops the size from the meta line when size_bytes is absent", () => {
    render(<AssetCard asset={asset({ size_bytes: null })} onOpen={vi.fn()} />);
    // Meta line is just the kind, no separator.
    expect(screen.getByText("code")).toBeTruthy();
  });

  it("renders an icon for a known kind", () => {
    const { container } = render(
      <AssetCard asset={asset({ kind: "image" })} onOpen={vi.fn()} />,
    );
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("falls back to the generic file icon for an unknown kind", () => {
    const { container } = render(
      <AssetCard asset={asset({ kind: "mystery" })} onOpen={vi.fn()} />,
    );
    // Still renders (the `?? "file"` fallback path) without throwing.
    expect(container.querySelector("svg")).toBeTruthy();
    expect(screen.getByText(/^mystery ·/)).toBeTruthy();
  });

  it("calls onOpen with the asset when clicked", () => {
    const onOpen = vi.fn();
    const a = asset();
    render(<AssetCard asset={a} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith(a);
  });

  it("does not throw when clicked without an onOpen handler", () => {
    render(<AssetCard asset={asset()} />);
    expect(() => fireEvent.click(screen.getByRole("button"))).not.toThrow();
  });
});
