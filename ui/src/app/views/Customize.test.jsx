// Copyright (c) 2026 John Carter. All rights reserved.
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api.js", () => ({
  listModels: vi.fn(),
  getPrefs: vi.fn(() => Promise.resolve({})),
  putPrefs: vi.fn(() => Promise.resolve()),
  listMemoryRecords: vi.fn(() =>
    Promise.resolve({
      groups: [],
      summaries: [],
      recall_window: {
        max_sessions: 5,
        events_per_session: 2,
        text_truncate: 120,
        ordering: "relevance",
        enabled: true,
      },
      withheld_record_count: 0,
      next_cursor: null,
    }),
  ),
  listMCPServers: vi.fn(() => Promise.resolve({ servers: [] })),
  patchMCPServer: vi.fn(() => Promise.resolve()),
  deleteMCPServer: vi.fn(() => Promise.resolve()),
  reauthMCPServer: vi.fn(() =>
    Promise.resolve({ auth_start_url: "https://x" }),
  ),
  registerMCPServer: vi.fn(() =>
    Promise.resolve({ server_id: "srv-1", auth_start_url: "https://x" }),
  ),
  getFeaturedServers: vi.fn(() => Promise.resolve({ servers: [] })),
  enableFeaturedServer: vi.fn(() =>
    Promise.resolve({ server_id: "srv-feat", auth_start_url: null }),
  ),
}));

import * as api from "../../api.js";
import Customize, { PrefsSyncNotice } from "./Customize.jsx";
import {
  EFFORTS,
  MODEL_DISPLAY_META,
  __resetModelsCacheForTest,
  loadModels,
} from "../data.js";
import {
  STORAGE_KEYS,
  __resetChannelPrefsForTest,
  __resetServerSyncForTest,
} from "../../hooks/useChannelPrefs.js";
import { TOKEN_KEY } from "../../lib/auth.js";

const SERVER_ALLOWLIST = [
  { id: "claude-opus-4-6", label: "Claude Opus 4.6", tier: "Flagship" },
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", tier: "Balanced" },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5", tier: "Fast" },
];

function renderCustomize() {
  return render(
    <MemoryRouter initialEntries={["/app/customize"]}>
      <Customize />
    </MemoryRouter>
  );
}

describe("Customize", () => {
  let storage;
  beforeEach(async () => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    // Reset attrs the hook sets on <html>.
    for (const a of Array.from(document.documentElement.attributes)) {
      if (a.name.startsWith("data-")) document.documentElement.removeAttribute(a.name);
    }
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    __resetModelsCacheForTest();
    api.listModels.mockReset();
    api.listModels.mockResolvedValue({ models: SERVER_ALLOWLIST });
    api.getPrefs.mockReset();
    api.getPrefs.mockResolvedValue({});
    api.putPrefs.mockReset();
    api.putPrefs.mockResolvedValue();
    // Pre-warm the cache so synchronous renders see the model list.
    await loadModels();
  });
  afterEach(() => {
    // Unconditional: a test that throws between useFakeTimers() and its own
    // useRealTimers() would otherwise leave the fake clock installed, and
    // every later `waitFor` in the file would hang on it.
    vi.useRealTimers();
    vi.unstubAllGlobals();
    __resetModelsCacheForTest();
  });

  it("renders the 'Customize' header + tagline", () => {
    renderCustomize();
    expect(screen.getByRole("heading", { level: 2, name: "Customize" })).toBeTruthy();
    expect(screen.getByText(/Tune Channel's appearance/i)).toBeTruthy();
  });

  it("links to the signed-in devices view (#296)", () => {
    // The sessions view is its own route, so this row is the only way a user
    // finds it — a route with no entry point is unreachable code.
    renderCustomize();
    expect(screen.getByRole("heading", { level: 3, name: "Security" })).toBeTruthy();
    const link = screen.getByRole("link", { name: "Manage" });
    expect(link.getAttribute("href")).toBe("/app/sessions");
  });

  it("renders three section headers: Appearance / Defaults / Behavior", () => {
    renderCustomize();
    expect(screen.getByRole("heading", { level: 3, name: "Appearance" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Defaults" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Behavior" })).toBeTruthy();
  });

  it("Theme seg-ctl marks the persisted theme as .on", () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    renderCustomize();
    expect(screen.getByRole("button", { name: /Light/ }).className).toContain("on");
    expect(screen.getByRole("button", { name: /^Dark$/ }).className).not.toContain("on");
  });

  it("clicking the Dark button persists theme=dark and updates the .on class", () => {
    storage["channel-theme"] = "light";
    __resetChannelPrefsForTest();
    renderCustomize();
    fireEvent.click(screen.getByRole("button", { name: /^Dark$/ }));
    expect(storage["channel-theme"]).toBe("dark");
    expect(screen.getByRole("button", { name: /^Dark$/ }).className).toContain("on");
  });

  it("renders 5 accent swatches with the correct OKLCH backgrounds", () => {
    const { container } = renderCustomize();
    const swatches = container.querySelectorAll(".swatch");
    expect(swatches.length).toBe(5);
    expect(swatches[0].style.background).toMatch(/oklch\(0\.6 0\.13 42\)/);
    expect(swatches[0].getAttribute("title")).toBe("Clay");
  });

  it("clicking an accent swatch persists the hue as the new accent", () => {
    renderCustomize();
    const fern = document.body.querySelector("[title='Fern']"); // hue 150
    expect(fern).toBeTruthy();
    fireEvent.click(fern);
    expect(storage["channel-accent"]).toBe("150");
    expect(fern.className).toContain("on");
  });

  it("Density seg-ctl persists the chosen value", () => {
    renderCustomize();
    expect(screen.getByRole("button", { name: "Cozy" }).className).toContain("on");
    fireEvent.click(screen.getByRole("button", { name: "Compact" }));
    expect(storage["channel-density"]).toBe("compact");
    expect(screen.getByRole("button", { name: "Compact" }).className).toContain("on");
  });

  it("Default model seg-ctl renders one button per API allowlist entry + persists the chosen id", () => {
    renderCustomize();
    for (const sm of SERVER_ALLOWLIST) {
      const expected = MODEL_DISPLAY_META[sm.id]?.short ?? sm.label;
      expect(screen.getByRole("button", { name: expected })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: "Haiku 4.5" }));
    expect(storage["channel-model"]).toBe("claude-haiku-4-5");
    expect(screen.getByRole("button", { name: "Haiku 4.5" }).className).toContain("on");
  });

  it("Reasoning effort seg-ctl renders one button per EFFORTS entry + persists the choice", () => {
    renderCustomize();
    for (const e of EFFORTS) {
      expect(screen.getByRole("button", { name: e })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: "Max" }));
    expect(storage["channel-effort"]).toBe("Max");
    expect(screen.getByRole("button", { name: "Max" }).className).toContain("on");
  });

  it("Default-model hint text follows the active model's desc string", () => {
    storage["channel-model"] = "claude-haiku-4-5";
    __resetChannelPrefsForTest();
    renderCustomize();
    const haikuDesc = MODEL_DISPLAY_META["claude-haiku-4-5"].desc;
    expect(screen.getByText(haikuDesc)).toBeTruthy();
  });

  it("renders three Behavior toggles with the correct default states", () => {
    const { container } = renderCustomize();
    const toggles = container.querySelectorAll(".toggle");
    expect(toggles.length).toBe(3);
    expect(toggles[0].className).toContain("on");
    expect(toggles[1].className).not.toContain("on");
    expect(toggles[2].className).toContain("on");
  });

  it("clicking each Behavior toggle persists the new value via the prefs hook", () => {
    const { container } = renderCustomize();
    const toggles = container.querySelectorAll(".toggle");

    fireEvent.click(toggles[0]);
    expect(toggles[0].className).not.toContain("on");
    expect(storage[STORAGE_KEYS.sendOnEnter]).toBe("0");

    fireEvent.click(toggles[1]);
    expect(toggles[1].className).toContain("on");
    expect(storage[STORAGE_KEYS.showReasoning]).toBe("1");

    fireEvent.click(toggles[2]);
    expect(toggles[2].className).not.toContain("on");
    expect(storage[STORAGE_KEYS.suggestFollowups]).toBe("0");
  });

  it("Behavior toggle initial state reflects persisted prefs (round-trip)", () => {
    storage[STORAGE_KEYS.sendOnEnter] = "0";
    storage[STORAGE_KEYS.showReasoning] = "1";
    storage[STORAGE_KEYS.suggestFollowups] = "0";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    const { container } = renderCustomize();
    const toggles = container.querySelectorAll(".toggle");
    expect(toggles[0].className).not.toContain("on");
    expect(toggles[1].className).toContain("on");
    expect(toggles[2].className).not.toContain("on");
  });

  it("clicking the Light button (when theme is dark) switches to light", () => {
    storage["channel-theme"] = "dark";
    __resetChannelPrefsForTest();
    renderCustomize();
    fireEvent.click(screen.getByRole("button", { name: /Light/ }));
    expect(storage["channel-theme"]).toBe("light");
  });

  it("clicking the Cozy button (when density is compact) switches to cozy", () => {
    storage["channel-density"] = "compact";
    __resetChannelPrefsForTest();
    renderCustomize();
    fireEvent.click(screen.getByRole("button", { name: "Cozy" }));
    expect(storage["channel-density"]).toBe("cozy");
  });

  it("Behavior toggles render their label + hint text", () => {
    renderCustomize();
    expect(screen.getByText("Send on Enter")).toBeTruthy();
    expect(screen.getByText(/Press Enter to send/)).toBeTruthy();
    expect(screen.getByText("Show reasoning trace")).toBeTruthy();
    expect(screen.getByText(/model's thinking/)).toBeTruthy();
    expect(screen.getByText("Suggest follow-ups")).toBeTruthy();
    expect(screen.getByText(/related prompts/)).toBeTruthy();
  });

  it("falls back to the first API entry when prefs.model points at an unknown id", () => {
    storage["channel-model"] = "no-such-model";
    __resetChannelPrefsForTest();
    renderCustomize();
    // The first API entry (Opus 4.6) should be highlighted + supply the hint.
    expect(screen.getByRole("button", { name: "Opus 4.6" }).className).toContain("on");
    expect(screen.getByText(MODEL_DISPLAY_META["claude-opus-4-6"].desc)).toBeTruthy();
  });

  it("renders a 'Loading models…' placeholder before the API resolves", async () => {
    __resetModelsCacheForTest();
    let resolvePromise;
    api.listModels.mockReturnValue(new Promise((resolve) => { resolvePromise = resolve; }));
    renderCustomize();
    expect(screen.getByTestId("models-loading")).toBeTruthy();
    // Resolve to silence the unhandled-promise warning.
    resolvePromise({ models: SERVER_ALLOWLIST });
    // Wait for the post-resolve render so afterEach's reset doesn't fire
    // while React is still processing the state update.
    await waitFor(() => expect(screen.queryByTestId("models-loading")).toBeNull());
  });

  it("renders an error placeholder when the API call fails", async () => {
    __resetModelsCacheForTest();
    api.listModels.mockRejectedValueOnce(new Error("network"));
    renderCustomize();
    await waitFor(() => expect(screen.getByTestId("models-error")).toBeTruthy());
  });

  it("renders the MCP servers empty state by default", async () => {
    renderCustomize();
    expect(
      await screen.findByText(/no mcp servers yet/i),
    ).toBeInTheDocument();
  });

  it("renders the MCP servers section heading", async () => {
    renderCustomize();
    expect(
      await screen.findByRole("heading", { name: /mcp servers/i }),
    ).toBeInTheDocument();
  });

  it("renders MCP server rows when the list is non-empty", async () => {
    api.listMCPServers.mockResolvedValueOnce({
      servers: [
        {
          server_id: "srv-1",
          name: "Hive",
          url: "https://hive.example.com/mcp",
          tool_prefix: "hive",
          auth_status: "active",
          globally_enabled: true,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    renderCustomize();
    expect(await screen.findByText("Hive")).toBeInTheDocument();
    expect(
      screen.getByText("https://hive.example.com/mcp"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /reconnect/i }),
    ).toBeInTheDocument();
  });

  it("hides Reconnect for static-token servers (no OAuth reauth flow)", async () => {
    api.listMCPServers.mockResolvedValueOnce({
      servers: [
        {
          server_id: "srv-static",
          name: "GitHub",
          url: "https://api.githubcopilot.com/mcp/",
          tool_prefix: "github",
          auth_type: "static_token",
          auth_status: "active",
          globally_enabled: true,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    renderCustomize();
    expect(await screen.findByText("GitHub")).toBeInTheDocument();
    // Static-token servers can't be OAuth-reauthorized, so no Reconnect.
    expect(
      screen.queryByRole("button", { name: /reconnect/i }),
    ).not.toBeInTheDocument();
    // Remove is still available.
    expect(
      screen.getByRole("button", { name: /remove/i }),
    ).toBeInTheDocument();
  });

  it("shows an error message when listMCPServers fails", async () => {
    api.listMCPServers.mockRejectedValueOnce(new Error("boom"));
    renderCustomize();
    await waitFor(() =>
      expect(
        screen.getByText(/couldn't load mcp servers/i),
      ).toBeInTheDocument(),
    );
  });

  it("renders Enable-globally aria-label for globally-disabled servers", async () => {
    api.listMCPServers.mockResolvedValue({
      servers: [
        {
          server_id: "srv-1",
          name: "Hive",
          url: "https://hive.example.com/mcp",
          tool_prefix: "hive",
          auth_status: "active",
          globally_enabled: false,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    renderCustomize();
    expect(
      await screen.findByLabelText(/enable hive globally/i),
    ).toBeInTheDocument();
  });

  it("clicking the global-enable toggle calls patchMCPServer + refreshes", async () => {
    api.listMCPServers.mockResolvedValue({
      servers: [
        {
          server_id: "srv-1",
          name: "Hive",
          url: "https://hive.example.com/mcp",
          tool_prefix: "hive",
          auth_status: "active",
          globally_enabled: true,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    renderCustomize();
    const toggle = await screen.findByLabelText(/disable hive globally/i);
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(api.patchMCPServer).toHaveBeenCalledWith("srv-1", {
        globally_enabled: false,
      }),
    );
  });

  it("clicking Reconnect calls reauthMCPServer and opens a new tab", async () => {
    api.listMCPServers.mockResolvedValue({
      servers: [
        {
          server_id: "srv-1",
          name: "Hive",
          url: "https://hive.example.com/mcp",
          tool_prefix: "hive",
          auth_status: "expired",
          globally_enabled: true,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    renderCustomize();
    const reconnect = await screen.findByRole("button", { name: /reconnect/i });
    fireEvent.click(reconnect);
    await waitFor(() =>
      expect(api.reauthMCPServer).toHaveBeenCalledWith("srv-1"),
    );
    expect(openSpy).toHaveBeenCalledWith(
      "https://x",
      "_blank",
      "noopener,noreferrer",
    );
    openSpy.mockRestore();
  });

  it("clicking Remove calls deleteMCPServer when the confirm dialog is accepted", async () => {
    api.listMCPServers.mockResolvedValue({
      servers: [
        {
          server_id: "srv-1",
          name: "Hive",
          url: "https://hive.example.com/mcp",
          tool_prefix: "hive",
          auth_status: "active",
          globally_enabled: true,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderCustomize();
    const remove = await screen.findByRole("button", { name: /remove/i });
    fireEvent.click(remove);
    await waitFor(() =>
      expect(api.deleteMCPServer).toHaveBeenCalledWith("srv-1"),
    );
    confirmSpy.mockRestore();
  });

  it("clicking Remove is a no-op when the confirm dialog is cancelled", async () => {
    api.listMCPServers.mockResolvedValue({
      servers: [
        {
          server_id: "srv-1",
          name: "Hive",
          url: "https://hive.example.com/mcp",
          tool_prefix: "hive",
          auth_status: "active",
          globally_enabled: true,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    api.deleteMCPServer.mockClear();
    renderCustomize();
    const remove = await screen.findByRole("button", { name: /remove/i });
    fireEvent.click(remove);
    // give a microtask to flush
    await new Promise((r) => setTimeout(r, 0));
    expect(api.deleteMCPServer).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("clicking Add server opens the AddMCPServerModal, Cancel closes it", async () => {
    renderCustomize();
    const addButton = await screen.findByRole("button", { name: /^add server$/i });
    fireEvent.click(addButton);
    expect(
      await screen.findByRole("heading", { name: /add mcp server/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: /add mcp server/i }),
      ).not.toBeInTheDocument(),
    );
  });

  it("renders the Featured integrations section from getFeaturedServers", async () => {
    api.getFeaturedServers.mockResolvedValueOnce({
      servers: [
        {
          featured_id: "github",
          name: "GitHub",
          url: "https://api.githubcopilot.com/mcp/",
          description: "GitHub's official MCP server.",
          docs_url: "https://github.com/settings/personal-access-tokens",
          auth_type: "static_token",
          tool_prefix: "github",
          default_globally_enabled: false,
        },
      ],
    });
    renderCustomize();
    expect(
      await screen.findByRole("heading", { name: /featured integrations/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^enable$/i })).toBeInTheDocument();
  });

  it("dedupes a featured entry already registered (shows Enabled, not Enable)", async () => {
    api.listMCPServers.mockResolvedValue({
      servers: [
        {
          server_id: "srv-gh",
          name: "GitHub",
          url: "https://api.githubcopilot.com/mcp/",
          tool_prefix: "github",
          auth_type: "static_token",
          auth_status: "active",
          globally_enabled: false,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    api.getFeaturedServers.mockResolvedValueOnce({
      servers: [
        {
          featured_id: "github",
          name: "GitHub",
          url: "https://api.githubcopilot.com/mcp/",
          description: "GitHub's official MCP server.",
          docs_url: "https://github.com/settings/personal-access-tokens",
          auth_type: "static_token",
          tool_prefix: "github",
          default_globally_enabled: false,
        },
      ],
    });
    renderCustomize();
    // The featured GitHub card renders its "Enabled" pill…
    const featured = (await screen.findByRole("heading", {
      name: /featured integrations/i,
    })).closest(".mcp-featured");
    expect(within(featured).getByText(/enabled/i)).toBeInTheDocument();
    expect(
      within(featured).queryByRole("button", { name: /^enable$/i }),
    ).not.toBeInTheDocument();
  });

  it("renders accessible status text + aria-label on the auth-status dot", async () => {
    api.listMCPServers.mockResolvedValueOnce({
      servers: [
        {
          server_id: "srv-1",
          name: "Hive",
          url: "https://hive.example.com/mcp",
          tool_prefix: "hive",
          auth_status: "expired",
          globally_enabled: true,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    renderCustomize();
    // Visible badge text — shown next to the server name.
    expect(
      await screen.findByText(/reconnect needed/i),
    ).toBeInTheDocument();
    // Dot itself carries the same label for screen reader users.
    const dot = await screen.findByRole("img", { name: /reconnect needed/i });
    expect(dot).toBeInTheDocument();
  });

  it("renders 'Not yet connected' badge for a never_authed server", async () => {
    api.listMCPServers.mockResolvedValueOnce({
      servers: [
        {
          server_id: "srv-1", name: "Hive",
          url: "https://hive.example.com/mcp", tool_prefix: "hive",
          auth_status: "never_authed",
          globally_enabled: true, created_at: "x", updated_at: "x",
        },
      ],
    });
    renderCustomize();
    expect(await screen.findByText(/not yet connected/i)).toBeInTheDocument();
  });

  it("renders 'Revoked' badge for a revoked-status server", async () => {
    api.listMCPServers.mockResolvedValueOnce({
      servers: [
        {
          server_id: "srv-1", name: "Hive",
          url: "https://hive.example.com/mcp", tool_prefix: "hive",
          auth_status: "revoked",
          globally_enabled: true, created_at: "x", updated_at: "x",
        },
      ],
    });
    renderCustomize();
    expect(await screen.findByText(/^Revoked$/)).toBeInTheDocument();
  });

  it("falls back to the raw status string for unknown auth_status values", async () => {
    api.listMCPServers.mockResolvedValueOnce({
      servers: [
        {
          server_id: "srv-1", name: "Hive",
          url: "https://hive.example.com/mcp", tool_prefix: "hive",
          // Defensive: future / unknown values shouldn't crash the row.
          auth_status: "synthetic_future_value",
          globally_enabled: true, created_at: "x", updated_at: "x",
        },
      ],
    });
    renderCustomize();
    expect(
      await screen.findByText(/synthetic_future_value/i),
    ).toBeInTheDocument();
  });

  it("toggleGlobal surfaces a user-visible error when patchMCPServer rejects", async () => {
    api.listMCPServers.mockResolvedValue({
      servers: [
        {
          server_id: "srv-1",
          name: "Hive",
          url: "https://hive.example.com/mcp",
          tool_prefix: "hive",
          auth_status: "active",
          globally_enabled: true,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    api.patchMCPServer.mockRejectedValueOnce(new Error("500"));
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    renderCustomize();
    fireEvent.click(await screen.findByLabelText(/disable hive globally/i));
    await waitFor(() =>
      expect(screen.getByText(/couldn't update hive/i)).toBeInTheDocument(),
    );
    errorSpy.mockRestore();
  });

  it("removeServer surfaces a user-visible error when deleteMCPServer rejects", async () => {
    api.listMCPServers.mockResolvedValue({
      servers: [
        {
          server_id: "srv-1",
          name: "Hive",
          url: "https://hive.example.com/mcp",
          tool_prefix: "hive",
          auth_status: "active",
          globally_enabled: true,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    api.deleteMCPServer.mockRejectedValueOnce(new Error("403"));
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    renderCustomize();
    fireEvent.click(await screen.findByRole("button", { name: /remove/i }));
    await waitFor(() =>
      expect(screen.getByText(/couldn't remove hive/i)).toBeInTheDocument(),
    );
    confirmSpy.mockRestore();
    errorSpy.mockRestore();
  });

  it("reauth surfaces a user-visible error when reauthMCPServer rejects", async () => {
    api.listMCPServers.mockResolvedValue({
      servers: [
        {
          server_id: "srv-1",
          name: "Hive",
          url: "https://hive.example.com/mcp",
          tool_prefix: "hive",
          auth_status: "expired",
          globally_enabled: true,
          created_at: "x",
          updated_at: "x",
        },
      ],
    });
    api.reauthMCPServer.mockRejectedValueOnce(new Error("502"));
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    renderCustomize();
    fireEvent.click(await screen.findByRole("button", { name: /reconnect/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/couldn't start reconnect for hive/i),
      ).toBeInTheDocument(),
    );
    errorSpy.mockRestore();
  });

  it("successful registration through the modal refreshes the MCP server list", async () => {
    api.listMCPServers.mockClear();
    api.listMCPServers.mockResolvedValue({ servers: [] });
    api.registerMCPServer.mockResolvedValueOnce({
      server_id: "srv-1",
      auth_start_url: "https://x",
    });
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    renderCustomize();
    // Initial mount triggers one listMCPServers call.
    await waitFor(() => expect(api.listMCPServers).toHaveBeenCalledTimes(1));
    fireEvent.click(
      await screen.findByRole("button", { name: /^add server$/i }),
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name"), {
      target: { value: "Hive" },
    });
    fireEvent.change(within(dialog).getByLabelText("Server URL"), {
      target: { value: "https://hive.example.com/mcp" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: /^add server$/i }),
    );
    // onRegistered → refreshMCP → second listMCPServers call.
    await waitFor(() => expect(api.listMCPServers).toHaveBeenCalledTimes(2));
    openSpy.mockRestore();
  });

  it("?mcp_authed=error&reason=invalid_state surfaces a human-readable message", async () => {
    api.listMCPServers.mockResolvedValue({ servers: [] });
    render(
      <MemoryRouter initialEntries={["/app/customize?mcp_authed=error&reason=invalid_state"]}>
        <Customize />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(
        screen.getByText(/oauth session expired or invalid/i),
      ).toBeInTheDocument(),
    );
  });

  it.each([
    ["no_code", /didn't return an auth code/i],
    ["server_gone", /registration was removed/i],
    ["blocked_url", /url is no longer allowed/i],
  ])(
    "?mcp_authed=error&reason=%s surfaces its specific message",
    async (reason, expected) => {
      api.listMCPServers.mockResolvedValue({ servers: [] });
      render(
        <MemoryRouter initialEntries={[`/app/customize?mcp_authed=error&reason=${reason}`]}>
          <Customize />
        </MemoryRouter>,
      );
      await waitFor(() =>
        expect(screen.getByText(expected)).toBeInTheDocument(),
      );
    },
  );

  it("?mcp_authed=error with no reason still shows a fallback message", async () => {
    api.listMCPServers.mockResolvedValue({ servers: [] });
    render(
      <MemoryRouter initialEntries={["/app/customize?mcp_authed=error"]}>
        <Customize />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(
        screen.getByText(/couldn't connect to mcp server: unknown error/i),
      ).toBeInTheDocument(),
    );
  });

  it("?mcp_authed=error with an unknown reason passes the raw reason through", async () => {
    api.listMCPServers.mockResolvedValue({ servers: [] });
    render(
      <MemoryRouter initialEntries={["/app/customize?mcp_authed=error&reason=access_denied"]}>
        <Customize />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(
        screen.getByText(/couldn't connect to mcp server: access_denied/i),
      ).toBeInTheDocument(),
    );
  });

  it("?mcp_authed=error does NOT trigger an extra MCP refresh", async () => {
    api.listMCPServers.mockClear();
    api.listMCPServers.mockResolvedValue({ servers: [] });
    render(
      <MemoryRouter initialEntries={["/app/customize?mcp_authed=error&reason=token_exchange"]}>
        <Customize />
      </MemoryRouter>,
    );
    // Wait for the error message to land — by that point any list
    // refresh would have fired too.
    await screen.findByText(/failed to exchange the authorization code/i);
    // Mount fires one refresh. The error-branch effect must NOT fire a
    // second one.
    expect(api.listMCPServers).toHaveBeenCalledTimes(1);
  });

  it("?mcp_authed=ok in the URL triggers an MCP refresh on mount", async () => {
    api.listMCPServers.mockClear();
    api.listMCPServers.mockResolvedValue({ servers: [] });
    render(
      <MemoryRouter initialEntries={["/app/customize?mcp_authed=ok&server_id=srv-1"]}>
        <Customize />
      </MemoryRouter>,
    );
    await waitFor(() =>
      // Once for mount + once for the search-params effect.
      expect(api.listMCPServers).toHaveBeenCalledTimes(2),
    );
  });

  // ---- prefsSyncError indicator (#574) -----------------------------------
  //
  // #573 made a rejected pref PUT detectable (`prefsSyncError` +
  // `retryPrefsSync`) and nothing consumed it, so the failure was still
  // invisible. These drive the REAL hook — a mgmt token in storage plus a
  // rejecting `putPrefs` — rather than stubbing `useChannelPrefs`, so they
  // fail if the wiring between hook and view breaks, not just if the JSX
  // stops mentioning a field.

  /** Comfortably past the hook's 200ms write debounce. */
  const PUT_DEBOUNCE = 250;

  function apiErrorWithStatus(status) {
    // `statusOf` in the hook reads `err.status`; `null` models the
    // transport-level throw that carries no HTTP status at all.
    const err = new Error("putPrefs failed");
    if (status !== null) err.status = status;
    return err;
  }

  /**
   * Render Customize with a live mgmt session, change Density, and let the
   * debounced PUT fail with `status`. Returns once `prefsSyncError` is set.
   */
  async function renderWithFailedWrite(status) {
    storage[TOKEN_KEY] = "tok";
    // Back to the default so clicking Compact is always a real change —
    // `setPref` short-circuits when the value is already current, and no
    // write means no failure to surface.
    delete storage[STORAGE_KEYS.density];
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    api.putPrefs.mockRejectedValue(apiErrorWithStatus(status));
    const utils = renderCustomize();
    // Real clock first: the hydrate round-trip has to settle before fake
    // timers take over for the debounce (react-component SKILL §5.2 case B).
    await waitFor(() => expect(api.getPrefs).toHaveBeenCalled());

    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Compact" }));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    vi.useRealTimers();
    return utils;
  }

  it("shows no sync indicator while every pref write is confirmed", async () => {
    storage[TOKEN_KEY] = "tok";
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    renderCustomize();
    await waitFor(() => expect(api.getPrefs).toHaveBeenCalled());

    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Compact" }));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    vi.useRealTimers();

    expect(api.putPrefs).toHaveBeenCalledWith({ density: "compact" });
    expect(screen.queryByTestId("prefs-sync-error")).toBeNull();
  });

  it("surfaces a transient write failure, naming the pref that didn't stick", async () => {
    await renderWithFailedWrite(503);

    const alert = screen.getByTestId("prefs-sync-error");
    expect(alert.getAttribute("data-variant")).toBe("retryable");
    expect(alert.textContent).toContain("Some settings haven't reached your account");
    // The affected key, in this page's own vocabulary — not `density`.
    expect(within(alert).getByText("Density")).toBeTruthy();
    expect(alert.textContent).toContain("Still saved on this device");
    expect(alert.textContent).toContain("keeps retrying in the background");
    // Re-auth is NOT what this state is waiting on.
    expect(within(alert).queryByRole("link", { name: "Sign in again" })).toBeNull();
  });

  it("a transport-level throw (no HTTP status) still surfaces as retryable", async () => {
    await renderWithFailedWrite(null);

    const alert = screen.getByTestId("prefs-sync-error");
    expect(alert.getAttribute("data-variant")).toBe("retryable");
    expect(within(alert).getByText("Density")).toBeTruthy();
  });

  it("does NOT roll the control back when the write fails", async () => {
    await renderWithFailedWrite(503);

    // #573 keeps the unconfirmed value on purpose. Reverting would take the
    // network's word over the user's and break offline pref changes.
    expect(screen.getByRole("button", { name: "Compact" }).className).toContain("on");
    expect(screen.getByRole("button", { name: "Cozy" }).className).not.toContain("on");
    expect(storage[STORAGE_KEYS.density]).toBe("compact");
  });

  it("renders the 401 as a re-authentication prompt, not a retry notice", async () => {
    await renderWithFailedWrite(401);

    const alert = screen.getByTestId("prefs-sync-error");
    expect(alert.getAttribute("data-variant")).toBe("unauthorized");
    expect(alert.textContent).toContain("Sign in again to save these settings");
    expect(alert.textContent).toContain("Your session expired");
    // The retryable copy must not leak into the 401 state — "we'll keep
    // trying" is exactly what a held-for-re-auth write is not doing.
    expect(alert.textContent).not.toContain("keeps retrying in the background");
    expect(alert.textContent).not.toContain("Some settings haven't reached your account");
    const signIn = within(alert).getByRole("link", { name: "Sign in again" });
    expect(signIn.getAttribute("href")).toBe("/app/login");
    // Still names the pref.
    expect(within(alert).getByText("Density")).toBeTruthy();
  });

  it("gives the two states different chrome, not just different words", async () => {
    await renderWithFailedWrite(503);
    const retryable = screen.getByTestId("prefs-sync-error");
    const retryableIcon = retryable.querySelector("[data-icon]");
    expect(retryableIcon.getAttribute("data-icon")).toBe("refresh");
    expect(retryableIcon.style.color).toBe("var(--accent)");
    expect(retryable.style.background).toBe("var(--raised)");
    expect(retryable.style.borderColor).toBe("var(--border)");

    cleanup();
    await renderWithFailedWrite(401);
    const unauthorized = screen.getByTestId("prefs-sync-error");
    const unauthorizedIcon = unauthorized.querySelector("[data-icon]");
    expect(unauthorizedIcon.getAttribute("data-icon")).toBe("shield");
    expect(unauthorizedIcon.style.color).toBe("var(--danger)");
    expect(unauthorized.style.background).toBe("var(--danger-soft)");
    expect(unauthorized.style.borderColor).toBe("var(--danger)");
  });

  it("names every affected pref when more than one write is outstanding", async () => {
    await renderWithFailedWrite(503);

    vi.useFakeTimers();
    // Second failing write, on a different pref.
    fireEvent.click(screen.getByRole("button", { name: "Max" }));
    await act(async () => {
      vi.advanceTimersByTime(PUT_DEBOUNCE);
    });
    vi.useRealTimers();

    const alert = screen.getByTestId("prefs-sync-error");
    expect(within(alert).getByText("Density, Reasoning effort")).toBeTruthy();
  });

  it("clears the indicator when Try again succeeds, re-sending the held value", async () => {
    await renderWithFailedWrite(503);
    api.putPrefs.mockReset();
    api.putPrefs.mockResolvedValue();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await waitFor(() => expect(screen.queryByTestId("prefs-sync-error")).toBeNull());
    // The value the server rejected is what got re-sent — #573 held it
    // rather than discarding it.
    expect(api.putPrefs).toHaveBeenCalledWith({ density: "compact" });
  });

  it("keeps the indicator up when Try again fails again", async () => {
    await renderWithFailedWrite(503);

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(api.putPrefs).toHaveBeenCalledTimes(2));

    const alert = screen.getByTestId("prefs-sync-error");
    expect(alert.getAttribute("data-variant")).toBe("retryable");
    expect(screen.getByRole("button", { name: "Try again" }).disabled).toBe(false);
  });

  it("Try again on the 401 notice re-sends too (the escape hatch after re-auth)", async () => {
    await renderWithFailedWrite(401);
    api.putPrefs.mockReset();
    api.putPrefs.mockResolvedValue();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await waitFor(() => expect(screen.queryByTestId("prefs-sync-error")).toBeNull());
    expect(api.putPrefs).toHaveBeenCalledWith({ density: "compact" });
  });

  it("disables Try again while the re-send is in flight", async () => {
    await renderWithFailedWrite(503);
    let settle;
    api.putPrefs.mockReset();
    api.putPrefs.mockReturnValue(new Promise((resolve) => { settle = resolve; }));

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    const pending = await screen.findByRole("button", { name: "Retrying…" });
    expect(pending.disabled).toBe(true);

    await act(async () => { settle(); });
    await waitFor(() => expect(screen.queryByTestId("prefs-sync-error")).toBeNull());
  });

  it("PrefsSyncNotice renders nothing when there is no error", () => {
    render(<PrefsSyncNotice error={null} onRetry={vi.fn()} />);
    expect(screen.queryByTestId("prefs-sync-error")).toBeNull();
  });

  it("PrefsSyncNotice falls back to the raw key for an unlabelled pref", () => {
    render(
      <MemoryRouter>
        <PrefsSyncNotice
          error={{ keys: ["someNewPref"], statuses: {}, unauthorized: false }}
          onRetry={vi.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("someNewPref")).toBeTruthy();
  });
});
