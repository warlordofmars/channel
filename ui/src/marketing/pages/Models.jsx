// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import SiteLayout from "../SiteLayout.jsx";

/**
 * Marketing Models page. Translated from pages/models.body.html per
 * §Translation rules.
 */
export default function Models() {
  return (
    <SiteLayout>
      {/* PAGE HEADER */}
      <section className="page-head wrap">
        <div className="eyebrow">Powered by Claude</div>
        <h1>Three models. One simple dial.</h1>
        <p>Channel runs on Anthropic&apos;s Claude models. Reach for depth when it matters and speed when it doesn&apos;t — without juggling tabs or providers.</p>
      </section>

      {/* MODEL CARDS */}
      <section className="section wrap" style={{ paddingTop: "48px" }}>
        <div className="models-grid">
          <div className="model-card feat">
            <div className="mn">Claude Opus 4.8</div>
            <span className="mt">Flagship</span>
            <p className="md">Anthropic&apos;s most capable model — complex reasoning, long-horizon agentic coding, and high-autonomy work. Defaults to high reasoning effort.</p>
          </div>
          <div className="model-card">
            <div className="mn">Claude Sonnet 4.6</div>
            <span className="mt">Balanced</span>
            <p className="md">The best blend of speed and intelligence. The safe default that handles the vast majority of everyday writing, analysis, and code.</p>
          </div>
          <div className="model-card">
            <div className="mn">Claude Haiku 4.5</div>
            <span className="mt">Fast</span>
            <p className="md">Fastest and most cost-effective, built for low-latency chat, classification, and high-volume automation.</p>
          </div>
        </div>
      </section>

      {/* COMPARISON TABLE */}
      <section className="section wrap" style={{ paddingTop: "0" }}>
        <div className="section-head">
          <div className="eyebrow">Compare</div>
          <h2>Pick the right tier per task.</h2>
        </div>
        <div className="cmp">
          <table>
            <thead>
              <tr>
                <th>&nbsp;</th>
                <th>Opus 4.8</th>
                <th>Sonnet 4.6</th>
                <th>Haiku 4.5</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="cnm">Best for</td>
                <td>Hard reasoning, agents</td>
                <td>Everyday work</td>
                <td>Speed &amp; volume</td>
              </tr>
              <tr>
                <td className="cnm">Context window</td>
                <td className="mono">1M tokens</td>
                <td className="mono">1M tokens</td>
                <td className="mono">200K tokens</td>
              </tr>
              <tr>
                <td className="cnm">Relative speed</td>
                <td>Considered</td>
                <td>Fast</td>
                <td>Fastest</td>
              </tr>
              <tr>
                <td className="cnm">Vision</td>
                <td>Yes</td>
                <td>Yes</td>
                <td>Yes</td>
              </tr>
              <tr>
                <td className="cnm">Effort levels</td>
                <td className="mono">low–max</td>
                <td className="mono">low–max</td>
                <td className="mono">low–high</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      {/* REASONING EFFORT EXPLAINER */}
      <section className="section wrap" style={{ paddingTop: "0" }}>
        <div className="section-head">
          <div className="eyebrow">Reasoning effort</div>
          <h2>Tell Channel how hard to think.</h2>
          <p>Every message has an effort dial — from quick replies to deep, deliberate reasoning. Turn it up for the gnarly problems and down for the obvious ones, so you only spend the compute a task is worth.</p>
        </div>
        <div className="features">
          <div className="feature">
            <h3>Low</h3>
            <p>Snappy answers for lookups, quick edits, and simple questions.</p>
            <span className="tag">// fastest</span>
          </div>
          <div className="feature">
            <h3>Standard &amp; High</h3>
            <p>The everyday range — balanced quality for most real work.</p>
            <span className="tag">// default</span>
          </div>
          <div className="feature">
            <h3>Max</h3>
            <p>Deliberate, multi-step reasoning for the hardest problems.</p>
            <span className="tag">// deepest</span>
          </div>
        </div>
      </section>

      {/* CTA BAND */}
      <section className="section wrap">
        <div className="cta-band">
          <h2>Use the whole lineup.</h2>
          <p>Free includes Sonnet &amp; Haiku. Pro and Max unlock Opus 4.8 and higher effort.</p>
          <div className="hero-cta" style={{ marginTop: "0" }}>
            <Link className="btn btn-primary btn-lg" to="/app">Open Channel</Link>
            <Link className="btn btn-ghost btn-lg" to="/pricing">See pricing</Link>
          </div>
        </div>
      </section>
    </SiteLayout>
  );
}
