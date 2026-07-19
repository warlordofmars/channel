// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import mermaid from "mermaid";

// mermaid.render needs a DOM-unique id per invocation (it stages a temporary
// node under that id while measuring). A monotonic counter keeps ids unique
// across every diagram mounted in the session.
let _renderSeq = 0;
function nextRenderId() {
  _renderSeq += 1;
  return `mermaid-svg-${_renderSeq}`;
}

// Configure mermaid before each render so a fresh diagram always picks up the
// currently-active light/dark theme (the app toggles `data-theme` on the root
// element). `securityLevel: "strict"` makes mermaid run its generated SVG
// through its bundled DOMPurify sanitiser (scripts, event handlers, and
// foreignObject HTML are stripped) before returning it — which is what makes
// it safe to inject the markup via dangerouslySetInnerHTML below. The source
// string reaching mermaid is the assistant's own diagram text, and the output
// is sanitiser-scrubbed, so no untrusted markup survives to the DOM. Colours
// come from mermaid's own built-in theme palettes (they live inside the
// mermaid package, not this codebase), so no colour literal is hardcoded
// here; the container chrome in app.css uses the project's CSS-var tokens.
function initMermaid() {
  const dark =
    document.documentElement.getAttribute("data-theme") === "dark";
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: dark ? "dark" : "neutral",
    fontFamily: "inherit",
  });
}

/**
 * Render a completed ```mermaid fence as an inline SVG diagram.
 *
 * `renderMarkdown` routes a fenced `mermaid` code block here only once the
 * stream is complete (while streaming, the fence renders as plain code — the
 * source is still arriving and would not parse). On mount we call
 * `mermaid.render(id, source)`; the resolved SVG string is injected into the
 * container. If mermaid rejects (invalid diagram syntax) we fall back to
 * showing the raw source with a small "Mermaid parse error" caption rather
 * than crashing the surrounding message.
 *
 * The render is async, so a rapidly-changing `source` (or an unmount) could
 * land a stale result; the effect's `cancelled` flag drops any resolution
 * that arrives after the effect has been torn down.
 */
export default function MermaidBlock({ source }) {
  const [svg, setSvg] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(
    function renderDiagram() {
      let cancelled = false;
      initMermaid();

      function onRendered(result) {
        if (!cancelled) {
          setSvg(result.svg);
          setFailed(false);
        }
      }
      function onError() {
        if (!cancelled) {
          setSvg("");
          setFailed(true);
        }
      }

      mermaid.render(nextRenderId(), source).then(onRendered, onError);

      return function cancel() {
        cancelled = true;
      };
    },
    [source],
  );

  if (failed) {
    return (
      <div className="mermaid-block mermaid-error">
        <pre className="mermaid-source">
          <code>{source}</code>
        </pre>
        <div className="mermaid-error-caption">Mermaid parse error</div>
      </div>
    );
  }

  // mermaid's strict security level sanitises the SVG it returns, so injecting
  // the markup is safe (see initMermaid above).
  return (
    <div
      className="mermaid-block"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
