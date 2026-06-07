// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen } from "@testing-library/react";
import ToolResultBlock from "./ToolResultBlock.jsx";

describe("ToolResultBlock", () => {
  it("renders the default branch when kind is unrecognised", () => {
    render(<ToolResultBlock kind="unknown" summary="hello" />);
    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  it("renders summary text by default (no kind specified)", () => {
    render(<ToolResultBlock summary="the answer is 4" />);
    expect(screen.getByText("the answer is 4")).toBeInTheDocument();
  });

  it("tags the wrapper with data-kind for future-branch dispatch", () => {
    const { container } = render(<ToolResultBlock kind="custom" summary="x" />);
    expect(container.firstChild).toHaveAttribute("data-kind", "custom");
  });

  it("falls back to data-kind=default when kind is omitted", () => {
    const { container } = render(<ToolResultBlock summary="y" />);
    expect(container.firstChild).toHaveAttribute("data-kind", "default");
  });
});
