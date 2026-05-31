// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Render an assistant turn's accumulated text as rich markdown.
 *
 * Uses ``react-markdown`` + ``remark-gfm`` so the agent's headings,
 * bold/italic, ordered + unordered lists, code spans, fenced code
 * blocks, links, blockquotes, tables, strikethrough, and task lists
 * all render with proper semantics. Raw HTML is dropped by default
 * (react-markdown sanitizes; we don't pass ``rehypeRaw``).
 *
 * When ``streaming`` is true, a blinking ``.cursor`` span is appended
 * after the rendered output so the user sees an active-stream
 * indicator at the trailing edge of the most-recently emitted bytes.
 */
export function renderMarkdown(text, streaming) {
  return (
    <>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      {streaming && <span className="cursor" />}
    </>
  );
}
