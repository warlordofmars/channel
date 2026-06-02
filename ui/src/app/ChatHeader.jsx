// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import Icon from "../components/Icon.jsx";
import ChatRowMenu from "./ChatRowMenu.jsx";
import RenameChatModal from "./RenameChatModal.jsx";
import DeleteChatModal from "./DeleteChatModal.jsx";
import { useChats } from "../hooks/ChatsContext.jsx";

/**
 * Header strip at the top of the Conversation view. Shows the active
 * chat's title as a single big button — clicking anywhere on it opens
 * the same ChatRowMenu (Pin / Rename / Archive / Change project /
 * Remove from project / Delete) used by the sidebar row's ⋮ button.
 *
 * Renders an empty strip (no title button) when ``chat`` is null so
 * the layout doesn't jump while the chat list loads.
 *
 * Local state — does NOT route through Sidebar. The header's menu
 * and the sidebar row's menu are independent. Archive optimistically
 * removes the chat from useChatList's state and routes back to /app
 * since the header always sits on the active chat's URL.
 */
export default function ChatHeader({ chat }) {
  const navigate = useNavigate();
  const { archiveChat } = useChats();
  const [menuOpen, setMenuOpen] = useState(false);
  const [anchorRect, setAnchorRect] = useState(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  if (!chat) {
    return (
      <div className="chat-hd">
        <div className="chat-hd-spacer" />
        <button
          type="button"
          className="icon-btn chat-hd-share"
          disabled
          aria-disabled="true"
          aria-label="Share (coming soon)"
          title="Coming soon"
        >
          <Icon name="arrow-up" size={16} />
        </button>
      </div>
    );
  }

  function openMenu(e) {
    setAnchorRect(e.currentTarget.getBoundingClientRect());
    setMenuOpen(true);
  }

  return (
    <div className="chat-hd">
      <button type="button" className="chat-hd-title" onClick={openMenu}>
        <span>{chat.title}</span>
        <Icon name="chevron-down" size={16} />
      </button>
      <button
        type="button"
        className="icon-btn chat-hd-share"
        disabled
        aria-disabled="true"
        aria-label="Share (coming soon)"
        title="Coming soon"
      >
        <Icon name="arrow-up" size={16} />
      </button>
      {menuOpen && (
        <ChatRowMenu
          open
          anchorRect={anchorRect}
          onClose={() => setMenuOpen(false)}
          onRename={() => setRenameOpen(true)}
          onArchive={() => {
            archiveChat(chat.chat_id);
            navigate("/app");
          }}
          onDelete={() => setDeleteOpen(true)}
        />
      )}
      {renameOpen && (
        <RenameChatModal
          open
          chatId={chat.chat_id}
          currentTitle={chat.title}
          onClose={() => setRenameOpen(false)}
        />
      )}
      {deleteOpen && (
        <DeleteChatModal
          open
          chatId={chat.chat_id}
          chatTitle={chat.title}
          isActive={true}
          onClose={() => setDeleteOpen(false)}
        />
      )}
    </div>
  );
}
