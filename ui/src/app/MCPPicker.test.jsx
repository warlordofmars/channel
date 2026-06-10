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

  it("shows 'Needs reconnect' on a non-active server and reflects it in the pill count", () => {
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
    // Even though Zed is in INHERIT mode's "active" derivation, its
    // expired auth_status means the pill count is 0 — it can't carry
    // tool calls until the user reconnects.
    expect(
      screen.getByRole("button", { name: /0 tool servers/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    expect(screen.getByText(/needs reconnect/i)).toBeInTheDocument();
  });

  it("blocks selecting a non-active server but allows unselecting one that's already active in explicit mode", () => {
    const onChange = vi.fn();
    const expired = [
      {
        server_id: "z",
        name: "Zed",
        tool_prefix: "zed",
        globally_enabled: true,
        auth_status: "expired",
      },
    ];
    // Explicit mode with Zed already in the list — user must be able
    // to take it out of the active set even though its status is expired.
    render(
      <MCPPicker
        servers={expired}
        mode="explicit"
        explicitIds={["z"]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    const checkbox = screen.getByLabelText(/^Zed$/).querySelector("input");
    expect(checkbox.disabled).toBe(false);  // currently checked → unselectable allowed
    fireEvent.click(checkbox);
    expect(onChange).toHaveBeenCalledWith({
      mode: "explicit",
      explicit_server_ids: [],
    });
  });

  it("non-active server with no current selection is disabled (can't be selected)", () => {
    const expired = [
      {
        server_id: "z",
        name: "Zed",
        tool_prefix: "zed",
        globally_enabled: false,  // not in inherit's active set
        auth_status: "expired",
      },
    ];
    render(
      <MCPPicker
        servers={expired}
        mode="explicit"
        explicitIds={[]}  // not selected
        onChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    const checkbox = screen.getByLabelText(/^Zed$/).querySelector("input");
    expect(checkbox.disabled).toBe(true);
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

  it("backdrop Escape key closes the popover", () => {
    const { container } = render(
      <MCPPicker servers={SERVERS} mode="inherit" explicitIds={[]} onChange={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    const backdrop = container.querySelector(".backdrop");
    fireEvent.keyDown(backdrop, { key: "Escape" });
    expect(container.querySelector(".pop")).toBeNull();
  });

  it("backdrop Enter key closes the popover", () => {
    const { container } = render(
      <MCPPicker servers={SERVERS} mode="inherit" explicitIds={[]} onChange={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    const backdrop = container.querySelector(".backdrop");
    fireEvent.keyDown(backdrop, { key: "Enter" });
    expect(container.querySelector(".pop")).toBeNull();
  });

  it("backdrop ignores unrelated keys", () => {
    const { container } = render(
      <MCPPicker servers={SERVERS} mode="inherit" explicitIds={[]} onChange={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /tool servers?\b/i }));
    const backdrop = container.querySelector(".backdrop");
    fireEvent.keyDown(backdrop, { key: "a" });
    expect(container.querySelector(".pop")).not.toBeNull();
  });
});
