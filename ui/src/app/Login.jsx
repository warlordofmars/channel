// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";
import GoogleG from "./GoogleG.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";

/**
 * Centered Google sign-in card. The prototype simulates auth with a 1.2s
 * timeout; we redirect to the real /auth/login endpoint (wired in Phase 5
 * — Google OAuth issues a mgmt JWT, then redirects to /app on success).
 * Translated from design-sources/app/shell.jsx `Login` function. Desktop
 * traffic-lights frame branch dropped per spec §"Window frames".
 *
 * Wrapped in `.stage.full > .win` so `.auth`'s `flex: 1` resolves against a
 * full-viewport flex column and the card actually centers vertically — the
 * prototype rendered Login inside `.win`, so we mirror that here.
 *
 * Owns `data-theme = theme` (the chat-app theme) while mounted, matching
 * Shell's behaviour — so /app/login renders in the app theme regardless of
 * what marketing left behind.
 *
 * When `window.channelDesktop?.isDesktop` is truthy (Electron renderer), the
 * button calls `window.channelDesktop.login()` directly via the contextBridge
 * IPC instead of redirecting to /auth/login. On success the returned JWT is
 * stored in localStorage and the user is navigated to /app.
 */
export default function Login() {
  const { theme } = useChannelPrefs();
  const navigate = useNavigate();
  const [error, setError] = useState(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  // The SPA isn't SSR'd; window is always defined in both Electron's
  // sandboxed renderer and any browser-side test environment.
  const desktop = window.channelDesktop;

  async function handleDesktopLogin() {
    setError(null);
    try {
      const token = await desktop.login();
      localStorage.setItem("starter_mgmt_token", token);
      navigate("/app");
    } catch (err) {
      const code = err?.message;
      if (code === "USER_CANCELLED") {
        setError("Login cancelled.");
      } else if (code === "TIMEOUT") {
        setError("Login timed out. Please try again.");
      } else {
        setError("Login failed.");
      }
    }
  }

  return (
    <div className="stage full">
      <div className="win">
        <div className="auth">
          <div className="auth-card">
            <span className="mk"><ChannelMark size={46} /></span>
            <h1>Sign in to Channel</h1>
            <p>Your workspace for thinking with AI.</p>
            {desktop?.isDesktop ? (
              <button
                type="button"
                className="google-btn"
                onClick={handleDesktopLogin}
              >
                <GoogleG size={19} /> Sign in with Google
              </button>
            ) : (
              <button
                type="button"
                className="google-btn"
                onClick={() => globalThis.location.assign("/auth/login")}
              >
                <GoogleG size={19} /> Continue with Google
              </button>
            )}
            {error && <div className="error">{error}</div>}
            <div className="auth-fine">
              By continuing, you agree to Channel&apos;s <a href="/terms">Terms</a> and <a href="/privacy">Privacy Policy</a>.
            </div>
          </div>
          <div className="auth-foot">
            New here? Your account is created automatically on first sign-in.
          </div>
        </div>
      </div>
    </div>
  );
}
