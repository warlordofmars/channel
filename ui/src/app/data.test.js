// Copyright (c) 2026 John Carter. All rights reserved.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api.js", () => ({
  listModels: vi.fn(),
}));

import * as api from "../api.js";
import {
  EFFORTS,
  QUICK_ACTIONS,
  PROJECTS,
  ARTIFACTS,
  PROJECT_DOCS,
  MODEL_DISPLAY_META,
  cachedModels,
  loadModels,
  mergeWithDisplayMeta,
  __resetModelsCacheForTest,
} from "./data.js";

describe("data mocks", () => {
  it("re-exports the Phase 6c mock arrays unchanged", () => {
    expect(EFFORTS).toEqual(["Low", "Medium", "High", "Max"]);
    expect(QUICK_ACTIONS.length).toBe(4);
  });

  it("exports PROJECTS with the verbatim 6-entry mock array", () => {
    expect(PROJECTS).toHaveLength(6);
    expect(PROJECTS[0]).toMatchObject({
      id: "p1",
      name: "Analytics Rewrite",
      chats: 24,
      docs: 8,
      color: 42,
    });
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
    const kinds = new Set(ARTIFACTS.map((a) => a.kind));
    expect(kinds).toEqual(new Set(["Document", "Code", "Interactive", "Chart", "Data"]));
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

describe("MODEL_DISPLAY_META", () => {
  it("covers the three known model ids with short + desc fields", () => {
    expect(MODEL_DISPLAY_META["claude-opus-4-6"]).toMatchObject({
      short: expect.any(String),
      desc: expect.any(String),
    });
    expect(MODEL_DISPLAY_META["claude-sonnet-4-6"]).toMatchObject({
      short: expect.any(String),
      desc: expect.any(String),
    });
    expect(MODEL_DISPLAY_META["claude-haiku-4-5"]).toMatchObject({
      short: expect.any(String),
      desc: expect.any(String),
    });
  });
});

describe("mergeWithDisplayMeta", () => {
  it("layers display meta on top of an api row when the id is known", () => {
    const merged = mergeWithDisplayMeta({ id: "claude-opus-4-6", label: "Claude Opus 4.6", tier: "Flagship" });
    expect(merged.short).toBe("Opus 4.6");
    expect(merged.desc).toMatch(/Most capable/);
    expect(merged.name).toBe("Claude Opus 4.6");
    expect(merged.tier).toBe("Flagship");
  });

  it("falls back to label when the id has no display meta", () => {
    const merged = mergeWithDisplayMeta({ id: "claude-experimental-x", label: "Experimental X" });
    expect(merged.short).toBe("Experimental X");
    expect(merged.name).toBe("Experimental X");
    expect(merged.desc).toBe("");
  });

  it("falls back to id when neither label nor display meta is present", () => {
    const merged = mergeWithDisplayMeta({ id: "claude-mystery-z" });
    expect(merged.short).toBe("claude-mystery-z");
    expect(merged.name).toBe("claude-mystery-z");
    expect(merged.desc).toBe("");
  });
});

describe("loadModels / cachedModels", () => {
  beforeEach(() => {
    __resetModelsCacheForTest();
    api.listModels.mockReset();
  });
  afterEach(() => {
    __resetModelsCacheForTest();
  });

  it("returns null before the first fetch lands", () => {
    expect(cachedModels()).toBeNull();
  });

  it("fetches and caches the API allowlist, merged with display meta", async () => {
    api.listModels.mockResolvedValue({
      models: [
        { id: "claude-opus-4-6", label: "Claude Opus 4.6", tier: "Flagship" },
        { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", tier: "Balanced" },
      ],
    });
    const result = await loadModels();
    expect(result).toHaveLength(2);
    expect(result[0]).toMatchObject({ id: "claude-opus-4-6", short: "Opus 4.6" });
    expect(cachedModels()).toBe(result);
  });

  it("dedupes concurrent calls during the initial mount stampede", async () => {
    api.listModels.mockResolvedValue({
      models: [{ id: "claude-opus-4-6", label: "Claude Opus 4.6", tier: "Flagship" }],
    });
    const [a, b, c] = await Promise.all([loadModels(), loadModels(), loadModels()]);
    expect(api.listModels).toHaveBeenCalledTimes(1);
    expect(a).toBe(b);
    expect(b).toBe(c);
  });

  it("returns the cached value on subsequent calls without re-fetching", async () => {
    api.listModels.mockResolvedValue({
      models: [{ id: "claude-haiku-4-5", label: "Claude Haiku 4.5", tier: "Fast" }],
    });
    await loadModels();
    expect(api.listModels).toHaveBeenCalledTimes(1);
    await loadModels();
    expect(api.listModels).toHaveBeenCalledTimes(1);
  });

  it("rejects when the API call fails and does not cache the failure", async () => {
    api.listModels.mockRejectedValue(new Error("network"));
    await expect(loadModels()).rejects.toThrow("network");
    expect(cachedModels()).toBeNull();
    // A retry call should re-invoke the API.
    api.listModels.mockResolvedValue({
      models: [{ id: "claude-opus-4-6", label: "Claude Opus 4.6", tier: "Flagship" }],
    });
    await loadModels();
    expect(api.listModels).toHaveBeenCalledTimes(2);
  });
});
