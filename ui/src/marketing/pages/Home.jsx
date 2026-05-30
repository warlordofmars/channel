// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ImageSlot from "../ImageSlot.jsx";
import SiteLayout from "../SiteLayout.jsx";

/**
 * Marketing landing page. Translated from design-sources/site/landing.src.html's
 * <main> block. Internal Channel-X.html hrefs rewritten to React Router <Link>;
 * image placeholders use <ImageSlot data-image-slot="…" />.
 * Wrapped in SiteLayout for shared Nav + Footer.
 */
export default function Home() {
  return (
    <SiteLayout>
      {/* HERO */}
      <section className="hero wrap">
        <div className="eyebrow">Meet Channel</div>
        <h1>The workspace for thinking with <span className="accent">AI.</span></h1>
        <p className="sub">Channel brings frontier models, your projects, and everything you make into one fast, private app — in your browser and on your desktop.</p>
        <div className="hero-cta">
          <a className="btn btn-primary btn-lg" href="#download">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M16.4 12.6c0-2.2 1.8-3.3 1.9-3.3-1-1.5-2.6-1.7-3.2-1.7-1.4-.1-2.6.8-3.3.8-.7 0-1.7-.8-2.8-.8-1.4 0-2.7.8-3.5 2.1-1.5 2.6-.4 6.4 1 8.5.7 1 1.5 2.2 2.6 2.1 1-.04 1.4-.7 2.7-.7 1.3 0 1.6.7 2.7.6 1.1-.02 1.8-1 2.5-2 .8-1.2 1.1-2.3 1.1-2.3s-2.2-.8-2.2-3.3zM14.3 6c.6-.7 1-1.7.9-2.7-.9.04-1.9.6-2.5 1.3-.5.6-1 1.6-.9 2.6 1 .08 2-.5 2.5-1.2z"/></svg>
            Download for Mac
          </a>
          <Link className="btn btn-ghost btn-lg" to="/app">Open in browser</Link>
        </div>
        <div className="hero-note">Free to start · macOS · Windows · Linux</div>
      </section>

      {/* PRODUCT SCREEN CAPTURE (follows page theme) */}
      <section className="wrap mock-stage">
        <ImageSlot
          name="hero-screen-light"
          className="hero-shot light"
          src="/screens/hero-home-light.png"
          alt="The Channel chat app in light theme — chat home with the model picker, recents, and quick actions"
        />
        <ImageSlot
          name="hero-screen-dark"
          className="hero-shot dark"
          src="/screens/hero-home-dark.png"
          alt="The Channel chat app in dark theme — chat home with the model picker, recents, and quick actions"
        />
      </section>

      {/* FEATURES */}
      <section className="section wrap" id="features">
        <div className="section-head">
          <div className="eyebrow">Why Channel</div>
          <h2>Built for real work, not demos.</h2>
          <p>The pieces you actually need to get things done with a model — organized, persistent, and yours.</p>
        </div>
        <div className="features">
          <div className="feature">
            <div className="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.2H19.5A1.5 1.5 0 0 1 21 9.7V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></div>
            <h3>Projects</h3>
            <p>Group related chats and attach the docs, code, and context a topic needs. Channel remembers across every conversation in the project.</p>
            <span className="tag">// shared knowledge</span>
          </div>
          <div className="feature">
            <div className="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5" opacity="0.6"/></svg></div>
            <h3>Artifacts</h3>
            <p>Documents, code, charts, and live interfaces appear beside the chat — editable, versioned, and saved so you can pick them back up anytime.</p>
            <span className="tag">// docs · code · apps</span>
          </div>
          <div className="feature">
            <div className="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l1.8 6.2L20 11l-6.2 1.8L12 19l-1.8-6.2L4 11l6.2-1.8z"/></svg></div>
            <h3>Every model, one place</h3>
            <p>Switch between flagship and fast models, and dial reasoning effort up or down per message. Use the right tool without leaving the chat.</p>
            <span className="tag">// low → max effort</span>
          </div>
          <div className="feature">
            <div className="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="8.5" cy="10" r="1.6"/><path d="M21 16l-5-4-7 6"/></svg></div>
            <h3>Files &amp; vision</h3>
            <p>Drop in PDFs, spreadsheets, images, and data sources. Channel reads them, reasons over them, and cites what it used.</p>
            <span className="tag">// pdf · img · csv · data</span>
          </div>
          <div className="feature">
            <div className="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8-7 9-4-1-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/></svg></div>
            <h3>Private by default</h3>
            <p>The desktop app keeps your work local-first. Nothing trains on your data, and you stay in control of what leaves your machine.</p>
            <span className="tag">// your data, yours</span>
          </div>
          <div className="feature">
            <div className="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18M12 3c-2.5 2.7-2.5 15.3 0 18"/></svg></div>
            <h3>Web &amp; desktop</h3>
            <p>Open Channel in any browser, or install the native app for global hotkeys, offline drafts, and a window that's always a keystroke away.</p>
            <span className="tag">// same channel, everywhere</span>
          </div>
        </div>
      </section>

      {/* PLATFORMS */}
      <section className="section wrap" id="download" style={{ paddingTop: "24px" }}>
        <div className="section-head">
          <div className="eyebrow">Everywhere you work</div>
          <h2>Install once. Pick up anywhere.</h2>
        </div>
        <div className="platforms">
          <div className="plat"><span className="ic"><svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M16.4 12.6c0-2.2 1.8-3.3 1.9-3.3-1-1.5-2.6-1.7-3.2-1.7-1.4-.1-2.6.8-3.3.8-.7 0-1.7-.8-2.8-.8-1.4 0-2.7.8-3.5 2.1-1.5 2.6-.4 6.4 1 8.5.7 1 1.5 2.2 2.6 2.1 1-.04 1.4-.7 2.7-.7 1.3 0 1.6.7 2.7.6 1.1-.02 1.8-1 2.5-2 .8-1.2 1.1-2.3 1.1-2.3s-2.2-.8-2.2-3.3zM14.3 6c.6-.7 1-1.7.9-2.7-.9.04-1.9.6-2.5 1.3-.5.6-1 1.6-.9 2.6 1 .08 2-.5 2.5-1.2z"/></svg></span><div><div className="t">macOS</div><div className="s">Universal · 14+</div></div></div>
          <div className="plat"><span className="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M3 5.5l7.5-1v7.2H3zM11.5 4.3L21 3v8.7h-9.5zM3 12.7h7.5V20L3 18.8zM11.5 12.7H21V21l-9.5-1.3z"/></svg></span><div><div className="t">Windows</div><div className="s">10 · 11</div></div></div>
          <div className="plat"><span className="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M9 4c-1 1.5-1 4 0 6-1.5 1.5-3 4-3 7 0 1.5 1.5 2 3 2h6c1.5 0 3-.5 3-2 0-3-1.5-5.5-3-7 1-2 1-4.5 0-6-.8-1.2-2-1.5-3-1.5S9.8 2.8 9 4z"/><path d="M10 9h.01M14 9h.01"/></svg></span><div><div className="t">Linux</div><div className="s">.deb · .rpm · AppImage</div></div></div>
          <div className="plat"><span className="ic"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18M12 3c-2.5 2.7-2.5 15.3 0 18"/></svg></span><div><div className="t">Web</div><div className="s">Any modern browser</div></div></div>
        </div>
      </section>

      {/* MODELS */}
      <section className="section wrap" id="models">
        <div className="section-head">
          <div className="eyebrow">Powered by Claude</div>
          <h2>Three models. One simple dial.</h2>
          <p>Channel runs on Anthropic's Claude models. Reach for depth when it matters and speed when it doesn't — without juggling tabs or providers.</p>
        </div>
        <div className="models-grid">
          <div className="model-card feat">
            <div className="mn">Claude Opus 4.8</div>
            <span className="mt">Flagship</span>
            <p className="md">Anthropic's most capable model — complex reasoning, long-horizon agentic coding, and high-autonomy work, with a 1M-token context window.</p>
          </div>
          <div className="model-card">
            <div className="mn">Claude Sonnet 4.6</div>
            <span className="mt">Balanced</span>
            <p className="md">The best blend of speed and intelligence. The safe default that handles 90% of everyday writing, analysis, and code.</p>
          </div>
          <div className="model-card">
            <div className="mn">Claude Haiku 4.5</div>
            <span className="mt">Fast</span>
            <p className="md">Fastest and most cost-effective, built for low-latency chat, classification, and high-volume automation.</p>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="section wrap">
        <div className="cta-band">
          <h2>Open a channel to better work.</h2>
          <p>Free to start. No credit card. Bring your own keys or use ours.</p>
          <div className="hero-cta" style={{ marginTop: "0" }}>
            <a className="btn btn-primary btn-lg" href="#download">Download Channel</a>
            <Link className="btn btn-ghost btn-lg" to="/pricing">See pricing</Link>
          </div>
        </div>
      </section>
    </SiteLayout>
  );
}
