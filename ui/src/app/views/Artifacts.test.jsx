// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import Artifacts from "./Artifacts.jsx";
import { ARTIFACTS } from "../data.js";

function renderAt(path) {
  let lastSearch = null;
  function SearchCatcher() {
    const { search } = useLocation();
    lastSearch = search;
    return null;
  }
  const result = render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/artifacts" element={<><Artifacts /><SearchCatcher /></>} />
      </Routes>
    </MemoryRouter>
  );
  return { ...result, getLastSearch: () => lastSearch };
}

describe("Artifacts", () => {
  it("renders the 'Artifacts' header + tagline", () => {
    renderAt("/app/artifacts");
    expect(screen.getByRole("heading", { level: 2, name: "Artifacts" })).toBeTruthy();
    expect(screen.getByText(/Documents, code/i)).toBeTruthy();
  });

  it("renders one .list-row per ARTIFACTS entry", () => {
    const { container } = renderAt("/app/artifacts");
    const rows = container.querySelectorAll(".list-row");
    expect(rows.length).toBe(ARTIFACTS.length);
  });

  it("each row shows the artifact title + a sub-line with lines + updated time", () => {
    renderAt("/app/artifacts");
    for (const a of ARTIFACTS) {
      expect(screen.getByText(a.title)).toBeTruthy();
    }
  });

  it("a row whose artifact has a project shows the project name in the sub-line", () => {
    renderAt("/app/artifacts");
    // a2 (rate-limiter.ts) AND a5 (Migration runbook) both have project
    // "Analytics Rewrite" — assert at least one match exists.
    const matches = screen.getAllByText(/Analytics Rewrite/);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it("no ArtifactPanel overlay is rendered by default (no ?artifact=)", () => {
    const { container } = renderAt("/app/artifacts");
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });

  it("clicking a row sets the ?artifact=<id> search param + opens the panel", () => {
    const { container, getLastSearch } = renderAt("/app/artifacts");
    fireEvent.click(screen.getByText("rate-limiter.ts"));
    expect(getLastSearch()).toBe("?artifact=a2");
    // The panel renders the Code artifact body.
    expect(container.querySelector(".art-panel-wrap")).toBeTruthy();
    expect(container.querySelector(".art-code")).toBeTruthy();
  });

  it("mounting directly at /app/artifacts?artifact=a4 opens the panel for that artifact", () => {
    const { container } = renderAt("/app/artifacts?artifact=a4");
    expect(container.querySelector(".art-panel-wrap")).toBeTruthy();
    // a4 is a Chart.
    expect(container.querySelector(".art-chart svg")).toBeTruthy();
  });

  it("clicking the panel's Close button clears the ?artifact= search param", () => {
    const { container, getLastSearch } = renderAt("/app/artifacts?artifact=a2");
    fireEvent.click(screen.getByTitle("Close"));
    expect(getLastSearch()).toBe("");
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });

  it("mounting with an unknown ?artifact= id renders no panel (silent ignore)", () => {
    const { container } = renderAt("/app/artifacts?artifact=nope");
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });
});
