// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import SiteLayout from "../SiteLayout.jsx";

const CYCLE_KEY = "channel-pricing-cycle";

function readCycle() {
  try {
    const v = localStorage.getItem(CYCLE_KEY);
    return v === "annual" ? "annual" : "monthly";
  } catch {
    return "monthly";
  }
}

const TIERS = [
  {
    name: "Free",
    desc: "Everything you need to get a feel for Channel.",
    monthly: "$0",
    annual: "$0",
    per: "/forever",
    cta: "Get started",
    to: "/app",
    style: "ghost",
    popular: false,
    feats: [
      "Claude Sonnet 4.6 & Haiku 4.5",
      "Daily message limit",
      "3 projects",
      "Web & desktop apps",
      "File & image uploads",
    ],
  },
  {
    name: "Pro",
    desc: "For everyday work with the full model lineup.",
    monthly: "$20",
    annual: "$16",
    per: "/mo",
    cta: "Start Pro trial",
    to: "/app",
    style: "primary",
    popular: true,
    feats: [
      "Everything in Free",
      "Claude Opus 4.8 + High effort",
      "5× higher usage limits",
      "Unlimited projects & artifacts",
      "Connect data sources",
      "Priority during peak hours",
    ],
  },
  {
    name: "Max",
    desc: "For power users who live in the app all day.",
    monthly: "$100",
    annual: "$80",
    per: "/mo",
    cta: "Go Max",
    to: "/app",
    style: "ghost",
    popular: false,
    feats: [
      "Everything in Pro",
      "20× usage & Max reasoning effort",
      "Early access to new models",
      "Bring-your-own API keys",
      "Extended context windows",
    ],
  },
  {
    name: "Team",
    desc: "Shared projects and central billing for your group.",
    monthly: "$30",
    annual: "$24",
    per: "/seat/mo",
    cta: "Contact sales",
    to: "#",
    style: "ghost",
    popular: false,
    feats: [
      "Everything in Max",
      "Shared team projects",
      "Central admin & billing",
      "SSO & SCIM",
      "No-training data guarantee",
    ],
  },
];

/**
 * Marketing Pricing page with monthly/annual toggle. Translated from
 * pricing.src.html; the prototype's inline JS toggle is replaced with
 * React useState + useEffect localStorage round-trip per spec line 215.
 */
export default function Pricing() {
  const [cycle, setCycle] = useState(() => readCycle());

  useEffect(() => {
    try { localStorage.setItem(CYCLE_KEY, cycle); } catch { /* private mode */ }
  }, [cycle]);

  function priceFor(tier) {
    return cycle === "annual" ? tier.annual : tier.monthly;
  }

  return (
    <SiteLayout>
      <section className="price-head wrap">
        <div className="eyebrow">Pricing</div>
        <h1>Simple, honest pricing.</h1>
        <p>Start free. Upgrade when Channel becomes part of how you work. Cancel anytime.</p>
        <div className="billing-toggle">
          <button
            type="button"
            className={cycle === "monthly" ? "on" : ""}
            onClick={() => setCycle("monthly")}
          >
            Monthly
          </button>
          <button
            type="button"
            className={cycle === "annual" ? "on" : ""}
            onClick={() => setCycle("annual")}
          >
            Annual <span className="save">−20%</span>
          </button>
        </div>
      </section>

      <section className="wrap">
        <div className="tiers">
          {TIERS.map((tier) => (
            <div key={tier.name} className={`tier${tier.popular ? " feat" : ""}`}>
              {tier.popular && <span className="badge-feat">Most popular</span>}
              <div className="tname">{tier.name}</div>
              <div className="tdesc">{tier.desc}</div>
              <div className="price">
                <span className="amt">{priceFor(tier)}</span>
                <span className="per">{tier.per}</span>
              </div>
              {tier.to === "#" ? (
                <a className={`btn btn-${tier.style} tbtn`} href="#">{tier.cta}</a>
              ) : (
                <Link className={`btn btn-${tier.style} tbtn`} to={tier.to}>{tier.cta}</Link>
              )}
              <ul className="feats">
                {tier.feats.map((feat) => (
                  <li key={feat}><span className="ck">✓</span> {feat}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      <section className="section wrap">
        <div className="section-head">
          <div className="eyebrow">Questions</div>
          <h2>Good to know.</h2>
        </div>
        <div className="faq">
          <div className="faq-item">
            <h3>What counts as a message?</h3>
            <p>Each prompt you send to a model. Heavier models and higher reasoning effort use more of your allowance — Channel always shows what a message will cost before you send it.</p>
          </div>
          <div className="faq-item">
            <h3>Can I use my own API keys?</h3>
            <p>Yes. On Max and Team you can bring your own keys and route requests through your own provider accounts, with usage tracked right in Customize.</p>
          </div>
          <div className="faq-item">
            <h3>Is my data used for training?</h3>
            <p>Never. Your conversations, files, and artifacts are yours. The desktop app is local-first, and Team plans include a contractual no-training guarantee.</p>
          </div>
          <div className="faq-item">
            <h3>Can I switch plans later?</h3>
            <p>Anytime, in one click. Upgrades take effect immediately and downgrades apply at the end of your billing period. No lock-in, no cancellation fees.</p>
          </div>
        </div>
      </section>

      <section className="section wrap">
        <div className="cta-band">
          <h2>Try Channel free today.</h2>
          <p>Start on the free plan and upgrade only when you&apos;re ready.</p>
          <div className="hero-cta" style={{ marginTop: 0 }}>
            <Link className="btn btn-primary btn-lg" to="/app">Open Channel</Link>
            <a className="btn btn-ghost btn-lg" href="#download">Download app</a>
          </div>
        </div>
      </section>
    </SiteLayout>
  );
}
