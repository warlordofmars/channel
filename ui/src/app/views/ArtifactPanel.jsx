// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Icon from "../../components/Icon.jsx";
import { artIcon } from "./artifactHelpers.js";

/**
 * Slide-in artifact viewer panel. Translated from
 * design-sources/app/views.jsx `ArtifactPanel` + `renderArtifactBody`.
 * Five renderers (Code, Chart, Data, Interactive, Document/default) —
 * each lifted verbatim from the design source. Copy + Download are
 * Phase-6e no-ops; Close fires `onClose`.
 *
 * When `artifact` is null the component returns nothing, so callers can
 * mount it unconditionally and just flip the prop to show/hide.
 */
export default function ArtifactPanel({ artifact, onClose }) {
  if (!artifact) return null;
  const lines = artifact.lines && artifact.lines !== "—" ? " · " + artifact.lines : "";
  const noop = () => {};
  return (
    <div className="art-panel-wrap">
      <div className="art-backdrop" onClick={onClose} />
      <div className="art-panel">
        <div className="art-phead">
          <span className="ic"><Icon name={artIcon(artifact.kind)} size={18} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="t">{artifact.title}</div>
            <div className="s mono">{artifact.kind}{lines}</div>
          </div>
          <button type="button" className="icon-btn" title="Copy" onClick={noop}>
            <Icon name="copy" size={17} />
          </button>
          <button type="button" className="icon-btn" title="Download" onClick={noop}>
            <Icon name="download" size={17} />
          </button>
          <button type="button" className="icon-btn" title="Close" onClick={onClose}>
            <Icon name="close" size={18} />
          </button>
        </div>
        <div className="art-pbody">{renderArtifactBody(artifact)}</div>
      </div>
    </div>
  );
}

function renderArtifactBody(a) {
  switch (a.kind) {
    case "Code":
      return (
        <pre className="art-code"><code>{`export function createIngestionBuffer(opts: BufferOpts) {
  const queue: Event[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;

  function flush() {
    if (queue.length === 0) return;
    const batch = queue.splice(0, queue.length);
    opts.sink.writeColumnar(batch);   // bulk insert
    timer = null;
  }

  return {
    push(ev: Event) {
      queue.push(ev);
      if (queue.length >= opts.maxBatch) return flush();
      timer ??= setTimeout(flush, opts.flushMs);
    },
    drain: flush,
  };
}`}</code></pre>
      );
    case "Chart": {
      const bars = [42, 68, 55, 80, 61, 73, 90];
      const max = 100;
      return (
        <div className="art-chart">
          <svg viewBox="0 0 320 160" width="100%" height="220">
            {bars.map((v, i) => (
              <rect
                key={i}
                x={12 + i * 44}
                y={150 - (v / max) * 130}
                width="28"
                height={(v / max) * 130}
                rx="3"
                fill="var(--accent)"
                opacity={0.55 + i * 0.06}
              />
            ))}
            <line x1="8" y1="150" x2="316" y2="150" stroke="var(--border)" strokeWidth="1" />
          </svg>
          <div className="art-cap mono">Elevation gain by segment (m)</div>
        </div>
      );
    }
    case "Data":
      return (
        <table className="art-table">
          <thead>
            <tr><th>Month</th><th>Spend</th><th>Budget</th><th>Δ</th></tr>
          </thead>
          <tbody>
            {[
              ["Jan", "$4,210", "$4,500", "−6%"],
              ["Feb", "$4,880", "$4,500", "+8%"],
              ["Mar", "$3,940", "$4,500", "−12%"],
              ["Apr", "$5,120", "$5,000", "+2%"],
            ].map((r, i) => (
              <tr key={i}>
                {r.map((c, j) => (
                  <td key={j} className={j > 0 ? "mono" : ""}>{c}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
    case "Interactive":
      return (
        <div className="art-interactive">
          <div className="art-ph">
            <Icon name="play" size={22} />
            <div>Interactive preview</div>
            <div className="mono">React · runs in a sandbox</div>
          </div>
        </div>
      );
    default:
      return (
        <div className="art-doc">
          <h4>Summary</h4>
          <p>This runbook covers the cut-over from the row store to the columnar pipeline, including the dual-write window, verification queries, and rollback triggers.</p>
          <h4>Steps</h4>
          <p>1. Enable dual-write and let both stores receive events for 24 hours.<br />2. Backfill historical partitions oldest-first, verifying row counts per day.<br />3. Flip reads to the columnar store behind a feature flag, starting at 5%.</p>
          <p>Keep the row store warm for one full billing cycle before decommissioning.</p>
        </div>
      );
  }
}
