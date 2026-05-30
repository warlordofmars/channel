// Copyright (c) 2026 John Carter. All rights reserved.

/**
 * Shared utilities for the Phase 6e views. Ported verbatim from
 * design-sources/app/views.jsx (top of file): `colorFor`, `inkFor`, and
 * `artIcon`. Pure helpers — no React, no DOM — so they live in a `.js`
 * sibling instead of a `.jsx` component.
 */

/** OKLCH light-band swatch (project card badge background). */
export function colorFor(h) {
  return `oklch(0.92 0.05 ${h})`;
}

/** Darker OKLCH ink for legible text on a `colorFor(h)` background. */
export function inkFor(h) {
  return `oklch(0.45 0.12 ${h})`;
}

/** Map an artifact kind to the Icon-set name used as its badge glyph. */
export function artIcon(kind) {
  return kind === "Code" ? "code"
       : kind === "Chart" ? "customize"
       : kind === "Interactive" ? "sparkle"
       : kind === "Data" ? "database"
       : "doc";
}
