// Copyright (c) 2026 John Carter. All rights reserved.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ChannelMark from "./ChannelMark.jsx";

describe("ChannelMark", () => {
  it("renders an SVG element with the supplied size", () => {
    const { container } = render(<ChannelMark size={34} />);
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg.getAttribute("width")).toBe("34");
    expect(svg.getAttribute("height")).toBe("34");
    expect(svg.getAttribute("viewBox")).toBe("0 0 24 24");
  });

  it("defaults to size 22 when omitted", () => {
    const { container } = render(<ChannelMark />);
    expect(container.querySelector("svg").getAttribute("width")).toBe("22");
  });

  it("wraps the SVG in a .ch-mark span (channel.css styles this)", () => {
    const { container } = render(<ChannelMark size={24} />);
    const wrapper = container.querySelector("span.ch-mark");
    expect(wrapper).toBeTruthy();
    expect(wrapper.querySelector("svg")).toBeTruthy();
  });

  it("contains two vertical bars between the rounded square", () => {
    const { container } = render(<ChannelMark size={24} />);
    // Outer rounded square + two vertical bars = 3 rects
    const rects = container.querySelectorAll("svg rect");
    expect(rects.length).toBe(3);
  });

  it("accepts a color prop overriding the default accent fill", () => {
    const { container } = render(<ChannelMark size={24} color="hotpink" />);
    const outerRect = container.querySelector("svg rect");
    expect(outerRect.getAttribute("fill")).toBe("hotpink");
  });
});
