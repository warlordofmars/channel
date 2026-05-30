// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import { EFFORTS, MODELS, QUICK_ACTIONS, RECENTS } from "./data.js";

describe("MODELS", () => {
  it("exposes exactly the three Claude models (Opus 4.8, Sonnet 4.6, Haiku 4.5)", () => {
    expect(MODELS.length).toBe(3);
    const ids = MODELS.map((m) => m.id);
    expect(ids).toEqual(["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]);
  });

  it("each model has id, name, short, tier, desc string fields", () => {
    for (const m of MODELS) {
      expect(typeof m.id).toBe("string");
      expect(typeof m.name).toBe("string");
      expect(typeof m.short).toBe("string");
      expect(typeof m.tier).toBe("string");
      expect(typeof m.desc).toBe("string");
    }
  });
});

describe("EFFORTS", () => {
  it("is the Low / Medium / High / Max segmented set", () => {
    expect(EFFORTS).toEqual(["Low", "Medium", "High", "Max"]);
  });
});

describe("RECENTS", () => {
  it("has at least 10 entries grouped by time-bucket strings", () => {
    expect(RECENTS.length).toBeGreaterThanOrEqual(10);
    for (const r of RECENTS) {
      expect(typeof r.id).toBe("string");
      expect(typeof r.title).toBe("string");
      expect(typeof r.group).toBe("string");
    }
  });

  it("has at least one entry in 'Today' and 'Yesterday' groups", () => {
    const groups = new Set(RECENTS.map((r) => r.group));
    expect(groups.has("Today")).toBe(true);
    expect(groups.has("Yesterday")).toBe(true);
  });
});

describe("QUICK_ACTIONS", () => {
  it("has four chips: Write, Learn, Code, Analyze data", () => {
    expect(QUICK_ACTIONS.length).toBe(4);
    const labels = QUICK_ACTIONS.map((q) => q.label);
    expect(labels).toEqual(["Write", "Learn", "Code", "Analyze data"]);
  });

  it("each chip has id, icon, label string fields", () => {
    for (const q of QUICK_ACTIONS) {
      expect(typeof q.id).toBe("string");
      expect(typeof q.icon).toBe("string");
      expect(typeof q.label).toBe("string");
    }
  });
});
