// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Nav from "./Nav.jsx";

function renderNavAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Nav />
    </MemoryRouter>
  );
}

describe("Nav", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the brand link pointing at /", () => {
    renderNavAt("/");
    const brand = screen.getByText("Channel").closest("a");
    expect(brand.getAttribute("href")).toBe("/");
  });

  it("Product / Models / Pricing / Download route to their dedicated pages", () => {
    renderNavAt("/");
    expect(screen.getByRole("link", { name: "Product" }).getAttribute("href")).toBe("/product");
    expect(screen.getByRole("link", { name: "Models" }).getAttribute("href")).toBe("/models");
    expect(screen.getByRole("link", { name: "Pricing" }).getAttribute("href")).toBe("/pricing");
    const downloads = screen.getAllByRole("link", { name: /^download/i });
    for (const d of downloads) expect(d.getAttribute("href")).toBe("/download");
  });

  it("marks the active route's link with the .on class", () => {
    renderNavAt("/product");
    expect(screen.getByRole("link", { name: "Product" }).className).toContain("on");
    expect(screen.getByRole("link", { name: "Models" }).className).not.toContain("on");
  });

  it("renders a Docs link to /docs/ outside the SPA route tree (#231)", () => {
    renderNavAt("/");
    const docs = screen.getByRole("link", { name: "Docs" });
    expect(docs.getAttribute("href")).toBe("/docs/");
    expect(docs.tagName).toBe("A");
    // Not active on a marketing route.
    expect(docs.className).not.toContain("on");
  });

  it("marks the Docs link active on a /docs path (parity no-op) (#231)", () => {
    // No SPA route is /docs today, but the active-state check is kept for
    // parity; MemoryRouter exercises the startsWith('/docs') branch.
    renderNavAt("/docs/getting-started");
    expect(screen.getByRole("link", { name: "Docs" }).className).toContain("on");
  });

  it("renders a Sign in link pointing at /app", () => {
    renderNavAt("/");
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/app");
  });

  it("renders the ThemeToggle button", () => {
    renderNavAt("/");
    expect(screen.getByRole("button", { name: /toggle theme/i })).toBeTruthy();
  });
});
