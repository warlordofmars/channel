// Copyright (c) 2026 John Carter. All rights reserved.

/**
 * Shared pure helpers for the asset views. `colorFor` / `inkFor` remain
 * the project-badge swatch pair (consumed by Projects.jsx +
 * ProjectDetail.jsx). `artIcon` / `kindLabel` / `formatBytes` /
 * `relativeTime` back the real Artifacts browse view + ArtifactPanel
 * (#328). All pure — no React, no DOM — so they live in a `.js` sibling.
 */

/** OKLCH light-band swatch (project card badge background). */
export function colorFor(h) {
  return `oklch(0.92 0.05 ${h})`;
}

/** Darker OKLCH ink for legible text on a `colorFor(h)` background. */
export function inkFor(h) {
  return `oklch(0.45 0.12 ${h})`;
}

/**
 * Map an asset `kind` (`code | document | data | image | diagram`) to the
 * Icon-set glyph used as its badge. `document` and any unknown kind fall
 * back to `doc`.
 */
export function artIcon(kind) {
  switch (kind) {
    case "code":
      return "code";
    case "data":
      return "database";
    case "image":
      return "image";
    case "diagram":
      return "customize";
    default:
      return "doc";
  }
}

/** Title-case a lowercase asset kind for display (`code` → `Code`). */
export function kindLabel(kind) {
  if (!kind) return "Asset";
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

const _BYTE_UNITS = ["KB", "MB", "GB", "TB"];

/**
 * Human-readable byte size. `< 1 KB` renders as bytes; larger sizes step
 * through KB/MB/GB/TB with one decimal below 10 of a unit. A null /
 * NaN input renders as an empty string so callers can drop it silently
 * from a meta line.
 */
export function formatBytes(bytes) {
  if (bytes == null || Number.isNaN(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  let val = bytes / 1024;
  let i = 0;
  while (val >= 1024 && i < _BYTE_UNITS.length - 1) {
    val /= 1024;
    i += 1;
  }
  return `${val.toFixed(val < 10 ? 1 : 0)} ${_BYTE_UNITS[i]}`;
}

/**
 * Relative time from an ISO timestamp (`created_at`). `now` is injected
 * for deterministic tests; production callers let it default to
 * `Date.now()`. An unparseable timestamp renders as an empty string.
 */
export function relativeTime(iso, now = Date.now()) {
  // `new Date(null)` is the epoch (not Invalid Date), so a falsy input
  // would otherwise render "56y ago" — guard it to the empty string so
  // callers can drop a missing timestamp from a meta line.
  if (!iso) return "";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return "";
  const sec = Math.max(0, Math.floor((now - ts) / 1000));
  if (sec < 45) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  const wk = Math.floor(day / 7);
  if (wk < 5) return `${wk}w ago`;
  // Switch to years strictly at 365 days — keying the boundary off the
  // month count instead let days 360–364 (month 12) fall through to a
  // bogus "0y ago".
  if (day < 365) return `${Math.floor(day / 30)}mo ago`;
  return `${Math.floor(day / 365)}y ago`;
}
