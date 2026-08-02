// Copyright (c) 2026 John Carter. All rights reserved.

/**
 * Management-session storage for the SPA (#295, epic #241).
 *
 * The stored value is a JSON `{access_token, expires_at}` envelope, not
 * a bare JWT. `expires_at` is a millisecond epoch — the deadline
 * `api.js`'s silent-refresh wrapper compares against so it can renew
 * *before* the access token dies rather than after a request has
 * already 401'd.
 *
 * **The refresh token is deliberately absent from this module.** On the
 * web it lives in the `channel_refresh` HttpOnly cookie
 * (`src/channel/auth/refresh.py`), which JavaScript cannot read — that
 * is the entire point of the transport. Desktop carries its own copy
 * through the loopback redirect and will persist it in the OS keychain
 * via `safeStorage` (#297). Neither belongs in localStorage, so
 * `saveSession` takes an access token and nothing else.
 *
 * Key migration (#260, folded into #295)
 * --------------------------------------
 * The key was renamed `starter_mgmt_token` → `channel_mgmt_token` as
 * part of the wider template de-branding. Reads accept **either** key,
 * so a session created before the rename survives the deploy; every
 * write goes to the new key and deletes the legacy one, so a browser
 * self-migrates on its first save without anyone being signed out.
 *
 * The read order — new key first, legacy second — is only safe because
 * nothing writes the legacy key any more: `/auth/callback`'s
 * login-completion page moved to the new key in the same change
 * (`_html_redirect` in `src/channel/auth/mgmt_auth.py`). Were a writer
 * of the legacy key to come back, a fresh login could land under the
 * legacy key while a stale value sat under the new one, and this
 * function would return the stale session.
 *
 * Dropping `LEGACY_TOKEN_KEY` is a deliberate follow-up rather than
 * part of this change: it can go one release after this ships, by
 * which point every live session has re-saved onto the new key.
 */

/** Canonical localStorage key for the management session. */
export const TOKEN_KEY = "channel_mgmt_token";

/** Pre-#260 key. Read-only compatibility shim — never written. */
export const LEGACY_TOKEN_KEY = "starter_mgmt_token";

/** The shape `loadSession` returns when nothing usable is stored. */
const NO_SESSION = { access_token: "", expires_at: 0 };

/**
 * Structural shape a value must have to be persisted as an access token:
 * three non-empty base64/base64url segments.
 *
 * `saveSession` writes values that originate off-device — a
 * `POST /auth/refresh` response body, and the token the desktop loopback
 * hands back — so the write is only as trustworthy as its source. This
 * check is the boundary that keeps a malformed or hostile response from
 * being persisted as a credential and replayed on every subsequent
 * request. It is a *structural* check, not an authenticity one: the
 * signature is verified server-side on every call, and the SPA has no
 * signing secret with which to do better.
 *
 * Both alphabets are admitted (`+/=` as well as `-_`) because a JWT is
 * base64url but test fixtures and some encoders emit padded standard
 * base64, and rejecting those would fail closed on well-formed input.
 */
const JWT_SHAPE = /^[A-Za-z0-9+/_=-]+\.[A-Za-z0-9+/_=-]+\.[A-Za-z0-9+/_=-]+$/;

export function parseToken(token) {
  if (!token) return null;
  try {
    return JSON.parse(atob(token.split(".")[1].replaceAll("-", "+").replaceAll("_", "/")));
  } catch {
    return null;
  }
}

export function isTokenValid(token) {
  const payload = parseToken(token);
  return payload ? payload.exp * 1000 > Date.now() : false;
}

/**
 * Millisecond expiry read from a JWT's own `exp` claim, or 0 if
 * unreadable.
 *
 * This is what makes the envelope backward-compatible. A value written
 * before this change is a bare JWT, and one written by the
 * login-completion page still is — the server has no business
 * duplicating the envelope's shape into a Python string template just
 * to restate a deadline the token already carries.
 */
function expiryFromToken(token) {
  const claims = parseToken(token);
  return claims?.exp ? claims.exp * 1000 : 0;
}

/**
 * Decode a raw localStorage value into the envelope shape.
 *
 * A JWT is three base64url segments, so it can never begin with `{` —
 * that one character separates the enveloped form from a bare token
 * without attempting a parse on every read.
 */
function decodeStored(raw) {
  if (!raw) return null;
  if (!raw.startsWith("{")) return { access_token: raw, expires_at: 0 };
  try {
    const parsed = JSON.parse(raw);
    return {
      access_token: parsed.access_token ?? "",
      expires_at: Number(parsed.expires_at) || 0,
    };
  } catch {
    // Corrupt envelope — read as "no session" rather than throwing on
    // every render. There is nothing here to salvage, and a signed-out
    // user can sign back in; a thrown parse error just white-screens.
    return null;
  }
}

/**
 * Read the stored management session as `{access_token, expires_at}`.
 *
 * Always returns the full shape so callers can destructure without
 * guarding. `expires_at` falls back to the access token's own `exp`
 * claim when the envelope carries none, and is 0 when neither is
 * readable — which reads as "already expired", so the refresh wrapper
 * attempts a renewal instead of trusting an undatable token.
 */
export function loadSession() {
  const raw = localStorage.getItem(TOKEN_KEY) ?? localStorage.getItem(LEGACY_TOKEN_KEY);
  const stored = decodeStored(raw);
  if (!stored?.access_token) return NO_SESSION;
  return {
    access_token: stored.access_token,
    expires_at: stored.expires_at || expiryFromToken(stored.access_token),
  };
}

/** The stored access token, or `""` when there is no session. */
export function readToken() {
  return loadSession().access_token;
}

/**
 * Persist an access token, replacing whatever was there.
 *
 * `expiresAt` is a millisecond epoch; omit it and the token's own `exp`
 * claim is used. `POST /auth/refresh` answers with `expires_in`
 * (seconds), so its caller passes a deadline computed from the *local*
 * clock — which keeps the renewal schedule anchored to the same clock
 * the comparison uses rather than to the server's.
 *
 * Deleting the legacy key here is what makes the #260 migration
 * one-way: after any write there is exactly one key in the jar.
 *
 * **Throws** on anything that isn't structurally a JWT (see
 * `JWT_SHAPE`), storing nothing. Both callers already handle a throw
 * correctly, which is why this refuses rather than returning a status
 * nobody would check: in `api.js` the rejection lands on the
 * silent-refresh failure path (fall back to the existing token, arm the
 * cooldown), and in `Login.jsx` it lands in the existing catch that
 * renders "Login failed."
 */
export function saveSession(accessToken, expiresAt = 0) {
  if (typeof accessToken !== "string" || !JWT_SHAPE.test(accessToken)) {
    throw new TypeError("saveSession: refusing to store a malformed access token");
  }
  localStorage.setItem(
    TOKEN_KEY,
    JSON.stringify({
      access_token: accessToken,
      expires_at: Number(expiresAt) || expiryFromToken(accessToken),
    }),
  );
  localStorage.removeItem(LEGACY_TOKEN_KEY);
}

/**
 * Drop the local session under both keys.
 *
 * Clearing only the current key would leave a pre-rename value behind
 * for the next `loadSession` to resurrect — a signed-out user silently
 * signed back in.
 *
 * Local only: the refresh cookie and the server-side token family are
 * retired by `POST /auth/logout`, which the sign-out path calls
 * separately.
 */
export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(LEGACY_TOKEN_KEY);
}
