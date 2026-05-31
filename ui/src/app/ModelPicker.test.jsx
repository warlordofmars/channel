// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api.js", () => ({
  listModels: vi.fn(),
}));

import * as api from "../api.js";
import ModelPicker from "./ModelPicker.jsx";
import { MODELS } from "./data.js";

const opus = MODELS[0];
const sonnet = MODELS[1];

// The default mock: resolve with the full client allowlist so the
// existing assertions on every MODELS entry continue to pass after
// the API call settles. Individual tests override this for the
// failure-path / partial-allowlist coverage.
beforeEach(() => {
  vi.clearAllMocks();
  api.listModels.mockResolvedValue({
    models: MODELS.map((m) => ({ id: m.id, label: m.name })),
  });
});

describe("ModelPicker", () => {
  it("renders the trigger button with the current model's short name and effort", () => {
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    const btn = screen.getByRole("button");
    expect(btn.textContent).toContain("Opus 4.6");
    expect(btn.textContent).toContain("High");
  });

  it("opens the popover on click and shows every MODELS entry", async () => {
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button"));
    for (const m of MODELS) {
      expect(screen.getByText(m.name)).toBeTruthy();
    }
  });

  it("shows a check next to the selected model and not on others", () => {
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    fireEvent.click(screen.getByRole("button"));
    const opusOpt = screen.getByText(opus.name).closest(".opt");
    const sonnetOpt = screen.getByText(sonnet.name).closest(".opt");
    expect(opusOpt.querySelector(".ck")).toBeTruthy();
    expect(sonnetOpt.querySelector(".ck")).toBeFalsy();
  });

  it("clicking a different model invokes onModel with that model object", () => {
    const onModel = vi.fn();
    render(<ModelPicker model={opus} effort="High" onModel={onModel} onEffort={vi.fn()} />);
    fireEvent.click(screen.getByRole("button"));
    fireEvent.click(screen.getByText(sonnet.name));
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

  it("falls back to static MODELS on API failure", async () => {
    api.listModels.mockRejectedValue(new Error("network"));
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    // The fallback path keeps the full client MODELS list visible.
    fireEvent.click(screen.getByRole("button"));
    for (const m of MODELS) {
      expect(screen.getByText(m.name)).toBeTruthy();
    }
  });

  it("renders only the server allowlist on success", async () => {
    api.listModels.mockResolvedValue({
      models: [{ id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" }],
    });
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => {
      expect(screen.getByText(/Sonnet 4\.6/i)).toBeTruthy();
    });
    // Models NOT in the server allowlist must NOT render in the list.
    expect(screen.queryByText("Claude Opus 4.6")).toBeNull();
    expect(screen.queryByText("Claude Haiku 4.5")).toBeNull();
  });

  it("uses server label when client has no display metadata for the id", async () => {
    api.listModels.mockResolvedValue({
      models: [{ id: "claude-experimental-x", label: "Experimental X" }],
    });
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => {
      expect(screen.getByText("Experimental X")).toBeTruthy();
    });
  });

  it("falls back to the id when the server returns no label and the client has no display metadata", async () => {
    api.listModels.mockResolvedValue({
      models: [{ id: "claude-mystery-z" }],
    });
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    await waitFor(() => expect(api.listModels).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => {
      expect(screen.getByText("claude-mystery-z")).toBeTruthy();
    });
  });
});
