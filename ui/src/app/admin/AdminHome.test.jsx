// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import AdminHome from "./AdminHome.jsx";

function renderHome() {
  let lastPath = null;
  function PathCatcher() {
    const { pathname } = useLocation();
    lastPath = pathname;
    return null;
  }
  render(
    <MemoryRouter initialEntries={["/app/admin"]}>
      <Routes>
        <Route path="*" element={<><AdminHome /><PathCatcher /></>} />
      </Routes>
    </MemoryRouter>
  );
  return { lastPath: () => lastPath };
}

describe("AdminHome", () => {
  it("renders the Admin heading and both landing cards", () => {
    renderHome();
    expect(screen.getByRole("heading", { level: 2, name: "Admin" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /users/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: /dashboard/i })).toBeTruthy();
  });

  it("links Users to /app/admin/users and navigates on click", () => {
    const probe = renderHome();
    const link = screen.getByRole("link", { name: /users/i });
    expect(link.getAttribute("href")).toBe("/app/admin/users");
    fireEvent.click(link);
    expect(probe.lastPath()).toBe("/app/admin/users");
  });

  it("links Dashboard to /app/admin/dashboard and navigates on click", () => {
    const probe = renderHome();
    const link = screen.getByRole("link", { name: /dashboard/i });
    expect(link.getAttribute("href")).toBe("/app/admin/dashboard");
    fireEvent.click(link);
    expect(probe.lastPath()).toBe("/app/admin/dashboard");
  });
});
