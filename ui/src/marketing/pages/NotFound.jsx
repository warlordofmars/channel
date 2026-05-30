// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ChannelMark from "../../components/ChannelMark.jsx";
import SiteLayout from "../SiteLayout.jsx";

/**
 * Branded 404 page. Translated from pages/404.body.html. The source's
 * inline SVG mark is replaced with <ChannelMark size={64} />; the two
 * CTAs route via React Router <Link>.
 */
export default function NotFound() {
  return (
    <SiteLayout>
      <section className="notfound wrap">
        <span className="mk404">
          <ChannelMark size={64} />
        </span>
        <div className="eyebrow" style={{ marginBottom: "12px" }}>Error 404</div>
        <h1>This page wandered off.</h1>
        <p>The link may be broken or the page might have moved. Let's get you back to thinking.</p>
        <div className="hero-cta" style={{ justifyContent: "center", marginTop: "30px" }}>
          <Link className="btn btn-primary btn-lg" to="/">Back to home</Link>
          <Link className="btn btn-ghost btn-lg" to="/app">Open Channel</Link>
        </div>
      </section>
    </SiteLayout>
  );
}
