// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AccountPopover from "./AccountPopover.jsx";

describe("AccountPopover", () => {
  it("renders the user name and email", () => {
    render(<AccountPopover userName="Ada Lovelace" email="ada@example.com" onSignOut={vi.fn()} />);
    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
    expect(screen.getByText("ada@example.com")).toBeTruthy();
  });

  it("renders a Sign out row", () => {
    render(<AccountPopover userName="x" email="x@example.com" onSignOut={vi.fn()} />);
    expect(screen.getByText("Sign out")).toBeTruthy();
  });

  it("clicking Sign out invokes onSignOut", () => {
    const onSignOut = vi.fn();
    render(<AccountPopover userName="x" email="x@example.com" onSignOut={onSignOut} />);
    fireEvent.click(screen.getByText("Sign out"));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });
});
