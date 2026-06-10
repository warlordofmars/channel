// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AddMCPServerModal from "./AddMCPServerModal.jsx";

vi.mock("../api.js", () => ({
  registerMCPServer: vi.fn(),
}));

import { registerMCPServer } from "../api.js";

describe("AddMCPServerModal", () => {
  beforeEach(() => {
    vi.spyOn(window, "open").mockImplementation(() => null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("submits name + url and opens the returned auth_start_url", async () => {
    registerMCPServer.mockResolvedValueOnce({
      server_id: "srv-1",
      auth_start_url: "https://auth.example/authorize?...",
    });
    const onClose = vi.fn();
    const onRegistered = vi.fn();
    render(
      <AddMCPServerModal open onClose={onClose} onRegistered={onRegistered} />,
    );

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Hive" },
    });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://hive.example.com/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add server/i }));

    await waitFor(() => expect(registerMCPServer).toHaveBeenCalled());
    expect(window.open).toHaveBeenCalledWith(
      "https://auth.example/authorize?...",
      "_blank",
      "noopener,noreferrer",
    );
    expect(onRegistered).toHaveBeenCalledWith("srv-1");
    expect(onClose).toHaveBeenCalled();
  });

  it("shows the consent disclosure before submit", () => {
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    expect(
      screen.getByText(/We will register Channel with this server using OAuth/i),
    ).toBeInTheDocument();
  });

  it("surfaces error from registerMCPServer", async () => {
    registerMCPServer.mockRejectedValueOnce(new Error("registerMCPServer 502"));
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "X" },
    });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://x.example/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add server/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/couldn't register that server/i),
      ).toBeInTheDocument(),
    );
  });

  it("submit is disabled until name and url are filled", () => {
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    const submit = screen.getByRole("button", { name: /add server/i });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "X" },
    });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://x/mcp" },
    });
    expect(submit).not.toBeDisabled();
  });

  it("submit is a no-op when busy", async () => {
    // Don't resolve the first call — submit hangs in busy state.
    let resolveIt;
    registerMCPServer.mockReturnValueOnce(
      new Promise((res) => {
        resolveIt = res;
      }),
    );
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "X" },
    });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://x/mcp" },
    });
    const submit = screen.getByRole("button", { name: /add server/i });
    fireEvent.click(submit);
    // Second click while busy is ignored — registerMCPServer still called once.
    fireEvent.click(submit);
    expect(registerMCPServer).toHaveBeenCalledTimes(1);
    resolveIt({ server_id: "x", auth_start_url: "https://x" });
    await waitFor(() => expect(registerMCPServer).toHaveBeenCalledTimes(1));
  });

  it("Cancel button calls onClose", () => {
    const onClose = vi.fn();
    render(<AddMCPServerModal open onClose={onClose} onRegistered={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("resets local state when reopened — no stale name/url/error from a prior session", async () => {
    registerMCPServer.mockRejectedValueOnce(new Error("502"));
    const { rerender } = render(
      <AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />,
    );
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Hive" } });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://hive.example.com/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add server/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/couldn't register that server/i),
      ).toBeInTheDocument(),
    );

    // Close → reopen: stale name/url/error must be cleared.
    rerender(
      <AddMCPServerModal open={false} onClose={() => {}} onRegistered={() => {}} />,
    );
    rerender(
      <AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />,
    );
    expect(screen.getByLabelText("Name").value).toBe("");
    expect(screen.getByLabelText("Server URL").value).toBe("");
    expect(
      screen.queryByText(/couldn't register that server/i),
    ).not.toBeInTheDocument();
  });
});
