// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Link } from "react-router-dom";
import SiteLayout from "../SiteLayout.jsx";

/**
 * Marketing Download page. Download URLs resolve per environment using
 * ``VITE_RELEASE_CHANNEL`` — ADR-0010 codifies the two-channel split
 * (dev env → ``dev`` channel, prod env → ``latest``) and CI passes the
 * matching value into the SPA build per branch.
 *
 * Two release pipelines feed two URL surfaces:
 *
 *   - ``publish-desktop-mac`` (sub-project B, signed + notarised): uploads
 *     the macOS .dmg to S3 at ``/updates/<channel>/Channel-mac.dmg`` on
 *     every push to main/development, AND to the GitHub release on push
 *     to main. So the SIGNED macOS .dmg for ``latest`` lives at GH; the
 *     SIGNED macOS .dmg for ``dev`` lives at S3.
 *   - ``publish-dev-artifacts`` (older, unsigned matrix builds): uploads
 *     all-platform unsigned artefacts to the GitHub ``dev`` pre-release
 *     tag on every push to development. Win/Linux only this round (no
 *     signing flow yet); the macOS .dmg here would be Gatekeeper-rejected
 *     on Sequoia, so we deliberately do NOT link to it.
 *
 * URL routing per platform:
 *   - macOS .dmg:
 *     - ``latest`` channel → GH ``releases/latest/download/`` (signed)
 *     - any other channel  → S3 ``channel-<channel>.warlordofmars.net``
 *                            (signed)
 *   - Windows .exe + Linux artefacts (unsigned, this round):
 *     - ``latest`` channel → GH ``releases/latest/download/``
 *     - any other channel  → GH ``releases/download/<tag>/``
 *
 * When ``VITE_RELEASE_CHANNEL`` is unset, the default is ``latest`` —
 * preserves the prod behaviour without needing the env var set on the
 * prod build.
 */
// Use `||` (not `??`) so an empty-string ``VITE_RELEASE_CHANNEL=``
// (a common CI foot-gun) falls back to ``"latest"`` rather than
// producing ``/releases/download//<asset>`` (double-slash, 404) or
// the ``channel-.warlordofmars.net`` dangling-hyphen NXDOMAIN form.
const CHANNEL = import.meta.env.VITE_RELEASE_CHANNEL || "latest";

// GitHub-release base for unsigned Win/Linux artefacts (and the SIGNED
// macOS .dmg on ``latest`` only — see MAC_DMG_URL below).
const GH_RELEASE_BASE =
  CHANNEL === "latest"
    ? "https://github.com/warlordofmars/channel/releases/latest/download"
    : `https://github.com/warlordofmars/channel/releases/download/${CHANNEL}`;

// macOS .dmg URL routes differently per channel because the SIGNED dmg
// for non-prod channels lives on S3, not GitHub. The GitHub ``dev``
// pre-release tag is populated by ``publish-dev-artifacts`` from the
// UNSIGNED matrix builds — clicking that would give Sequoia testers a
// Gatekeeper-rejected bundle. publish-desktop-mac uploads the signed,
// notarised, stapled .dmg to ``/updates/<channel>/`` on the
// channel-scoped CloudFront distribution.
const MAC_DMG_URL =
  CHANNEL === "latest"
    ? `${GH_RELEASE_BASE}/Channel-mac.dmg`
    : `https://channel-${CHANNEL}.warlordofmars.net/updates/${CHANNEL}/Channel-mac.dmg`;

export default function Download() {
  return (
    <SiteLayout>
      <section className="page-head wrap">
        <div className="eyebrow">Download</div>
        <h1>Get Channel on your desktop.</h1>
        <p>
          Native apps for macOS, Windows, and Linux — with global hotkeys,
          offline drafts, and a window that&apos;s always a keystroke away. Or
          just open it in your browser.
        </p>
      </section>

      <section className="section wrap" style={{ paddingTop: "44px" }}>
        <div className="dl-grid">
          <div className="dl-card feat">
            <div className="dlic">
              <svg width="34" height="34" viewBox="0 0 24 24" fill="currentColor">
                <path d="M16.4 12.6c0-2.2 1.8-3.3 1.9-3.3-1-1.5-2.6-1.7-3.2-1.7-1.4-.1-2.6.8-3.3.8-.7 0-1.7-.8-2.8-.8-1.4 0-2.7.8-3.5 2.1-1.5 2.6-.4 6.4 1 8.5.7 1 1.5 2.2 2.6 2.1 1-.04 1.4-.7 2.7-.7 1.3 0 1.6.7 2.7.6 1.1-.02 1.8-1 2.5-2 .8-1.2 1.1-2.3 1.1-2.3s-2.2-.8-2.2-3.3zM14.3 6c.6-.7 1-1.7.9-2.7-.9.04-1.9.6-2.5 1.3-.5.6-1 1.6-.9 2.6 1 .08 2-.5 2.5-1.2z" />
              </svg>
            </div>
            <h3>macOS</h3>
            <div className="dlv">Universal · macOS 14+</div>
            <a className="btn btn-primary" href={MAC_DMG_URL} style={{ width: "100%" }}>
              Download .dmg
            </a>
          </div>

          <div className="dl-card">
            <div className="dlic">
              <svg width="30" height="30" viewBox="0 0 24 24" fill="currentColor">
                <path d="M3 5.5l7.5-1v7.2H3zM11.5 4.3L21 3v8.7h-9.5zM3 12.7h7.5V20L3 18.8zM11.5 12.7H21V21l-9.5-1.3z" />
              </svg>
            </div>
            <h3>Windows</h3>
            <div className="dlv">Windows 10 · 11</div>
            <a className="btn btn-ghost" href={`${GH_RELEASE_BASE}/Channel-Setup.exe`} style={{ width: "100%" }}>
              Download .exe
            </a>
          </div>

          <div className="dl-card">
            <div className="dlic">
              <svg
                width="30"
                height="30"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M9 4c-1 1.5-1 4 0 6-1.5 1.5-3 4-3 7 0 1.5 1.5 2 3 2h6c1.5 0 3-.5 3-2 0-3-1.5-5.5-3-7 1-2 1-4.5 0-6-.8-1.2-2-1.5-3-1.5S9.8 2.8 9 4z" />
                <path d="M10 9h.01M14 9h.01" />
              </svg>
            </div>
            <h3>Linux</h3>
            <div className="dlv">AppImage · .deb · .rpm</div>
            <a className="btn btn-ghost" href={`${GH_RELEASE_BASE}/Channel-linux.AppImage`} style={{ width: "100%" }}>
              Download AppImage
            </a>
            <div className="dl-alt">
              <a href={`${GH_RELEASE_BASE}/Channel-linux.deb`}>.deb</a>
              {" · "}
              <a href={`${GH_RELEASE_BASE}/Channel-linux.rpm`}>.rpm</a>
            </div>
          </div>
        </div>

        <div
          style={{
            textAlign: "center",
            marginTop: "24px",
            fontSize: "14px",
            color: "var(--ink-soft)",
          }}
        >
          Prefer the browser?{" "}
          <Link
            to="/app"
            style={{
              color: "var(--accent-ink)",
              fontWeight: 600,
              textDecoration: "none",
            }}
          >
            Open Channel on the web →
          </Link>
        </div>
      </section>

      <section className="section wrap" style={{ paddingTop: 0 }}>
        <div className="dl-req prose">
          <h2>System requirements</h2>
          <h3>macOS</h3>
          <ul>
            <li>macOS 14 Sonoma or later</li>
            <li>Apple Silicon or Intel (universal build)</li>
            <li>~300 MB disk space</li>
          </ul>
          <h3>Windows</h3>
          <ul>
            <li>Windows 10 (64-bit) or Windows 11</li>
            <li>~350 MB disk space</li>
          </ul>
          <h3>Linux</h3>
          <ul>
            <li>Ubuntu 22.04+, Fedora 39+, or equivalent</li>
            <li>.deb, .rpm, and AppImage builds available</li>
          </ul>
          <p>
            macOS auto-updates in the background — you&apos;ll see a &ldquo;Relaunch
            to update&rdquo; prompt in the sidebar when a new build is ready. Windows and
            Linux: re-download from this page when a new build lands.
          </p>
        </div>
      </section>

      <section className="section wrap">
        <div className="cta-band">
          <h2>Sign in once, sync everywhere.</h2>
          <p>Your projects, chats, and artifacts follow you across web and desktop.</p>
          <div className="hero-cta" style={{ marginTop: 0 }}>
            <Link className="btn btn-primary btn-lg" to="/app">
              Sign in with Google
            </Link>
            <Link className="btn btn-ghost btn-lg" to="/pricing">
              See pricing
            </Link>
          </div>
        </div>
      </section>
    </SiteLayout>
  );
}
