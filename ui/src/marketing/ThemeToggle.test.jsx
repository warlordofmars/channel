// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ThemeToggle from "./ThemeToggle.jsx";

describe("ThemeToggle", () => {
  let storage;

  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders a button with aria-label 'Toggle theme'", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: /toggle theme/i })).toBeTruthy();
  });

  it("renders the moon icon when site theme is light", () => {
    storage["channel-site-theme"] = "light";
    const { container } = render(<ThemeToggle />);
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("flips siteTheme on click", () => {
    storage["channel-site-theme"] = "light";
    render(<ThemeToggle />);
    const btn = screen.getByRole("button", { name: /toggle theme/i });
    fireEvent.click(btn);
    expect(storage["channel-site-theme"]).toBe("dark");
    fireEvent.click(btn);
    expect(storage["channel-site-theme"]).toBe("light");
  });
});
