// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Footer from "./Footer.jsx";

function renderFooterAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Footer />
    </MemoryRouter>
  );
}

describe("Footer", () => {
  it("renders three column headings: Product, Company, Download", () => {
    renderFooterAt("/");
    expect(screen.getByText("Product")).toBeTruthy();
    expect(screen.getByText("Company")).toBeTruthy();
    // The "Download" column heading exists; nav links may also exist with
    // that name. We assert >= 1.
    expect(screen.getAllByText("Download").length).toBeGreaterThanOrEqual(1);
  });

  it("renders the brand mark + wordmark in the footer", () => {
    const { container } = renderFooterAt("/");
    expect(container.querySelector(".ch-mark")).toBeTruthy();
    expect(screen.getByText("Channel")).toBeTruthy();
  });

  it("includes a current-year copyright line", () => {
    renderFooterAt("/");
    const year = new Date().getFullYear().toString();
    const text = document.body.textContent || "";
    expect(text.includes(year)).toBe(true);
  });

  it("on Home, Features/Models/macOS/Windows/Linux are in-page anchors", () => {
    renderFooterAt("/");
    expect(screen.getByRole("link", { name: "Features" }).getAttribute("href")).toBe("#features");
    expect(screen.getByRole("link", { name: "Models" }).getAttribute("href")).toBe("#models");
    expect(screen.getByRole("link", { name: "macOS" }).getAttribute("href")).toBe("#download");
    expect(screen.getByRole("link", { name: "Windows" }).getAttribute("href")).toBe("#download");
    expect(screen.getByRole("link", { name: "Linux" }).getAttribute("href")).toBe("#download");
  });

  it("off Home, Features/Models/Download links route to dedicated pages", () => {
    renderFooterAt("/pricing");
    expect(screen.getByRole("link", { name: "Features" }).getAttribute("href")).toBe("/product");
    expect(screen.getByRole("link", { name: "Models" }).getAttribute("href")).toBe("/models");
    for (const name of ["macOS", "Windows", "Linux"]) {
      expect(screen.getByRole("link", { name }).getAttribute("href")).toBe("/download");
    }
  });

  it("renders a Docs link to /docs/ as a plain <a> outside the SPA (#231)", () => {
    renderFooterAt("/");
    const docs = screen.getByRole("link", { name: "Docs" });
    expect(docs.getAttribute("href")).toBe("/docs/");
    expect(docs.tagName).toBe("A");
  });
});
