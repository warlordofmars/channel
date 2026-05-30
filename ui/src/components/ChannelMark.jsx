// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";

/**
 * Channel brand mark: rounded square (--accent fill) with two vertical bars
 * (--on-accent fill) framing a central gap — a literal "channel".
 *
 * Ported verbatim from ~/Downloads/design_handoff_channel/design-sources/app/icons.jsx
 * (the `ChannelMark` export). The .ch-mark span wrapper is styled by
 * channel.css for inline-flex alignment; do not drop it.
 */
export default function ChannelMark({ size = 22, color = "var(--accent)" }) {
  const r = size * 0.26;
  return (
    <span className="ch-mark" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 24 24">
        <rect x="0.5" y="0.5" width="23" height="23" rx={r} fill={color} />
        <rect x="6.6" y="6" width="3.1" height="12" rx="1.2" fill="var(--on-accent)" />
        <rect x="14.3" y="6" width="3.1" height="12" rx="1.2" fill="var(--on-accent)" />
      </svg>
    </span>
  );
}
