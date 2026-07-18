// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Icon from "../components/Icon.jsx";
import AssetCard from "./AssetCard.jsx";
import { useAssetContent } from "../hooks/useAssetContent.js";

/**
 * Inline image render for the chat transcript (#360 slice A, #361).
 *
 * An `kind=image` asset that clears the inline threshold renders as a
 * responsive `<img>` in the flow instead of an `AssetCard`. Because asset
 * content is Bearer-authenticated (no presigned URLs), the bytes are
 * fetched via the shared `useAssetContent` hook (blob → object URL, revoked
 * on unmount) rather than a plain `src`.
 *
 * Fallback to the card in two cases, so a transcript never shows a broken
 * image:
 *   - the descriptor doesn't clear `isInlineImage` (kind / mime / size) —
 *     Conversation renders `AssetCard` directly, never mounting this
 *     component; and
 *   - the content fetch fails (404 gone / 502) — this component swaps
 *     itself for `AssetCard` on the `error` status.
 *
 * Clicking the image opens the full-fidelity `ArtifactPanel` via the same
 * `onOpen(asset)` URL seam the card uses (`/app/artifacts?artifact=<id>`),
 * so the capped inline render is a preview and the panel remains the
 * source of full resolution + Copy/Download.
 */

// Raster MIME types we render inline. SVG/TIFF/BMP and any non-image MIME
// fall through to the card (design decision Q2). Kept module-level so
// `isInlineImage` stays a pure, importable predicate.
const INLINE_IMAGE_MIMES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);

// 5 MB byte cap. The descriptor carries no pixel dimensions (only mime +
// size_bytes), so the inline/card decision keys off bytes; CSS caps the
// on-screen size so even a large-dimension image never dumps full-res into
// the flow.
const MAX_INLINE_IMAGE_BYTES = 5 * 1024 * 1024;

/**
 * Whether an asset descriptor should render inline as an `<img>` (vs. a
 * card). True only for a raster image within the byte cap. Pure — the
 * fetch-success half of the design's inline condition is enforced by the
 * component's `error` → card fallback, not here.
 */
export function isInlineImage(asset) {
  return (
    asset != null &&
    asset.kind === "image" &&
    INLINE_IMAGE_MIMES.has(asset.mime) &&
    typeof asset.size_bytes === "number" &&
    asset.size_bytes <= MAX_INLINE_IMAGE_BYTES
  );
}

export default function InlineImage({ asset, onOpen }) {
  const { status, url } = useAssetContent(asset.chat_id, asset.asset_id, {
    mode: "blob",
  });

  function handleOpen() {
    onOpen?.(asset);
  }

  // Content fetch failed (or the descriptor lacked an id the hook needs) —
  // degrade to the card rather than render a broken <img>.
  if (status === "error" || status === "idle") {
    return <AssetCard asset={asset} onOpen={onOpen} />;
  }

  // Hold a placeholder while the blob resolves so the transcript doesn't
  // reflow when the image lands.
  if (status !== "ready") {
    return <div className="convo-img-loading" aria-hidden="true" />;
  }

  return (
    <button
      type="button"
      className="convo-img"
      onClick={handleOpen}
      title={asset.title}
    >
      <img src={url} alt={asset.title} loading="lazy" decoding="async" />
      <span className="convo-img-expand" aria-hidden="true">
        <Icon name="expand" size={16} />
      </span>
    </button>
  );
}
