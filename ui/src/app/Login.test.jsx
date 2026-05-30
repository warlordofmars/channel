// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Login from "./Login.jsx";

describe("Login", () => {
  let assignSpy;

  beforeEach(() => {
    assignSpy = vi.fn();
    vi.stubGlobal("location", { ...globalThis.location, assign: assignSpy });
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders the 'Sign in to Channel' heading", () => {
    render(<Login />);
    expect(screen.getByRole("heading", { name: /sign in to channel/i })).toBeTruthy();
  });

  it("renders the subtext 'Your workspace for thinking with AI.'", () => {
    render(<Login />);
    expect(screen.getByText(/workspace for thinking with AI/i)).toBeTruthy();
  });

  it("renders a 'Continue with Google' button with the Google G mark", () => {
    const { container } = render(<Login />);
    const btn = screen.getByRole("button", { name: /continue with google/i });
    expect(btn).toBeTruthy();
    // The button contains a multi-coloured SVG (the Google G)
    const fills = Array.from(btn.querySelectorAll("svg path")).map((p) => p.getAttribute("fill"));
    expect(fills).toEqual(expect.arrayContaining(["#EA4335", "#4285F4", "#FBBC05", "#34A853"]));
  });

  it("clicking 'Continue with Google' assigns location to /auth/login", () => {
    render(<Login />);
    fireEvent.click(screen.getByRole("button", { name: /continue with google/i }));
    expect(assignSpy).toHaveBeenCalledWith("/auth/login");
  });

  it("renders the fine-print legal line", () => {
    render(<Login />);
    expect(screen.getByText(/terms/i)).toBeTruthy();
    expect(screen.getByText(/privacy policy/i)).toBeTruthy();
  });

  it("renders the 'New here?' footer line", () => {
    render(<Login />);
    expect(screen.getByText(/new here\?/i)).toBeTruthy();
  });

  it("renders the ChannelMark logo (.ch-mark) at the top of the card", () => {
    const { container } = render(<Login />);
    expect(container.querySelector(".ch-mark")).toBeTruthy();
  });

  it("wraps .auth in .stage.full > .win so the card centers vertically", () => {
    // Without the .stage.full > .win wrappers, .auth's `flex: 1` has no
    // flex parent and the sign-in card collapses to its content size near
    // the top of the viewport instead of centering vertically.
    const { container } = render(<Login />);
    const stage = container.querySelector(".stage.full");
    expect(stage).toBeTruthy();
    const win = stage.querySelector(".win");
    expect(win).toBeTruthy();
    const auth = win.querySelector(".auth");
    expect(auth).toBeTruthy();
  });
});
