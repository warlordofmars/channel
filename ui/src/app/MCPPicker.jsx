// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useMemo, useState } from "react";
import Icon from "../components/Icon.jsx";

/**
 * Composer popover for picking MCP servers active in this chat.
 *
 * Mirrors ModelPicker / AttachMenu's backdrop+pop pattern (CLAUDE.md
 * §"Chat-app popovers"). The caller owns the persistence — this
 * component is dumb about the underlying API.
 *
 * Props:
 *   - servers: [{ server_id, name, tool_prefix, globally_enabled,
 *       auth_status }] — full registered set (from /api/mcp/servers)
 *   - mode: "inherit" | "explicit"
 *   - explicitIds: string[] — only used when mode === "explicit"
 *   - onChange({ mode, explicit_server_ids }) — caller persists via
 *     putChatMCPSettings
 */
export default function MCPPicker({ servers, mode, explicitIds, onChange }) {
  const [open, setOpen] = useState(false);

  const activeIds = useMemo(() => {
    if (!servers || servers.length === 0) return [];
    if (mode === "explicit") {
      return explicitIds.filter((id) => servers.some((s) => s.server_id === id));
    }
    return servers.filter((s) => s.globally_enabled).map((s) => s.server_id);
  }, [servers, mode, explicitIds]);

  // The pill label counts only servers that will actually fire — a
  // selected server in `expired` / `revoked` / `never_authed` state
  // can't carry tool calls until the user reconnects. Counting it
  // would mislead "N tool servers" past its real capacity.
  const usableActiveCount = useMemo(() => {
    if (!servers || servers.length === 0) return 0;
    return activeIds.filter((id) =>
      servers.some((s) => s.server_id === id && s.auth_status === "active"),
    ).length;
  }, [servers, activeIds]);

  function toggle(serverId) {
    const next = activeIds.includes(serverId)
      ? activeIds.filter((id) => id !== serverId)
      : [...activeIds, serverId];
    onChange({ mode: "explicit", explicit_server_ids: next });
  }

  function resetToInherit() {
    onChange({ mode: "inherit", explicit_server_ids: [] });
  }

  function closePopover() {
    setOpen(false);
  }

  function togglePopover() {
    setOpen((o) => !o);
  }

  // Escape closes the popover for real keyboard users. The backdrop's
  // ``tabIndex={-1}`` means it never receives focus, so its own
  // ``onKeyDown`` only fires from tests that dispatch the event
  // directly. Window-level listener matches the pattern in
  // ChatRowMenu / Modal.
  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === "Escape") closePopover();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const label = `${usableActiveCount} tool ${usableActiveCount === 1 ? "server" : "servers"}`;

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        className="mcp-pill"
        onClick={togglePopover}
        aria-label={label}
      >
        <Icon name="plug" size={14} />
        <span>{label}</span>
        <Icon name="chevron-down" size={14} />
      </button>
      {open && (
        <>
          <div
            className="backdrop"
            onClick={closePopover}
            aria-hidden="true"
          />
          <div className="pop" style={{ bottom: "calc(100% + 8px)", right: 0 }}>
            <div className="pop-h">MCP servers for this chat</div>
            {(!servers || servers.length === 0) && (
              <div className="opt" data-testid="mcp-empty">
                <div style={{ flex: 1 }}>
                  <div className="ds">
                    No servers registered. Add one from Customize.
                  </div>
                </div>
              </div>
            )}
            {servers && servers.map((s) => {
              const checked = activeIds.includes(s.server_id);
              // Inactive servers (expired/revoked/never_authed) can't
              // be SELECTED, but if they're already in the active list
              // the user MUST be able to UNCHECK them — otherwise
              // explicit mode traps the selection and the only escape
              // is "Reset to global defaults".
              const inactive = s.auth_status !== "active";
              const disabled = inactive && !checked;
              return (
                <label
                  key={s.server_id}
                  className={"opt" + (disabled ? " disabled" : "")}
                  aria-label={s.name}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={() => toggle(s.server_id)}
                  />
                  <div style={{ flex: 1 }}>
                    <div className="nm">{s.name}</div>
                    <div className="ds">
                      {inactive
                        ? "Needs reconnect"
                        : `Tools available as ${s.tool_prefix}_*`}
                    </div>
                  </div>
                </label>
              );
            })}
            {mode === "explicit" && (
              <div className="pop-h" style={{ marginTop: 4 }}>
                <button
                  type="button"
                  className="ck"
                  onClick={resetToInherit}
                >
                  Reset to global defaults
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
