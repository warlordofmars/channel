// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect } from "react";
import Footer from "./Footer.jsx";
import Nav from "./Nav.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";

/**
 * Wraps every marketing page with the shared Nav + Footer chrome. Applies
 * `siteTheme` to `data-theme` so the marketing site renders with the user's
 * marketing preference (default light) — independent of the chat app theme.
 * The chat-app wrappers (Shell, Login) apply `theme` themselves on mount,
 * so leaving the marketing route automatically swaps `data-theme` over.
 */
export default function SiteLayout({ children }) {
  const { siteTheme } = useChannelPrefs();

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", siteTheme);
  }, [siteTheme]);

  return (
    <>
      <Nav />
      <main>{children}</main>
      <Footer />
    </>
  );
}
