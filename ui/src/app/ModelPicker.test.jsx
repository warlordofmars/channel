// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api.js", () => ({
  listModels: vi.fn(),
}));

import * as api from "../api.js";
import ModelPicker from "./ModelPicker.jsx";
import { __resetModelsCacheForTest } from "./data.js";

// Synthetic test allowlist — exercises every assertion path without
// depending on the production model ids.
const SERVER_ALLOWLIST = [
  { id: "claude-opus-4-6", label: "Claude Opus 4.6", tier: "Flagship" },
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", tier: "Balanced" },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5", tier: "Fast" },
];

// Display-merged shapes that mergeWithDisplayMeta produces — these are
// what consumers actually see in the picker. Mirrors the production
// MODEL_DISPLAY_META keys.
const opus = { id: "claude-opus-4-6", name: "Claude Opus 4.6", short: "Opus 4.6", tier: "Flagship", desc: "Most capable" };
const sonnet = { id: "claude-sonnet-4-6", name: "Claude Sonnet 4.6", short: "Sonnet 4.6", tier: "Balanced", desc: "Best blend" };

beforeEach(() => {
  vi.clearAllMocks();
  __resetModelsCacheForTest();
  api.listModels.mockResolvedValue({ models: SERVER_ALLOWLIST });
});

afterEach(() => {
  __resetModelsCacheForTest();
});

describe("ModelPicker", () => {
  it("renders the trigger button with the current model's short name and effort", () => {
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    const btn = screen.getByRole("button");
    expect(btn.textContent).toContain("Opus 4.6");
    expect(btn.textContent).toContain("High");
  });

  it("opens the popover on click and shows every API-sourced model", async () => {
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByText("Claude Opus 4.6")).toBeTruthy());
    expect(screen.getByText("Claude Sonnet 4.6")).toBeTruthy();
    expect(screen.getByText("Claude Haiku 4.5")).toBeTruthy();
  });

  it("shows a check next to the selected model and not on others", async () => {
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByText("Claude Opus 4.6")).toBeTruthy());
    const opusOpt = screen.getByText("Claude Opus 4.6").closest(".opt");
    const sonnetOpt = screen.getByText("Claude Sonnet 4.6").closest(".opt");
    expect(opusOpt.querySelector(".ck")).toBeTruthy();
    expect(sonnetOpt.querySelector(".ck")).toBeFalsy();
  });

  it("clicking a different model invokes onModel with that model object", async () => {
    const onModel = vi.fn();
    render(<ModelPicker model={opus} effort="High" onModel={onModel} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByText("Claude Sonnet 4.6")).toBeTruthy());
    fireEvent.click(screen.getByText("Claude Sonnet 4.6"));
    expect(onModel).toHaveBeenCalledWith(expect.objectContaining({ id: sonnet.id }));
  });

  it("renders the 4 effort segments (Low/Medium/High/Max) with the current highlighted", () => {
    render(<ModelPicker model={opus} effort="Medium" onModel={vi.fn()} onEffort={vi.fn()} />);
    fireEvent.click(screen.getByRole("button"));
    const low = screen.getByRole("button", { name: "Low" });
    const med = screen.getByRole("button", { name: "Medium" });
    expect(med.className).toContain("on");
    expect(low.className).not.toContain("on");
  });

  it("clicking an effort segment invokes onEffort with the new value", () => {
    const onEffort = vi.fn();
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={onEffort} />);
    fireEvent.click(screen.getByRole("button", { name: /opus/i }));
    fireEvent.click(screen.getByRole("button", { name: "Max" }));
    expect(onEffort).toHaveBeenCalledWith("Max");
  });

  it("clicking the backdrop closes the popover", () => {
    const { container } = render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /opus/i }));
    expect(container.querySelector(".pop")).toBeTruthy();
    fireEvent.click(container.querySelector(".backdrop"));
    expect(container.querySelector(".pop")).toBeFalsy();
  });

  it("falls back to model.name when model.short is undefined", () => {
    const noShortModel = { id: "x", name: "Custom Model", short: undefined, tier: "Custom", desc: "Test fallback" };
    render(<ModelPicker model={noShortModel} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    expect(screen.getByRole("button").textContent).toContain("Custom Model");
  });

  it("shows a 'Loading models…' row before the API resolves", async () => {
    // Hold the API promise open so the loading state stays visible.
    let resolvePromise;
    api.listModels.mockReturnValue(new Promise((resolve) => { resolvePromise = resolve; }));
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByTestId("models-loading")).toBeTruthy();
    // Resolve to avoid an unhandled-rejection warning at teardown.
    resolvePromise({ models: SERVER_ALLOWLIST });
  });

  it("shows an error row with a Retry affordance on API failure", async () => {
    api.listModels.mockRejectedValueOnce(new Error("network"));
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByTestId("models-error")).toBeTruthy());

    // Retry path — second call resolves with the full list.
    api.listModels.mockResolvedValueOnce({ models: SERVER_ALLOWLIST });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByText("Claude Opus 4.6")).toBeTruthy());
  });

  it("renders only the server allowlist on success", async () => {
    api.listModels.mockReset();
    api.listModels.mockResolvedValue({
      models: [{ id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", tier: "Balanced" }],
    });
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByText("Claude Sonnet 4.6")).toBeTruthy());
    expect(screen.queryByText("Claude Opus 4.6")).toBeNull();
    expect(screen.queryByText("Claude Haiku 4.5")).toBeNull();
  });

  it("uses server label when the id has no client display meta", async () => {
    api.listModels.mockReset();
    api.listModels.mockResolvedValue({
      models: [{ id: "claude-experimental-x", label: "Experimental X", tier: "Custom" }],
    });
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByText("Experimental X")).toBeTruthy());
  });

  it("falls back to the id when the server returns no label and the id is unknown", async () => {
    api.listModels.mockReset();
    api.listModels.mockResolvedValue({
      models: [{ id: "claude-mystery-z" }],
    });
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByText("claude-mystery-z")).toBeTruthy());
  });
});
