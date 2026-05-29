// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import PageLayout from "./PageLayout.jsx";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useNavigate: () => mockNavigate };
});

beforeEach(() => {
  mockNavigate.mockReset();
});

function renderInRouter(ui, path = "/") {
  return render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>);
}

describe("PageLayout", () => {
  it("renders children", async () => {
    await act(async () =>
      renderInRouter(<PageLayout><p>test content</p></PageLayout>)
    );
    expect(screen.getByText("test content")).toBeTruthy();
  });

  it("renders nav with logo and wordmark", async () => {
    const { container } = await act(async () =>
      renderInRouter(<PageLayout><span /></PageLayout>)
    );
    expect(container.querySelector('img[alt="Channel"]')).toBeTruthy();
    expect(screen.getAllByText("Channel").length).toBeGreaterThanOrEqual(1);
  });

  it("renders Docs link in header", async () => {
    await act(async () =>
      renderInRouter(<PageLayout><span /></PageLayout>)
    );
    expect(screen.getAllByText("Docs").length).toBeGreaterThanOrEqual(1);
  });

  it("renders Sign in button", async () => {
    await act(async () =>
      renderInRouter(<PageLayout><span /></PageLayout>)
    );
    expect(screen.getByText("Sign in")).toBeTruthy();
  });

  it("Sign in navigates to /app", async () => {
    await act(async () =>
      renderInRouter(<PageLayout><span /></PageLayout>)
    );
    fireEvent.click(screen.getByText("Sign in"));
    expect(mockNavigate).toHaveBeenCalledWith("/app");
  });

  it("clicking logo navigates to /", async () => {
    await act(async () =>
      renderInRouter(<PageLayout><span /></PageLayout>)
    );
    const logo = screen.getAllByText("Channel")[0].closest("span");
    fireEvent.click(logo);
    expect(mockNavigate).toHaveBeenCalledWith("/");
  });

  it("renders footer with copyright", async () => {
    await act(async () =>
      renderInRouter(<PageLayout><span /></PageLayout>)
    );
    expect(screen.getByText(/© 2026 Channel/)).toBeTruthy();
  });

  it("renders footer Docs link", async () => {
    const { container } = await act(async () =>
      renderInRouter(<PageLayout><span /></PageLayout>)
    );
    const footer = container.querySelector("footer");
    expect(within(footer).getByText("Docs")).toBeTruthy();
  });

  it("Sign in button uses nav variant with visible border", async () => {
    await act(async () =>
      renderInRouter(<PageLayout><span /></PageLayout>)
    );
    const btn = screen.getByRole("button", { name: "Sign in" });
    expect(btn.className).toContain("border-white/60");
    expect(btn.className).toContain("marketing-signin-btn");
  });

  it("mounts the consent banner when no consent has been stored", async () => {
    localStorage.removeItem("starter_ga_consent");
    await act(async () =>
      renderInRouter(<PageLayout><span /></PageLayout>)
    );
    expect(screen.getByRole("dialog", { name: "Cookie consent" })).toBeTruthy();
  });

  it("renders Cookie preferences footer link", async () => {
    const { container } = await act(async () =>
      renderInRouter(<PageLayout><span /></PageLayout>)
    );
    const footer = container.querySelector("footer");
    expect(within(footer).getByText("Cookie preferences")).toBeTruthy();
  });

  it("renders a mobile hamburger button with correct aria-label", async () => {
    await act(async () => renderInRouter(<PageLayout><span /></PageLayout>));
    const btn = screen.getByLabelText("Open menu");
    expect(btn).toBeTruthy();
    expect(btn.getAttribute("aria-expanded")).toBe("false");
  });

  it("hamburger toggles the mobile drawer and flips its aria label", async () => {
    await act(async () => renderInRouter(<PageLayout><span /></PageLayout>));
    const btn = screen.getByLabelText("Open menu");
    await act(async () => fireEvent.click(btn));
    const closeBtn = screen.getByLabelText("Close menu");
    expect(closeBtn.getAttribute("aria-expanded")).toBe("true");
    const drawer = closeBtn.closest("header").querySelector(".md\\:hidden nav");
    expect(drawer).toBeTruthy();
    await act(async () => fireEvent.click(closeBtn));
    expect(screen.getByLabelText("Open menu").getAttribute("aria-expanded")).toBe("false");
  });

  it("mobile drawer contains Docs link", async () => {
    await act(async () => renderInRouter(<PageLayout><span /></PageLayout>));
    await act(async () => fireEvent.click(screen.getByLabelText("Open menu")));
    const drawer = document.querySelector("header .md\\:hidden nav");
    expect(drawer).toBeTruthy();
    expect(within(drawer).getByText("Docs")).toBeTruthy();
    // Sign in lives in the navbar, not the drawer
    expect(within(drawer).queryByRole("button", { name: "Sign in" })).toBeNull();
  });

  it("navbar Sign in navigates to /app at every breakpoint", async () => {
    await act(async () => renderInRouter(<PageLayout><span /></PageLayout>));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Sign in" })));
    expect(mockNavigate).toHaveBeenCalledWith("/app");
  });

  it("navbar theme toggle flips the theme and updates its aria-label", async () => {
    localStorage.removeItem("starter_theme");
    await act(async () => renderInRouter(<PageLayout><span /></PageLayout>));
    const btn = screen.getByLabelText(/Switch to (dark|light) mode/);
    const before = btn.getAttribute("aria-label");
    await act(async () => fireEvent.click(btn));
    const after = screen.getByLabelText(/Switch to (dark|light) mode/).getAttribute("aria-label");
    expect(after).not.toBe(before);
  });

  it("Cookie preferences click clears stored consent and re-shows the banner", async () => {
    localStorage.setItem("starter_ga_consent", "reject");
    const { container } = await act(async () =>
      renderInRouter(<PageLayout><span /></PageLayout>)
    );
    expect(screen.queryByRole("dialog", { name: "Cookie consent" })).toBeNull();
    const footer = container.querySelector("footer");
    const link = within(footer).getByText("Cookie preferences");
    await act(async () => fireEvent.click(link));
    expect(localStorage.getItem("starter_ga_consent")).toBeNull();
    expect(screen.getByRole("dialog", { name: "Cookie consent" })).toBeTruthy();
  });
});
