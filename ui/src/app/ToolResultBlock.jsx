// Copyright (c) 2026 John Carter. All rights reserved.
//
// Discriminated-union renderer for tool results in the Conversation
// view. The default branch is the plain monospace summary used by
// current_time / web_search. The code-output branch (#183) renders
// the sandbox Lambda result: stdout (collapsible >5 lines), stderr
// (inside <details>), inline images, and a meta line.

import { useState } from "react";

export default function ToolResultBlock({ kind, summary, payload }) {
  if (kind === "code-output") {
    return <CodeOutputBlock payload={payload} />;
  }
  return (
    <div className="tool-result-block" data-kind={kind || "default"}>
      <pre className="tool-result-summary">{summary}</pre>
    </div>
  );
}

function CodeOutputBlock({ payload }) {
  // Render-safe defaults so a missing payload doesn't crash the row.
  const [expanded, setExpanded] = useState(false);
  if (!payload) {
    return (
      <div className="tool-result-block" data-kind="code-output" />
    );
  }
  const stdout = payload.stdout || "";
  const lines = stdout.split("\n");
  // Trailing-newline-only stdout produces one extra empty line; trim
  // it so the line count matches what the user perceives.
  const lineCount =
    stdout.length > 0 && stdout.endsWith("\n") ? lines.length - 1 : lines.length;
  const collapsed = !expanded && lineCount > 5;
  const shown = collapsed ? lines.slice(0, 5).join("\n") : stdout;
  const stderrLen = (payload.stderr || "").length;
  return (
    <div className="tool-result-block" data-kind="code-output">
      {stdout && (
        <div className="code-output-pane">
          <div className="code-output-toolbar">
            <span>stdout</span>
            {lineCount > 5 && (
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="code-output-toggle"
              >
                {expanded ? "Collapse" : `Show all ${lineCount} lines`}
              </button>
            )}
          </div>
          <pre className="code-output-text">{shown}</pre>
        </div>
      )}
      {stderrLen > 0 && (
        <details className="code-output-stderr">
          <summary>stderr ({stderrLen} bytes)</summary>
          <pre>{payload.stderr}</pre>
        </details>
      )}
      {Array.isArray(payload.images) &&
        payload.images.map((img, i) => (
          <img
            key={i}
            src={`data:${img.mime};base64,${img.b64}`}
            alt={`code-exec output ${i + 1}`}
            className="code-output-image"
          />
        ))}
      <div className="code-output-meta">
        exit {payload.exit_code} · {payload.duration_ms}ms
        {payload.truncated && " · output truncated"}
        {payload.timed_out && " · timed out at 270s"}
      </div>
    </div>
  );
}
