// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import Projects from "./Projects.jsx";
import { PROJECTS } from "../data.js";

function renderAtProjects() {
  let lastPath = null;
  function PathCatcher() {
    const { pathname } = useLocation();
    lastPath = pathname;
    return null;
  }
  const result = render(
    <MemoryRouter initialEntries={["/app/projects"]}>
      <Routes>
        <Route path="/app/projects" element={<><Projects /><PathCatcher /></>} />
        <Route path="/app/projects/:id" element={<PathCatcher />} />
      </Routes>
    </MemoryRouter>
  );
  return { ...result, getLastPath: () => lastPath };
}

describe("Projects", () => {
  it("renders the 'Projects' header + tagline", () => {
    renderAtProjects();
    expect(screen.getByRole("heading", { level: 2, name: "Projects" })).toBeTruthy();
    expect(screen.getByText(/Group related chats/i)).toBeTruthy();
  });

  it("renders one card per PROJECTS entry plus a 'New project' tile", () => {
    const { container } = renderAtProjects();
    const cards = container.querySelectorAll(".card");
    // PROJECTS.length tiles + 1 "new" tile.
    expect(cards.length).toBe(PROJECTS.length + 1);
    expect(screen.getByText(/New project/i)).toBeTruthy();
    // Each project name is present in the tree.
    for (const p of PROJECTS) {
      expect(screen.getByText(p.name)).toBeTruthy();
    }
  });

  it("clicking a project card navigates to /app/projects/<id>", () => {
    const { getLastPath } = renderAtProjects();
    fireEvent.click(screen.getByText("Analytics Rewrite"));
    expect(getLastPath()).toBe("/app/projects/p1");
  });

  it("clicking the 'New project' tile does not throw (no-op at Phase 6e)", () => {
    renderAtProjects();
    expect(() => fireEvent.click(screen.getByText(/New project/i))).not.toThrow();
  });

  it("each project card's badge is themed with the project's color hue", () => {
    const { container } = renderAtProjects();
    // The first PROJECTS entry (Analytics Rewrite) has color 42 → background = oklch(0.92 0.05 42).
    // The first .card.badge with that exact OKLCH proves the theming wiring.
    const badges = Array.from(container.querySelectorAll(".card .badge"));
    expect(badges.length).toBe(PROJECTS.length);
    expect(badges[0].style.background).toContain("oklch(0.92 0.05 42)");
  });
});
