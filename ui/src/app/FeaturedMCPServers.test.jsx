// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api.js", () => ({
  getFeaturedServers: vi.fn(),
  enableFeaturedServer: vi.fn(),
}));

import * as api from "../api.js";
import FeaturedMCPServers from "./FeaturedMCPServers.jsx";

const GITHUB = {
  featured_id: "github",
  name: "GitHub",
  url: "https://api.githubcopilot.com/mcp/",
  description: "GitHub's official MCP server — read repos, issues, and PRs.",
  docs_url: "https://github.com/settings/personal-access-tokens",
  auth_type: "static_token",
  tool_prefix: "github",
  default_globally_enabled: false,
};

// A hypothetical OAuth-DCR featured entry, exercising the non-static path.
const DCR = {
  featured_id: "acme",
  name: "Acme",
  url: "https://acme.example.com/mcp",
  description: "Acme's MCP server (OAuth).",
  docs_url: "https://acme.example.com/docs",
  auth_type: "oauth_dcr",
  tool_prefix: "acme",
  default_globally_enabled: true,
};

function renderFeatured(props = {}) {
  return render(<FeaturedMCPServers onEnabled={vi.fn()} {...props} />);
}

describe("FeaturedMCPServers", () => {
  beforeEach(() => {
    api.getFeaturedServers.mockReset();
    api.enableFeaturedServer.mockReset();
    api.getFeaturedServers.mockResolvedValue({ servers: [GITHUB] });
    api.enableFeaturedServer.mockResolvedValue({
      server_id: "srv-feat",
      auth_start_url: null,
    });
    vi.spyOn(window, "open").mockImplementation(() => null);
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing while the catalog is loading", () => {
    api.getFeaturedServers.mockReturnValueOnce(new Promise(() => {}));
    const { container } = renderFeatured();
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the catalog is empty", async () => {
    api.getFeaturedServers.mockResolvedValueOnce({ servers: [] });
    const { container } = renderFeatured();
    await waitFor(() => expect(api.getFeaturedServers).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("shows a load error when getFeaturedServers rejects", async () => {
    api.getFeaturedServers.mockRejectedValueOnce(new Error("boom"));
    renderFeatured();
    expect(
      await screen.findByText(/couldn't load featured integrations/i),
    ).toBeInTheDocument();
  });

  it("renders a card with name, description, and an Enable button", async () => {
    renderFeatured();
    expect(
      await screen.findByRole("heading", { name: /featured integrations/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    expect(screen.getByText(/read repos, issues, and prs/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^enable$/i })).toBeInTheDocument();
  });

  it("clicking Enable on a static_token entry reveals the PAT field + token link", async () => {
    renderFeatured();
    fireEvent.click(await screen.findByRole("button", { name: /^enable$/i }));
    const input = await screen.findByLabelText(/github access token/i);
    expect(input).toHaveAttribute("type", "password");
    const link = screen.getByRole("link", { name: /create a token/i });
    expect(link).toHaveAttribute("href", GITHUB.docs_url);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("the inner Enable button is disabled until a token is entered", async () => {
    renderFeatured();
    fireEvent.click(await screen.findByRole("button", { name: /^enable$/i }));
    const input = await screen.findByLabelText(/github access token/i);
    const submit = within(input.closest(".mcp-featured-enable")).getByRole(
      "button",
      { name: /^enable$/i },
    );
    expect(submit).toBeDisabled();
    fireEvent.change(input, { target: { value: "ghp_test" } });
    expect(submit).not.toBeDisabled();
  });

  it("submitting a valid PAT POSTs featured_id/name/url/token only, then calls onEnabled", async () => {
    const onEnabled = vi.fn();
    renderFeatured({ onEnabled });
    fireEvent.click(await screen.findByRole("button", { name: /^enable$/i }));
    const input = await screen.findByLabelText(/github access token/i);
    // Trailing whitespace must be trimmed off the pasted token.
    fireEvent.change(input, { target: { value: "  ghp_test  " } });
    const submit = within(input.closest(".mcp-featured-enable")).getByRole(
      "button",
      { name: /^enable$/i },
    );
    fireEvent.click(submit);
    await waitFor(() => expect(api.enableFeaturedServer).toHaveBeenCalled());
    expect(api.enableFeaturedServer).toHaveBeenCalledWith({
      featured_id: "github",
      name: "GitHub",
      url: "https://api.githubcopilot.com/mcp/",
      token: "ghp_test",
    });
    await waitFor(() => expect(onEnabled).toHaveBeenCalled());
    // The PAT field closes after a successful enable.
    await waitFor(() =>
      expect(screen.queryByLabelText(/github access token/i)).not.toBeInTheDocument(),
    );
  });

  it("shows 'Enabling…' on the submit button while the enable is in flight", async () => {
    let resolveEnable;
    api.enableFeaturedServer.mockReturnValueOnce(
      new Promise((r) => {
        resolveEnable = r;
      }),
    );
    renderFeatured();
    fireEvent.click(await screen.findByRole("button", { name: /^enable$/i }));
    const input = await screen.findByLabelText(/github access token/i);
    fireEvent.change(input, { target: { value: "ghp_test" } });
    const submit = within(input.closest(".mcp-featured-enable")).getByRole(
      "button",
      { name: /^enable$/i },
    );
    fireEvent.click(submit);
    expect(await screen.findByRole("button", { name: /enabling/i })).toBeInTheDocument();
    resolveEnable({ server_id: "srv-feat", auth_start_url: null });
    await waitFor(() => expect(api.enableFeaturedServer).toHaveBeenCalledTimes(1));
  });

  it("guards against a double submit while an enable is in flight (direct path)", async () => {
    // The non-static Enable button has no disabled state, so a rapid
    // double-click re-enters submitEnable — the busy guard must drop the
    // second call.
    let resolveEnable;
    api.getFeaturedServers.mockResolvedValueOnce({ servers: [DCR] });
    api.enableFeaturedServer.mockReturnValueOnce(
      new Promise((r) => {
        resolveEnable = r;
      }),
    );
    renderFeatured();
    const enable = await screen.findByRole("button", { name: /^enable$/i });
    fireEvent.click(enable);
    fireEvent.click(enable);
    resolveEnable({ server_id: "srv-acme", auth_start_url: null });
    await waitFor(() => expect(api.enableFeaturedServer).toHaveBeenCalledTimes(1));
  });

  it("Cancel backs out of the PAT step and drops the pasted token", async () => {
    renderFeatured();
    fireEvent.click(await screen.findByRole("button", { name: /^enable$/i }));
    const input = await screen.findByLabelText(/github access token/i);
    fireEvent.change(input, { target: { value: "ghp_secret" } });
    fireEvent.click(
      within(input.closest(".mcp-featured-enable")).getByRole("button", {
        name: /cancel/i,
      }),
    );
    await waitFor(() =>
      expect(screen.queryByLabelText(/github access token/i)).not.toBeInTheDocument(),
    );
    // Reopening shows an empty field — no stale secret retained.
    fireEvent.click(screen.getByRole("button", { name: /^enable$/i }));
    expect(await screen.findByLabelText(/github access token/i)).toHaveValue("");
  });

  it("a non-static featured entry enables directly and opens its auth URL", async () => {
    api.getFeaturedServers.mockResolvedValueOnce({ servers: [DCR] });
    api.enableFeaturedServer.mockResolvedValueOnce({
      server_id: "srv-acme",
      auth_start_url: "https://acme.example.com/auth",
    });
    // Omit onEnabled + registeredUrls to also cover the default props.
    render(<FeaturedMCPServers />);
    fireEvent.click(await screen.findByRole("button", { name: /^enable$/i }));
    await waitFor(() =>
      expect(api.enableFeaturedServer).toHaveBeenCalledWith({
        featured_id: "acme",
        name: "Acme",
        url: "https://acme.example.com/mcp",
        token: null,
      }),
    );
    expect(window.open).toHaveBeenCalledWith(
      "https://acme.example.com/auth",
      "_blank",
      "noopener,noreferrer",
    );
    // No PAT field is ever revealed for the OAuth path.
    expect(screen.queryByLabelText(/access token/i)).not.toBeInTheDocument();
  });

  it("uses a token-free error message when a non-static (OAuth) enable fails", async () => {
    api.getFeaturedServers.mockResolvedValueOnce({ servers: [DCR] });
    api.enableFeaturedServer.mockRejectedValueOnce(new Error("nope"));
    renderFeatured();
    fireEvent.click(await screen.findByRole("button", { name: /^enable$/i }));
    const err = await screen.findByText(/couldn't enable acme/i);
    expect(err).toBeInTheDocument();
    // OAuth entries involve no pasted token, so the message must NOT
    // tell the user to check a token.
    expect(err.textContent).toMatch(/please try again/i);
    expect(err.textContent).not.toMatch(/check the token/i);
  });

  it("surfaces an error and never logs the token when enable fails", async () => {
    api.enableFeaturedServer.mockRejectedValueOnce(new Error("nope"));
    renderFeatured();
    fireEvent.click(await screen.findByRole("button", { name: /^enable$/i }));
    const input = await screen.findByLabelText(/github access token/i);
    fireEvent.change(input, { target: { value: "ghp_supersecret" } });
    fireEvent.click(
      within(input.closest(".mcp-featured-enable")).getByRole("button", {
        name: /^enable$/i,
      }),
    );
    expect(
      await screen.findByText(/couldn't enable github/i),
    ).toBeInTheDocument();
    // console.error is called with String(e) only — never the raw token.
    const logged = console.error.mock.calls.flat().join(" ");
    expect(logged).not.toContain("ghp_supersecret");
  });

  it("dedupes an already-registered entry: shows Enabled + per-chat note, no Enable", async () => {
    renderFeatured({ registeredUrls: [GITHUB.url] });
    const enabled = await screen.findByText(/^enabled$/i);
    expect(enabled).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^enable$/i })).not.toBeInTheDocument();
    // default_globally_enabled=false → the write-tools note is shown.
    expect(
      screen.getByText(/write tools stay off until you enable this server per-chat/i),
    ).toBeInTheDocument();
  });

  it("an enabled entry with default_globally_enabled=true omits the per-chat note", async () => {
    api.getFeaturedServers.mockResolvedValueOnce({ servers: [DCR] });
    renderFeatured({ registeredUrls: [DCR.url] });
    expect(await screen.findByText(/^enabled$/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/write tools stay off/i),
    ).not.toBeInTheDocument();
  });

  it("ignores a late resolve after unmount (no state update on a dead component)", async () => {
    let resolveLoad;
    api.getFeaturedServers.mockReturnValueOnce(
      new Promise((r) => {
        resolveLoad = r;
      }),
    );
    const { unmount } = renderFeatured();
    unmount();
    resolveLoad({ servers: [GITHUB] });
    // No throw / act warning — the cancelled guard swallowed the update.
    await Promise.resolve();
    expect(screen.queryByText("GitHub")).not.toBeInTheDocument();
  });

  it("ignores a late rejection after unmount", async () => {
    let rejectLoad;
    api.getFeaturedServers.mockReturnValueOnce(
      new Promise((_r, rej) => {
        rejectLoad = rej;
      }),
    );
    const { unmount } = renderFeatured();
    unmount();
    rejectLoad(new Error("late"));
    await Promise.resolve();
    expect(
      screen.queryByText(/couldn't load featured integrations/i),
    ).not.toBeInTheDocument();
  });
});
