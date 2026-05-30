// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Download from "./Download.jsx";

function renderPage() {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  return render(<MemoryRouter><Download /></MemoryRouter>);
}

describe("Download page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders a top-level h1 heading", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });

  it("mentions all three desktop platforms", () => {
    renderPage();
    const body = document.body.textContent || "";
    expect(body).toMatch(/mac/i);
    expect(body).toMatch(/windows/i);
    expect(body).toMatch(/linux/i);
  });

  it("renders Nav + Footer", () => {
    renderPage();
    expect(screen.getAllByText("Channel").length).toBeGreaterThanOrEqual(2);
  });

  it("rewrote every prototype 'Channel*.html' href to a Router path", () => {
    renderPage();
    expect(document.querySelectorAll('a[href*="Channel "]').length).toBe(0);
  });

  it("renders the dev-preview banner explaining unsigned builds", () => {
    renderPage();
    const banner = screen.getByTestId("dev-preview-banner");
    expect(banner).toBeTruthy();
    expect(banner.textContent || "").toMatch(/dev preview/i);
    expect(banner.textContent || "").toMatch(/unsigned/i);
  });

  it("points each platform download at the dev GitHub release", () => {
    renderPage();
    const base = "https://github.com/warlordofmars/channel/releases/download/dev";
    const hrefs = Array.from(document.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(hrefs).toContain(`${base}/Channel-mac-arm64.dmg`);
    expect(hrefs).toContain(`${base}/Channel-mac-x64.dmg`);
    expect(hrefs).toContain(`${base}/Channel-Setup-win.exe`);
    expect(hrefs).toContain(`${base}/Channel-linux.AppImage`);
    expect(hrefs).toContain(`${base}/Channel-linux.deb`);
    expect(hrefs).toContain(`${base}/Channel-linux.rpm`);
  });
});
