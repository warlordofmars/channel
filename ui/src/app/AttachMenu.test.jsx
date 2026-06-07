// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AttachMenu, { ALLOWED_MIMES } from "./AttachMenu.jsx";

describe("AttachMenu", () => {
  it("renders a round + button labeled 'Attach files'", () => {
    const { container } = render(<AttachMenu onFiles={vi.fn()} />);
    expect(container.querySelector(".cbtn.round")).toBeTruthy();
    expect(screen.getByTitle("Attach files")).toBeTruthy();
  });

  it("opens the popover on + click and shows a single 'Choose files' option", () => {
    render(<AttachMenu onFiles={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Attach files"));
    expect(screen.getByText("Choose files")).toBeTruthy();
  });

  it("clicking the backdrop closes the popover", () => {
    const { container } = render(<AttachMenu onFiles={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Attach files"));
    expect(container.querySelector(".pop")).toBeTruthy();
    fireEvent.click(container.querySelector(".backdrop"));
    expect(container.querySelector(".pop")).toBeFalsy();
  });

  it("clicking + a second time toggles the popover closed", () => {
    const { container } = render(<AttachMenu onFiles={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Attach files"));
    expect(container.querySelector(".pop")).toBeTruthy();
    fireEvent.click(screen.getByTitle("Attach files"));
    expect(container.querySelector(".pop")).toBeFalsy();
  });

  it("the hidden file input carries the v1 MIME allowlist in `accept`", () => {
    render(<AttachMenu onFiles={vi.fn()} />);
    const input = screen.getByTestId("attach-file-input");
    expect(input.getAttribute("accept")).toBe(ALLOWED_MIMES.join(","));
    expect(input.multiple).toBe(true);
  });

  it("file picker change event forwards the FileList to onFiles and clears the input value", () => {
    const onFiles = vi.fn();
    render(<AttachMenu onFiles={onFiles} />);
    const input = screen.getByTestId("attach-file-input");
    const file = new File(["x"], "spec.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(onFiles).toHaveBeenCalledTimes(1);
    const filesArg = onFiles.mock.calls[0][0];
    expect(filesArg.length).toBe(1);
    expect(filesArg[0].name).toBe("spec.pdf");
    expect(input.value).toBe("");
  });

  it("clicking 'Choose files' closes the popover (file dialog is microtask-deferred)", async () => {
    const { container } = render(<AttachMenu onFiles={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Attach files"));
    fireEvent.click(screen.getByText("Choose files"));
    expect(container.querySelector(".pop")).toBeFalsy();
  });

  it("clicking 'Choose files' programmatically clicks the hidden file input", async () => {
    render(<AttachMenu onFiles={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Attach files"));
    const input = screen.getByTestId("attach-file-input");
    const inputClick = vi.spyOn(input, "click");
    fireEvent.click(screen.getByText("Choose files"));
    // The click is microtask-deferred — let the queue drain.
    await Promise.resolve();
    expect(inputClick).toHaveBeenCalled();
  });

  it("uses 'attach' terminology in user-facing strings, not 'upload'", () => {
    render(<AttachMenu onFiles={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Attach files"));
    // The button + popover affordance copy must not say "upload".
    const allText = document.body.textContent ?? "";
    expect(allText.toLowerCase()).not.toContain("upload");
  });
});
