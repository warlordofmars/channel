// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/useAssetContent.js", () => ({
  useAssetContent: vi.fn(),
}));

import { useAssetContent } from "../hooks/useAssetContent.js";
import InlineImage, { isInlineImage } from "./InlineImage.jsx";

const MB = 1024 * 1024;

function imageAsset(over = {}) {
  return {
    asset_id: "as-img",
    chat_id: "c1",
    msg_id: "m1",
    kind: "image",
    title: "sunset.png",
    mime: "image/png",
    size_bytes: 3.8 * MB,
    origin: "generated",
    created_at: "2026-07-17T00:00:00.000000+00:00",
    source: { msg_id: "m1", tool_use_id: "t1" },
    ...over,
  };
}

beforeEach(() => {
  useAssetContent.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("isInlineImage", () => {
  it("is true for a raster image within the 5 MB cap", () => {
    expect(isInlineImage(imageAsset({ mime: "image/png" }))).toBe(true);
    expect(isInlineImage(imageAsset({ mime: "image/jpeg" }))).toBe(true);
    expect(isInlineImage(imageAsset({ mime: "image/gif" }))).toBe(true);
    expect(isInlineImage(imageAsset({ mime: "image/webp" }))).toBe(true);
  });

  it("is true exactly at the 5 MB boundary and false one byte over", () => {
    expect(isInlineImage(imageAsset({ size_bytes: 5 * MB }))).toBe(true);
    expect(isInlineImage(imageAsset({ size_bytes: 5 * MB + 1 }))).toBe(false);
  });

  it("is false for a non-raster image MIME", () => {
    expect(isInlineImage(imageAsset({ mime: "image/svg+xml" }))).toBe(false);
    expect(isInlineImage(imageAsset({ mime: "image/tiff" }))).toBe(false);
  });

  it("is false for a non-image kind", () => {
    expect(isInlineImage(imageAsset({ kind: "document", mime: "image/png" }))).toBe(
      false,
    );
  });

  it("is false when size_bytes is missing or non-numeric", () => {
    expect(isInlineImage(imageAsset({ size_bytes: null }))).toBe(false);
    expect(isInlineImage(imageAsset({ size_bytes: undefined }))).toBe(false);
  });

  it("is false for a null / undefined asset", () => {
    expect(isInlineImage(null)).toBe(false);
    expect(isInlineImage(undefined)).toBe(false);
  });
});

describe("InlineImage", () => {
  it("renders a responsive <img> from the blob object URL when ready", () => {
    useAssetContent.mockReturnValue({
      status: "ready",
      url: "blob:test-url",
      text: null,
      httpStatus: null,
    });
    render(<InlineImage asset={imageAsset()} onOpen={vi.fn()} />);
    const img = screen.getByRole("img");
    expect(img.getAttribute("src")).toBe("blob:test-url");
    expect(img.getAttribute("alt")).toBe("sunset.png");
    expect(img.getAttribute("loading")).toBe("lazy");
    expect(img.getAttribute("decoding")).toBe("async");
  });

  it("requests the content in blob mode keyed off the descriptor ids", () => {
    useAssetContent.mockReturnValue({
      status: "ready",
      url: "blob:test-url",
      text: null,
      httpStatus: null,
    });
    render(<InlineImage asset={imageAsset()} onOpen={vi.fn()} />);
    expect(useAssetContent).toHaveBeenCalledWith("c1", "as-img", { mode: "blob" });
  });

  it("opens the panel via onOpen when the image is clicked", () => {
    useAssetContent.mockReturnValue({
      status: "ready",
      url: "blob:test-url",
      text: null,
      httpStatus: null,
    });
    const onOpen = vi.fn();
    const a = imageAsset();
    render(<InlineImage asset={a} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith(a);
  });

  it("does not throw when clicked without an onOpen handler", () => {
    useAssetContent.mockReturnValue({
      status: "ready",
      url: "blob:test-url",
      text: null,
      httpStatus: null,
    });
    render(<InlineImage asset={imageAsset()} />);
    expect(() => fireEvent.click(screen.getByRole("button"))).not.toThrow();
  });

  it("shows a placeholder while the blob is loading", () => {
    useAssetContent.mockReturnValue({
      status: "loading",
      url: null,
      text: null,
      httpStatus: null,
    });
    const { container } = render(
      <InlineImage asset={imageAsset()} onOpen={vi.fn()} />,
    );
    expect(container.querySelector(".convo-img-loading")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("falls back to the card when the content fetch errors", () => {
    useAssetContent.mockReturnValue({
      status: "error",
      url: null,
      text: null,
      httpStatus: 404,
    });
    render(<InlineImage asset={imageAsset()} onOpen={vi.fn()} />);
    // The AssetCard fallback shows the title + kind·size meta, not an <img>.
    expect(screen.getByText("sunset.png")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("falls back to the card when the hook stays idle (no fetchable ids)", () => {
    useAssetContent.mockReturnValue({
      status: "idle",
      url: null,
      text: null,
      httpStatus: null,
    });
    render(<InlineImage asset={imageAsset()} onOpen={vi.fn()} />);
    expect(screen.getByText("sunset.png")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("passes onOpen through to the fallback card", () => {
    useAssetContent.mockReturnValue({
      status: "error",
      url: null,
      text: null,
      httpStatus: 502,
    });
    const onOpen = vi.fn();
    const a = imageAsset();
    render(<InlineImage asset={a} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onOpen).toHaveBeenCalledWith(a);
  });
});
