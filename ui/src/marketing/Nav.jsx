// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link, useLocation } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";
import ThemeToggle from "./ThemeToggle.jsx";

/**
 * Sticky top nav for every marketing page. Brand mark + 4 nav links + theme
 * toggle + Sign in + Download CTA. Translated from landing.src.html.
 *
 * The Product/Models/Download links route to the dedicated /product /models
 * /download pages regardless of current route — same-page anchor scroll on
 * Home felt unintuitive when the user wanted to read the dedicated page.
 * Active link is marked with the design's `.on` class.
 */
export default function Nav() {
  const { pathname } = useLocation();

  return (
    <header className="nav">
      <div className="wrap nav-in">
        <Link className="brand" to="/">
          <ChannelMark size={26} />
          Channel
        </Link>
        <nav className="nav-links">
          <Link to="/product" className={pathname === "/product" ? "on" : ""}>Product</Link>
          <Link to="/models" className={pathname === "/models" ? "on" : ""}>Models</Link>
          <Link to="/pricing" className={pathname === "/pricing" ? "on" : ""}>Pricing</Link>
          <Link to="/download" className={pathname === "/download" ? "on" : ""}>Download</Link>
        </nav>
        <div className="nav-right">
          <ThemeToggle />
          <Link className="btn btn-ghost" to="/app">Sign in</Link>
          <Link className="btn btn-primary" to="/download">
            Download<span className="sr-only"> app</span>
          </Link>
        </div>
      </div>
    </header>
  );
}
