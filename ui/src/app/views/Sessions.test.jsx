// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import Sessions from "./Sessions.jsx";
import { listSessions, revokeSession, revokeAllSessions, endSession } from "../../api.js";

vi.mock("../../api.js", () => ({
  listSessions: vi.fn(),
  revokeSession: vi.fn(),
  revokeAllSessions: vi.fn(),
  endSession: vi.fn(),
}));

describe("Sessions View", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "confirm").mockImplementation(() => true);
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  it("renders loading state initially, then empty list", async () => {
    listSessions.mockResolvedValueOnce({ sessions: [] });
    render(<Sessions />);
    expect(screen.getByText("Loading sessions…")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("No active sessions found.")).toBeInTheDocument();
    });
  });

  it("renders a list of active sessions and formats dates", async () => {
    listSessions.mockResolvedValueOnce({
      sessions: [
        { device_id: "dev-12345", last_used_at: "2026-08-01T12:00:00Z" },
        { device_id: "dev-67890", last_used_at: null },
        { device_id: "dev-invalid-date", last_used_at: "not-a-date" },
      ],
    });

    render(<Sessions />);
    await waitFor(() => {
      expect(screen.queryByText("Loading sessions…")).not.toBeInTheDocument();
    });

    expect(screen.getAllByText("Device • dev")[0]).toBeInTheDocument();
    expect(screen.getAllByText(/Last used: /)[0]).toBeInTheDocument();
    
    // Check fallback formatting for invalid date string
    expect(screen.getByText("Last used: not-a-date")).toBeInTheDocument();
    
    // There should be three "Sign out" buttons for the individual rows
    const signOutBtns = screen.getAllByRole("button", { name: "Sign out" });
    expect(signOutBtns).toHaveLength(3);
  });

  it("displays error if listSessions fails", async () => {
    listSessions.mockRejectedValueOnce(new Error("API Down"));
    render(<Sessions />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load sessions.")).toBeInTheDocument();
    });
  });

  it("handles revoking a single session successfully", async () => {
    const user = userEvent.setup();
    listSessions.mockResolvedValueOnce({
      sessions: [
        { device_id: "dev-1", last_used_at: "2026-08-01T12:00:00Z" },
        { device_id: "dev-2", last_used_at: "2026-08-02T12:00:00Z" },
      ],
    });
    revokeSession.mockResolvedValueOnce();

    render(<Sessions />);
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: "Sign out" })).toHaveLength(2);
    });

    const btns = screen.getAllByRole("button", { name: "Sign out" });
    await user.click(btns[0]);

    expect(revokeSession).toHaveBeenCalledWith("dev-1");
    // Row 1 should be removed, leaving only 1 single-revoke button
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: "Sign out" })).toHaveLength(1);
    });
  });

  it("displays error if revoking a single session fails", async () => {
    const user = userEvent.setup();
    listSessions.mockResolvedValueOnce({
      sessions: [{ device_id: "dev-1", last_used_at: "2026-08-01T12:00:00Z" }],
    });
    revokeSession.mockRejectedValueOnce(new Error("Revoke failed"));

    render(<Sessions />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => {
      expect(screen.getByText("Failed to revoke session.")).toBeInTheDocument();
    });
  });

  it("handles revoking all sessions", async () => {
    const user = userEvent.setup();
    listSessions.mockResolvedValueOnce({
      sessions: [{ device_id: "dev-1", last_used_at: "2026-08-01T12:00:00Z" }],
    });
    revokeAllSessions.mockResolvedValueOnce();

    render(<Sessions />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign out of all devices" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Sign out of all devices" }));

    expect(window.confirm).toHaveBeenCalled();
    expect(revokeAllSessions).toHaveBeenCalled();
    expect(endSession).toHaveBeenCalled();
  });

  it("displays error if revoking all sessions fails", async () => {
    const user = userEvent.setup();
    listSessions.mockResolvedValueOnce({
      sessions: [{ device_id: "dev-1", last_used_at: "2026-08-01T12:00:00Z" }],
    });
    revokeAllSessions.mockRejectedValueOnce(new Error("Revoke all failed"));

    render(<Sessions />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign out of all devices" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Sign out of all devices" }));

    await waitFor(() => {
      expect(screen.getByText("Failed to sign out everywhere.")).toBeInTheDocument();
    });
  });

  it("does nothing if revoke all confirm is cancelled", async () => {
    vi.spyOn(window, "confirm").mockImplementation(() => false);
    const user = userEvent.setup();
    listSessions.mockResolvedValueOnce({
      sessions: [{ device_id: "dev-1", last_used_at: "2026-08-01T12:00:00Z" }],
    });

    render(<Sessions />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign out of all devices" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Sign out of all devices" }));

    expect(revokeAllSessions).not.toHaveBeenCalled();
    expect(endSession).not.toHaveBeenCalled();
  });
});
