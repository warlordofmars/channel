// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useRef, useState } from "react";
import Icon from "../components/Icon.jsx";

// v1 MIME allowlist — mirrors the backend allowlist in
// ``src/channel/api/attachments.py``. Repeated client-side as a UX
// guard so the user gets immediate feedback rather than waiting for
// the presign 400. The server still enforces.
export const ALLOWED_MIMES = [
  "application/pdf",
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "text/csv",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/plain",
  "text/markdown",
];
const ACCEPT_ATTR = ALLOWED_MIMES.join(",");

/**
 * Composer + button popover. Opens a small popover with "Choose files"
 * which triggers a hidden ``<input type=file multiple>``. Selected
 * files are emitted via ``onFiles(FileList)`` to the parent
 * (``Composer``), which owns the validate → presign → upload →
 * finalize pipeline and the pending-attachment state.
 *
 * Strings use "attach"/"attached" per Channel's 2026-06-03 UI copy
 * convention — never "upload"/"uploaded".
 */
export default function AttachMenu({ onFiles }) {
  const [open, setOpen] = useState(false);
  const fileInputRef = useRef(null);

  function openPicker() {
    setOpen(false);
    // Defer the click() so React finishes the setOpen → re-render
    // before the browser opens the native file dialog. Without the
    // microtask hop, some browsers race the dialog past the popover
    // close and leave a stuck backdrop.
    queueMicrotask(() => fileInputRef.current?.click());
  }

  function onFileInputChange(e) {
    onFiles(e.target.files);
    // Reset so picking the same filename again still fires onchange.
    e.target.value = "";
  }

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        className="cbtn round"
        onClick={() => setOpen((o) => !o)}
        title="Attach files"
        aria-label="Attach files"
      >
        <Icon name="plus" size={19} />
      </button>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={ACCEPT_ATTR}
        style={{ display: "none" }}
        onChange={onFileInputChange}
        data-testid="attach-file-input"
      />
      {open && (
        <>
          <div className="backdrop" onClick={() => setOpen(false)} />
          <div className="pop" style={{ bottom: "calc(100% + 8px)", left: 0, minWidth: 240 }}>
            <div className="opt" onClick={openPicker} role="button" tabIndex={0}>
              <span style={{ color: "var(--ink-soft)" }}>
                <Icon name="file" size={18} />
              </span>
              <span className="nm" style={{ fontWeight: 500 }}>Choose files</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
