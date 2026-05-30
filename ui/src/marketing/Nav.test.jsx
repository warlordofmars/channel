// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Nav from "./Nav.jsx";

function renderNav() {
  return render(
    <MemoryRouter>
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
    renderNav();
    const brand = screen.getByText("Channel").closest("a");
    expect(brand.getAttribute("href")).toBe("/");
  });

  it("renders four primary nav links: Product, Models, Pricing, Download", () => {
    renderNav();
    expect(screen.getByRole("link", { name: "Product" }).getAttribute("href")).toBe("/product");
    expect(screen.getByRole("link", { name: "Models" }).getAttribute("href")).toBe("/models");
    expect(screen.getByRole("link", { name: "Pricing" }).getAttribute("href")).toBe("/pricing");
    expect(screen.getByRole("link", { name: "Download" }).getAttribute("href")).toBe("/download");
  });

  it("renders a Sign in link pointing at /app", () => {
    renderNav();
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/app");
  });

  it("renders the ThemeToggle button", () => {
    renderNav();
    expect(screen.getByRole("button", { name: /toggle theme/i })).toBeTruthy();
  });

  it("renders a Download CTA button pointing at /download", () => {
    renderNav();
    const downloads = screen.getAllByRole("link", { name: /download/i });
    // Two: one in nav-links row, one in nav-right CTA. Both → /download.
    expect(downloads.length).toBeGreaterThanOrEqual(2);
    for (const d of downloads) expect(d.getAttribute("href")).toBe("/download");
  });
});
