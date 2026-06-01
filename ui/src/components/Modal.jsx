// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect } from "react";

/**
 * Shared modal primitive. Backdrop + centered panel. Owns Esc-to-close
 * and backdrop-click-to-close. Renders nothing when ``open`` is false.
 *
 * Caller is responsible for the panel's content (heading, body, button
 * row). Click inside the panel does NOT close — only backdrop clicks
 * and Esc do.
 *
 * Focus management: caller is responsible for setting initial focus
 * (e.g. via ``ref`` + ``useEffect`` on the first focusable element).
 * A future iteration could add an automatic focus trap; for now the
 * simpler shape covers Rename + Delete confirms cleanly.
 */
export default function Modal({ open, onClose, children }) {
  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <>
      <div className="modal-backdrop" onClick={onClose} />
      <div className="modal" role="dialog" aria-modal="true">
        {children}
      </div>
    </>
  );
}
