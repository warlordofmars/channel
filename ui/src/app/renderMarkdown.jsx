// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";

/**
 * Tiny markdown renderer for the mock-stream replies — paragraphs (split on
 * blank lines), **bold** spans, and 1. / 2. / 3. ordered lists. Ported
 * verbatim from design-sources/app/chat.jsx (`renderInline` + `renderMarkdown`).
 *
 * When `streaming` is true, a blinking `.cursor` span is appended to the
 * last block IFF that block is a paragraph — so the cursor doesn't appear
 * inside an OL where the design has no styling for it.
 */
export function renderInline(text, keyPrefix) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((seg, i) => {
    if (seg.startsWith("**") && seg.endsWith("**")) {
      return <strong key={`${keyPrefix}-${i}`}>{seg.slice(2, -2)}</strong>;
    }
    return <React.Fragment key={`${keyPrefix}-${i}`}>{seg}</React.Fragment>;
  });
}

export function renderMarkdown(text, streaming) {
  const blocks = text.split(/\n\n+/);
  const out = [];
  blocks.forEach((blk, bi) => {
    const lines = blk.split("\n");
    const isOl = lines.every((l) => /^\d+\.\s/.test(l.trim()) || l.trim() === "");
    if (isOl && lines.some((l) => /^\d+\.\s/.test(l.trim()))) {
      out.push(
        <ol key={`b${bi}`}>
          {lines
            .filter((l) => l.trim())
            .map((l, li) => (
              <li key={li}>
                {renderInline(l.replace(/^\d+\.\s/, ""), `b${bi}l${li}`)}
              </li>
            ))}
        </ol>
      );
    } else {
      const last = bi === blocks.length - 1;
      out.push(
        <p key={`b${bi}`}>
          {renderInline(blk, `b${bi}`)}
          {streaming && last && <span className="cursor" />}
        </p>
      );
    }
  });
  return out;
}
