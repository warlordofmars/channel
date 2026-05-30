// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Footer from "./Footer.jsx";

function renderFooter() {
  return render(
    <MemoryRouter>
      <Footer />
    </MemoryRouter>
  );
}

describe("Footer", () => {
  it("renders three column headings: Product, Company, Download", () => {
    renderFooter();
    expect(screen.getByText("Product")).toBeTruthy();
    expect(screen.getByText("Company")).toBeTruthy();
    // The "Download" column heading exists; nav links may also exist with
    // that name. We assert >= 1.
    expect(screen.getAllByText("Download").length).toBeGreaterThanOrEqual(1);
  });

  it("renders the brand mark + wordmark in the footer", () => {
    const { container } = renderFooter();
    expect(container.querySelector(".ch-mark")).toBeTruthy();
    expect(screen.getByText("Channel")).toBeTruthy();
  });

  it("includes a current-year copyright line", () => {
    renderFooter();
    const year = new Date().getFullYear().toString();
    const text = document.body.textContent || "";
    expect(text.includes(year)).toBe(true);
  });

  it("uses React Router Link for internal product/company links", () => {
    renderFooter();
    // /product, /pricing, /about, /privacy etc. all use Link → href starts with /
    const internalAnchors = Array.from(document.querySelectorAll("footer a"))
      .filter((a) => a.getAttribute("href")?.startsWith("/"));
    expect(internalAnchors.length).toBeGreaterThanOrEqual(4);
  });
});
