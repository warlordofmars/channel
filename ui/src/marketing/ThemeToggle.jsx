// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";

/**
 * Round icon button that flips the marketing site's theme between light and
 * dark. Reads + writes `channel-site-theme` via `useChannelPrefs` so the app
 * and site themes can be toggled independently.
 */
export default function ThemeToggle() {
  const { siteTheme, toggleSiteTheme } = useChannelPrefs();
  return (
    <button
      type="button"
      className="icon-btn-m"
      onClick={toggleSiteTheme}
      aria-label="Toggle theme"
      title="Toggle theme"
    >
      <Icon name={siteTheme === "dark" ? "sun" : "moon"} size={16} />
    </button>
  );
}
