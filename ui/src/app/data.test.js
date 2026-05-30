// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import { MODELS, EFFORTS, RECENTS, QUICK_ACTIONS, SAMPLE_REPLY, SAMPLE_USER } from "./data.js";

describe("data mocks", () => {
  it("re-exports the Phase 6c mock arrays unchanged", () => {
    expect(MODELS.length).toBeGreaterThanOrEqual(3);
    expect(EFFORTS).toEqual(["Low", "Medium", "High", "Max"]);
    expect(RECENTS.length).toBeGreaterThanOrEqual(15);
    expect(QUICK_ACTIONS.length).toBe(4);
  });

  it("exports SAMPLE_USER as the canned question that pairs with SAMPLE_REPLY", () => {
    expect(SAMPLE_USER).toMatch(/columnar store/i);
    expect(SAMPLE_USER).toMatch(/ingest/i);
  });

  it("exports SAMPLE_REPLY with markdown features the renderer must handle", () => {
    // Has paragraphs (\n\n), bold (**...**), and at least one ordered-list line.
    expect(SAMPLE_REPLY).toMatch(/\n\n/);
    expect(SAMPLE_REPLY).toMatch(/\*\*[^*]+\*\*/);
    expect(SAMPLE_REPLY).toMatch(/^\d+\.\s/m);
  });
});
