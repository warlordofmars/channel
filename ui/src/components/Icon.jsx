// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";

/**
 * 24×24 stroke icon set. `currentColor` stroke so icons inherit the text
 * colour of the surrounding context.
 *
 * Ported verbatim from ~/Downloads/design_handoff_channel/design-sources/app/icons.jsx.
 * Keep cases in sync with that file if the design adds/removes glyphs.
 */
export default function Icon({ name, size = 18, stroke = 1.6, style = {} }) {
  const p = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: stroke,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    style: { display: "block", ...style },
  };
  switch (name) {
    case "plus":         return <svg {...p}><path d="M12 5v14M5 12h14"/></svg>;
    case "chat":         return <svg {...p}><path d="M4 5h16v11H9l-4 3v-3H4z"/></svg>;
    case "cowork":       return <svg {...p}><path d="M4 7h10M4 12h16M4 17h7"/><circle cx="18" cy="7" r="1.4"/><circle cx="14" cy="17" r="1.4"/></svg>;
    case "code":         return <svg {...p}><path d="M9 8l-4 4 4 4M15 8l4 4-4 4"/></svg>;
    case "projects":     return <svg {...p}><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.2H19.5A1.5 1.5 0 0 1 21 9.7V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>;
    case "artifacts":    return <svg {...p}><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5" strokeWidth={stroke*0.85} opacity="0.6"/></svg>;
    case "customize":    return <svg {...p}><path d="M6 4v6M6 14v6M12 4v3M12 11v9M18 4v9M18 17v3"/><circle cx="6" cy="12" r="2"/><circle cx="12" cy="9" r="2"/><circle cx="18" cy="15" r="2"/></svg>;
    case "search":       return <svg {...p}><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/></svg>;
    case "sidebar":      return <svg {...p}><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M9.5 5v14"/></svg>;
    case "mic":          return <svg {...p}><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>;
    case "wave":         return <svg {...p}><path d="M4 12v0M8 8v8M12 5v14M16 8v8M20 12v0" strokeWidth={stroke+0.2}/></svg>;
    case "arrow-up":     return <svg {...p}><path d="M12 19V6M6 12l6-6 6 6"/></svg>;
    case "arrow-right":  return <svg {...p}><path d="M5 12h14M13 6l6 6-6 6"/></svg>;
    case "chevron-down": return <svg {...p}><path d="M6 9l6 6 6-6"/></svg>;
    case "chevron-right":return <svg {...p}><path d="M9 6l6 6-6 6"/></svg>;
    case "attach":       return <svg {...p}><path d="M19 11l-7.5 7.5a4 4 0 0 1-5.7-5.7L13 5.6a2.6 2.6 0 0 1 3.7 3.7l-7.3 7.3a1.2 1.2 0 0 1-1.8-1.7l6.8-6.8"/></svg>;
    case "image":        return <svg {...p}><rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="8.5" cy="10" r="1.6"/><path d="M21 16l-5-4-7 6"/></svg>;
    case "write":        return <svg {...p}><path d="M4 20h4L19 9a2 2 0 0 0-3-3L5 16z"/><path d="M14 7l3 3"/></svg>;
    case "learn":        return <svg {...p}><path d="M3 8l9-4 9 4-9 4z"/><path d="M7 10v5c0 1.2 2.2 2.5 5 2.5s5-1.3 5-2.5v-5"/></svg>;
    case "settings":     return <svg {...p}><circle cx="12" cy="12" r="3.2"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.5 5.5l1.8 1.8M16.7 16.7l1.8 1.8M18.5 5.5l-1.8 1.8M7.3 16.7l-1.8 1.8"/></svg>;
    case "sun":          return <svg {...p}><circle cx="12" cy="12" r="4"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.5 4.5l1.8 1.8M17.7 17.7l1.8 1.8M19.5 4.5l-1.8 1.8M6.3 17.7l-1.8 1.8"/></svg>;
    case "moon":         return <svg {...p}><path d="M20 14.5A8 8 0 0 1 9.5 4 7 7 0 1 0 20 14.5z"/></svg>;
    case "download":     return <svg {...p}><path d="M12 4v11M7 11l5 4 5-4M5 20h14"/></svg>;
    case "leaf":         return <svg {...p}><path d="M5 19c0-8 6-13 14-14 0 9-5 14-14 14z"/><path d="M5 19c3-4 6-6 9-7" strokeWidth={stroke*0.85} opacity="0.7"/></svg>;
    case "copy":         return <svg {...p}><rect x="8" y="8" width="12" height="12" rx="2.2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>;
    case "refresh":      return <svg {...p}><path d="M20 7a8 8 0 1 0 1.5 6"/><path d="M20 3v4.5h-4.5"/></svg>;
    case "thumb-up":     return <svg {...p}><path d="M7 11v8H4v-8z"/><path d="M7 11l4-7a2 2 0 0 1 2 2v3h5a2 2 0 0 1 2 2.3l-1 5A2 2 0 0 1 17 19H7"/></svg>;
    case "thumb-down":   return <svg {...p}><path d="M17 13V5h3v8z"/><path d="M17 13l-4 7a2 2 0 0 1-2-2v-3H6a2 2 0 0 1-2-2.3l1-5A2 2 0 0 1 7 5h10"/></svg>;
    case "close":        return <svg {...p}><path d="M6 6l12 12M18 6L6 18"/></svg>;
    case "check":        return <svg {...p}><path d="M5 12.5l4.5 4.5L19 6.5"/></svg>;
    case "file":         return <svg {...p}><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/></svg>;
    case "doc":          return <svg {...p}><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 13h6M9 16.5h4"/></svg>;
    case "globe":        return <svg {...p}><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18M12 3c-2.5 2.7-2.5 15.3 0 18"/></svg>;
    case "sparkle":      return <svg {...p}><path d="M12 3l1.8 6.2L20 11l-6.2 1.8L12 19l-1.8-6.2L4 11l6.2-1.8z"/></svg>;
    case "menu":         return <svg {...p}><path d="M4 7h16M4 12h16M4 17h16"/></svg>;
    case "pin":          return <svg {...p}><path d="M9 4h6l-1 5 3 3v2H7v-2l3-3z"/><path d="M12 14v6"/></svg>;
    case "dots":         return <svg {...p}><circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/></svg>;
    case "star":         return <svg {...p}><path d="M12 3.5l2.6 5.7 6.2.6-4.7 4.1 1.4 6.1L12 16.9 6.5 20l1.4-6.1L3.2 9.8l6.2-.6z"/></svg>;
    case "database":     return <svg {...p}><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></svg>;
    case "play":         return <svg {...p}><path d="M7 5l12 7-12 7z"/></svg>;
    case "expand":       return <svg {...p}><path d="M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5"/></svg>;
    default:             return null;
  }
}
