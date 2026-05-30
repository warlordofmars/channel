// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";

/**
 * Site footer present on every marketing page. Translated verbatim from the
 * <footer> block in landing.src.html. Internal links rewritten to <Link>;
 * in-page anchors (#features, #models, #download) stay as plain <a>.
 */
export default function Footer() {
  const year = new Date().getFullYear();
  return (
    <footer className="footer">
      <div className="wrap footer-in">
        <div>
          <Link className="brand" to="/">
            <ChannelMark size={24} />
            Channel
          </Link>
          <div className="tagline">A calmer, faster workspace for thinking with AI.</div>
        </div>
        <div className="foot-spacer"></div>
        <div className="foot-col">
          <h4>Product</h4>
          <a href="#features">Features</a>
          <a href="#models">Models</a>
          <Link to="/pricing">Pricing</Link>
          <Link to="/app">Open app</Link>
        </div>
        <div className="foot-col">
          <h4>Download</h4>
          <a href="#download">macOS</a>
          <a href="#download">Windows</a>
          <a href="#download">Linux</a>
        </div>
        <div className="foot-col">
          <h4>Company</h4>
          <Link to="/about">About</Link>
          <Link to="/blog">Blog</Link>
          <Link to="/careers">Careers</Link>
          <Link to="/privacy">Privacy</Link>
        </div>
      </div>
      <div className="wrap foot-legal">
        © {year} Channel Labs · Built for people who think out loud.
      </div>
    </footer>
  );
}
