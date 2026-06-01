// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ChatRowMenu from "./ChatRowMenu.jsx";

const FAKE_RECT = { top: 100, left: 50, bottom: 130, right: 250, width: 200, height: 30 };

function renderMenu(overrides = {}) {
  const props = {
    open: true,
    anchorRect: FAKE_RECT,
    onClose: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  };
  const utils = render(<ChatRowMenu {...props} />);
  return { ...utils, props };
}

describe("ChatRowMenu", () => {
  it("renders nothing when open=false", () => {
    const { container } = render(
      <ChatRowMenu open={false} anchorRect={FAKE_RECT}
        onClose={() => {}} onRename={() => {}} onDelete={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when anchorRect is missing", () => {
    const { container } = render(
      <ChatRowMenu open={true} anchorRect={null}
        onClose={() => {}} onRename={() => {}} onDelete={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders all 5 menu items in spec order", () => {
    renderMenu();
    const items = screen.getAllByRole("menuitem");
    expect(items.map((el) => el.textContent.trim())).toEqual([
      "Pin",
      "Rename",
      "Change project",
      "Remove from project",
      "Delete",
    ]);
  });

  it("Pin / Change project / Remove from project are disabled placeholders", () => {
    renderMenu();
    expect(screen.getByRole("menuitem", { name: /^pin$/i }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("menuitem", { name: /change project/i }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("menuitem", { name: /remove from project/i }).getAttribute("aria-disabled")).toBe("true");
  });

  it("Rename and Delete are NOT disabled", () => {
    renderMenu();
    expect(screen.getByRole("menuitem", { name: /^rename$/i }).getAttribute("aria-disabled")).not.toBe("true");
    expect(screen.getByRole("menuitem", { name: /^delete$/i }).getAttribute("aria-disabled")).not.toBe("true");
  });

  it("Delete item is danger-styled", () => {
    renderMenu();
    const del = screen.getByRole("menuitem", { name: /^delete$/i });
    expect(del.classList.contains("danger")).toBe(true);
  });

  it("clicking Rename calls onRename + onClose", () => {
    const { props } = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /rename/i }));
    expect(props.onRename).toHaveBeenCalled();
    expect(props.onClose).toHaveBeenCalled();
  });

  it("clicking Delete calls onDelete + onClose", () => {
    const { props } = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
    expect(props.onDelete).toHaveBeenCalled();
    expect(props.onClose).toHaveBeenCalled();
  });

  it("clicking a disabled item is a no-op", () => {
    const { props } = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /^pin$/i }));
    expect(props.onRename).not.toHaveBeenCalled();
    expect(props.onDelete).not.toHaveBeenCalled();
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("backdrop click closes", () => {
    const { container, props } = renderMenu();
    fireEvent.click(container.querySelector(".backdrop"));
    expect(props.onClose).toHaveBeenCalled();
  });

  it("Escape closes", () => {
    const { props } = renderMenu();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(props.onClose).toHaveBeenCalled();
  });

  it("non-Escape keydown does not close", () => {
    const { props } = renderMenu();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("popover flips upward when anchorRect.bottom is near viewport bottom", () => {
    const spy = vi.spyOn(window, "innerHeight", "get").mockReturnValue(600);
    const highRect = { top: 500, left: 50, bottom: 530, right: 250, width: 200, height: 30 };
    renderMenu({ anchorRect: highRect });
    const pop = screen.getByRole("menu");
    // When flipped, bottom is set instead of top
    expect(pop.style.bottom).not.toBe("");
    expect(pop.style.top).toBe("");
    spy.mockRestore();
  });
});
