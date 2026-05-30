// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import { colorFor, inkFor, artIcon } from "./artifactHelpers.js";

describe("artifactHelpers", () => {
  it("colorFor returns an OKLCH light-band swatch for the given hue", () => {
    expect(colorFor(42)).toBe("oklch(0.92 0.05 42)");
    expect(colorFor(310)).toBe("oklch(0.92 0.05 310)");
  });

  it("inkFor returns a darker OKLCH ink colour for legible foreground text", () => {
    expect(inkFor(42)).toBe("oklch(0.45 0.12 42)");
    expect(inkFor(310)).toBe("oklch(0.45 0.12 310)");
  });

  it("artIcon maps each known artifact kind to the right Icon name", () => {
    expect(artIcon("Code")).toBe("code");
    expect(artIcon("Chart")).toBe("customize");
    expect(artIcon("Interactive")).toBe("sparkle");
    expect(artIcon("Data")).toBe("database");
  });

  it("artIcon falls back to 'doc' for the default (Document) and any unknown kind", () => {
    expect(artIcon("Document")).toBe("doc");
    expect(artIcon("Unknown")).toBe("doc");
    expect(artIcon(undefined)).toBe("doc");
  });
});
