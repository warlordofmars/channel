// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Icon from "../components/Icon.jsx";
import AssetCard from "./AssetCard.jsx";
import { useAssetContent } from "../hooks/useAssetContent.js";
import { parseCsv } from "./views/ArtifactPanel.jsx";

/**
 * Inline data (CSV) preview for the chat transcript (#360 slice C, #363).
 *
 * A `kind=data` asset whose payload is text/CSV renders as a capped inline
 * `.art-table` in the flow instead of an `AssetCard`. Content is
 * Bearer-authenticated (no presigned URLs), so the CSV text is fetched via
 * the shared `useAssetContent` hook in `"text"` mode (#361) — the same hook
 * the inline image slice introduced — rather than a plain fetch.
 *
 * The preview is capped at 10 data rows × 8 columns; the full-fidelity view
 * lives in `ArtifactPanel`, reached via an explicit "Open full table"
 * affordance (the table itself stays selectable/scrollable, so the button —
 * not the whole element — is the panel click target, per design Q4). When
 * the CSV exceeds the cap the affordance is prefixed with the true
 * `N rows × M cols` so the reader knows there's more.
 *
 * Fallback to the card in two cases so a transcript never shows a broken
 * preview:
 *   - the descriptor doesn't clear `isInlineData` (kind / mime) —
 *     Conversation renders `AssetCard` directly, never mounting this
 *     component; and
 *   - the content fetch fails, or the payload isn't parseable as CSV —
 *     this component swaps itself for `AssetCard`.
 */

// Preview caps (design Q1 / #360 decision table). "Rows" counts data
// (body) rows, excluding the header. v1 starting values — tune under live
// verification.
const MAX_INLINE_ROWS = 10;
const MAX_INLINE_COLS = 8;

// Byte ceiling on inline-fetching a data payload. Mirrors InlineImage's
// `MAX_INLINE_IMAGE_BYTES` gate: the descriptor's `size_bytes` bounds the
// worst-case transcript fetch so a pathologically large CSV is NOT pulled
// into memory + parsed just to show a 10×8 preview. Oversized data cards
// and streams on demand in the panel instead. Generous enough (5 MB) that
// every realistic inline CSV — the ≤100 KB inline-stored ones and the
// low-MB S3-backed exports — still previews inline.
const MAX_INLINE_DATA_BYTES = 5 * 1024 * 1024;

// CSV-ish text MIMEs we parse into an inline table. An explicit allowlist
// rather than a `text/*` prefix test: a `kind=data` asset carrying
// `text/markdown` / `text/html` is NOT tabular and would render a garbage
// one-column table, so those (and the binary xlsx `application/…sheet`) card
// instead. `text/csv` is the canonical CSV mime; `text/plain` is a tolerant
// fallback for a producer that emits CSV without the precise type. Kept
// module-level so `isInlineData` stays a pure, importable predicate.
const INLINE_DATA_MIMES = new Set(["text/csv", "text/plain"]);

/**
 * Whether an asset descriptor should render inline as a CSV table (vs. a
 * card). True only for a `kind=data` asset carrying a CSV-ish text MIME
 * within the byte cap. ORIGIN-AGNOSTIC by design (#363): an uploaded CSV and
 * a generated CSV render identically; the predicate never inspects `origin`.
 * Binary spreadsheet uploads (xlsx — `application/…sheet`), non-CSV text
 * types, any missing-MIME data asset, and oversized payloads fall through to
 * the card, which is the correct fallback for a payload `parseCsv` can't
 * sensibly render inline. Pure — the fetch-success / parseable half of the
 * design's inline condition is enforced by the component's card fallback,
 * not here.
 */
export function isInlineData(asset) {
  return (
    asset != null &&
    asset.kind === "data" &&
    INLINE_DATA_MIMES.has(asset.mime) &&
    typeof asset.size_bytes === "number" &&
    asset.size_bytes <= MAX_INLINE_DATA_BYTES
  );
}

export default function InlineData({ asset, onOpen }) {
  const { status, text } = useAssetContent(asset.chat_id, asset.asset_id, {
    mode: "text",
  });

  function handleOpen() {
    onOpen?.(asset);
  }

  // Content fetch failed (or the descriptor lacked an id the hook needs) —
  // degrade to the card rather than render an empty table.
  if (status === "error" || status === "idle") {
    return <AssetCard asset={asset} onOpen={onOpen} />;
  }

  // Hold a placeholder while the text resolves so the transcript doesn't
  // reflow when the table lands.
  if (status !== "ready") {
    return <div className="convo-data-loading" aria-hidden="true" />;
  }

  const rows = parseCsv(text || "");
  // Empty / non-parseable payload → card (design: payload isn't parseable
  // CSV → AssetCard). `parseCsv` never throws; an empty matrix is the
  // "nothing tabular here" signal.
  if (rows.length === 0) {
    return <AssetCard asset={asset} onOpen={onOpen} />;
  }

  const totalCols = rows.reduce((max, row) => Math.max(max, row.length), 0);
  const [header, ...body] = rows;
  const truncated = body.length > MAX_INLINE_ROWS || totalCols > MAX_INLINE_COLS;

  const shownHeader = header.slice(0, MAX_INLINE_COLS);
  const shownBody = body
    .slice(0, MAX_INLINE_ROWS)
    .map((row) => row.slice(0, MAX_INLINE_COLS));

  return (
    <div className="convo-data">
      <div className="convo-data-scroll">
        <table className="art-table convo-data-table">
          <thead>
            <tr>
              {shownHeader.map((cell, i) => (
                <th key={i}>{cell}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shownBody.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j} className={j > 0 ? "mono" : ""}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button
        type="button"
        className="convo-open-full"
        onClick={handleOpen}
        title="Open full table"
      >
        {truncated && (
          <span className="convo-open-full-meta mono">
            {body.length} rows × {totalCols} cols
          </span>
        )}
        <span className="convo-open-full-cta">
          <Icon name="expand" size={13} />
          Open full table
        </span>
      </button>
    </div>
  );
}
