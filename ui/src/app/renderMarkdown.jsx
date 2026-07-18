// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import Icon from "../components/Icon.jsx";

const REMARK_PLUGINS = [remarkGfm, remarkBreaks];

// Opening fence: up to 3 leading spaces (CommonMark), 3+ backticks,
// optional info string. Mirrors `_FENCE_OPEN_RE` in the backend producer
// (src/channel/agents/asset_producers.py) so client fence ordinals line
// up 1:1 with the `source.fence_index` the extraction records — the
// decoration is deterministic by ordinal, no content hashing.
const FENCE_OPEN_RE = /^ {0,3}(`{3,})(.*)$/;

/**
 * Enumerate every backtick-fenced code block in `text`, returning each
 * fence's ordinal plus its character span in the ORIGINAL string. This is
 * a faithful mirror of `extract_code_fences` in
 * `src/channel/agents/asset_producers.py`:
 *
 *   - backtick fences only (3+), up to 3 leading spaces;
 *   - the closer needs at least as many backticks as the opener and
 *     nothing else on the line;
 *   - an unterminated fence at end-of-text runs to the last line;
 *   - lines inside a fence body are never treated as openers.
 *
 * `fence_index` counts EVERY fence (mermaid + sub-threshold included), so
 * the ordinal here matches the backend's regardless of which fences were
 * promoted to assets.
 */
export function scanFences(text) {
  const lines = text.split("\n");
  // Character offset of the first char of each line in the original text.
  const starts = [];
  let offset = 0;
  for (const line of lines) {
    starts.push(offset);
    offset += line.length + 1; // +1 for the "\n" split consumed
  }
  const fences = [];
  let fenceIndex = 0;
  let i = 0;
  while (i < lines.length) {
    const open = FENCE_OPEN_RE.exec(lines[i]);
    if (!open) {
      i += 1;
      continue;
    }
    const ticks = open[1];
    const closeRe = new RegExp("^ {0,3}`{" + ticks.length + ",}\\s*$");
    let j = i + 1;
    let closed = false;
    while (j < lines.length) {
      if (closeRe.test(lines[j])) {
        closed = true;
        break;
      }
      j += 1;
    }
    const closeLine = closed ? j : lines.length - 1;
    fences.push({
      fenceIndex,
      start: starts[i],
      end: starts[closeLine] + lines[closeLine].length,
    });
    fenceIndex += 1;
    i = closed ? j + 1 : lines.length;
  }
  return fences;
}

function markdownBlock(key, text, streaming) {
  return (
    <React.Fragment key={key}>
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS}>{text}</ReactMarkdown>
      {streaming && <span className="cursor" />}
    </React.Fragment>
  );
}

/**
 * A located fence for a persisted `kind=code` asset. Per the #360 design
 * (Q3, option a+affordance), the fence is NO LONGER swapped for an
 * `AssetCard` — it renders as its OWN native `<pre><code>` block from the
 * fence's own source text (the persisted message text is unchanged;
 * `react-markdown` may normalize the rendered display, e.g. a trailing
 * newline in the code block) decorated with a small "open in panel" button.
 * The button reuses the existing `openAsset` URL seam via `onOpen(asset)` so
 * the full `ArtifactPanel` (Copy / Download / full-height) stays reachable;
 * the inline render is the code, the panel is the tooling.
 */
function CodeFenceDecorated({ fenceText, asset, onOpen }) {
  function handleOpen() {
    onOpen?.(asset);
  }
  return (
    <div className="code-decorated">
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS}>{fenceText}</ReactMarkdown>
      <button
        type="button"
        className="code-open-panel"
        onClick={handleOpen}
        title="Open in panel"
      >
        <span className="code-open-panel-ic" aria-hidden="true">
          <Icon name="expand" size={13} />
        </span>
        Open in panel
      </button>
    </div>
  );
}

/**
 * Render `text` with each fence that matches a persisted `kind=code` asset
 * DECORATED in place. `codeAssets` is a Map<fenceIndex, asset>; each fence
 * whose ordinal is present renders as its native fenced code block wrapped
 * in a `<CodeFenceDecorated>` carrying the "open in panel" affordance — the
 * fence text is preserved, not replaced. The surrounding markdown (including
 * any NON-located fences) renders as contiguous `react-markdown` segments so
 * their context is preserved. When no ordinal is locatable (text edited, or
 * a producer/SPA scan drift) we degrade to a plain single-block render
 * rather than dropping the code.
 */
function renderWithFenceDecorations(text, streaming, codeAssets, onOpenAsset) {
  const located = scanFences(text).filter((f) => codeAssets.has(f.fenceIndex));
  if (located.length === 0) {
    return markdownBlock("md", text, streaming);
  }
  const segments = [];
  let cut = 0;
  located.forEach((f, k) => {
    const pre = text.slice(cut, f.start);
    if (pre.trim() !== "") {
      segments.push(<ReactMarkdown key={`md-${k}`} remarkPlugins={REMARK_PLUGINS}>{pre}</ReactMarkdown>);
    }
    const asset = codeAssets.get(f.fenceIndex);
    segments.push(
      <CodeFenceDecorated
        key={`code-${asset.asset_id}`}
        fenceText={text.slice(f.start, f.end)}
        asset={asset}
        onOpen={onOpenAsset}
      />,
    );
    cut = f.end;
  });
  const tail = text.slice(cut);
  if (tail.trim() !== "") {
    segments.push(<ReactMarkdown key="md-tail" remarkPlugins={REMARK_PLUGINS}>{tail}</ReactMarkdown>);
  }
  return (
    <>
      {segments}
      {streaming && <span className="cursor" />}
    </>
  );
}

/**
 * Render an assistant turn's accumulated text as rich markdown.
 *
 * Uses ``react-markdown`` + ``remark-gfm`` so the agent's headings,
 * bold/italic, ordered + unordered lists, code spans, fenced code
 * blocks, links, blockquotes, tables, strikethrough, and task lists
 * all render with proper semantics. Raw HTML is dropped by default
 * (react-markdown sanitizes; we don't pass ``rehypeRaw``).
 *
 * ``remark-breaks`` renders single ``\n`` soft breaks as ``<br>``
 * (the chat-UI convention) instead of CommonMark's collapse-to-space
 * default — agent output frequently uses single newlines for visual
 * structure. Paragraph (``\n\n``) behaviour is unchanged because the
 * plugin only rewrites soft breaks inside paragraph nodes (#210).
 *
 * When ``streaming`` is true, a blinking ``.cursor`` span is appended
 * after the rendered output so the user sees an active-stream
 * indicator at the trailing edge of the most-recently emitted bytes.
 *
 * ``opts.codeAssets`` (Map<fenceIndex, asset>) + ``opts.onOpenAsset``
 * (#327 → #362) DECORATE specific fenced code blocks for persisted code
 * assets: the fence renders as its native code block with an "open in
 * panel" affordance (no card swap — see #360 Q3). The common case — no
 * code assets — takes the single-``ReactMarkdown`` fast path unchanged.
 */
export function renderMarkdown(text, streaming, { codeAssets, onOpenAsset } = {}) {
  if (!codeAssets || codeAssets.size === 0) {
    return markdownBlock("md", text, streaming);
  }
  return renderWithFenceDecorations(text, streaming, codeAssets, onOpenAsset);
}
