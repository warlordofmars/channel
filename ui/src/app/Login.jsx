// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import ChannelMark from "../components/ChannelMark.jsx";
import GoogleG from "./GoogleG.jsx";

/**
 * Centered Google sign-in card. The prototype simulates auth with a 1.2s
 * timeout; we redirect to the real /auth/login endpoint (wired in Phase 5
 * — Google OAuth issues a mgmt JWT, then redirects to /app on success).
 * Translated from design-sources/app/shell.jsx `Login` function. Desktop
 * traffic-lights frame branch dropped per spec §"Window frames".
 */
export default function Login() {
  return (
    <div className="auth">
      <div className="auth-card">
        <span className="mk"><ChannelMark size={46} /></span>
        <h1>Sign in to Channel</h1>
        <p>Your workspace for thinking with AI.</p>
        <button
          type="button"
          className="google-btn"
          onClick={() => globalThis.location.assign("/auth/login")}
        >
          <GoogleG size={19} /> Continue with Google
        </button>
        <div className="auth-fine">
          By continuing, you agree to Channel&apos;s <a href="/terms">Terms</a> and <a href="/privacy">Privacy Policy</a>.
        </div>
      </div>
      <div className="auth-foot">
        New here? Your account is created automatically on first sign-in.
      </div>
    </div>
  );
}
