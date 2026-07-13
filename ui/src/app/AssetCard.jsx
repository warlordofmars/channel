// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Icon from "../components/Icon.jsx";

/**
 * Inline asset card rendered in the chat transcript (epic #321, #327).
 *
 * The card is icon + title + meta line (`kind · size`) — no thumbnail in
 * v1 (design decision Q4). Clicking it calls `onOpen(asset)`; the caller
 * (Conversation) navigates to the browse route so ArtifactPanel opens
 * with the asset descriptor. The card never fetches the payload itself —
 * bytes are pulled lazily by the viewer via the #325 content endpoint.
 *
 * Reuses the `.art-inline` token styles that used to back the dead
 * `t.artifact` slot; the element is a `<button>` so it's keyboard- and
 * screen-reader-accessible.
 */

// Asset.kind -> Icon.jsx glyph name. `kind` is one of
// code | document | data | image | diagram (SSE/REST contract); anything
// unexpected falls back to the generic file glyph.
const KIND_ICON = {
  code: "code",
  document: "doc",
  data: "database",
  image: "image",
  diagram: "artifacts",
};

const _KB = 1024;

/**
 * Human-readable byte size for the card's meta line. Returns "" for a
 * missing / non-numeric size so the meta line degrades to just the kind.
 * Small values keep one decimal (2.3 KB); values ≥ 10 in a unit round to
 * a whole number (12 KB) to stay compact.
 */
export function formatBytes(bytes) {
  if (typeof bytes !== "number" || Number.isNaN(bytes)) return "";
  if (bytes < _KB) return `${bytes} B`;
  const kb = bytes / _KB;
  if (kb < _KB) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  const mb = kb / _KB;
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

export default function AssetCard({ asset, onOpen }) {
  function handleOpen() {
    onOpen?.(asset);
  }

  const iconName = KIND_ICON[asset.kind] ?? "file";
  const size = formatBytes(asset.size_bytes);
  const meta = size ? `${asset.kind} · ${size}` : asset.kind;

  return (
    <button
      type="button"
      className="art-inline"
      onClick={handleOpen}
      title={asset.title}
    >
      <div className="ah">
        <span className="ic">
          <Icon name={iconName} size={18} />
        </span>
        <div className="at-wrap">
          <div className="at">{asset.title}</div>
          <div className="as">{meta}</div>
        </div>
        <span className="expand-ic">
          <Icon name="expand" size={16} />
        </span>
      </div>
    </button>
  );
}
