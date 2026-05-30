// Copyright (c) 2026 John Carter. All rights reserved.
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import SiteLayout from "./SiteLayout.jsx";

function renderLayout(children) {
  return render(
    <MemoryRouter>
      <SiteLayout>{children}</SiteLayout>
    </MemoryRouter>
  );
}

describe("SiteLayout", () => {
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
    document.documentElement.removeAttribute("data-theme");
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the supplied children inside a <main>", () => {
    renderLayout(<div data-testid="page-body">Body</div>);
    expect(screen.getByTestId("page-body")).toBeTruthy();
  });

  it("renders Nav and Footer alongside the children", () => {
    renderLayout(<div data-testid="page-body" />);
    // Brand text appears in both Nav and Footer
    const brand = screen.getAllByText("Channel");
    expect(brand.length).toBeGreaterThanOrEqual(2);
  });

  it("applies data-theme to <html> from siteTheme", async () => {
    storage["channel-site-theme"] = "dark";
    await act(async () => renderLayout(<div />));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("defaults data-theme to 'light' when site storage is empty", async () => {
    await act(async () => renderLayout(<div />));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("restores the app theme on unmount", async () => {
    storage["channel-theme"] = "dark";
    storage["channel-site-theme"] = "light";
    const { unmount } = await act(async () => renderLayout(<div />));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    await act(async () => unmount());
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("falls back to default app theme on unmount when storage throws", async () => {
    storage["channel-site-theme"] = "light";
    const { unmount } = await act(async () => renderLayout(<div />));
    // After mount, swap localStorage for one that throws on read so the
    // unmount-time `getItem` hits the catch branch.
    vi.stubGlobal("localStorage", {
      getItem: () => { throw new Error("denied"); },
      setItem: () => {},
      removeItem: () => {},
    });
    await act(async () => unmount());
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});
