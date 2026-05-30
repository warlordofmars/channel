// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import { cn } from "./utils";

describe("cn", () => {
  it("merges class names", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("handles conditional classes", () => {
    expect(cn("foo", false && "bar", "baz")).toBe("foo baz");
  });

  it("joins multiple class names", () => {
    expect(cn("bg-red-500", "bg-blue-500")).toBe("bg-red-500 bg-blue-500");
  });

  it("handles undefined and null gracefully", () => {
    expect(cn("foo", undefined, null)).toBe("foo");
  });
});
