// Copyright (c) 2026 John Carter. All rights reserved.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Icon from "./Icon.jsx";

describe("Icon", () => {
  it("renders an SVG for the 'plus' name", () => {
    const { container } = render(<Icon name="plus" />);
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg.getAttribute("viewBox")).toBe("0 0 24 24");
  });

  it("applies the supplied size to width and height", () => {
    const { container } = render(<Icon name="plus" size={28} />);
    const svg = container.querySelector("svg");
    expect(svg.getAttribute("width")).toBe("28");
    expect(svg.getAttribute("height")).toBe("28");
  });

  it("applies the supplied stroke-width", () => {
    const { container } = render(<Icon name="plus" stroke={2.4} />);
    const svg = container.querySelector("svg");
    expect(svg.getAttribute("stroke-width")).toBe("2.4");
  });

  it("uses currentColor for stroke", () => {
    const { container } = render(<Icon name="plus" />);
    expect(container.querySelector("svg").getAttribute("stroke")).toBe("currentColor");
  });

  it("returns null for an unknown name", () => {
    const { container } = render(<Icon name="not-a-real-icon" />);
    expect(container.querySelector("svg")).toBeFalsy();
  });

  it.each([
    "plus",
    "chat",
    "cowork",
    "code",
    "projects",
    "artifacts",
    "customize",
    "search",
    "sidebar",
    "mic",
    "wave",
    "arrow-up",
    "arrow-right",
    "chevron-down",
    "chevron-right",
    "attach",
    "image",
    "write",
    "learn",
    "settings",
    "sun",
    "moon",
    "download",
    "leaf",
    "copy",
    "refresh",
    "thumb-up",
    "thumb-down",
    "close",
    "check",
    "file",
    "doc",
    "globe",
    "sparkle",
    "menu",
    "pin",
    "dots",
    "star",
    "database",
    "play",
    "expand",
    "folder-x",
    "more-vertical",
    "pencil",
    "trash",
  ])("renders an SVG for the '%s' name", (name) => {
    const { container } = render(<Icon name={name} />);
    expect(container.querySelector("svg")).toBeTruthy();
  });
});
