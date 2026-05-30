// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";
import ThemeToggle from "./ThemeToggle.jsx";

/**
 * Sticky top nav for every marketing page. Brand mark + 4 nav links + theme
 * toggle + Sign in + Download CTA. Translated from landing.src.html
 * lines 11-30; internal links rewritten to <Link>.
 */
export default function Nav() {
  return (
    <header className="nav">
      <div className="wrap nav-in">
        <Link className="brand" to="/">
          <ChannelMark size={26} />
          Channel
        </Link>
        <nav className="nav-links">
          <Link to="/product">Product</Link>
          <Link to="/models">Models</Link>
          <Link to="/pricing">Pricing</Link>
          <Link to="/download">Download</Link>
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
