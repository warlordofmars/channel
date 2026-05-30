// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ImageSlot from "../ImageSlot.jsx";
import SiteLayout from "../SiteLayout.jsx";

/**
 * Marketing Product page. Translated from pages/product.body.html per
 * §Translation rules.
 */
export default function Product() {
  return (
    <SiteLayout>
      {/* PAGE HEADER */}
      <section className="page-head wrap">
        <div className="eyebrow">Product</div>
        <h1>Everything you need to think with AI.</h1>
        <p>Channel is one fast, private workspace — frontier models, your projects, and the things you make, all in the same window.</p>
      </section>

      {/* FEATURES GRID */}
      <section className="section wrap">
        <div className="features">
          <div className="feature">
            <div className="ic">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.2H19.5A1.5 1.5 0 0 1 21 9.7V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
              </svg>
            </div>
            <h3>Projects</h3>
            <p>Group related chats and attach the docs, code, and context a topic needs. Channel carries that knowledge into every conversation in the project.</p>
            <span className="tag">// shared knowledge</span>
          </div>
          <div className="feature">
            <div className="ic">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" />
                <path d="M12 12l8-4.5M12 12v9M12 12L4 7.5" opacity="0.6" />
              </svg>
            </div>
            <h3>Artifacts</h3>
            <p>Documents, code, charts, and live interfaces open beside the chat — editable, versioned, and saved so you can return to them anytime.</p>
            <span className="tag">// docs · code · apps</span>
          </div>
          <div className="feature">
            <div className="ic">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3l1.8 6.2L20 11l-6.2 1.8L12 19l-1.8-6.2L4 11l6.2-1.8z" />
              </svg>
            </div>
            <h3>Every model, one dial</h3>
            <p>Switch between Claude Opus, Sonnet, and Haiku and set reasoning effort per message — the right tool without leaving the conversation.</p>
            <span className="tag">// low → max effort</span>
          </div>
          <div className="feature">
            <div className="ic">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="5" width="18" height="14" rx="2.5" />
                <circle cx="8.5" cy="10" r="1.6" />
                <path d="M21 16l-5-4-7 6" />
              </svg>
            </div>
            <h3>Files &amp; vision</h3>
            <p>Drop in PDFs, spreadsheets, images, and data sources. Channel reads them, reasons over them, and cites what it used.</p>
            <span className="tag">// pdf · img · csv · data</span>
          </div>
          <div className="feature">
            <div className="ic">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3l7 3v5c0 4.5-3 8-7 9-4-1-7-4.5-7-9V6z" />
                <path d="M9 12l2 2 4-4" />
              </svg>
            </div>
            <h3>Private by default</h3>
            <p>The desktop app is local-first. Nothing trains on your data, and you decide what ever leaves your machine.</p>
            <span className="tag">// your data, yours</span>
          </div>
          <div className="feature">
            <div className="ic">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="9" />
                <path d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18M12 3c-2.5 2.7-2.5 15.3 0 18" />
              </svg>
            </div>
            <h3>Web &amp; desktop</h3>
            <p>Open Channel in any browser, or install the native app for global hotkeys, offline drafts, and a window that&#39;s a keystroke away.</p>
            <span className="tag">// same channel, everywhere</span>
          </div>
        </div>
      </section>

      {/* PRODUCT SPOTLIGHT */}
      <section className="section wrap" style={{ paddingTop: "0" }}>
        <div className="spotlight">
          <ImageSlot
            name="product-spotlight"
            className="spotlight-slot"
            src="/screens/app-03-conversation.png"
            alt="The Channel chat app — an active conversation showing streaming responses"
          />
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section className="section wrap">
        <div className="section-head">
          <div className="eyebrow">How it works</div>
          <h2>Built around the way you actually work.</h2>
        </div>
        <div className="features">
          <div className="feature">
            <div className="ic">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
            </div>
            <h3>1 · Start anywhere</h3>
            <p>Open a blank chat, pick a quick action, or jump into a project. Channel meets you where the thought starts.</p>
          </div>
          <div className="feature">
            <div className="ic">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 8l-4 4 4 4M15 8l4 4-4 4" />
              </svg>
            </div>
            <h3>2 · Make things</h3>
            <p>Ask for a doc, a script, a chart, or a working prototype. It appears as an artifact you can edit and keep.</p>
          </div>
          <div className="feature">
            <div className="ic">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 7h10M4 12h16M4 17h7" />
                <circle cx="18" cy="7" r="1.4" />
                <circle cx="14" cy="17" r="1.4" />
              </svg>
            </div>
            <h3>3 · Keep context</h3>
            <p>Everything lands in a project — chats, knowledge, and artifacts — so the next session picks up where you left off.</p>
          </div>
        </div>
      </section>

      {/* CTA BAND */}
      <section className="section wrap">
        <div className="cta-band">
          <h2>See it for yourself.</h2>
          <p>Free to start. No credit card. Bring your own keys or use ours.</p>
          <div className="hero-cta" style={{ marginTop: "0" }}>
            <Link className="btn btn-primary btn-lg" to="/app">Open Channel</Link>
            <Link className="btn btn-ghost btn-lg" to="/download">Download app</Link>
          </div>
        </div>
      </section>
    </SiteLayout>
  );
}
