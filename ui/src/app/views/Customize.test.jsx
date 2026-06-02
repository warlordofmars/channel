// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api.js", () => ({
  listModels: vi.fn(),
}));

import * as api from "../../api.js";
import Customize from "./Customize.jsx";
import {
  EFFORTS,
  MODEL_DISPLAY_META,
  __resetModelsCacheForTest,
  loadModels,
} from "../data.js";
import {
  STORAGE_KEYS,
  __resetChannelPrefsForTest,
  __resetServerSyncForTest,
} from "../../hooks/useChannelPrefs.js";

const SERVER_ALLOWLIST = [
  { id: "claude-opus-4-6", label: "Claude Opus 4.6", tier: "Flagship" },
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", tier: "Balanced" },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5", tier: "Fast" },
];

function renderCustomize() {
  return render(
    <MemoryRouter initialEntries={["/app/customize"]}>
      <Customize />
    </MemoryRouter>
  );
}

describe("Customize", () => {
  let storage;
  beforeEach(async () => {
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
    __resetServerSyncForTest();
    __resetModelsCacheForTest();
    api.listModels.mockReset();
    api.listModels.mockResolvedValue({ models: SERVER_ALLOWLIST });
    // Pre-warm the cache so synchronous renders see the model list.
    await loadModels();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    __resetModelsCacheForTest();
  });

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
    expect(swatches[0].style.background).toMatch(/oklch\(0\.6 0\.13 42\)/);
    expect(swatches[0].getAttribute("title")).toBe("Clay");
  });

  it("clicking an accent swatch persists the hue as the new accent", () => {
    renderCustomize();
    const fern = document.body.querySelector("[title='Fern']"); // hue 150
    expect(fern).toBeTruthy();
    fireEvent.click(fern);
    expect(storage["channel-accent"]).toBe("150");
    expect(fern.className).toContain("on");
  });

  it("Density seg-ctl persists the chosen value", () => {
    renderCustomize();
    expect(screen.getByRole("button", { name: "Cozy" }).className).toContain("on");
    fireEvent.click(screen.getByRole("button", { name: "Compact" }));
    expect(storage["channel-density"]).toBe("compact");
    expect(screen.getByRole("button", { name: "Compact" }).className).toContain("on");
  });

  it("Default model seg-ctl renders one button per API allowlist entry + persists the chosen id", () => {
    renderCustomize();
    for (const sm of SERVER_ALLOWLIST) {
      const expected = MODEL_DISPLAY_META[sm.id]?.short ?? sm.label;
      expect(screen.getByRole("button", { name: expected })).toBeTruthy();
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
    const haikuDesc = MODEL_DISPLAY_META["claude-haiku-4-5"].desc;
    expect(screen.getByText(haikuDesc)).toBeTruthy();
  });

  it("renders three Behavior toggles with the correct default states", () => {
    const { container } = renderCustomize();
    const toggles = container.querySelectorAll(".toggle");
    expect(toggles.length).toBe(3);
    expect(toggles[0].className).toContain("on");
    expect(toggles[1].className).not.toContain("on");
    expect(toggles[2].className).toContain("on");
  });

  it("clicking each Behavior toggle persists the new value via the prefs hook", () => {
    const { container } = renderCustomize();
    const toggles = container.querySelectorAll(".toggle");

    fireEvent.click(toggles[0]);
    expect(toggles[0].className).not.toContain("on");
    expect(storage[STORAGE_KEYS.sendOnEnter]).toBe("0");

    fireEvent.click(toggles[1]);
    expect(toggles[1].className).toContain("on");
    expect(storage[STORAGE_KEYS.showReasoning]).toBe("1");

    fireEvent.click(toggles[2]);
    expect(toggles[2].className).not.toContain("on");
    expect(storage[STORAGE_KEYS.suggestFollowups]).toBe("0");
  });

  it("Behavior toggle initial state reflects persisted prefs (round-trip)", () => {
    storage[STORAGE_KEYS.sendOnEnter] = "0";
    storage[STORAGE_KEYS.showReasoning] = "1";
    storage[STORAGE_KEYS.suggestFollowups] = "0";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    const { container } = renderCustomize();
    const toggles = container.querySelectorAll(".toggle");
    expect(toggles[0].className).not.toContain("on");
    expect(toggles[1].className).toContain("on");
    expect(toggles[2].className).not.toContain("on");
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

  it("falls back to the first API entry when prefs.model points at an unknown id", () => {
    storage["channel-model"] = "no-such-model";
    __resetChannelPrefsForTest();
    renderCustomize();
    // The first API entry (Opus 4.6) should be highlighted + supply the hint.
    expect(screen.getByRole("button", { name: "Opus 4.6" }).className).toContain("on");
    expect(screen.getByText(MODEL_DISPLAY_META["claude-opus-4-6"].desc)).toBeTruthy();
  });

  it("renders a 'Loading models…' placeholder before the API resolves", async () => {
    __resetModelsCacheForTest();
    let resolvePromise;
    api.listModels.mockReturnValue(new Promise((resolve) => { resolvePromise = resolve; }));
    renderCustomize();
    expect(screen.getByTestId("models-loading")).toBeTruthy();
    // Resolve to silence the unhandled-promise warning.
    resolvePromise({ models: SERVER_ALLOWLIST });
    // Wait for the post-resolve render so afterEach's reset doesn't fire
    // while React is still processing the state update.
    await waitFor(() => expect(screen.queryByTestId("models-loading")).toBeNull());
  });

  it("renders an error placeholder when the API call fails", async () => {
    __resetModelsCacheForTest();
    api.listModels.mockRejectedValueOnce(new Error("network"));
    renderCustomize();
    await waitFor(() => expect(screen.getByTestId("models-error")).toBeTruthy());
  });
});
