// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import {
  artIcon,
  colorFor,
  formatBytes,
  inkFor,
  kindLabel,
  relativeTime,
} from "./artifactHelpers.js";

describe("artifactHelpers", () => {
  it("colorFor returns an OKLCH light-band swatch for the given hue", () => {
    expect(colorFor(42)).toBe("oklch(0.92 0.05 42)");
    expect(colorFor(310)).toBe("oklch(0.92 0.05 310)");
  });

  it("inkFor returns a darker OKLCH ink colour for legible foreground text", () => {
    expect(inkFor(42)).toBe("oklch(0.45 0.12 42)");
    expect(inkFor(310)).toBe("oklch(0.45 0.12 310)");
  });

  it("artIcon maps each known asset kind to the right Icon glyph", () => {
    expect(artIcon("code")).toBe("code");
    expect(artIcon("data")).toBe("database");
    expect(artIcon("image")).toBe("image");
    expect(artIcon("diagram")).toBe("customize");
  });

  it("artIcon falls back to 'doc' for document and any unknown kind", () => {
    expect(artIcon("document")).toBe("doc");
    expect(artIcon("mystery")).toBe("doc");
    expect(artIcon(undefined)).toBe("doc");
  });

  it("kindLabel title-cases a lowercase kind", () => {
    expect(kindLabel("code")).toBe("Code");
    expect(kindLabel("document")).toBe("Document");
    expect(kindLabel("image")).toBe("Image");
  });

  it("kindLabel returns 'Asset' for a null/empty kind", () => {
    expect(kindLabel(null)).toBe("Asset");
    expect(kindLabel("")).toBe("Asset");
  });

  it("formatBytes renders bytes under 1 KB verbatim", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1023)).toBe("1023 B");
  });

  it("formatBytes steps through KB/MB/GB/TB with one decimal below 10 of a unit", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(20 * 1024)).toBe("20 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(3 * 1024 * 1024 * 1024)).toBe("3.0 GB");
    expect(formatBytes(2 * 1024 ** 4)).toBe("2.0 TB");
    // Overflow past TB stays in TB (the last unit).
    expect(formatBytes(5000 * 1024 ** 4)).toBe("5000 TB");
  });

  it("formatBytes returns an empty string for null / NaN", () => {
    expect(formatBytes(null)).toBe("");
    expect(formatBytes(undefined)).toBe("");
    expect(formatBytes(NaN)).toBe("");
  });

  it("relativeTime buckets an ISO timestamp against an injected now", () => {
    const now = Date.parse("2026-07-13T12:00:00Z");
    const at = (secondsAgo) => new Date(now - secondsAgo * 1000).toISOString();
    expect(relativeTime(at(10), now)).toBe("just now");
    expect(relativeTime(at(120), now)).toBe("2m ago");
    expect(relativeTime(at(3 * 3600), now)).toBe("3h ago");
    expect(relativeTime(at(2 * 86400), now)).toBe("2d ago");
    expect(relativeTime(at(10 * 86400), now)).toBe("1w ago");
    expect(relativeTime(at(60 * 86400), now)).toBe("2mo ago");
    expect(relativeTime(at(800 * 86400), now)).toBe("2y ago");
  });

  it("relativeTime clamps a future timestamp to 'just now'", () => {
    const now = Date.parse("2026-07-13T12:00:00Z");
    expect(relativeTime(new Date(now + 5000).toISOString(), now)).toBe("just now");
  });

  it("relativeTime returns an empty string for an unparseable timestamp", () => {
    expect(relativeTime("not-a-date", Date.now())).toBe("");
  });

  it("relativeTime returns an empty string for a missing timestamp (not the epoch)", () => {
    // `new Date(null)` is the epoch, so without the falsy guard this would
    // render a bogus multi-decade "Xy ago".
    expect(relativeTime(null, Date.now())).toBe("");
    expect(relativeTime(undefined, Date.now())).toBe("");
    expect(relativeTime("", Date.now())).toBe("");
  });

  it("relativeTime defaults now to the current clock", () => {
    expect(relativeTime(new Date().toISOString())).toBe("just now");
  });
});
