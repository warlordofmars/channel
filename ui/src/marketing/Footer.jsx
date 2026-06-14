// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link, useLocation } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";

/**
 * Site footer present on every marketing page. Translated from the <footer>
 * block in landing.src.html (Home variant) and the non-home prototypes.
 *
 * Per design: on Home (/), Features/Models/macOS/Windows/Linux scroll to
 * in-page sections (#features, #models, #download). On other routes, the
 * design uses dedicated page links (Channel Product.html / Channel
 * Download.html etc.) — so we route to /product, /models, /download
 * instead. Without this branch the bare anchors silently do nothing on
 * non-home pages.
 */
export default function Footer() {
  const year = new Date().getFullYear();
  const { pathname } = useLocation();
  const isHome = pathname === "/";
  const features = isHome ? <a href="#features">Features</a> : <Link to="/product">Features</Link>;
  const models = isHome ? <a href="#models">Models</a> : <Link to="/models">Models</Link>;
  const dl = (label) => (isHome ? <a href="#download">{label}</a> : <Link to="/download">{label}</Link>);
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
          {features}
          {models}
          <Link to="/pricing">Pricing</Link>
          {/* /docs/ is VitePress, outside the SPA route tree — plain <a> for
              full-page navigation (a <Link> would route client-side and fail). */}
          <a href="/docs/">Docs</a>
          <Link to="/app">Open app</Link>
        </div>
        <div className="foot-col">
          <h4>Download</h4>
          {dl("macOS")}
          {dl("Windows")}
          {dl("Linux")}
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
