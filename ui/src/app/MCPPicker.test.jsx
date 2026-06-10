// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import MCPPicker from "./MCPPicker.jsx";

describe("MCPPicker", () => {
  const SERVERS = [
    { server_id: "a", name: "Alpha", tool_prefix: "alpha", globally_enabled: true,  auth_status: "active" },
    { server_id: "b", name: "Beta",  tool_prefix: "beta",  globally_enabled: true,  auth_status: "active" },
    { server_id: "c", name: "Gone",  tool_prefix: "gone",  globally_enabled: false, auth_status: "active" },
  ];

  it("renders the inline pill with active count", () => {
    render(
      <MCPPicker servers={SERVERS} mode="inherit" explicitIds={[]} onChange={() => {}} />,
    );
    expect(
      screen.getByRole("button", { name: /2 tool servers/i }),
    ).toBeInTheDocument();
  });

  it("renders the singular pill label with exactly one active server", () => {
    const oneServer = [SERVERS[0]];
    render(
      <MCPPicker servers={oneServer} mode="inherit" explicitIds={[]} onChange={() => {}} />,
    );
    expect(
      screen.getByRole("button", { name: /1 tool server\b/i }),
    ).toBeInTheDocument();
  });

  it("opens the popover and lists registered servers", () => {
    render(
      <MCPPicker servers={SERVERS} mode="inherit" explicitIds={[]} onChange={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("Gone")).toBeInTheDocument();
  });

  it("toggling a server in inherit mode flips to explicit mode with the new list", () => {
    const onChange = vi.fn();
    render(
      <MCPPicker servers={SERVERS} mode="inherit" explicitIds={[]} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    fireEvent.click(screen.getByLabelText(/^Alpha$/));
    expect(onChange).toHaveBeenCalledWith({
      mode: "explicit",
      explicit_server_ids: ["b"],
    });
  });

  it("toggling a server in explicit mode adds it to the list", () => {
    const onChange = vi.fn();
    render(
      <MCPPicker
        servers={SERVERS}
        mode="explicit"
        explicitIds={["a"]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    fireEvent.click(screen.getByLabelText(/^Beta$/));
    expect(onChange).toHaveBeenCalledWith({
      mode: "explicit",
      explicit_server_ids: ["a", "b"],
    });
  });

  it("Reset to global defaults switches back to inherit mode", () => {
    const onChange = vi.fn();
    render(
      <MCPPicker
        servers={SERVERS}
        mode="explicit"
        explicitIds={["a"]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    fireEvent.click(screen.getByRole("button", { name: /reset to global defaults/i }));
    expect(onChange).toHaveBeenCalledWith({
      mode: "inherit",
      explicit_server_ids: [],
    });
  });

  it("renders the empty-state placeholder when no servers are registered", () => {
    render(
      <MCPPicker servers={[]} mode="inherit" explicitIds={[]} onChange={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /0 tool servers/i }));
    expect(screen.getByTestId("mcp-empty")).toBeInTheDocument();
  });

  it("disables servers whose auth_status is not active", () => {
    const expired = [
      {
        server_id: "z",
        name: "Zed",
        tool_prefix: "zed",
        globally_enabled: true,
        auth_status: "expired",
      },
    ];
    render(
      <MCPPicker servers={expired} mode="inherit" explicitIds={[]} onChange={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    expect(screen.getByText(/needs reconnect/i)).toBeInTheDocument();
  });

  it("backdrop click closes the popover", () => {
    const { container } = render(
      <MCPPicker servers={SERVERS} mode="inherit" explicitIds={[]} onChange={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    const backdrop = container.querySelector(".backdrop");
    expect(backdrop).toBeTruthy();
    fireEvent.click(backdrop);
    expect(container.querySelector(".pop")).toBeNull();
  });
});
