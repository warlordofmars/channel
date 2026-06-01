// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef, useState } from "react";
import Modal from "../components/Modal.jsx";
import { useChats } from "../hooks/ChatsContext.jsx";

/**
 * Rename one chat. Pre-fills + pre-selects the input on mount so the
 * user can type immediately to replace the title (macOS Finder rename
 * pattern). Save is disabled when empty or unchanged. Enter submits.
 *
 * On API failure, shows an inline error line and leaves the modal
 * open so the user can retry without losing typed input. The
 * underlying useChatList.renameChat already applied an optimistic
 * local update — see useChatList.js for the revert semantics.
 */
export default function RenameChatModal({ open, chatId, currentTitle, onClose }) {
  const { renameChat } = useChats();
  const [value, setValue] = useState(currentTitle);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    setValue(currentTitle);
    setError(null);
    setBusy(false);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) {
        el.focus();
        el.select();
      }
    });
  }, [open, currentTitle]);

  const trimmed = value.trim();
  const disabled = busy || trimmed.length === 0 || trimmed === currentTitle;

  async function handleSave() {
    setBusy(true);
    setError(null);
    try {
      await renameChat(chatId, trimmed);
      onClose();
    } catch {
      setError("Couldn't rename the chat. Try again.");
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose}>
      <h2 className="modal-heading">Rename chat</h2>
      <label className="modal-label" htmlFor="rename-input">Title</label>
      <input
        id="rename-input"
        ref={inputRef}
        className="modal-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && !disabled && handleSave()}
      />
      {error && <p className="modal-error">{error}</p>}
      <div className="modal-buttons">
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={disabled} onClick={handleSave}>
          Save
        </button>
      </div>
    </Modal>
  );
}
