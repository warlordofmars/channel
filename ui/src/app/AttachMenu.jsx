// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import Icon from "../components/Icon.jsx";

const ITEMS = [
  { ic: "file",     label: "Upload a file",           sample: { kind: "file",  name: "spec-v2.pdf",       ic: "doc" } },
  { ic: "image",    label: "Add photos or images",    sample: { kind: "image", name: "diagram.png",       ic: "image" } },
  { ic: "database", label: "Connect data source",     sample: { kind: "data",  name: "events.parquet",    ic: "database" } },
];

/**
 * Composer + button popover. Three mock sample attachments — clicking one
 * appends it to the composer's attachment chips list via `onAdd`. Translated
 * from design-sources/app/chat.jsx `AttachMenu` function.
 */
export default function AttachMenu({ onAdd }) {
  const [open, setOpen] = useState(false);

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        className="cbtn round"
        onClick={() => setOpen((o) => !o)}
        title="Add attachment"
      >
        <Icon name="plus" size={19} />
      </button>
      {open && (
        <>
          <div className="backdrop" onClick={() => setOpen(false)} />
          <div className="pop" style={{ bottom: "calc(100% + 8px)", left: 0, minWidth: 240 }}>
            {ITEMS.map((it, i) => (
              <div
                className="opt"
                key={i}
                onClick={() => { onAdd(it.sample); setOpen(false); }}
              >
                <span style={{ color: "var(--ink-soft)" }}>
                  <Icon name={it.ic} size={18} />
                </span>
                <span className="nm" style={{ fontWeight: 500 }}>{it.label}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
