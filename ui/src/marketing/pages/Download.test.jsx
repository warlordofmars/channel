// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

describe("Download — releases/latest URLs (default channel)", () => {
  afterEach(() => vi.unstubAllGlobals());

  // Default behaviour (VITE_RELEASE_CHANNEL unset → "latest"): links
  // point at the unversioned ``releases/latest/download/`` redirect so
  // the URL never goes stale. GitHub resolves ``latest`` to the most
  // recent non-prerelease release at click time. The prod build does
  // not set VITE_RELEASE_CHANNEL, so the default path is what ships
  // on the prod marketing site.
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

describe("Download — releases/<channel> URLs (VITE_RELEASE_CHANNEL=dev)", () => {
  // The dev marketing site sets VITE_RELEASE_CHANNEL=dev at SPA build
  // time (see deploy-dev in ci.yml). The Download page must then point
  // at the explicit-tag release form so the buttons resolve to a real
  // asset on the ``dev`` pre-release, not the not-yet-existing
  // ``latest`` redirect target.
  //
  // CHANNEL + RELEASE_BASE are module-level constants in Download.jsx
  // (evaluated at import time), so we ``stubEnv`` + ``resetModules`` +
  // dynamic-import a fresh module instance to exercise the non-default
  // branch.
  const devBase = "https://github.com/warlordofmars/channel/releases/download/dev";

  beforeEach(() => {
    vi.stubEnv("VITE_RELEASE_CHANNEL", "dev");
    vi.resetModules();
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  async function renderPageWithChannel() {
    const mod = await import("./Download.jsx");
    const Component = mod.default;
    return render(<MemoryRouter><Component /></MemoryRouter>);
  }

  it.each([
    ["Download .dmg",      `${devBase}/Channel-mac.dmg`],
    ["Download .exe",      `${devBase}/Channel-Setup.exe`],
    ["Download AppImage",  `${devBase}/Channel-linux.AppImage`],
  ])("primary button %s points at the dev pre-release asset %s", async (label, expectedHref) => {
    await renderPageWithChannel();
    const link = screen.getByRole("link", { name: new RegExp(label, "i") });
    expect(link.getAttribute("href")).toBe(expectedHref);
  });

  it.each([
    [".deb", `${devBase}/Channel-linux.deb`],
    [".rpm", `${devBase}/Channel-linux.rpm`],
  ])("secondary Linux link %s points at the dev pre-release asset %s", async (label, expectedHref) => {
    await renderPageWithChannel();
    const link = screen.getByRole("link", { name: new RegExp(`^\\${label}$`, "i") });
    expect(link.getAttribute("href")).toBe(expectedHref);
  });

  it("every download link uses the releases/download/dev/ tagged-asset form", async () => {
    await renderPageWithChannel();
    const downloadLinks = screen
      .getAllByRole("link")
      .map((a) => a.getAttribute("href") ?? "")
      .filter((href) => href.includes("/releases/"));
    expect(downloadLinks.length).toBeGreaterThan(0);
    for (const href of downloadLinks) {
      expect(href).toContain("/releases/download/dev/");
      expect(href).not.toContain("/releases/latest/");
    }
  });
});

describe("Download — empty-string VITE_RELEASE_CHANNEL falls back to latest", () => {
  // An accidental ``VITE_RELEASE_CHANNEL=`` (empty string) in CI must
  // NOT produce ``/releases/download//<asset>`` (the double-slash form
  // is a guaranteed 404). The Download module uses ``|| "latest"``
  // (not ``?? "latest"``) so empty-string treats as falsy and the
  // fallback kicks in.
  beforeEach(() => {
    vi.stubEnv("VITE_RELEASE_CHANNEL", "");
    vi.resetModules();
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("empty-string channel falls back to the releases/latest redirect", async () => {
    const mod = await import("./Download.jsx");
    const Component = mod.default;
    render(<MemoryRouter><Component /></MemoryRouter>);
    const downloadLinks = screen
      .getAllByRole("link")
      .map((a) => a.getAttribute("href") ?? "")
      .filter((href) => href.includes("/releases/"));
    expect(downloadLinks.length).toBeGreaterThan(0);
    for (const href of downloadLinks) {
      expect(href).toContain("/releases/latest/download/");
      expect(href).not.toContain("/releases/download//");
    }
  });
});
