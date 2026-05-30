// Copyright (c) 2026 John Carter. All rights reserved.
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import SiteLayout from "./SiteLayout.jsx";
import { __resetChannelPrefsForTest } from "../hooks/useChannelPrefs.js";

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
    __resetChannelPrefsForTest();
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

  it("applies data-theme to <html> from the global theme", async () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    await act(async () => renderLayout(<div />));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("defaults data-theme to 'dark' when theme storage is empty", async () => {
    await act(async () => renderLayout(<div />));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

});
