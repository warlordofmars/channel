// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";

/**
 * Round icon button that flips the global `theme` between light and dark.
 * One preference applies to both marketing and the chat app, so toggling
 * here carries into /app and back.
 */
export default function ThemeToggle() {
  const { theme, toggleTheme } = useChannelPrefs();
  return (
    <button
      type="button"
      className="icon-btn-m"
      onClick={toggleTheme}
      aria-label="Toggle theme"
      title="Toggle theme"
    >
      <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
    </button>
  );
}
