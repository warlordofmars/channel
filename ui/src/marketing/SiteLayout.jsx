// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect } from "react";
import Footer from "./Footer.jsx";
import Nav from "./Nav.jsx";
import { useChannelPrefs, STORAGE_KEYS, DEFAULTS } from "../hooks/useChannelPrefs.js";

/**
 * Wraps every marketing page with the shared Nav + Footer chrome. Overrides
 * `data-theme` with `siteTheme` while the marketing route is mounted, then
 * restores the app's `theme` on unmount so navigating to /app/* picks the
 * app preference back up. Marketing and app themes are still persisted
 * independently via separate localStorage keys in useChannelPrefs.
 */
export default function SiteLayout({ children }) {
  const { siteTheme } = useChannelPrefs();

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", siteTheme);
    return () => {
      const appTheme = (() => {
        try {
          return localStorage.getItem(STORAGE_KEYS.theme) ?? DEFAULTS.theme;
        } catch {
          return DEFAULTS.theme;
        }
      })();
      document.documentElement.setAttribute("data-theme", appTheme);
    };
  }, [siteTheme]);

  return (
    <>
      <Nav />
      <main>{children}</main>
      <Footer />
    </>
  );
}
