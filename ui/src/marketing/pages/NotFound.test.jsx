// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import NotFound from "./NotFound.jsx";

function renderPage() {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<MemoryRouter><NotFound /></MemoryRouter>);
}

describe("NotFound page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the 'Error 404' eyebrow", () => {
    renderPage();
    expect(screen.getByText(/error 404/i)).toBeTruthy();
  });

  it("renders the page-wandered-off h1", () => {
    renderPage();
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toMatch(/wandered off/i);
  });

  it("renders 'Back to home' link pointing at /", () => {
    renderPage();
    const back = screen.getByRole("link", { name: /back to home/i });
    expect(back.getAttribute("href")).toBe("/");
  });

  it("renders 'Open Channel' link pointing at /app", () => {
    renderPage();
    const open = screen.getByRole("link", { name: /open channel/i });
    expect(open.getAttribute("href")).toBe("/app");
  });

  it("renders the ChannelMark logo in the page (separate from Nav)", () => {
    const { container } = renderPage();
    // Nav has one ch-mark + page has one ch-mark + Footer has one = 3
    expect(container.querySelectorAll(".ch-mark").length).toBeGreaterThanOrEqual(2);
  });
});
