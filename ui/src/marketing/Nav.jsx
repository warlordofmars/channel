// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link, useLocation } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";
import ThemeToggle from "./ThemeToggle.jsx";

/**
 * Sticky top nav for every marketing page. Brand mark + 4 nav links + theme
 * toggle + Sign in + Download CTA. Translated from landing.src.html.
 *
 * Per design, the Product/Models/Download links behave differently on Home
 * vs other routes: on /, they're in-page anchors scrolling to #features /
 * #models / #download sections; elsewhere, they navigate to the dedicated
 * /product, /models, /download routes (mirroring `Channel Product.html`
 * etc. in the non-home design prototypes). useLocation lets us pick.
 */
export default function Nav() {
  const { pathname } = useLocation();
  const isHome = pathname === "/";

  return (
    <header className="nav">
      <div className="wrap nav-in">
        <Link className="brand" to="/">
          <ChannelMark size={26} />
          Channel
        </Link>
        <nav className="nav-links">
          {isHome
            ? <a href="#features">Product</a>
            : <Link to="/product" className={pathname === "/product" ? "on" : ""}>Product</Link>}
          {isHome
            ? <a href="#models">Models</a>
            : <Link to="/models" className={pathname === "/models" ? "on" : ""}>Models</Link>}
          <Link to="/pricing" className={pathname === "/pricing" ? "on" : ""}>Pricing</Link>
          {isHome
            ? <a href="#download">Download</a>
            : <Link to="/download" className={pathname === "/download" ? "on" : ""}>Download</Link>}
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
