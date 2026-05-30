// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ModelPicker from "./ModelPicker.jsx";
import { MODELS } from "./data.js";

const opus = MODELS[0];
const sonnet = MODELS[1];

describe("ModelPicker", () => {
  it("renders the trigger button with the current model's short name and effort", () => {
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    const btn = screen.getByRole("button");
    expect(btn.textContent).toContain("Opus 4.8");
    expect(btn.textContent).toContain("High");
  });

  it("opens the popover on click and shows every MODELS entry", () => {
    render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
    fireEvent.click(screen.getByRole("button"));
    for (const m of MODELS) {
      expect(screen.getByText(m.name)).toBeTruthy();
    }
  });

  it("shows a check next to the selected model and not on others", () => {
    const { container } = render(<ModelPicker model={opus} effort="High" onModel={vi.fn()} onEffort={vi.fn()} />);
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
    expect(onModel).toHaveBeenCalledWith(sonnet);
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
});
