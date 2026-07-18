// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/useAssetContent.js", () => ({
  useAssetContent: vi.fn(),
}));

import { useAssetContent } from "../hooks/useAssetContent.js";
import InlineDocument, { capMarkdown, isInlineDocument } from "./InlineDocument.jsx";

function docAsset(over = {}) {
  return {
    asset_id: "as-doc",
    chat_id: "c1",
    msg_id: "m1",
    kind: "document",
    title: "notes.md",
    mime: "text/markdown",
    size_bytes: 900,
    origin: "generated",
    created_at: "2026-07-18T00:00:00.000000+00:00",
    source: { msg_id: "m1" },
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

describe("isInlineDocument", () => {
  it("is true for a kind=document asset with a text markdown mime", () => {
    expect(isInlineDocument(docAsset({ mime: "text/markdown" }))).toBe(true);
    expect(isInlineDocument(docAsset({ mime: "text/plain" }))).toBe(true);
  });

  it("is ORIGIN-AGNOSTIC — an uploaded markdown file is inline-eligible (#363)", () => {
    expect(
      isInlineDocument(docAsset({ origin: "upload", mime: "text/markdown" })),
    ).toBe(true);
  });

  it("is false for a PDF (kind=document but application/pdf)", () => {
    expect(isInlineDocument(docAsset({ mime: "application/pdf" }))).toBe(false);
  });

  it("is false for a non-markdown text mime (html) — allowlist, not text/* prefix", () => {
    expect(isInlineDocument(docAsset({ mime: "text/html" }))).toBe(false);
  });

  it("is false when the mime is missing or non-string", () => {
    expect(isInlineDocument(docAsset({ mime: null }))).toBe(false);
    expect(isInlineDocument(docAsset({ mime: undefined }))).toBe(false);
  });

  it("is true exactly at the 5 MB byte cap and false one byte over", () => {
    const MB = 1024 * 1024;
    expect(isInlineDocument(docAsset({ size_bytes: 5 * MB }))).toBe(true);
    expect(isInlineDocument(docAsset({ size_bytes: 5 * MB + 1 }))).toBe(false);
  });

  it("is false when size_bytes is missing or non-numeric", () => {
    expect(isInlineDocument(docAsset({ size_bytes: null }))).toBe(false);
    expect(isInlineDocument(docAsset({ size_bytes: undefined }))).toBe(false);
  });

  it("is false for a non-document kind", () => {
    expect(isInlineDocument(docAsset({ kind: "data" }))).toBe(false);
  });

  it("is false for a null / undefined asset", () => {
    expect(isInlineDocument(null)).toBe(false);
    expect(isInlineDocument(undefined)).toBe(false);
  });
});

describe("capMarkdown", () => {
  it("returns short text unchanged and not truncated", () => {
    expect(capMarkdown("hello", 100, 10)).toEqual({
      text: "hello",
      truncated: false,
    });
  });

  it("truncates on the character cap", () => {
    expect(capMarkdown("abcdefghij", 5, 10)).toEqual({
      text: "abcde",
      truncated: true,
    });
  });

  it("truncates on the line cap", () => {
    expect(capMarkdown("a\nb\nc\nd", 100, 2)).toEqual({
      text: "a\nb",
      truncated: true,
    });
  });

  it("truncates on both caps together", () => {
    // 6 chars over the 4-char cap → slice to "a\nb\nc"; that is 3 lines,
    // over the 2-line cap → slice to "a\nb".
    expect(capMarkdown("a\nb\nc\nd\ne", 5, 2)).toEqual({
      text: "a\nb",
      truncated: true,
    });
  });

  it("does not truncate exactly at the boundaries", () => {
    expect(capMarkdown("abcde", 5, 10)).toEqual({
      text: "abcde",
      truncated: false,
    });
    expect(capMarkdown("a\nb", 100, 2)).toEqual({
      text: "a\nb",
      truncated: false,
    });
  });

  it("coerces a null / undefined payload to an empty string", () => {
    expect(capMarkdown(null)).toEqual({ text: "", truncated: false });
    expect(capMarkdown(undefined)).toEqual({ text: "", truncated: false });
  });
});

describe("InlineDocument", () => {
  it("renders markdown from the text when ready", () => {
    ready("# Report\n\nBody text here");
    render(<InlineDocument asset={docAsset()} onOpen={vi.fn()} />);
    expect(screen.getByText("Report")).toBeTruthy();
    expect(screen.getByText("Open full document")).toBeTruthy();
  });

  it("requests the content in text mode keyed off the descriptor ids", () => {
    ready("# Report");
    render(<InlineDocument asset={docAsset()} onOpen={vi.fn()} />);
    expect(useAssetContent).toHaveBeenCalledWith("c1", "as-doc", { mode: "text" });
  });

  it("clamps (fade) when the document exceeds the preview cap", () => {
    const long = Array.from({ length: 60 }, (_, i) => `line ${i}`).join("\n");
    ready(long);
    const { container } = render(
      <InlineDocument asset={docAsset()} onOpen={vi.fn()} />,
    );
    expect(container.querySelector(".convo-doc.is-clamped")).toBeTruthy();
  });

  it("does not clamp a short document", () => {
    ready("# Short\n\njust a line");
    const { container } = render(
      <InlineDocument asset={docAsset()} onOpen={vi.fn()} />,
    );
    expect(container.querySelector(".convo-doc")).toBeTruthy();
    expect(container.querySelector(".convo-doc.is-clamped")).toBeNull();
  });

  it("opens the panel via onOpen when Open full document is clicked", () => {
    ready("# Report");
    const onOpen = vi.fn();
    const a = docAsset();
    render(<InlineDocument asset={a} onOpen={onOpen} />);
    fireEvent.click(screen.getByText("Open full document"));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith(a);
  });

  it("does not throw when the affordance is clicked without an onOpen handler", () => {
    ready("# Report");
    render(<InlineDocument asset={docAsset()} />);
    expect(() =>
      fireEvent.click(screen.getByText("Open full document")),
    ).not.toThrow();
  });

  it("shows a placeholder while the text is loading", () => {
    useAssetContent.mockReturnValue({
      status: "loading",
      url: null,
      text: null,
      httpStatus: null,
    });
    const { container } = render(
      <InlineDocument asset={docAsset()} onOpen={vi.fn()} />,
    );
    expect(container.querySelector(".convo-doc-loading")).toBeTruthy();
    expect(container.querySelector(".convo-doc-body")).toBeNull();
  });

  it("falls back to the card when the content fetch errors", () => {
    useAssetContent.mockReturnValue({
      status: "error",
      url: null,
      text: null,
      httpStatus: 404,
    });
    const { container } = render(
      <InlineDocument asset={docAsset()} onOpen={vi.fn()} />,
    );
    expect(screen.getByText("notes.md")).toBeTruthy();
    expect(container.querySelector(".convo-doc-body")).toBeNull();
  });

  it("falls back to the card when the hook stays idle (no fetchable ids)", () => {
    useAssetContent.mockReturnValue({
      status: "idle",
      url: null,
      text: null,
      httpStatus: null,
    });
    const { container } = render(
      <InlineDocument asset={docAsset()} onOpen={vi.fn()} />,
    );
    expect(screen.getByText("notes.md")).toBeTruthy();
    expect(container.querySelector(".convo-doc-body")).toBeNull();
  });

  it("passes onOpen through to the fallback card", () => {
    useAssetContent.mockReturnValue({
      status: "error",
      url: null,
      text: null,
      httpStatus: 502,
    });
    const onOpen = vi.fn();
    const a = docAsset();
    render(<InlineDocument asset={a} onOpen={onOpen} />);
    fireEvent.click(screen.getByText("notes.md"));
    expect(onOpen).toHaveBeenCalledWith(a);
  });
});
