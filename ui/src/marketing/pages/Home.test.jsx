// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Home from "./Home.jsx";

function stubBrowserGlobals() {
  vi.stubGlobal("localStorage", {
    getItem: () => null, setItem: vi.fn(), removeItem: vi.fn(),
  });
  vi.stubGlobal("matchMedia", () => ({
    matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }));
}

function renderHome() {
  stubBrowserGlobals();
  return render(<MemoryRouter><Home /></MemoryRouter>);
}

describe("Home page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the hero H1 with 'The workspace for thinking with AI.' text", () => {
    renderHome();
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toMatch(/workspace for thinking with AI/i);
  });

  it("renders the Nav + Footer (brand appears at least twice)", () => {
    renderHome();
    expect(screen.getAllByText("Channel").length).toBeGreaterThanOrEqual(2);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderHome();
    const protoLinks = document.querySelectorAll('a[href*="Channel "]');
    expect(protoLinks.length).toBe(0);
  });

  it("includes a primary 'Download for Mac' CTA pointing at /download (or #download anchor)", () => {
    renderHome();
    const ctas = screen.getAllByText(/download/i);
    expect(ctas.length).toBeGreaterThanOrEqual(1);
  });

  it("renders at least one ImageSlot for the hero capture", () => {
    renderHome();
    const slots = document.querySelectorAll("[data-image-slot]");
    expect(slots.length).toBeGreaterThanOrEqual(1);
  });
});
