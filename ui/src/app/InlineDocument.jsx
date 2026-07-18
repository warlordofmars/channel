// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Icon from "../components/Icon.jsx";
import AssetCard from "./AssetCard.jsx";
import { useAssetContent } from "../hooks/useAssetContent.js";
import { renderMarkdown } from "./renderMarkdown.jsx";

/**
 * Inline document (markdown) preview for the chat transcript (#360 slice C,
 * #363).
 *
 * A `kind=document` asset whose payload is text/markdown renders as a capped
 * inline markdown preview in the flow instead of an `AssetCard`. Content is
 * Bearer-authenticated (no presigned URLs), so the text is fetched via the
 * shared `useAssetContent` hook in `"text"` mode (#361) — the same hook the
 * inline image slice introduced — rather than a plain fetch.
 *
 * The preview is capped at ~40 rendered lines / ~2 KB with a fade; the
 * full-fidelity view lives in `ArtifactPanel`, reached via an explicit
 * "Open full document" affordance (the preview itself stays
 * selectable/scrollable, so the button — not the whole element — is the
 * panel click target, per design Q4).
 *
 * Fallback to the card in two cases so a transcript never shows a broken
 * preview:
 *   - the descriptor doesn't clear `isInlineDocument` (kind / mime) —
 *     Conversation renders `AssetCard` directly, never mounting this
 *     component. In particular a PDF (`kind=document`, `application/pdf`)
 *     cards, since it has no inline markdown path (#363 scope); and
 *   - the content fetch fails — this component swaps itself for `AssetCard`.
 */

// Preview caps (design Q1 / #360 decision table). Character count is used
// as a ~2 KB proxy (≈1 char/byte for the ASCII-dominant markdown we cap).
// v1 starting values — tune under live verification.
const MAX_INLINE_LINES = 40;
const MAX_INLINE_CHARS = 2048;

// Byte ceiling on inline-fetching a document payload. Mirrors InlineImage's
// `MAX_INLINE_IMAGE_BYTES` gate: `size_bytes` bounds the worst-case
// transcript fetch so a pathologically large markdown/text doc is NOT pulled
// into memory just to show a ~40-line preview. Oversized docs card and
// stream on demand in the panel instead. Generous enough (5 MB) that every
// realistic inline document — the ≤100 KB inline-stored ones and the low-MB
// S3-backed ones the design contemplates rendering inline — still previews.
const MAX_INLINE_DOCUMENT_BYTES = 5 * 1024 * 1024;

// Text MIMEs `renderMarkdown` can sensibly render inline. An explicit
// allowlist rather than a `text/*` prefix test: `text/html` (and other text
// types) get dropped/mishandled by react-markdown and would show a blank
// preview beneath the affordance, so they card instead. `text/markdown` is
// the canonical doc mime; `text/plain` is rendered as prose. A PDF
// (`application/pdf`) is `kind=document` but not text — no inline markdown
// path in #363's scope, so it cards. Module-level so `isInlineDocument`
// stays a pure, importable predicate.
const INLINE_DOCUMENT_MIMES = new Set(["text/markdown", "text/plain"]);

/**
 * Whether an asset descriptor should render inline as a markdown preview
 * (vs. a card). True only for a `kind=document` asset carrying a markdown /
 * plain-text MIME within the byte cap. ORIGIN-AGNOSTIC by design (#363): an
 * uploaded markdown file and a generated one render identically; the
 * predicate never inspects `origin`. A PDF (`application/pdf`), non-markdown
 * text types (`text/html`), any missing-MIME document, and oversized
 * payloads fall through to the card. Pure — the fetch-success half of the
 * design's inline condition is enforced by the component's card fallback,
 * not here.
 */
export function isInlineDocument(asset) {
  return (
    asset != null &&
    asset.kind === "document" &&
    INLINE_DOCUMENT_MIMES.has(asset.mime) &&
    typeof asset.size_bytes === "number" &&
    asset.size_bytes <= MAX_INLINE_DOCUMENT_BYTES
  );
}

/**
 * Cap raw markdown to at most `maxChars` characters AND `maxLines` lines,
 * whichever bites first. Returns `{ text, truncated }` — `truncated` drives
 * the fade + "Open full document" emphasis. Pure + exported so each cap
 * branch is a directly-covered unit.
 */
export function capMarkdown(text, maxChars = MAX_INLINE_CHARS, maxLines = MAX_INLINE_LINES) {
  const raw = text || "";
  let capped = raw;
  let truncated = false;
  if (capped.length > maxChars) {
    capped = capped.slice(0, maxChars);
    truncated = true;
  }
  const lines = capped.split("\n");
  if (lines.length > maxLines) {
    capped = lines.slice(0, maxLines).join("\n");
    truncated = true;
  }
  return { text: capped, truncated };
}

export default function InlineDocument({ asset, onOpen }) {
  const { status, text } = useAssetContent(asset.chat_id, asset.asset_id, {
    mode: "text",
  });

  function handleOpen() {
    onOpen?.(asset);
  }

  // Content fetch failed (or the descriptor lacked an id the hook needs) —
  // degrade to the card rather than render an empty preview.
  if (status === "error" || status === "idle") {
    return <AssetCard asset={asset} onOpen={onOpen} />;
  }

  // Hold a placeholder while the text resolves so the transcript doesn't
  // reflow when the preview lands.
  if (status !== "ready") {
    return <div className="convo-doc-loading" aria-hidden="true" />;
  }

  const { text: capped, truncated } = capMarkdown(text);

  return (
    <div className={truncated ? "convo-doc is-clamped" : "convo-doc"}>
      <div className="convo-doc-body">{renderMarkdown(capped, false)}</div>
      <button
        type="button"
        className="convo-open-full"
        onClick={handleOpen}
        title="Open full document"
      >
        <span className="convo-open-full-cta">
          <Icon name="expand" size={13} />
          Open full document
        </span>
      </button>
    </div>
  );
}
