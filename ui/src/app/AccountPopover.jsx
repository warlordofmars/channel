// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Icon from "../components/Icon.jsx";

/**
 * The popover that opens above the sidebar account row. Shows the
 * authenticated user's display name + email and a Sign out option.
 * Translated from the popover JSX inside design-sources/app/shell.jsx
 * `Sidebar`. Caller controls open/close.
 */
export default function AccountPopover({ userName, email, onSignOut }) {
  return (
    <div className="pop" style={{ bottom: "calc(100% + 6px)", left: 0, right: 0, minWidth: 0 }}>
      <div style={{ padding: "8px 10px 6px" }}>
        <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>{userName}</div>
        <div className="mono" style={{ fontSize: 11.5, color: "var(--ink-faint)", marginTop: 1 }}>{email}</div>
      </div>
      <div style={{ height: 1, background: "var(--border-soft)", margin: "4px 0" }} />
      <div className="opt" onClick={onSignOut}>
        <span style={{ color: "var(--ink-soft)" }}><Icon name="arrow-right" size={16} /></span>
        <span className="nm" style={{ fontWeight: 500 }}>Sign out</span>
      </div>
    </div>
  );
}
