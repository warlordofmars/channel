// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import ImageSlot from "../ImageSlot.jsx";
import SiteLayout from "../SiteLayout.jsx";

/**
 * Marketing About page. Translated from pages/about.body.html per
 * §Translation rules. The team-photo image is rendered as an ImageSlot.
 */
export default function About() {
  return (
    <SiteLayout>
      {/* PAGE HEAD */}
      <section className="page-head wrap">
        <div className="eyebrow">About</div>
        <h1>A calmer place to think.</h1>
        <p>
          Channel Labs is a small team building the workspace we wanted for
          ourselves — fast, private, and out of the way, so the tools fade and
          the thinking comes forward.
        </p>
      </section>

      {/* MISSION */}
      <section className="section wrap" style={{ paddingTop: "40px" }}>
        <div className="prose">
          <p>
            AI tools mostly feel like demos — flashy, noisy, and quick to lose
            the thread. We wanted something that respects attention: a single
            window where models, your projects, and the things you make live
            together, and where your work stays yours.
          </p>
          <p>
            So we built Channel around three convictions. That the model should
            be a dial, not a decision you agonize over. That what you create
            should persist as something you can edit and own, not vanish into a
            transcript. And that privacy is a default, not a setting you go
            hunting for.
          </p>
          <p>
            We&apos;re a remote team spread across a few time zones, shipping in
            the open and listening closely to the people who live in the app all
            day.
          </p>
        </div>
      </section>

      {/* TEAM PHOTO */}
      <section className="section wrap" style={{ paddingTop: "0" }}>
        <div className="prose" style={{ maxWidth: "920px" }}>
          <ImageSlot name="team-photo" className="about-slot" aspect="16 / 7" />
        </div>
      </section>

      {/* STATS */}
      <section className="section wrap" style={{ paddingTop: "0" }}>
        <div className="stats">
          <div className="stat">
            <div className="sv">2024</div>
            <div className="sl">Founded</div>
          </div>
          <div className="stat">
            <div className="sv">18</div>
            <div className="sl">People</div>
          </div>
          <div className="stat">
            <div className="sv">90+</div>
            <div className="sl">Countries using Channel</div>
          </div>
          <div className="stat">
            <div className="sv">100%</div>
            <div className="sl">Remote</div>
          </div>
        </div>
      </section>

      {/* PRINCIPLES */}
      <section className="section wrap">
        <div className="section-head">
          <div className="eyebrow">Principles</div>
          <h2>What we hold to.</h2>
        </div>
        <div className="features">
          <div className="feature">
            <h3>Attention is sacred</h3>
            <p>
              Every pixel earns its place. We cut before we add, and we never
              ship noise to look busy.
            </p>
          </div>
          <div className="feature">
            <h3>Your data is yours</h3>
            <p>
              Local-first by default. Nothing trains on your work, and you
              control what leaves your machine.
            </p>
          </div>
          <div className="feature">
            <h3>Ship in the open</h3>
            <p>
              We build with the people who use Channel, in public, and we say
              what&apos;s mock and what&apos;s real.
            </p>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="section wrap">
        <div className="cta-band">
          <h2>Come build with us.</h2>
          <p>We&apos;re hiring across engineering, design, and go-to-market.</p>
          <div className="hero-cta" style={{ marginTop: "0" }}>
            <Link className="btn btn-primary btn-lg" to="/careers">
              See open roles
            </Link>
            <Link className="btn btn-ghost btn-lg" to="/app">
              Try Channel
            </Link>
          </div>
        </div>
      </section>
    </SiteLayout>
  );
}
