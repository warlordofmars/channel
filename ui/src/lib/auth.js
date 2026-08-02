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
 * **The refresh token never enters localStorage.** On the web it lives
 * in the `channel_refresh` HttpOnly cookie
 * (`src/channel/auth/refresh.py`), which JavaScript cannot read — that
 * is the entire point of the transport. Desktop has no cookie: it
 * carries its own copy through the loopback redirect (#292) and hands it
 * to the OS keychain via `safeStorage` (#297), reached through
 * `window.channelDesktop.tokenStorage`. So `saveSession` still takes an
 * access token and nothing else, and the refresh token is read and
 * written only by {@link readRefreshToken} / {@link saveRefreshToken},
 * which are no-ops in a browser.
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
 *
 * The `-` is written FIRST in each character class, where it is
 * unambiguously a literal. A trailing `-` is equally literal and was the
 * original spelling, but it read as a truncated range to a reviewer —
 * and a false negative here rejects every real base64url token and locks
 * everyone out, so the class is spelled to be unmisreadable. The
 * `accepts real base64url tokens` test pins the behaviour either way.
 */
const JWT_SHAPE = /^[-A-Za-z0-9+/_=]+\.[-A-Za-z0-9+/_=]+\.[-A-Za-z0-9+/_=]+$/;

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
 * Decode a raw localStorage value into the envelope shape, or `null` if
 * it does not yield a usable session.
 *
 * A JWT is three base64url segments, so it can never begin with `{` —
 * that one character separates the enveloped form from a bare token
 * without attempting a parse on every read.
 *
 * Returning `null` for *every* unusable shape — absent, corrupt JSON, or
 * a well-formed envelope carrying no `access_token` — is what lets
 * `loadSession` chain the two keys with `??`. An envelope that parsed
 * but held nothing would otherwise count as a hit and suppress the
 * legacy fallback.
 */
function decodeStored(raw) {
  if (!raw) return null;
  if (!raw.startsWith("{")) return { access_token: raw, expires_at: 0 };
  try {
    const parsed = JSON.parse(raw);
    if (!parsed?.access_token) return null;
    return {
      access_token: parsed.access_token,
      expires_at: Number(parsed.expires_at) || 0,
    };
  } catch {
    // Corrupt envelope — read as "nothing here" rather than throwing on
    // every render. A signed-out user can sign back in; a thrown parse
    // error just white-screens.
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
  // Falls through to the legacy key whenever the new one yields nothing
  // *usable*, not merely when it is absent. A corrupt or access-token-less
  // envelope under the new key would otherwise force a re-login while a
  // perfectly good pre-rename session sat untouched beside it — the exact
  // failure the read-both migration exists to prevent. This cannot
  // resurrect a stale session: nothing writes the legacy key any more, so
  // whatever is there predates the rename, and any successful save removes
  // it.
  const stored =
    decodeStored(localStorage.getItem(TOKEN_KEY)) ??
    decodeStored(localStorage.getItem(LEGACY_TOKEN_KEY));
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
  // Desktop's refresh token lives outside the browser jar, so clearing
  // localStorage alone would leave a 30-day credential on disk after a
  // sign-out. Fire-and-forget: `ipcRenderer.invoke` posts to the main
  // process synchronously and only the *reply* is async, so the delete
  // is already on its way even though `Sidebar.signOut` navigates in the
  // same tick. Errors are swallowed for the same reason every other
  // teardown step here is unconditional — a failed delete must not
  // prevent the visible sign-out.
  //
  // Wrapped as well as `.catch`-ed because the two failure shapes are
  // different: a bridge whose `clear` is missing or is not a function
  // throws *synchronously*, before there is a promise to attach a
  // handler to, so `.catch` alone would let it escape and abort a
  // sign-out that must always complete. `readRefreshToken` /
  // `saveRefreshToken` already cover both shapes with their own `try`;
  // this makes the third call site consistent with them.
  try {
    keychain()?.clear().catch(ignoreKeychainFailure);
  } catch {
    ignoreKeychainFailure();
  }
}

// ---- Desktop keychain (#297) ----------------------------------------------

/**
 * The Electron main process's keychain bridge, or `null` in a browser.
 *
 * Read through `globalThis` rather than `window` so this module makes no
 * assumption about a DOM global existing; in a browser or an Electron
 * renderer the two are the same object anyway. `null` — not `undefined`
 * — for both the no-Electron case and the older-preload case (a bridge
 * without `tokenStorage`), so callers have exactly one absent value to
 * test.
 */
function keychain() {
  return globalThis.channelDesktop?.tokenStorage ?? null;
}

/**
 * Named so the swallowed rejection is legible in a stack trace and
 * countable by the coverage gate (see the UI conventions note on
 * anonymous inline functions).
 */
function ignoreKeychainFailure() {
  // Nothing actionable. A keychain that cannot be reached or written
  // costs the user a re-login at the access token's next expiry, which
  // is exactly where they were before this existed — whereas letting the
  // rejection escape turns it into an unhandled promise rejection during
  // sign-out or refresh.
}

/**
 * The desktop refresh token, or `""` when there isn't one.
 *
 * `""` covers every "nothing to present" case: a browser (no bridge), a
 * desktop session that predates this change, a bypass login that minted
 * no refresh family, and an unreadable or corrupt keychain file. All of
 * them mean the same thing to `performRefresh` — send no body and let
 * the cookie transport (or the resulting 401) decide.
 */
export async function readRefreshToken() {
  const bridge = keychain();
  if (!bridge) return "";
  try {
    const stored = await bridge.read();
    return typeof stored?.refresh_token === "string" ? stored.refresh_token : "";
  } catch {
    return "";
  }
}

/**
 * Persist the desktop refresh token, or drop it when there is none.
 *
 * An empty or non-string token clears the file rather than storing a
 * placeholder: the only way to reach that state is a rotation that came
 * back without a successor, and the predecessor is already dead
 * server-side, so keeping it would leave a credential that can never
 * work again.
 *
 * **Never throws.** The caller has just consumed a refresh token whose
 * successor this is; a rejection propagating out of `performRefresh`
 * would discard a perfectly good access token that was already saved and
 * arm the refresh cooldown for no reason. Returns whether the successor
 * was stored, so the failure is still observable to a test.
 */
export async function saveRefreshToken(refreshToken) {
  const bridge = keychain();
  if (!bridge) return false;
  try {
    if (typeof refreshToken !== "string" || refreshToken === "") {
      await bridge.clear();
      return false;
    }
    await bridge.write({ refresh_token: refreshToken });
    return true;
  } catch {
    return false;
  }
}
