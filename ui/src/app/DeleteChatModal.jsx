// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import Modal from "../components/Modal.jsx";
import { useChats } from "../hooks/ChatsContext.jsx";

/**
 * Confirm-and-delete one chat. Permanently removes the chat from both
 * DynamoDB (message rows + chat-index row) and AgentCore Memory (all
 * events for the chat's session). Delete is irreversible by design.
 *
 * Default focus is on Cancel so a stray Enter dismisses; user must
 * Tab to Delete + Enter (or click) to confirm. macOS convention for
 * destructive defaults.
 *
 * If ``isActive`` is true (the user is currently viewing this chat),
 * navigate to ``/app`` BEFORE invoking the delete so the user never
 * sees Conversation.jsx flash a 404 state during the optimistic local
 * removal.
 */
export default function DeleteChatModal({ open, chatId, chatTitle, isActive, onClose }) {
  const { deleteChat } = useChats();
  const navigate = useNavigate();
  const cancelRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => cancelRef.current?.focus());
  }, [open]);

  async function handleConfirm() {
    if (isActive) navigate("/app");
    try {
      await deleteChat(chatId);
    } finally {
      onClose();
    }
  }

  return (
    <Modal open={open} onClose={onClose}>
      <h2 className="modal-heading">Delete chat?</h2>
      <p className="modal-body">
        This will permanently delete &ldquo;{chatTitle}&rdquo; and remove it from
        the AI&rsquo;s memory. This action cannot be undone.
      </p>
      <div className="modal-buttons">
        <button
          type="button"
          ref={cancelRef}
          className="btn-secondary"
          onClick={onClose}
        >
          Cancel
        </button>
        <button type="button" className="btn-danger" onClick={handleConfirm}>
          Delete
        </button>
      </div>
    </Modal>
  );
}
