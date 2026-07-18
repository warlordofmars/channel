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

  it("renders the auth-type toggle; PAT is not selected by default and no token field shows", () => {
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    expect(
      screen.getByRole("radio", { name: /OAuth \(Dynamic Client Registration\)/i }),
    ).toBeChecked();
    expect(
      screen.getByRole("radio", { name: /Access token \/ PAT/i }),
    ).not.toBeChecked();
    // Token input hidden until PAT is selected.
    expect(screen.queryByLabelText("Access token")).not.toBeInTheDocument();
  });

  it("selecting PAT reveals a masked token input and switches the consent copy", () => {
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    fireEvent.click(screen.getByRole("radio", { name: /Access token \/ PAT/i }));
    const tokenInput = screen.getByLabelText("Access token");
    expect(tokenInput).toBeInTheDocument();
    expect(tokenInput).toHaveAttribute("type", "password");
    expect(
      screen.getByText(/encrypted at rest in Channel's database and sent to this server as a bearer credential/i),
    ).toBeInTheDocument();
  });

  it("toggling PAT then back to OAuth hides the token field again", () => {
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    fireEvent.click(screen.getByRole("radio", { name: /Access token \/ PAT/i }));
    expect(screen.getByLabelText("Access token")).toBeInTheDocument();
    // Switch back to OAuth — fires the oauth_dcr onChange handler.
    fireEvent.click(
      screen.getByRole("radio", { name: /OAuth \(Dynamic Client Registration\)/i }),
    );
    expect(screen.queryByLabelText("Access token")).not.toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: /OAuth \(Dynamic Client Registration\)/i }),
    ).toBeChecked();
  });

  it("PAT submit sends auth_type + token, does NOT open a tab, and closes", async () => {
    registerMCPServer.mockResolvedValueOnce({
      server_id: "srv-static",
      auth_start_url: null,
    });
    const onClose = vi.fn();
    const onRegistered = vi.fn();
    render(
      <AddMCPServerModal open onClose={onClose} onRegistered={onRegistered} />,
    );
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "GitHub" },
    });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://api.githubcopilot.com/mcp/" },
    });
    fireEvent.click(screen.getByRole("radio", { name: /Access token \/ PAT/i }));
    // Synthetic non-secret value (no GitHub PAT prefix) so secret
    // scanners don't flag the dummy fixture.
    const fakeBearer = "dummy-" + "bearer-value";
    fireEvent.change(screen.getByLabelText("Access token"), {
      target: { value: fakeBearer },
    });
    fireEvent.click(screen.getByRole("button", { name: /add server/i }));

    await waitFor(() => expect(onRegistered).toHaveBeenCalledWith("srv-static"));
    expect(registerMCPServer).toHaveBeenCalledWith({
      name: "GitHub",
      url: "https://api.githubcopilot.com/mcp/",
      auth_type: "static_token",
      token: fakeBearer,
    });
    // No auth_start_url → no OAuth tab.
    expect(window.open).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("PAT submit is disabled until a token is entered", () => {
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "GitHub" } });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://api.githubcopilot.com/mcp/" },
    });
    fireEvent.click(screen.getByRole("radio", { name: /Access token \/ PAT/i }));
    const submit = screen.getByRole("button", { name: /add server/i });
    // Name + URL filled but no token yet → still disabled.
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Access token"), {
      target: { value: "dummy-" + "bearer-value" },
    });
    expect(submit).not.toBeDisabled();
  });

  it("dcr_unsupported failure steers into the static-token path, preserving name + url", async () => {
    const err = Object.assign(new Error("registerMCPServer 400"), {
      code: "dcr_unsupported",
    });
    registerMCPServer.mockRejectedValueOnce(err);
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "GitHub" } });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://api.githubcopilot.com/mcp/" },
    });
    // Default oauth_dcr submit fails with dcr_unsupported.
    fireEvent.click(screen.getByRole("button", { name: /add server/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/doesn't support automatic registration/i),
      ).toBeInTheDocument(),
    );
    // Must NOT show the misleading generic copy.
    expect(
      screen.queryByText(/check the url and try again/i),
    ).not.toBeInTheDocument();
    // Toggle flipped to PAT + token input revealed and focused.
    expect(
      screen.getByRole("radio", { name: /Access token \/ PAT/i }),
    ).toBeChecked();
    const tokenInput = screen.getByLabelText("Access token");
    expect(tokenInput).toBeInTheDocument();
    expect(tokenInput).toHaveFocus();
    // Name + URL preserved so the user only needs to paste the token.
    expect(screen.getByLabelText("Name").value).toBe("GitHub");
    expect(screen.getByLabelText("Server URL").value).toBe(
      "https://api.githubcopilot.com/mcp/",
    );
  });

  it("generic (non-dcr_unsupported) failure keeps the check-the-URL copy", async () => {
    registerMCPServer.mockRejectedValueOnce(new Error("registerMCPServer 502"));
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "X" } });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://x.example/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add server/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/couldn't register that server. check the url/i),
      ).toBeInTheDocument(),
    );
    // Stays on the OAuth path — no forced switch to PAT.
    expect(
      screen.getByRole("radio", { name: /OAuth \(Dynamic Client Registration\)/i }),
    ).toBeChecked();
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

  it("Cancel is disabled while a registration is in flight", async () => {
    // Hang the API call so the modal stays in busy state.
    registerMCPServer.mockReturnValueOnce(new Promise(() => {}));
    render(<AddMCPServerModal open onClose={() => {}} onRegistered={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Hive" } });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://hive.example.com/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add server/i }));
    await waitFor(() => expect(registerMCPServer).toHaveBeenCalled());
    const cancel = screen.getByRole("button", { name: /cancel/i });
    expect(cancel).toBeDisabled();
  });

  it("onClose is ignored while busy — a late-success registration can't fire onRegistered behind the user's back", async () => {
    let resolveIt;
    registerMCPServer.mockReturnValueOnce(
      new Promise((res) => {
        resolveIt = res;
      }),
    );
    const onClose = vi.fn();
    const onRegistered = vi.fn();
    render(
      <AddMCPServerModal open onClose={onClose} onRegistered={onRegistered} />,
    );
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Hive" } });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://hive.example.com/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add server/i }));
    await waitFor(() => expect(registerMCPServer).toHaveBeenCalled());
    // Simulate user pressing Escape via Modal's keydown listener — the
    // window-level Esc handler in Modal.jsx calls onClose. With the
    // busy guard, our wrapped handleClose is a no-op so onClose
    // shouldn't fire.
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    // Resolve the in-flight request — submit's success branch still
    // fires window.open + onRegistered + onClose because that's the
    // normal completion path. The guard only blocks DISMISSAL while
    // busy, not the legitimate post-success close.
    resolveIt({ server_id: "srv-1", auth_start_url: "https://x" });
    await waitFor(() => expect(onRegistered).toHaveBeenCalledWith("srv-1"));
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
