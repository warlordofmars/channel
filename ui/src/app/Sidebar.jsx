// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { parseToken, TOKEN_KEY } from "../lib/auth.js";
import Icon from "../components/Icon.jsx";
import AccountPopover from "./AccountPopover.jsx";
import { RECENTS } from "./data.js";

/**
 * 264px left nav. Primary items (New chat / Projects / Artifacts / Customize)
 * + a Search input that filters RECENTS + time-grouped Recents + an account
 * row at the bottom.
 *
 * Translated from design-sources/app/shell.jsx `Sidebar` function. Per
 * spec deferrals: desktop traffic-lights branch dropped (web only),
 * "Relaunch to update" pill dropped (Electron only), "Channel Max" plan
 * label dropped (billing deferred). User name + email derive from the
 * mgmt JWT.
 *
 * Sign out clears the JWT and sends the user back to the marketing site at /.
 */
export default function Sidebar({ collapsed = false, onToggle = () => {} }) {
  const navigate = useNavigate();
  const [searching, setSearching] = useState(false);
  const [search, setSearch] = useState("");
  const [acctOpen, setAcctOpen] = useState(false);

  const token = localStorage.getItem(TOKEN_KEY) ?? "";
  const claims = parseToken(token) ?? {};
  const email = claims.email ?? "you@example.com";
  // Prefer Google's full display_name; fall back to the email's
  // local-part if absent (older tokens, test stubs, etc.).
  const userName =
    (claims.display_name && claims.display_name.trim()) ||
    email.split("@")[0] ||
    "You";
  // "John Carter" → "JC"; single-word name → first 2 letters.
  const initials = (() => {
    const words = userName.trim().split(/\s+/).filter(Boolean);
    if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
    return userName.slice(0, 2).toUpperCase();
  })();

  const filtered = RECENTS.filter((r) =>
    r.title.toLowerCase().includes(search.toLowerCase())
  );
  const groups = [];
  filtered.forEach((r) => {
    let g = groups.find((x) => x.name === r.group);
    if (!g) { g = { name: r.group, items: [] }; groups.push(g); }
    g.items.push(r);
  });

  function signOut() {
    localStorage.removeItem(TOKEN_KEY);
    globalThis.location.assign("/");
  }

  return (
    <div className={"sb" + (collapsed ? " collapsed" : "")}>
      <div className="sb-top">
        <div className="sb-actions">
          <button type="button" className="icon-btn" title="Toggle sidebar" onClick={onToggle}><Icon name="sidebar" size={18} /></button>
          <button type="button" className="icon-btn" title="Search" onClick={() => setSearching((s) => !s)}>
            <Icon name="search" size={18} />
          </button>
        </div>
      </div>

      <div className="sb-scroll">
        {searching && (
          <div style={{ padding: "0 2px 8px" }}>
            <div className="composer" style={{ padding: "7px 10px", boxShadow: "none" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--ink-faint)" }}>
                <Icon name="search" size={16} />
                <input
                  autoFocus
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search chats"
                  style={{
                    border: "none",
                    outline: "none",
                    background: "transparent",
                    color: "var(--ink)",
                    fontFamily: "var(--font-sans)",
                    fontSize: 13.5,
                    width: "100%",
                  }}
                />
              </div>
            </div>
          </div>
        )}

        <button type="button" className="nav-item primary">
          <span className="ic"><Icon name="plus" size={18} /></span> New chat
        </button>
        <button type="button" className="nav-item">
          <span className="ic"><Icon name="projects" size={18} /></span> Projects
        </button>
        <button type="button" className="nav-item">
          <span className="ic"><Icon name="artifacts" size={18} /></span> Artifacts
        </button>
        <button type="button" className="nav-item">
          <span className="ic"><Icon name="customize" size={18} /></span> Customize
        </button>

        {groups.map((g) => (
          <div key={g.name}>
            <div className="sb-section">{g.name}</div>
            {g.items.map((r) => (
              <button
                type="button"
                key={r.id}
                className="recent"
                onClick={() => navigate(`/app/c/${r.id}`)}
              >
                {r.title}
              </button>
            ))}
          </div>
        ))}
        {filtered.length === 0 && (
          <div style={{ padding: "20px 10px", fontSize: 13, color: "var(--ink-faint)" }}>
            No chats match &ldquo;{search}&rdquo;.
          </div>
        )}
      </div>

      <div className="sb-foot">
        <div className="account-wrap">
          {acctOpen && (
            <>
              <div className="backdrop" onClick={() => setAcctOpen(false)} />
              <AccountPopover userName={userName} email={email} onSignOut={signOut} />
            </>
          )}
          <button type="button" className="account" onClick={() => setAcctOpen((o) => !o)}>
            <span className="avatar">{initials}</span>
            <div style={{ flex: 1, textAlign: "left" }}>
              <div className="nm">{userName}</div>
              <div className="pl" style={{ fontSize: 11.5, color: "var(--ink-faint)" }}>{email}</div>
            </div>
            <Icon name="chevron-down" size={15} style={{ color: "var(--ink-faint)" }} />
          </button>
        </div>
      </div>
    </div>
  );
}
