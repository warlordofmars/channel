// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Customize from "./Customize.jsx";
import { MODELS, EFFORTS } from "../data.js";
import { __resetChannelPrefsForTest } from "../../hooks/useChannelPrefs.js";

function renderCustomize() {
  return render(
    <MemoryRouter initialEntries={["/app/customize"]}>
      <Customize />
    </MemoryRouter>
  );
}

describe("Customize", () => {
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
    // Reset attrs the hook sets on <html>.
    for (const a of Array.from(document.documentElement.attributes)) {
      if (a.name.startsWith("data-")) document.documentElement.removeAttribute(a.name);
    }
    __resetChannelPrefsForTest();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the 'Customize' header + tagline", () => {
    renderCustomize();
    expect(screen.getByRole("heading", { level: 2, name: "Customize" })).toBeTruthy();
    expect(screen.getByText(/Tune Channel's appearance/i)).toBeTruthy();
  });

  it("renders three section headers: Appearance / Defaults / Behavior", () => {
    renderCustomize();
    expect(screen.getByRole("heading", { level: 3, name: "Appearance" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Defaults" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Behavior" })).toBeTruthy();
  });

  it("Theme seg-ctl marks the persisted theme as .on", () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    renderCustomize();
    expect(screen.getByRole("button", { name: /Light/ }).className).toContain("on");
    expect(screen.getByRole("button", { name: /^Dark$/ }).className).not.toContain("on");
  });

  it("clicking the Dark button persists theme=dark and updates the .on class", () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    renderCustomize();
    fireEvent.click(screen.getByRole("button", { name: /^Dark$/ }));
    expect(storage["channel-theme"]).toBe("dark");
    expect(screen.getByRole("button", { name: /^Dark$/ }).className).toContain("on");
  });

  it("renders 5 accent swatches with the correct OKLCH backgrounds", () => {
    const { container } = renderCustomize();
    const swatches = container.querySelectorAll(".swatch");
    expect(swatches.length).toBe(5);
    // First swatch = hue 42 (Clay): background = oklch(0.60 0.13 42).
    // jsdom normalises 0.60 → 0.6, so match the normalised form.
    expect(swatches[0].style.background).toMatch(/oklch\(0\.6 0\.13 42\)/);
    // The title attribute carries the human label.
    expect(swatches[0].getAttribute("title")).toBe("Clay");
  });

  it("clicking an accent swatch persists the hue as the new accent", () => {
    renderCustomize();
    const fern = document.body.querySelector("[title='Fern']"); // hue 150
    expect(fern).toBeTruthy();
    fireEvent.click(fern);
    expect(storage["channel-accent"]).toBe("150");
    // The clicked swatch now has the .on class.
    expect(fern.className).toContain("on");
  });

  it("Density seg-ctl persists the chosen value", () => {
    renderCustomize();
    // Default is "cozy".
    expect(screen.getByRole("button", { name: "Cozy" }).className).toContain("on");
    fireEvent.click(screen.getByRole("button", { name: "Compact" }));
    expect(storage["channel-density"]).toBe("compact");
    expect(screen.getByRole("button", { name: "Compact" }).className).toContain("on");
  });

  it("Default model seg-ctl renders one button per MODELS entry + persists the chosen id", () => {
    renderCustomize();
    for (const m of MODELS) {
      expect(screen.getByRole("button", { name: m.short })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: "Haiku 4.5" }));
    expect(storage["channel-model"]).toBe("claude-haiku-4-5");
    expect(screen.getByRole("button", { name: "Haiku 4.5" }).className).toContain("on");
  });

  it("Reasoning effort seg-ctl renders one button per EFFORTS entry + persists the choice", () => {
    renderCustomize();
    for (const e of EFFORTS) {
      expect(screen.getByRole("button", { name: e })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: "Max" }));
    expect(storage["channel-effort"]).toBe("Max");
    expect(screen.getByRole("button", { name: "Max" }).className).toContain("on");
  });

  it("Default-model hint text follows the active model's desc string", () => {
    storage["channel-model"] = "claude-haiku-4-5";
    __resetChannelPrefsForTest();
    renderCustomize();
    const haiku = MODELS.find((m) => m.id === "claude-haiku-4-5");
    // Hint paragraph for the model row carries the active model's desc.
    expect(screen.getByText(haiku.desc)).toBeTruthy();
  });

  it("renders three Behavior toggles with the correct default states", () => {
    const { container } = renderCustomize();
    const toggles = container.querySelectorAll(".toggle");
    expect(toggles.length).toBe(3);
    // Send on Enter (true), Show reasoning trace (false), Suggest follow-ups (true).
    expect(toggles[0].className).toContain("on");
    expect(toggles[1].className).not.toContain("on");
    expect(toggles[2].className).toContain("on");
  });

  it("clicking a Behavior toggle flips its .on class (UI-only — no localStorage write)", () => {
    const { container } = renderCustomize();
    const toggles = container.querySelectorAll(".toggle");
    fireEvent.click(toggles[1]); // Show reasoning trace — starts off
    expect(toggles[1].className).toContain("on");
    // No localStorage key written — these are UI-only at Phase 6f.
    expect(Object.keys(storage).filter((k) => k.startsWith("channel-behavior"))).toEqual([]);
    fireEvent.click(toggles[1]);
    expect(toggles[1].className).not.toContain("on");
  });

  it("clicking the Light button (when theme is dark) switches to light", () => {
    storage["channel-theme"] = "dark";
    __resetChannelPrefsForTest();
    renderCustomize();
    fireEvent.click(screen.getByRole("button", { name: /Light/ }));
    expect(storage["channel-theme"]).toBe("light");
  });

  it("clicking the Cozy button (when density is compact) switches to cozy", () => {
    storage["channel-density"] = "compact";
    __resetChannelPrefsForTest();
    renderCustomize();
    fireEvent.click(screen.getByRole("button", { name: "Cozy" }));
    expect(storage["channel-density"]).toBe("cozy");
  });

  it("Behavior toggles render their label + hint text", () => {
    renderCustomize();
    expect(screen.getByText("Send on Enter")).toBeTruthy();
    expect(screen.getByText(/Press Enter to send/)).toBeTruthy();
    expect(screen.getByText("Show reasoning trace")).toBeTruthy();
    expect(screen.getByText(/model's thinking/)).toBeTruthy();
    expect(screen.getByText("Suggest follow-ups")).toBeTruthy();
    expect(screen.getByText(/related prompts/)).toBeTruthy();
  });

  it("falls back to MODELS[0] when prefs.model points at an unknown id", () => {
    storage["channel-model"] = "no-such-model";
    __resetChannelPrefsForTest();
    renderCustomize();
    // MODELS[0] (Opus 4.8) should be the highlighted button + supply the hint.
    expect(screen.getByRole("button", { name: "Opus 4.8" }).className).toContain("on");
    const opus = MODELS[0];
    expect(screen.getByText(opus.desc)).toBeTruthy();
  });
});
