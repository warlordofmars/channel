// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { enableFeaturedServer, getFeaturedServers } from "../api.js";
import Icon from "../components/Icon.jsx";

/**
 * "Featured integrations" list for the Customize view (#405).
 *
 * Fetches Channel's curated MCP catalog (`GET /api/mcp/featured`, backend
 * #277) and renders a one-click Enable affordance per entry. For a
 * `static_token` server (GitHub) the Enable button reveals an inline PAT
 * field with a "Create a token" link to the entry's `docs_url`; on submit
 * we POST `{ featured_id, token }` so the backend pins the canonical url,
 * tool prefix, credential type, and default global-enablement server-side.
 * A non-`static_token` entry enables directly (the backend returns an
 * `auth_start_url` to open, matching the OAuth register path).
 *
 * Dedupe: a featured entry whose canonical url already appears in the
 * caller's registered-server list shows "Enabled" instead of an Enable
 * button, and — for a read-write surface (`default_globally_enabled` is
 * false) — a one-line note that write tools stay off until enabled
 * per-chat.
 *
 * Secret handling reuses the #375 discipline: the pasted PAT is never
 * logged (only `String(e)` on failure) and `enableFeaturedServer` never
 * parses the error body.
 */
export default function FeaturedMCPServers({ registeredUrls = [], onEnabled }) {
  const [catalog, setCatalog] = useState(null);
  const [loadError, setLoadError] = useState(false);
  // featured_id whose inline PAT field is currently revealed (or null).
  const [activeId, setActiveId] = useState(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(function fetchFeaturedOnMount() {
    let cancelled = false;
    getFeaturedServers()
      .then(function onFeaturedLoaded({ servers }) {
        if (!cancelled) setCatalog(servers);
      })
      .catch(function onFeaturedError() {
        if (!cancelled) setLoadError(true);
      });
    return function cleanup() {
      cancelled = true;
    };
  }, []);

  function isEnabled(entry) {
    return registeredUrls.includes(entry.url);
  }

  // Enable click. A static_token server reveals the PAT field; anything
  // else (a future OAuth-DCR featured entry) enables directly.
  function startEnable(entry) {
    setError("");
    setToken("");
    if (entry.auth_type === "static_token") {
      setActiveId(entry.featured_id);
    } else {
      submitEnable(entry);
    }
  }

  function cancelEnable() {
    // Drop any pasted token when backing out — no stale secret in state.
    setActiveId(null);
    setToken("");
    setError("");
  }

  async function submitEnable(entry) {
    if (busy) return;
    setBusy(true);
    setError("");
    const isStatic = entry.auth_type === "static_token";
    try {
      const out = await enableFeaturedServer({
        featured_id: entry.featured_id,
        name: entry.name,
        url: entry.url,
        // Trim a pasted PAT's trailing whitespace; null for non-static.
        token: isStatic ? token.trim() : null,
      });
      // A non-static registration returns an OAuth authorization URL to
      // open; static-token registrations are ready immediately.
      if (out.auth_start_url) {
        window.open(out.auth_start_url, "_blank", "noopener,noreferrer");
      }
      setActiveId(null);
      setToken("");
      onEnabled?.();
    } catch (e) {
      // Log only String(e) — never the error object, whose body could
      // otherwise surface the pasted PAT in the console (#375).
      console.error("enableFeaturedServer failed", String(e));
      setError(`Couldn't enable ${entry.name}. Check the token and try again.`);
    } finally {
      setBusy(false);
    }
  }

  function onTokenChange(e) {
    setToken(e.target.value);
  }

  if (loadError) {
    return (
      <div className="mcp-featured">
        <h4 className="mcp-featured-title">Featured integrations</h4>
        <div className="hint" role="alert">
          Couldn't load featured integrations.
        </div>
      </div>
    );
  }

  // Nothing to show while loading or when the catalog is empty.
  if (catalog == null || catalog.length === 0) return null;

  return (
    <div className="mcp-featured">
      <h4 className="mcp-featured-title">Featured integrations</h4>
      <p className="hint">
        Connect a curated MCP server in one click. Channel pins its canonical
        URL and safe defaults for you.
      </p>
      <ul className="mcp-featured-list">
        {catalog.map((entry) => {
          const enabled = isEnabled(entry);
          const open = activeId === entry.featured_id && !enabled;
          return (
            <li key={entry.featured_id} className="mcp-featured-card">
              <div className="mcp-featured-head">
                <div className="mcp-featured-name">
                  <Icon name="star" size={15} aria-hidden="true" />
                  {entry.name}
                </div>
                {enabled ? (
                  <span className="mcp-featured-enabled">
                    <Icon name="check" size={14} aria-hidden="true" />
                    Enabled
                  </span>
                ) : (
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => startEnable(entry)}
                  >
                    Enable
                  </button>
                )}
              </div>
              <p className="mcp-featured-desc">{entry.description}</p>
              {enabled && !entry.default_globally_enabled && (
                <p className="mcp-featured-note">
                  Write tools stay off until you enable this server per-chat.
                </p>
              )}
              {open && (
                <div className="mcp-featured-enable">
                  <label className="mcp-field">
                    <span>Access token</span>
                    <input
                      aria-label={`${entry.name} access token`}
                      type="password"
                      value={token}
                      onChange={onTokenChange}
                      placeholder="Paste a personal access token"
                      autoComplete="off"
                      autoFocus
                    />
                  </label>
                  <a
                    className="mcp-featured-tokenlink"
                    href={entry.docs_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Create a token
                    <Icon name="arrow-right" size={13} aria-hidden="true" />
                  </a>
                  {error && (
                    <div className="mcp-err" role="alert">
                      {error}
                    </div>
                  )}
                  <div className="mcp-actions">
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={cancelEnable}
                      disabled={busy}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={() => submitEnable(entry)}
                      disabled={busy || !token.trim()}
                    >
                      {busy ? "Enabling…" : "Enable"}
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
