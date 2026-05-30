// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import SiteLayout from "../SiteLayout.jsx";

/**
 * Marketing Careers page. Translated from pages/careers.body.html per
 * §Translation rules.
 */

function ArrowIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

export default function Careers() {
  return (
    <SiteLayout>
      {/* PAGE HEAD */}
      <section className="page-head wrap">
        <div className="eyebrow">Careers</div>
        <h1>Build the workspace for thinking.</h1>
        <p>
          We&apos;re a small, remote team that ships in the open. If you care
          about craft, attention, and getting tools out of people&apos;s way,
          we&apos;d love to talk.
        </p>
      </section>

      {/* OPEN ROLES */}
      <section className="section wrap" style={{ paddingTop: "40px" }}>
        <div className="roles">
          <div className="dept-h">Engineering</div>
          <a className="role-row" href="#">
            <div>
              <div className="rt">Senior Product Engineer</div>
              <div className="rm">Remote · Full-time</div>
            </div>
            <span className="rgo">
              <ArrowIcon />
            </span>
          </a>
          <a className="role-row" href="#">
            <div>
              <div className="rt">Desktop Engineer (Electron)</div>
              <div className="rm">Remote · Full-time</div>
            </div>
            <span className="rgo">
              <ArrowIcon />
            </span>
          </a>
          <a className="role-row" href="#">
            <div>
              <div className="rt">Infrastructure Engineer</div>
              <div className="rm">Remote · Full-time</div>
            </div>
            <span className="rgo">
              <ArrowIcon />
            </span>
          </a>

          <div className="dept-h">Design</div>
          <a className="role-row" href="#">
            <div>
              <div className="rt">Product Designer</div>
              <div className="rm">Remote · Full-time</div>
            </div>
            <span className="rgo">
              <ArrowIcon />
            </span>
          </a>

          <div className="dept-h">Go-to-market</div>
          <a className="role-row" href="#">
            <div>
              <div className="rt">Developer Advocate</div>
              <div className="rm">Remote · Full-time</div>
            </div>
            <span className="rgo">
              <ArrowIcon />
            </span>
          </a>
          <a className="role-row" href="#">
            <div>
              <div className="rt">Founding Account Executive</div>
              <div className="rm">Remote · Full-time</div>
            </div>
            <span className="rgo">
              <ArrowIcon />
            </span>
          </a>
        </div>
      </section>

      {/* WHY CHANNEL LABS */}
      <section className="section wrap" style={{ paddingTop: "0" }}>
        <div className="section-head">
          <div className="eyebrow">Why Channel Labs</div>
          <h2>How we work.</h2>
        </div>
        <div className="features">
          <div className="feature">
            <h3>Remote &amp; async</h3>
            <p>
              Work where you do your best thinking. We optimize for deep focus
              over meetings.
            </p>
          </div>
          <div className="feature">
            <h3>Real ownership</h3>
            <p>
              Small team, big surface area. You&apos;ll own meaningful pieces of
              the product end to end.
            </p>
          </div>
          <div className="feature">
            <h3>Generous &amp; honest</h3>
            <p>
              Competitive pay, real equity, and a culture that says what&apos;s
              true — to each other and to users.
            </p>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="section wrap">
        <div className="cta-band">
          <h2>Don&apos;t see your role?</h2>
          <p>If you&apos;d be a fit for the team, tell us what you&apos;d want to build.</p>
          <div className="hero-cta" style={{ marginTop: "0" }}>
            <a className="btn btn-primary btn-lg" href="#">
              Get in touch
            </a>
          </div>
        </div>
      </section>
    </SiteLayout>
  );
}
