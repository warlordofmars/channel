// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect } from "react";
import Icon from "../components/Icon.jsx";

/**
 * Per-row popover anchored to a sidebar Recents chat. 5 items
 * matching the reference UX; Pin / Change project / Remove from
 * project are disabled placeholders (rendered for menu shape; future
 * features wire them).
 *
 * Positioning: ``anchorRect`` is the bounding rect of the row that
 * spawned the menu. The popover anchors near the row's right edge and
 * opens downward; if the row is near the viewport bottom, it flips
 * to opening upward instead.
 *
 * Backdrop click + Esc both close. Same pattern as ModelPicker /
 * AttachMenu / AccountPopover per CLAUDE.md §UI conventions.
 */
export default function ChatRowMenu({ open, anchorRect, onClose, onRename, onDelete }) {
  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || !anchorRect) return null;

  const MENU_HEIGHT_ESTIMATE = 220;
  const flipUp = anchorRect.bottom + MENU_HEIGHT_ESTIMATE > window.innerHeight;
  const style = {
    position: "fixed",
    left: anchorRect.right - 8,
    top: flipUp ? undefined : anchorRect.bottom + 4,
    bottom: flipUp ? window.innerHeight - anchorRect.top + 4 : undefined,
  };

  function handleClick(callback) {
    return () => {
      callback();
      onClose();
    };
  }

  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="pop chat-row-menu" style={style} role="menu">
        <button
          type="button"
          role="menuitem"
          className="chat-row-menu-item disabled"
          aria-disabled="true"
          title="Coming soon"
        >
          <Icon name="pin" size={16} /> <span>Pin</span>
        </button>
        <button
          type="button"
          role="menuitem"
          className="chat-row-menu-item"
          onClick={handleClick(onRename)}
        >
          <Icon name="pencil" size={16} /> <span>Rename</span>
        </button>
        <button
          type="button"
          role="menuitem"
          className="chat-row-menu-item disabled"
          aria-disabled="true"
          title="Coming soon"
        >
          <Icon name="projects" size={16} /> <span>Change project</span>
        </button>
        <button
          type="button"
          role="menuitem"
          className="chat-row-menu-item disabled"
          aria-disabled="true"
          title="Coming soon"
        >
          <Icon name="folder-x" size={16} /> <span>Remove from project</span>
        </button>
        <button
          type="button"
          role="menuitem"
          className="chat-row-menu-item danger"
          onClick={handleClick(onDelete)}
        >
          <Icon name="trash" size={16} /> <span>Delete</span>
        </button>
      </div>
    </>
  );
}
