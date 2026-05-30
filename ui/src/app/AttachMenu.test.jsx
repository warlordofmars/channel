// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AttachMenu from "./AttachMenu.jsx";

describe("AttachMenu", () => {
  it("renders a round + button that triggers the menu", () => {
    const { container } = render(<AttachMenu onAdd={vi.fn()} />);
    expect(container.querySelector(".cbtn.round")).toBeTruthy();
  });

  it("opens the popover on click and shows the three options", () => {
    render(<AttachMenu onAdd={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Add attachment"));
    expect(screen.getByText("Upload a file")).toBeTruthy();
    expect(screen.getByText("Add photos or images")).toBeTruthy();
    expect(screen.getByText("Connect data source")).toBeTruthy();
  });

  it("clicking 'Upload a file' invokes onAdd with a file-kind sample and closes the menu", () => {
    const onAdd = vi.fn();
    const { container } = render(<AttachMenu onAdd={onAdd} />);
    fireEvent.click(screen.getByTitle("Add attachment"));
    fireEvent.click(screen.getByText("Upload a file"));
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ kind: "file" }));
    expect(container.querySelector(".pop")).toBeFalsy();
  });

  it("clicking the backdrop closes the popover", () => {
    const { container } = render(<AttachMenu onAdd={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Add attachment"));
    expect(container.querySelector(".pop")).toBeTruthy();
    fireEvent.click(container.querySelector(".backdrop"));
    expect(container.querySelector(".pop")).toBeFalsy();
  });
});
