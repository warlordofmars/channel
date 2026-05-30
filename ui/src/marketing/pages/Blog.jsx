// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ImageSlot from "../ImageSlot.jsx";
import SiteLayout from "../SiteLayout.jsx";

/**
 * Marketing Blog page. Translated from pages/blog.body.html per
 * §Translation rules. Featured post + post-card grid, all images
 * as <ImageSlot data-image-slot="…" />.
 */
export default function Blog() {
  return (
    <SiteLayout>
      {/* PAGE HEADER */}
      <section className="page-head wrap">
        <div className="eyebrow">Blog</div>
        <h1>Notes from Channel Labs.</h1>
        <p>Product updates, design thinking, and the occasional deep dive into building with frontier models.</p>
      </section>

      {/* POST GRID */}
      <section className="section wrap" style={{ paddingTop: "40px" }}>
        <div className="post-grid">

          {/* Featured post */}
          <article className="post-card post-feat">
            <ImageSlot name="blog-featured-cover" className="post-thumb" aspect="16 / 9" />
            <div className="pbody">
              <div className="ptag">Product</div>
              <h3>Artifacts, now editable and versioned</h3>
              <p>The things you make with Channel are no longer disposable. Documents, code, and interfaces open beside the chat, keep their history, and live in your projects. Here's how we think about durable output.</p>
              <div className="pmeta">May 22, 2026 · 6 min read</div>
            </div>
          </article>

          {/* Post card 1 */}
          <article className="post-card">
            <ImageSlot name="blog-thumb-1" className="post-thumb" aspect="16 / 9" />
            <div className="ptag">Engineering</div>
            <h3>Designing the reasoning-effort dial</h3>
            <p>Why we put effort on a per-message control instead of hiding it in settings — and what we learned watching people use it.</p>
            <div className="pmeta">May 14, 2026 · 8 min read</div>
          </article>

          {/* Post card 2 */}
          <article className="post-card">
            <ImageSlot name="blog-thumb-2" className="post-thumb" aspect="16 / 9" />
            <div className="ptag">Design</div>
            <h3>Building a warm, technical interface</h3>
            <p>Clay tones, a mono accent, and one geometric mark. A look at the system behind Channel's calmer feel.</p>
            <div className="pmeta">May 3, 2026 · 5 min read</div>
          </article>

          {/* Post card 3 */}
          <article className="post-card">
            <ImageSlot name="blog-thumb-3" className="post-thumb" aspect="16 / 9" />
            <div className="ptag">Privacy</div>
            <h3>What local-first actually means for you</h3>
            <p>A plain-language walk through where your data lives, what never leaves your machine, and our no-training guarantee.</p>
            <div className="pmeta">Apr 25, 2026 · 7 min read</div>
          </article>

          {/* Post card 4 */}
          <article className="post-card">
            <ImageSlot name="blog-thumb-4" className="post-thumb" aspect="16 / 9" />
            <div className="ptag">Company</div>
            <h3>Channel raises the curtain</h3>
            <p>Why we started, what we're building, and an invitation to shape it with us in the open.</p>
            <div className="pmeta">Apr 10, 2026 · 4 min read</div>
          </article>

        </div>
      </section>

      {/* EMAIL CTA */}
      <section className="section wrap">
        <div className="cta-band">
          <h2>Get the next one in your inbox.</h2>
          <p>Occasional updates from the team. No noise, unsubscribe anytime.</p>
          <div className="hero-cta" style={{ marginTop: "0" }}>
            <a className="btn btn-primary btn-lg" href="#">Subscribe</a>
            <Link className="btn btn-ghost btn-lg" to="/app">Try Channel</Link>
          </div>
        </div>
      </section>
    </SiteLayout>
  );
}
