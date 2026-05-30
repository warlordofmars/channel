// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Pricing from "./Pricing.jsx";

describe("Pricing page", () => {
  let storage;

  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  function renderPricing() {
    return render(<MemoryRouter><Pricing /></MemoryRouter>);
  }

  it("renders a top-level h1 heading", () => {
    renderPricing();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });

  it("renders all four tier names: Free, Pro, Max, Team", () => {
    renderPricing();
    const body = document.body.textContent || "";
    expect(body).toMatch(/\bFree\b/);
    expect(body).toMatch(/\bPro\b/);
    expect(body).toMatch(/\bMax\b/);
    expect(body).toMatch(/\bTeam\b/);
  });

  it("defaults to monthly billing on first visit", () => {
    renderPricing();
    const monthlyBtn = screen.getByRole("button", { name: /monthly/i });
    expect(monthlyBtn.className).toContain("on");
  });

  it("reads the saved cycle from localStorage", () => {
    storage["channel-pricing-cycle"] = "annual";
    renderPricing();
    const annualBtn = screen.getByRole("button", { name: /annual/i });
    expect(annualBtn.className).toContain("on");
  });

  it("clicking annual switches the displayed price text and persists the choice", () => {
    renderPricing();
    fireEvent.click(screen.getByRole("button", { name: /annual/i }));
    expect(storage["channel-pricing-cycle"]).toBe("annual");
    // Pro tier shows $16 (annual):
    expect(document.body.textContent).toMatch(/\$16/);
  });

  it("clicking monthly after annual restores monthly prices", () => {
    storage["channel-pricing-cycle"] = "annual";
    renderPricing();
    fireEvent.click(screen.getByRole("button", { name: /monthly/i }));
    expect(storage["channel-pricing-cycle"]).toBe("monthly");
    expect(document.body.textContent).toMatch(/\$20/);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderPricing();
    expect(document.querySelectorAll('a[href*="Channel "]').length).toBe(0);
  });

  it("readCycle falls back to 'monthly' when localStorage.getItem throws", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => { throw new Error("private mode"); },
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    renderPricing();
    const monthlyBtn = screen.getByRole("button", { name: /monthly/i });
    expect(monthlyBtn.className).toContain("on");
  });

  it("useEffect silently degrades when localStorage.setItem throws", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => { throw new Error("quota exceeded"); },
      removeItem: vi.fn(),
    });
    renderPricing();
    // Clicking annual should not crash even when setItem throws
    expect(() => fireEvent.click(screen.getByRole("button", { name: /annual/i }))).not.toThrow();
  });
});
