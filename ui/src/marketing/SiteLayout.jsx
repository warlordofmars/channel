// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect } from "react";
import Footer from "./Footer.jsx";
import Nav from "./Nav.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";

/**
 * Wraps every marketing page with the shared Nav + Footer chrome and
 * applies the single `theme` preference to `data-theme`. The chat-app
 * wrappers (Shell, Login) do the same — so the marketing toggle's choice
 * carries straight into the app and back.
 */
export default function SiteLayout({ children }) {
  const { theme } = useChannelPrefs();

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <>
      <Nav />
      <main>{children}</main>
      <Footer />
    </>
  );
}
