// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import { Loader2 } from "lucide-react";

function GoogleIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
    </svg>
  );
}

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const errorParam = new URLSearchParams(globalThis.location.search).get("error");

  function handleSignIn() {
    setLoading(true);
    globalThis.location.href = "/auth/login";
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg)]">
      <div className="bg-[var(--surface)] border border-[var(--border)] rounded-xl p-12 text-center shadow-[0_2px_16px_rgba(0,0,0,.1)] max-w-sm w-full">
        <img src="/logo.svg" alt="Channel" className="w-24 h-24 block mx-auto mb-5" />
        <p className="text-[var(--text-muted)] mb-8">
          Channel
        </p>
        <button
          onClick={handleSignIn}
          disabled={loading}
          className="w-full py-3 bg-[var(--bg)] border border-[var(--border)] rounded-lg cursor-pointer text-[15px] text-[var(--text)] flex items-center justify-center gap-2 disabled:opacity-70 disabled:cursor-default hover:opacity-90 transition-opacity"
        >
          {loading ? (
            <>
              <Loader2 size={16} className="animate-spin" />
              Redirecting…
            </>
          ) : (
            <>
              <GoogleIcon />
              Sign in with Google
            </>
          )}
        </button>
        {errorParam && (
          <p className="text-[var(--danger)] mt-3 text-sm">
            Sign in was cancelled. Please try again.
          </p>
        )}
        <a
          href="/"
          className="inline-block mt-6 text-[13px] text-[var(--text-muted)] no-underline hover:underline"
        >
          ← Back to home
        </a>
      </div>
    </div>
  );
}
