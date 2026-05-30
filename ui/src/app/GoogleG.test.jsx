// Copyright (c) 2026 John Carter. All rights reserved.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GoogleG from "./GoogleG.jsx";

describe("GoogleG", () => {
  it("renders an SVG with the four official Google brand colours", () => {
    const { container } = render(<GoogleG />);
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    const fills = Array.from(svg.querySelectorAll("path")).map((p) => p.getAttribute("fill"));
    expect(fills).toEqual(expect.arrayContaining(["#EA4335", "#4285F4", "#FBBC05", "#34A853"]));
  });

  it("applies the supplied size to width and height", () => {
    const { container } = render(<GoogleG size={24} />);
    const svg = container.querySelector("svg");
    expect(svg.getAttribute("width")).toBe("24");
    expect(svg.getAttribute("height")).toBe("24");
  });

  it("defaults to size 18 when omitted", () => {
    const { container } = render(<GoogleG />);
    expect(container.querySelector("svg").getAttribute("width")).toBe("18");
  });

  it("uses viewBox 0 0 48 48 (Google's standard)", () => {
    const { container } = render(<GoogleG />);
    expect(container.querySelector("svg").getAttribute("viewBox")).toBe("0 0 48 48");
  });
});
