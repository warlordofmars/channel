// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import {
  MODELS,
  EFFORTS,
  QUICK_ACTIONS,
  PROJECTS,
  ARTIFACTS,
  PROJECT_DOCS,
} from "./data.js";

describe("data mocks", () => {
  it("re-exports the Phase 6c mock arrays unchanged", () => {
    expect(MODELS.length).toBeGreaterThanOrEqual(3);
    expect(EFFORTS).toEqual(["Low", "Medium", "High", "Max"]);
    expect(QUICK_ACTIONS.length).toBe(4);
  });

  it("exports PROJECTS with the verbatim 6-entry mock array", () => {
    expect(PROJECTS).toHaveLength(6);
    // Spot-check a known entry — id, name, color all matter for routing + theming.
    expect(PROJECTS[0]).toMatchObject({
      id: "p1",
      name: "Analytics Rewrite",
      chats: 24,
      docs: 8,
      color: 42,
    });
    // Every entry needs the fields Projects.jsx + ProjectDetail.jsx read.
    for (const p of PROJECTS) {
      expect(typeof p.id).toBe("string");
      expect(typeof p.name).toBe("string");
      expect(typeof p.desc).toBe("string");
      expect(typeof p.chats).toBe("number");
      expect(typeof p.docs).toBe("number");
      expect(typeof p.color).toBe("number");
    }
  });

  it("exports ARTIFACTS with the verbatim 6-entry mock array", () => {
    expect(ARTIFACTS).toHaveLength(6);
    // Every renderer kind that ArtifactPanel switches on must be represented.
    const kinds = new Set(ARTIFACTS.map((a) => a.kind));
    expect(kinds).toEqual(new Set(["Document", "Code", "Interactive", "Chart", "Data"]));
    // project can be null (a3 has no project) — keep that intentional null.
    expect(ARTIFACTS.find((a) => a.id === "a3").project).toBeNull();
  });

  it("exports PROJECT_DOCS as a 4-string array used by ProjectDetail", () => {
    expect(PROJECT_DOCS).toEqual([
      "product-spec-v2.pdf",
      "events-schema.sql",
      "migration-notes.md",
      "q3-targets.csv",
    ]);
  });
});
