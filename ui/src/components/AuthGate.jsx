// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { ensureAccessToken } from "../api.js";
import { isTokenValid, readToken } from "../lib/auth.js";

const LOGIN_ROUTE = "/app/login";

/**
 * How long to hold before falling through to the login page anyway.
 *
 * Without this, a `/auth/refresh` that never answers — a stalled
 * connection on flaky mobile, which is precisely the failure class the
 * 401-only rule enumerates, and precisely the platform #520 was reported
 * on — would hold the empty state until the browser's own fetch timeout,
 * a limit measured in minutes and not guaranteed to exist at all. That
 * would trade an unwanted bounce for a blank screen, which is worse:
 * before #520 the user at least reached a login page they could act on.
 *
 * Generous rather than tight, because the alternative it defers is a
 * full Google round trip: a renewal normally lands well inside a second,
 * so anything still outstanding at eight is not about to arrive.
 *
 * Falling through does **not** cancel the renewal. The promise belongs
 * to `api.js`'s single-flight slot and is shared with every concurrent
 * API call, so aborting it here would sabotage them; it runs on, and if
 * it was a 401 the session still ends by the one path allowed to end it.
 */
const RENEWAL_HOLD_MS = 8_000;

/**
 * The neutral hold rendered *only* while a renewal is genuinely in
 * flight.
 *
 * Deliberately empty and canvas-coloured: this stands in for the whole
 * app shell for the length of one `/auth/refresh` round trip, so a
 * spinner would draw the eye to a wait most users never see, and any
 * content would have to be unmounted again a moment later. What it must
 * not do is flash — see the ordering note in {@link AuthGate}.
 */
function SessionPending() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Restoring your session"
      data-testid="auth-pending"
      style={{ minHeight: "100vh", background: "var(--canvas)" }}
    />
  );
}

/**
 * Wraps every `/app/*` route: renders the children, holds for one token
 * renewal, or redirects to `/app/login`.
 *
 * NB: this is a client-side gate only. The mgmt JWT itself is verified
 * by the backend on every `/api/*` request; this is UX, not security.
 *
 * ## Why it awaits a refresh (#520)
 *
 * This gate is evaluated on **every render**, not once per page load —
 * `AppLayout` mounts it above the `Outlet`, so every in-app navigation
 * re-runs it. Until #520 it read the token and redirected synchronously
 * whenever that token was expired, without ever attempting
 * `/auth/refresh` (which fires only from `api.js`'s `authHeader`, i.e.
 * on an actual API call). The visible symptom was an hourly bounce to
 * Google in a *live* session: the access token quietly expired at the
 * 1h mark, the user tapped something that navigated, and the gate
 * redirected before any request could renew. Dev CloudWatch over a
 * six-hour window recorded zero `/api/*` 401s and two successful
 * cookie-transport refreshes — the server was refusing nothing; the
 * client was giving up locally.
 *
 * Four properties are load-bearing:
 *
 * 1. **One refresh path.** The renewal goes through `api.js`'s
 *    {@link ensureAccessToken}, which owns the single-flight promise.
 *    #290 hard-rotates on every use and reads a re-presented token as an
 *    OAuth 2.1 reuse breach that revokes the whole device family, so a
 *    rival rotation started here would sign the user out *everywhere* on
 *    the exact path built to keep them in.
 * 2. **One attempt per episode, not per render.** `settledFor` records
 *    the token an attempt has already resolved for, so re-renders and
 *    the redirect render itself cannot re-arm it. Single-flight covers
 *    concurrency; this covers the sequential navigation-heavy minute.
 *    A remount (the user comes back to `/app/*` later) is a new episode
 *    and may try again — bounded by the 30s cooldown `api.js` arms after
 *    a refusal.
 * 3. **Only a 401 ends the session.** That verdict is not re-derived
 *    here: `ensureAccessToken` ends the session itself when — and only
 *    when — `/auth/refresh` answered 401 *and* the stored token is
 *    unusable. Offline, DNS, 5xx, a malformed body, and #294's 429 all
 *    leave local state intact, and this gate then redirects without
 *    destroying anything, so a later attempt can still recover. On the
 *    401 path that does mean two things aim at `/app/login` — the hard
 *    `location.assign` inside `endSession` and the `Navigate` below,
 *    which renders once storage comes back empty. They agree on the
 *    destination, and keeping `endSession` as the sole give-up site is
 *    what leaves #483's hard-reload-vs-soft-redirect question one place
 *    to change; re-deriving the verdict here to skip one of them would
 *    put the 401-only rule in two places instead.
 * 4. **No flash on the common path.** A usable token returns the
 *    children on the first render with no state, no effect and no
 *    pending markup, which matters because "every render" includes every
 *    navigation.
 *
 * And one property that is not a constraint but a consequence of adding
 * a hold at all: the hold is **bounded** (`RENEWAL_HOLD_MS`), so a
 * renewal that never answers falls through to the login page instead of
 * holding an empty screen at the mercy of the browser's own fetch
 * timeout. Before #520 the user at least reached a page they could act
 * on; that must not regress.
 */
export default function AuthGate({ children }) {
  const token = readToken();
  const authed = isTokenValid(token);
  // A stored-but-unusable token is the only shape worth a round trip. No
  // token at all means either a visitor who never signed in or a session
  // cleared on purpose; `api.js` reads that same condition as "no
  // session" and would return without issuing a request, so asking would
  // cost a render of the pending hold to learn nothing.
  const stale = token !== "" && !authed;
  const [settledFor, setSettledFor] = useState(null);
  const renewing = stale && settledFor !== token;

  useEffect(() => {
    if (!renewing) return undefined;
    let live = true;
    // Named rather than inline so the unmount branch is its own v8
    // counter. `ensureAccessToken` resolves on both outcomes and reports
    // the result through storage, so one settler serves both arms of the
    // `then` — including the case where it ended the session itself.
    // Whichever of the renewal and the hold timer arrives first wins;
    // the second is a no-op, since re-recording the same token is a
    // state write React bails out of rather than a re-render.
    function settle() {
      if (live) setSettledFor(token);
    }
    const holdTimer = setTimeout(settle, RENEWAL_HOLD_MS);
    function abandon() {
      live = false;
      clearTimeout(holdTimer);
    }
    ensureAccessToken().then(settle, settle);
    return abandon;
  }, [renewing, token]);

  // Order matters: the usable-token case returns before anything can
  // render a pending state, so the overwhelmingly common render is
  // byte-for-byte what it was before #520.
  if (authed) return children;
  if (renewing) return <SessionPending />;
  return <Navigate to={LOGIN_ROUTE} replace />;
}
