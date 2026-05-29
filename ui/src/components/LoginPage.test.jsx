// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "./LoginPage.jsx";

describe("LoginPage", () => {
  beforeEach(() => {
    vi.stubGlobal("location", {
      ...window.location,
      href: "",
      search: "",
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the logo", async () => {
    await act(async () => render(<LoginPage />));
    const img = screen.getByAltText("Channel");
    expect(img.getAttribute("src")).toBe("/logo.svg");
  });

  it("renders the tagline", async () => {
    await act(async () => render(<LoginPage />));
    expect(screen.getByText(/Channel/)).toBeTruthy();
  });

  it("renders sign-in button with Google icon", async () => {
    const { container } = await act(async () => render(<LoginPage />));
    expect(screen.getByText("Sign in with Google")).toBeTruthy();
    const btn = container.querySelector("button");
    expect(btn.querySelector("svg")).toBeTruthy();
  });

  it("redirects to /auth/login when button is clicked", async () => {
    await act(async () => render(<LoginPage />));
    await act(async () => { fireEvent.click(screen.getByText("Sign in with Google")); });
    expect(window.location.href).toBe("/auth/login");
  });

  it("shows loading state after button click", async () => {
    await act(async () => render(<LoginPage />));
    await act(async () => { fireEvent.click(screen.getByText("Sign in with Google")); });
    expect(screen.getByText("Redirecting…")).toBeTruthy();
  });

  it("disables button while loading", async () => {
    await act(async () => render(<LoginPage />));
    await act(async () => { fireEvent.click(screen.getByText("Sign in with Google")); });
    expect(screen.getByRole("button").disabled).toBe(true);
  });

  it("renders back to home link", async () => {
    await act(async () => render(<LoginPage />));
    const link = screen.getByText("← Back to home");
    expect(link.getAttribute("href")).toBe("/");
  });

  it("shows error message when error param is present", async () => {
    vi.stubGlobal("location", { ...globalThis.location, href: "", search: "?error=cancelled" });
    await act(async () => render(<LoginPage />));
    expect(screen.getByText(/Sign in was cancelled/)).toBeTruthy();
  });

  it("does not show error message when no error param", async () => {
    await act(async () => render(<LoginPage />));
    expect(screen.queryByText(/Sign in was cancelled/)).toBeNull();
  });
});
