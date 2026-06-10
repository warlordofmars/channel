// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { registerMCPServer } from "../api.js";
import Modal from "../components/Modal.jsx";

/**
 * Register-server modal.
 *
 * Spike §Question 2 "OAuth scopes UX — pinned as open question":
 * v1 surfaces a generic disclosure on this page (the consent moment),
 * not per-scope detail. Real scope rendering is v2 once we see what
 * confuses users.
 *
 * On successful register, opens the returned `auth_start_url` in a new
 * tab. The new tab carries the OAuth flow; the user comes back to
 * `/app/customize?mcp_authed=ok&server_id=…` via the Channel
 * redirect target (`/auth/mcp/callback`). The Customize view watches
 * for that query param and refreshes the server list.
 */
export default function AddMCPServerModal({ open, onClose, onRegistered }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Reset local state each time the modal opens. Modal.jsx returns
  // null when `open` is false, but AddMCPServerModal itself stays
  // mounted — without this reset, reopening the dialog would show
  // the previous name/url/error from the prior session.
  useEffect(() => {
    if (open) {
      setName("");
      setUrl("");
      setBusy(false);
      setError("");
    }
  }, [open]);

  async function submit() {
    /* v8 ignore next -- defensive: button is also disabled in this state */
    if (busy || !name.trim() || !url.trim()) return;
    setBusy(true);
    setError("");
    try {
      const out = await registerMCPServer({ name: name.trim(), url: url.trim() });
      window.open(out.auth_start_url, "_blank", "noopener,noreferrer");
      onRegistered?.(out.server_id);
      onClose?.();
    } catch (e) {
      console.error("registerMCPServer failed", e);
      setError("Couldn't register that server. Check the URL and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose}>
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
        <p className="mcp-consent">
          We will register Channel with this server using OAuth Dynamic
          Client Registration, then open the server's authorization page
          in a new tab. You'll review the permissions the server requests
          before granting access. Tokens are encrypted at rest in
          Channel's database.
        </p>
        {error && (
          <div className="mcp-err" role="alert">
            {error}
          </div>
        )}
        <div className="mcp-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={busy || !name.trim() || !url.trim()}
            onClick={submit}
          >
            {busy ? "Registering…" : "Add server"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
