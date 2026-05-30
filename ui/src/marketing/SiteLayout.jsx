// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect } from "react";
import Footer from "./Footer.jsx";
import Nav from "./Nav.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";

/**
 * Wraps every marketing page with the shared Nav + Footer chrome. Also
 * applies `data-site-theme` to <html> so the marketing site can choose a
 * default that differs from the app's `data-theme`. The two attributes
 * coexist; channel.css selectors use `data-theme` for app surfaces and
 * site.css selectors use `data-site-theme` for marketing surfaces.
 */
export default function SiteLayout({ children }) {
  const { siteTheme } = useChannelPrefs();

  useEffect(() => {
    document.documentElement.setAttribute("data-site-theme", siteTheme);
  }, [siteTheme]);

  return (
    <>
      <Nav />
      <main>{children}</main>
      <Footer />
    </>
  );
}
