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
});

describe("Download — releases/latest URLs", () => {
  afterEach(() => vi.unstubAllGlobals());

  // Per #119: links point at the unversioned ``releases/latest/download/``
  // redirect so the URL never goes stale. GitHub resolves ``latest`` to
  // the most recent non-prerelease release at click time. PR #80's prior
  // ``releases/download/dev/`` form was a stop-gap before #116 wired up
  // proper versioned releases on push to main.
  const releasesBase = "https://github.com/warlordofmars/channel/releases/latest/download";

  function renderPage() {
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    return render(<MemoryRouter><Download /></MemoryRouter>);
  }

  it.each([
    ["Download .dmg",      `${releasesBase}/Channel-mac.dmg`],
    ["Download .exe",      `${releasesBase}/Channel-Setup.exe`],
    ["Download AppImage",  `${releasesBase}/Channel-linux.AppImage`],
  ])("primary button %s links to %s", (label, expectedHref) => {
    renderPage();
    const link = screen.getByRole("link", { name: new RegExp(label, "i") });
    expect(link.getAttribute("href")).toBe(expectedHref);
  });

  it.each([
    [".deb", `${releasesBase}/Channel-linux.deb`],
    [".rpm", `${releasesBase}/Channel-linux.rpm`],
  ])("secondary Linux link %s points at %s", (label, expectedHref) => {
    renderPage();
    const link = screen.getByRole("link", { name: new RegExp(`^\\${label}$`, "i") });
    expect(link.getAttribute("href")).toBe(expectedHref);
  });

  it("does not render the dev-preview banner", () => {
    renderPage();
    expect(screen.queryByTestId("dev-preview-banner")).toBeNull();
  });

  it("does not render an Intel-specific Mac download link (universal binary)", () => {
    renderPage();
    expect(screen.queryByText(/intel mac/i)).toBeNull();
  });

  it("notes the macOS-only auto-update story", () => {
    renderPage();
    expect(screen.getByText(/macOS auto-updates in the background/i)).toBeTruthy();
  });

  it("every download link uses the releases/latest redirect (forward-compatible URL)", () => {
    renderPage();
    const downloadLinks = screen
      .getAllByRole("link")
      .map((a) => a.getAttribute("href") ?? "")
      .filter((href) => href.includes("/releases/"));
    expect(downloadLinks.length).toBeGreaterThan(0);
    for (const href of downloadLinks) {
      expect(href).toContain("/releases/latest/download/");
    }
  });
});
