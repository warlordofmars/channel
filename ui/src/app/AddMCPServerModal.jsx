// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { registerMCPServer } from "../api.js";
import Modal from "../components/Modal.jsx";

/**
 * Register-server modal.
 *
 * Two credential paths (#375):
 *  - OAuth (Dynamic Client Registration) — the default. On successful
 *    register, opens the returned `auth_start_url` in a new tab; the user
 *    comes back to `/app/customize?mcp_authed=ok&server_id=…` via the
 *    Channel redirect target (`/auth/mcp/callback`).
 *  - Access token / PAT — the user pastes a pre-issued bearer token for a
 *    server that doesn't support DCR (e.g. GitHub's MCP server). The token
 *    is encrypted at rest and used directly; nothing is opened.
 *
 * Spike §Question 2 "OAuth scopes UX — pinned as open question":
 * v1 surfaces a generic disclosure on this page (the consent moment),
 * not per-scope detail. Real scope rendering is v2 once we see what
 * confuses users.
 */
export default function AddMCPServerModal({ open, onClose, onRegistered }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [authType, setAuthType] = useState("oauth_dcr");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Reset local state each time the modal opens. Modal.jsx returns
  // null when `open` is false, but AddMCPServerModal itself stays
  // mounted — without this reset, reopening the dialog would show
  // the previous name/url/token/error from the prior session.
  useEffect(() => {
    if (open) {
      setName("");
      setUrl("");
      setAuthType("oauth_dcr");
      setToken("");
      setBusy(false);
      setError("");
    }
  }, [open]);

  const isStatic = authType === "static_token";

  async function submit() {
    /* v8 ignore next -- defensive: the Add button is disabled whenever this guard would fire */
    if (busy || !name.trim() || !url.trim() || (isStatic && !token.trim())) return;
    setBusy(true);
    setError("");
    try {
      const out = await registerMCPServer({
        name: name.trim(),
        url: url.trim(),
        auth_type: authType,
        token: isStatic ? token : null,
      });
      // Static-token registrations return no auth_start_url — there is
      // no OAuth tab to open, the server is ready immediately.
      if (out.auth_start_url) {
        window.open(out.auth_start_url, "_blank", "noopener,noreferrer");
      }
      onRegistered?.(out.server_id);
      onClose?.();
    } catch (e) {
      console.error("registerMCPServer failed", e);
      if (e?.code === "dcr_unsupported") {
        // This server can't be auto-registered via DCR. Steer the user
        // into the static-token path added in #375 — flip the toggle,
        // reveal + focus the token input (autoFocus fires on mount), and
        // PRESERVE the name + url they already typed so they only need to
        // paste the token.
        setAuthType("static_token");
        setError(
          "This server doesn't support automatic registration. Add an access token (PAT) instead.",
        );
      } else {
        setError("Couldn't register that server. Check the URL and try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  // While registerMCPServer is in flight, suppress Modal's Escape /
  // backdrop dismissal — otherwise a late success would still fire
  // `window.open(...)` and `onRegistered(...)` after the user dismissed
  // the dialog. The Cancel button is also disabled below.
  const handleClose = busy ? () => {} : onClose;

  return (
    <Modal open={open} onClose={handleClose}>
      <div className="mcp-add-form">
        <h3 className="modal-h">Add MCP server</h3>
        <label className="mcp-field">
          <span>Name</span>
          <input
            aria-label="Name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Hive"
            autoFocus
          />
        </label>
        <label className="mcp-field">
          <span>Server URL</span>
          <input
            aria-label="Server URL"
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://hive.example.com/mcp"
          />
        </label>
        <div
          className="mcp-authtype"
          role="radiogroup"
          aria-label="Authentication type"
        >
          <label className="mcp-authtype-opt">
            <input
              type="radio"
              name="mcp-auth-type"
              value="oauth_dcr"
              checked={!isStatic}
              onChange={() => setAuthType("oauth_dcr")}
            />
            <span>OAuth (Dynamic Client Registration)</span>
          </label>
          <label className="mcp-authtype-opt">
            <input
              type="radio"
              name="mcp-auth-type"
              value="static_token"
              checked={isStatic}
              onChange={() => setAuthType("static_token")}
            />
            <span>Access token / PAT</span>
          </label>
        </div>
        {isStatic && (
          <label className="mcp-field">
            <span>Access token</span>
            <input
              aria-label="Access token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Paste a personal access token"
              autoComplete="off"
              autoFocus
            />
          </label>
        )}
        <p className="mcp-consent">
          {isStatic
            ? "The access token you paste is encrypted at rest in Channel's database and sent to this server as a bearer credential on each request. Nothing is opened — the server is ready to use immediately."
            : "We will register Channel with this server using OAuth Dynamic Client Registration, then open the server's authorization page in a new tab. You'll review the permissions the server requests before granting access. Tokens are encrypted at rest in Channel's database."}
        </p>
        {error && (
          <div className="mcp-err" role="alert">
            {error}
          </div>
        )}
        <div className="mcp-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={busy || !name.trim() || !url.trim() || (isStatic && !token.trim())}
            onClick={submit}
          >
            {busy ? "Registering…" : "Add server"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
