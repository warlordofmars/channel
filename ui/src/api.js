// Copyright (c) 2026 John Carter. All rights reserved.
/**
 * Channel API client — thin wrapper around fetch.
 * Token is read from localStorage via `lib/auth.js`.
 */

import {
  clearSession,
  isTokenValid,
  loadSession,
  readRefreshToken,
  readToken,
  saveRefreshToken,
  saveSession,
} from "./lib/auth.js";

const BASE = import.meta.env.VITE_API_BASE ?? "";

// ---- Silent refresh (#295, epic #241) --------------------------------------
//
// #291 cut the access token's TTL from 30 days to one hour. Without a
// client that redeems the refresh token #292 mints at login, that lands
// as an hourly forced sign-out — which is exactly what shipped between
// those two issues and this one. Every authenticated call below routes
// through `authHeader`, so renewing there covers the whole surface
// without a second place to forget.

/**
 * Renew this far ahead of expiry.
 *
 * Wide enough that an in-flight request never carries a token that
 * expires mid-transit, and that a clock a few minutes off the server's
 * still renews before the server considers the token dead.
 */
const REFRESH_SKEW_MS = 5 * 60 * 1000;

/**
 * How long to stop trying after a refresh is refused.
 *
 * Some clients legitimately have no refresh credential to present: any
 * session minted by a bypass login (which deliberately mints no refresh
 * family), a desktop session signed in before #297 persisted one, and a
 * Linux desktop whose keychain file could not be read. All of them spend
 * the last `REFRESH_SKEW_MS` of every access token in the renew branch,
 * and without a cooldown each would POST `/auth/refresh` once per API
 * call for those five minutes — straight into #294's rate limiter, to no
 * purpose.
 */
const REFRESH_COOLDOWN_MS = 30 * 1000;

/**
 * CSRF header required by `POST /auth/refresh`. The value is never
 * inspected server-side; only its non-emptiness matters (see the CSRF
 * section of `src/channel/auth/refresh.py`'s module docstring).
 */
const REFRESH_CSRF_HEADER = "X-Channel-Refresh";

const LOGIN_ROUTE = "/app/login";

/**
 * Name of the cross-document Web Lock that serialises the rotation
 * (#495). Web Locks are scoped per-origin, so every document of this
 * SPA — tab, window, installed PWA — contends for this one name.
 */
const REFRESH_LOCK_NAME = "channel:auth-refresh";

/**
 * How long a document waits for that lock before rotating anyway.
 *
 * Bounding the **wait** rather than the **hold** is deliberate. Web
 * Locks are released automatically when the holding document goes away,
 * so a closed or crashed tab strands nobody; the residual hazard is a
 * *live* holder whose `/auth/refresh` never answers — a stalled
 * connection on flaky mobile, not a refusal — which would otherwise
 * freeze every other document's API calls behind it indefinitely. #520
 * bounded `AuthGate`'s renewal hold (`RENEWAL_HOLD_MS`) at the same 8s
 * for the same reason: a stall must degrade to the pre-#495 behaviour,
 * never to a deadlock.
 *
 * The hold is deliberately *not* bounded. Releasing the lock while the
 * request is still in flight would hand the next document a token the
 * first is mid-rotation on, which is exactly the reuse breach this lock
 * exists to prevent — a self-inflicted version of the bug.
 */
const REFRESH_LOCK_WAIT_MS = 8_000;

/**
 * The one in-flight refresh, shared by every concurrent caller.
 *
 * **Single-flight is a correctness requirement, not an optimisation.**
 * #290 hard-rotates the refresh token on every use and treats a
 * re-presented token as an OAuth 2.1 reuse breach (RFC 9700 §4.14.2),
 * which revokes the entire device family. A page load fires several API
 * calls at once; if each ran its own refresh, the first would rotate the
 * token and the rest would present the now-revoked predecessor — logging
 * the user out on the exact path built to keep them signed in. Collapsing
 * them onto one promise makes that unrepresentable.
 *
 * **Per-document by construction** — two tabs are two module instances,
 * so this promise cannot span them. That is what
 * {@link refreshUnderCrossDocumentLock} adds (#495); this slot remains
 * the inner layer, and the only layer where Web Locks are unavailable.
 * Keeping both is not redundancy: the lock serialises *documents*, and
 * without this promise each of a page load's several concurrent callers
 * would still queue up behind one another for a rotation apiece.
 */
let refreshInFlight = null;

/** Epoch ms before which no refresh is attempted. See REFRESH_COOLDOWN_MS. */
let refreshBlockedUntil = 0;

/**
 * Whether the last failed refresh was refused by the server with a
 * **401** — the only status that is a verdict on the credential — as
 * opposed to unreachable. Re-derived by every attempt that resolves, so
 * it always describes the most recent real answer; it persists across
 * the cooldown window only because no attempt is made during it.
 */
let refreshRefused = false;

/**
 * Perform the actual rotation.
 *
 * **Two transports, one function.** The web SPA presents the
 * `channel_refresh` HttpOnly cookie, which JavaScript cannot read and
 * therefore cannot send explicitly — `credentials: "include"` is what
 * attaches it when `VITE_API_BASE` points the SPA at another origin
 * (same-origin deployments, CloudFront in prod and the Vite proxy in
 * dev, would send it either way). Electron has no cookie to present, so
 * it sends the token the OS keychain is holding in the request body and
 * gets the rotated successor back in the JSON body (#297). The server
 * accepts both and prefers the body (`refresh_session`).
 *
 * The branch is `readRefreshToken()` answering non-empty, not a desktop
 * feature test: a desktop session predating #297, or one minted by a
 * bypass login that deliberately mints no refresh family, has nothing to
 * send and correctly takes the bodyless path.
 */
async function performRefresh() {
  const refreshToken = await readRefreshToken();
  const headers = { [REFRESH_CSRF_HEADER]: "1" };
  if (refreshToken) headers["Content-Type"] = "application/json";
  const response = await fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    headers,
    credentials: "include",
    cache: "no-store",
    body: refreshToken ? JSON.stringify({ refresh_token: refreshToken }) : undefined,
  });
  if (!response.ok) throw new ApiError("refreshSession", response.status);
  const body = await response.json();
  // Persist the rotated refresh token BEFORE the access token. #290 hard-
  // rotates: the token just presented is already revoked server-side, so
  // the successor in this body is the session's only remaining long-lived
  // credential. `saveSession` throws on a malformed access token, and
  // doing it first would let that throw strand a session whose successor
  // was never written anywhere. No-op on the web, where the successor
  // came back as a `Set-Cookie` the browser has already stored and
  // `body.refresh_token` is absent by design.
  await saveRefreshToken(body.refresh_token);
  // `expires_in` is seconds from now; store an absolute local deadline.
  //
  // `saveSession` is the single validator for what may be persisted: it
  // throws on a missing or malformed `access_token` rather than writing
  // an unvalidated response body into browser storage, and that throw is
  // caught below as an ordinary refresh failure.
  //
  // `expires_in` absent falls through to `saveSession`'s own `exp`-claim
  // fallback rather than computing `Date.now() + 0` — which is truthy, so
  // it would win the fallback and store an already-expired deadline,
  // making the client re-rotate once per API call.
  const ttl = Number(body.expires_in);
  saveSession(body.access_token, ttl > 0 ? Date.now() + ttl * 1000 : 0);
  refreshRefused = false;
  return body.access_token;
}

/**
 * Arm the cooldown and report "no new token" to every awaiting caller.
 *
 * Records *why* it failed, because the two reasons deserve opposite
 * treatment. Only **401** is an authoritative "this credential is no
 * good" and, once the access token is also dead, ends the session.
 * Everything else — offline, DNS, TLS, 5xx, a malformed body — says
 * nothing about the credential, and destroying a recoverable session
 * over a transient blip that happened to straddle expiry is precisely
 * the class of spurious logout this whole change exists to remove.
 *
 * Deliberately `=== 401` rather than "any 4xx". `refresh_session`
 * documents exactly two client errors, and neither of the others is a
 * verdict on the credential: 403 means the CSRF header was missing (our
 * bug), and #294 is about to add **429** to this very endpoint — a rate
 * limit is the one response most likely to arrive in a burst, and
 * reading it as "you are signed out" would turn throttling into a mass
 * logout. The narrow test is what keeps that from landing silently.
 */
function onRefreshRejected(error) {
  refreshBlockedUntil = Date.now() + REFRESH_COOLDOWN_MS;
  refreshRefused = error instanceof ApiError && error.status === 401;
  return "";
}

/** Release the single-flight slot once the rotation has settled. */
function releaseRefreshSlot() {
  refreshInFlight = null;
}

/**
 * Rotate — unless another document already did it while we queued.
 *
 * Runs while this document holds {@link REFRESH_LOCK_NAME}, so the
 * session it re-reads is whatever the previous holder just wrote.
 * `localStorage` is shared across an origin's documents and written
 * synchronously, so the winner's freshly rotated access token is
 * already visible here — and reusing it is the whole point: presenting
 * the predecessor the winner has just consumed is precisely the reuse
 * breach (#290) that revokes the device family.
 *
 * The test is `ensureAccessToken`'s own skew test, so a token that
 * merely *exists* is not enough — one still inside the renewal window
 * (nobody rotated; we simply queued behind an unrelated caller) falls
 * through and rotates. No `token &&` guard is needed: `loadSession`
 * bottoms out at `expires_at: 0` for every unusable value — no session,
 * an unparseable envelope, a token with no `exp` claim — so the
 * comparison is `0 - Date.now() >= …`, and the false branch is the
 * rotating one.
 */
async function refreshUnlessAnotherDocumentAlreadyDid() {
  const { access_token: token, expires_at: expiresAt } = loadSession();
  if (expiresAt - Date.now() >= REFRESH_SKEW_MS) return token;
  return performRefresh();
}

/**
 * Serialise the rotation across every document of this origin (#495).
 *
 * The module-level {@link refreshInFlight} promise collapses concurrent
 * callers *within* a document, but two tabs are two module instances
 * sharing one cookie jar — both could rotate, and #290 reads the loser's
 * re-presented token as an OAuth 2.1 reuse breach that revokes the whole
 * device family. A Web Lock is origin-scoped, so it covers exactly the
 * set of documents that share the credential.
 *
 * **Every failure to acquire degrades to the pre-#495 behaviour, never
 * to no protection at all.** `navigator.locks` is absent in non-secure
 * contexts and in older browsers, the wait can time out against a
 * stalled holder, and `request` itself can reject. All of them still
 * rotate, which is exactly what this function replaced.
 *
 * `granted` — set as the callback's first *synchronous* statement, so
 * it cannot be `false` once a rotation has been attempted — is what
 * distinguishes "never got the lock" from "held it and the rotation
 * failed". The second must propagate to {@link onRefreshRejected} so a
 * 401 still ends the session and a 5xx still arms the cooldown;
 * retrying it here would rotate twice on every genuine failure.
 *
 * The unacquired path re-reads the session rather than rotating
 * blindly, because up to {@link REFRESH_LOCK_WAIT_MS} can have passed
 * since the caller last looked. Several documents queued behind one
 * stalled holder each escape on their own timer, and without the
 * re-read every one of them rotates; with it, a document that escapes
 * after another has already rotated reuses that result.
 *
 * It narrows the window, it does not close it — documents that began
 * waiting in the same tick share a deadline, so they escape together,
 * re-read the same stale session, and all rotate. Nothing here can fix
 * that; escaping at all is the deliberate concession to not
 * deadlocking, and reuse detection stays the authority on what follows.
 */
async function refreshUnderCrossDocumentLock() {
  const locks = globalThis.navigator?.locks;
  if (!locks) return performRefresh();

  const controller = new AbortController();
  const abandonWait = setTimeout(() => controller.abort(), REFRESH_LOCK_WAIT_MS);
  let granted = false;
  try {
    return await locks.request(REFRESH_LOCK_NAME, { signal: controller.signal }, () => {
      granted = true;
      // The wait is over, so from here the timer could only fire against a
      // lock we already hold. Per the Web Locks spec that is a no-op — an
      // `AbortSignal` drops a request that is still queued and does nothing
      // once it has been granted — but "the hold is unbounded" is a property
      // this file promises, and it should not rest on a spec footnote that
      // an implementation might read differently.
      clearTimeout(abandonWait);
      return refreshUnlessAnotherDocumentAlreadyDid();
    });
  } catch (error) {
    if (granted) throw error;
    return refreshUnlessAnotherDocumentAlreadyDid();
  } finally {
    clearTimeout(abandonWait);
  }
}

/**
 * Refresh at most once at a time; resolves to the new token or `""`.
 *
 * The `??=` read and its assignment run in the same synchronous tick, so
 * no second caller can slip between them and start a rival rotation.
 */
function refreshAccessToken() {
  refreshInFlight ??= refreshUnderCrossDocumentLock()
    .catch(onRefreshRejected)
    .finally(releaseRefreshSlot);
  return refreshInFlight;
}

/**
 * The router's `navigate`, once a router-aware component has handed it
 * over. `null` until then — see {@link setSessionEndNavigator}.
 */
let sessionEndNavigator = null;

/**
 * Register the in-app navigation {@link endSession} should use, or pass
 * `null` to unregister.
 *
 * **Why a registration seam at all (#483).** `endSession` is a plain
 * module function called from `ensureAccessToken` and from
 * `useChatStream`'s 401 branch, so it cannot call `useNavigate()` — hooks
 * only run inside a rendering component. Handing the router's `navigate`
 * *in* is what lets one module-level function perform a client-side route
 * change; the alternative (synthesising a `popstate` so the router
 * notices a `pushState`) would couple this module to the router's
 * internal history bookkeeping for no gain.
 *
 * The registered function is called as `navigate(to, options)` — React
 * Router's own signature, so `useNavigate()`'s return value can be
 * registered directly with no adapter. `replace` is used deliberately: a
 * session that has just been destroyed must not stay on the history
 * stack, or Back returns to a route the user can no longer load.
 *
 * Registration is last-write-wins and expected exactly once per router
 * instance, which is why the registrant must unregister on unmount —
 * a `navigate` belonging to an unmounted router would silently do
 * nothing, and this module cannot tell a live one from a dead one.
 *
 * Nothing is validated here on purpose. A `typeof` guard would be
 * unobservable: {@link endSession} already routes anything that is not a
 * working navigator to the same hard-navigation fallback, so the guard
 * could neither change an outcome nor be pinned by a test.
 */
export function setSessionEndNavigator(navigate) {
  sessionEndNavigator = navigate;
}

/**
 * Give up on the session: clear local state and route to the login page.
 *
 * The single place the SPA gives up on a session, so the "hard reload vs.
 * soft in-app redirect" question (#483) has exactly one site to change.
 *
 * **Soft when it can be, hard when it must be.** A registered navigator
 * routes in-app: the SPA stays mounted, so nothing re-downloads and
 * nothing repaints from scratch. `location.assign` tears the document
 * down and rebuilds it, which in a browser tab is a jarring reload and in
 * the Electron window is a white flash of unstyled HTML that also steals
 * focus from whatever app the user had switched to — the reported
 * symptom, made hourly by #291's 1h access-token TTL.
 *
 * The hard navigation stays as the fallback rather than being deleted.
 * `endSession` can fire before any component has mounted, or after the
 * registrant unmounted, and in those windows a soft redirect has nowhere
 * to go; doing nothing would strand a user whose session was just
 * destroyed on a page that can no longer load anything. Reaching the
 * login page the ugly way beats not reaching it. That is also why a
 * navigator which *throws* falls through here instead of propagating:
 * `endSession`'s callers (`ensureAccessToken`, `useChatStream`) treat it
 * as infallible, so an escaping error would both skip the redirect and
 * break the caller.
 *
 * Ordering is load-bearing and unchanged: local state is cleared BEFORE
 * either redirect, so a router-aware gate re-rendering as a synchronous
 * consequence of `navigate` already reads empty storage.
 */
export function endSession() {
  clearSession();
  if (sessionEndNavigator) {
    try {
      sessionEndNavigator(LOGIN_ROUTE, { replace: true });
      return;
    } catch {
      /* dead router — fall through to the document navigation below */
    }
  }
  globalThis.location.assign(LOGIN_ROUTE);
}

/**
 * The access token to send, renewing it first if it is about to expire.
 *
 * A refused refresh is **not** by itself a sign-out. `/auth/refresh`
 * answers 401 both for a dead token and for a client that has no
 * refresh credential at all, and that second case is the epic's
 * migration path rather than an error (see `refresh_session`'s
 * docstring): bypass logins, desktop sessions signed in before #297, and
 * any session minted before #292 all live there. Treating it as a
 * sign-out would evict
 * exactly those users five minutes *earlier* than the status quo. So
 * the still-valid token is used, and the session only ends once it is
 * genuinely unusable.
 *
 * **Exported for `AuthGate` (#520), which is the only caller outside
 * this module.** `AuthGate` used to redirect to `/app/login` the instant
 * a render saw an expired token, without ever reaching this function —
 * so an in-session navigation an hour after sign-in bounced the user to
 * Google while a live 30-day refresh cookie sat unused, and dev
 * CloudWatch recorded zero `/api/*` 401s because no request was ever
 * issued. The gate now awaits this instead of deciding for itself, which
 * is deliberately *not* the same as exporting `refreshAccessToken`: this
 * is the seam that already owns the whole policy — the pre-expiry skew,
 * the post-refusal cooldown, the single-flight collapse, and above all
 * the 401-only rule below. A second entry point into the rotation would
 * be a second place for those four to drift, and #290 punishes the
 * single-flight one by revoking the entire device family.
 *
 * Callers get back "the token to use, or `''` if there is none"; they do
 * not get, and do not need, the reason. `""` means either that there was
 * no session to begin with or that the session has just been ended here,
 * and both answer the same question the same way.
 */
export async function ensureAccessToken() {
  const { access_token: token, expires_at: expiresAt } = loadSession();
  // No session at all: let the request go out unauthenticated and 401.
  // AuthGate owns the redirect for that case; competing with it here
  // would race two navigations on a cold load. AuthGate short-circuits
  // on the same condition before it ever awaits this, so a visitor who
  // has never signed in costs no `/auth/refresh` round trip either.
  if (!token) return "";
  if (expiresAt - Date.now() >= REFRESH_SKEW_MS) return token;

  const refreshed = Date.now() < refreshBlockedUntil ? "" : await refreshAccessToken();
  if (refreshed) return refreshed;
  if (isTokenValid(token)) return token;
  if (refreshRefused) {
    endSession();
    return "";
  }
  // Unusable token, but the refresh endpoint was never reached, so the
  // refresh cookie may well still be good. Keep local state and let this
  // request fail on its own; a later attempt can still recover.
  return token;
}

async function authHeader() {
  const token = await ensureAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ---- Errors ----------------------------------------------------------------

/**
 * Error subclass for wrappers whose callers need to branch on the HTTP
 * status (#211). Carries the parsed FastAPI error body's `detail`
 * field — a string for plain HTTPExceptions, an array for Pydantic
 * validation errors — or null when the error body wasn't JSON (e.g. an
 * HTML 413 page from a proxy). The message keeps the legacy
 * `"<operation> <status>"` shape so existing callers matching on it
 * are unaffected.
 */
export class ApiError extends Error {
  constructor(operation, status, detail = null) {
    super(`${operation} ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

// ---- Chats ----------------------------------------------------------------
//
// Named-export wrappers for the chats endpoints. Task 14 (useChatList) and
// Task 15 (useChatStream) consume these as a stable contract. `streamMessage`
// returns the bare `Response` so the caller can consume `response.body` as a
// `ReadableStream`; the other four wrappers return parsed JSON (or nothing
// for the 204 PATCH response).

export async function createChat({ title = null, modelDefault = null } = {}) {
  const response = await fetch(`${BASE}/api/chats`, {
    method: "POST",
    headers: { ...(await authHeader()), "Content-Type": "application/json" },
    body: JSON.stringify({ title, model_default: modelDefault }),
  });
  if (!response.ok) throw new Error(`createChat ${response.status}`);
  return response.json();
}

export async function listChats({ limit = 50, cursor = null } = {}) {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (cursor) qs.set("cursor", cursor);
  const response = await fetch(`${BASE}/api/chats?${qs}`, {
    headers: await authHeader(),
  });
  if (!response.ok) throw new Error(`listChats ${response.status}`);
  return response.json();
}

export async function getChat(chatId, { limit = 200, before = null } = {}) {
  // Loads the NEWEST `limit` messages; `before` is the opaque cursor from
  // a prior response's `older_cursor`, paging toward the chat start (#270).
  const qs = new URLSearchParams({ limit: String(limit) });
  if (before) qs.set("before", before);
  const response = await fetch(`${BASE}/api/chats/${chatId}?${qs}`, {
    headers: await authHeader(),
  });
  if (!response.ok) throw new Error(`getChat ${response.status}`);
  return response.json();
}

export async function patchChat(chatId, { title, archived } = {}) {
  const response = await fetch(`${BASE}/api/chats/${chatId}`, {
    method: "PATCH",
    headers: { ...(await authHeader()), "Content-Type": "application/json" },
    body: JSON.stringify({ title, archived }),
  });
  if (!response.ok) throw new Error(`patchChat ${response.status}`);
}

export async function deleteChat(chatId) {
  const response = await fetch(`${BASE}/api/chats/${chatId}`, {
    method: "DELETE",
    headers: await authHeader(),
  });
  if (!response.ok) throw new Error(`deleteChat ${response.status}`);
}

export async function streamMessage(
  chatId,
  { message, model, effort, attachments, idempotencyKey, signal } = {},
) {
  const headers = { ...(await authHeader()), "Content-Type": "application/json" };
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  const response = await fetch(`${BASE}/api/chats/${chatId}/messages`, {
    method: "POST",
    headers,
    body: JSON.stringify({ message, model, effort, attachments }),
    signal,
  });
  if (!response.ok) {
    // #211: surface the status + FastAPI error detail so useChatStream
    // can map the refusal (422 validation, 413 too large, 401/403 auth)
    // to a user-safe message instead of eating it.
    let detail = null;
    try {
      detail = (await response.json()).detail ?? null;
    } catch {
      /* non-JSON error body (proxy HTML, empty) — status alone must do */
    }
    throw new ApiError("streamMessage", response.status, detail);
  }
  return response;
}

export async function listModels() {
  const response = await fetch(`${BASE}/api/models`, { headers: await authHeader() });
  if (!response.ok) throw new Error(`listModels ${response.status}`);
  return response.json();
}

export async function regenerate(chatId, { model, effort, signal } = {}) {
  const response = await fetch(`${BASE}/api/chats/${chatId}/regenerate`, {
    method: "POST",
    headers: { ...(await authHeader()), "Content-Type": "application/json" },
    body: JSON.stringify({ model, effort }),
    signal,
  });
  if (!response.ok) throw new Error(`regenerate ${response.status}`);
  return response;
}

// Per-message thumbs-up / thumbs-down feedback (issue #146).
// Overwrites any prior feedback for the same message; 204 on success,
// 404 if the chat or message is unknown. `note` is reserved for a
// future note-input modal — v1 callers always pass null.
export async function submitFeedback(chatId, msgId, { kind, note = null } = {}) {
  const response = await fetch(
    `${BASE}/api/chats/${chatId}/messages/${msgId}/feedback`,
    {
      method: "POST",
      headers: { ...(await authHeader()), "Content-Type": "application/json" },
      body: JSON.stringify({ kind, note }),
    },
  );
  if (!response.ok) throw new Error(`submitFeedback ${response.status}`);
}

// ---- Assets (#325 REST / #327 inline cards) -------------------------------
//
// Per-chat asset surface. `listChatAssets` returns the OLDEST-first card
// descriptors used to reattach inline cards on history load; the SPA
// groups them by `msg_id`. `getAssetContent` streams the raw bytes for
// the viewer (blob-URL for images, text for code/data/documents) — it
// returns the bare `Response` so the caller can read `.blob()` / the
// stored `Content-Type` header, and throws an `ApiError` carrying the
// HTTP status so callers can distinguish a gone object (404) from other
// S3 failures (502) and render a graceful state for each.

export async function listChatAssets(chatId) {
  const response = await fetch(`${BASE}/api/chats/${chatId}/assets`, {
    headers: await authHeader(),
  });
  if (!response.ok) throw new ApiError("listChatAssets", response.status);
  return response.json();
}

export async function getAssetContent(chatId, assetId) {
  const response = await fetch(
    `${BASE}/api/chats/${chatId}/assets/${assetId}/content`,
    { headers: await authHeader() },
  );
  if (!response.ok) throw new ApiError("getAssetContent", response.status);
  return response;
}

// ---- Auth ---------------------------------------------------------------
//
// POST /auth/logout records an immutable audit-log entry on the server
// (event_type=auth.logout) so a stolen-laptop scenario has a server-side
// signal for remediation. It also denies the access token's `jti` (#240)
// and revokes the presented refresh token's whole device family (#292) —
// the caller is still responsible for clearing local state. Best-effort
// by design — the SPA wraps the call in `.catch(...)` so transient API
// outages don't strand a signed-in user.
//
// `credentials: "include"` matters here: the family revoke needs the
// `channel_refresh` cookie, and without a session's refresh family being
// killed server-side, the cookie could silently re-mint access tokens
// after a "sign out". Same-origin deployments send it either way; this
// covers a cross-origin `VITE_API_BASE`.
//
// This is the ONE authenticated call that deliberately does NOT go
// through `authHeader()`. Two reasons, both load-bearing:
//
//  1. `Sidebar.signOut` fires this and then *synchronously* navigates
//     away. `authHeader()` is now async and may await a whole refresh
//     round trip, so the navigation would win the race and the request
//     would never leave the tab — silently losing the server-side family
//     revoke and the `jti` denylist write, which is the half of logout
//     that actually ends the session. Reading the token synchronously
//     keeps the `fetch` dispatched in the same tick, as it was before
//     silent refresh existed.
//  2. Rotating a token family one instant before revoking it is pure
//     waste, and it would burn a rotation for nothing.
//
// NOTE: an expired token here does NOT still revoke. `/auth/logout` is
// `Depends(require_mgmt_user)` and `decode_mgmt_jwt` enforces `exp`, so
// an expired token 401s before the handler body runs — no jti denylist
// write, no family revoke, no cookie clear — and `Sidebar.signOut`
// swallows that with `.catch(() => {})`. Signing out of a tab left idle
// past the 1h mark therefore leaves the refresh family live server-side.
// Pre-existing (the old sync `authHeader()` sent the same stored token),
// and silent refresh makes an expired token at sign-out much rarer, but
// closing it properly needs a server change: accept the refresh cookie
// alone as authority for logout. Needs a follow-up issue — none is filed
// yet, and this PR deliberately does not file one (several sessions are
// working this repo concurrently).

export async function logout() {
  const token = readToken();
  const res = await fetch(`${BASE}/auth/logout`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    credentials: "include",
  });
  if (!res.ok) throw new Error(`logout ${res.status}`);
}

// ---- Attachments (#177) --------------------------------------------------
//
// Three-step browser flow:
//   1. ``presignAttachment`` — POST metadata, get back a presigned PUT
//      URL + the headers the browser must send + a short-lived JWT that
//      ``finalizeAttachment`` redeems.
//   2. ``uploadToPresigned`` — PUT the raw bytes directly to S3. No
//      Authorization header (the presigned URL carries its own auth).
//   3. ``finalizeAttachment`` — POST presign_token + checksum to write
//      the canonical ``ATTACHMENT#{id}`` row and flip the lifecycle tag.
//
// User-facing copy in the surrounding UI must say "attach"/"attached"
// (never "upload"/"uploaded") per Channel's 2026-06-03 design input —
// these identifiers are implementation detail and stay as-is.

const _HEX = "0123456789abcdef";

/**
 * Compute the lowercase hex SHA-256 digest of a Blob/File via
 * ``crypto.subtle``. Reusable helper — used by the attach pipeline
 * before ``finalizeAttachment`` to hand the canonical row a fingerprint.
 *
 * Reads the blob via ``FileReader`` rather than ``Blob.arrayBuffer``
 * or ``Response``: jsdom v24's ``Blob.arrayBuffer`` is undefined and
 * its ``Response(blob)`` path reads the literal string
 * ``[object Blob]`` instead of the bytes, breaking unit tests under
 * vitest. ``FileReader.readAsArrayBuffer`` works identically in real
 * browsers, Node, and jsdom — and the cost is one byte copy.
 */
export async function sha256Hex(blob) {
  const result = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
  // Wrap defensively: CI's jsdom v24 returns a Buffer-flavoured value
  // that ``crypto.subtle.digest`` rejected with ``ERR_INVALID_ARG_TYPE``
  // even though it carries the right bytes. Coercing through Uint8Array
  // gives a guaranteed TypedArray that every env accepts.
  const view = new Uint8Array(result);
  const digest = await crypto.subtle.digest("SHA-256", view);
  const bytes = new Uint8Array(digest);
  let out = "";
  for (let i = 0; i < bytes.length; i += 1) {
    const b = bytes[i];
    out += _HEX[b >> 4] + _HEX[b & 0xf];
  }
  return out;
}

export async function presignAttachment({ name, mime, size_bytes }) {
  const response = await fetch(`${BASE}/api/attachments/presign`, {
    method: "POST",
    headers: { ...(await authHeader()), "Content-Type": "application/json" },
    body: JSON.stringify({ name, mime, size_bytes }),
  });
  if (!response.ok) throw new Error(`presignAttachment ${response.status}`);
  return response.json();
}

export async function uploadToPresigned(url, headers, blob) {
  const response = await fetch(url, {
    method: "PUT",
    headers,
    body: blob,
  });
  if (!response.ok) throw new Error(`uploadToPresigned ${response.status}`);
}

export async function finalizeAttachment({ presign_token, checksum_sha256 }) {
  const response = await fetch(`${BASE}/api/attachments`, {
    method: "POST",
    headers: { ...(await authHeader()), "Content-Type": "application/json" },
    body: JSON.stringify({ presign_token, checksum_sha256 }),
  });
  if (!response.ok) throw new Error(`finalizeAttachment ${response.status}`);
  return response.json();
}

// ---- Admin (#238) ----------------------------------------------------------
//
// Read-only admin surface (#235); requires an admin-role mgmt JWT — the
// SPA's AdminLayout client-gates for UX, `require_admin` server-side is
// the real boundary. CloudFront gotcha: through the deployed domain, API
// 403 responses are rewritten to a 200 index.html (401s pass through), so
// an ok-but-non-JSON body from these endpoints must be treated as
// unauthorized rather than parsed. Defensive only — an admin token never
// hits it in practice.

async function adminJson(operation, response) {
  if (!response.ok) {
    let detail = null;
    try {
      detail = (await response.json()).detail ?? null;
    } catch {
      /* non-JSON error body — status alone must do */
    }
    throw new ApiError(operation, response.status, detail);
  }
  try {
    return await response.json();
  } catch {
    throw new ApiError(operation, 403, "unauthorized (non-JSON response)");
  }
}

export async function getAdminUsers({ cursor = null, limit = 50, sort = null } = {}) {
  // `sort` ∈ last_chat_at (desc, server default) | created_at (desc) |
  // email (asc). A cursor is only valid under the sort it was issued
  // with — the server 400s on a mismatch and callers restart from page 1.
  const qs = new URLSearchParams({ limit: String(limit) });
  if (cursor) qs.set("cursor", cursor);
  if (sort) qs.set("sort", sort);
  const response = await fetch(`${BASE}/api/admin/users?${qs}`, {
    headers: await authHeader(),
  });
  return adminJson("getAdminUsers", response);
}

export async function getAdminUser(userId) {
  // user_id is the JWT sub (an email today) — encode for the path.
  const response = await fetch(
    `${BASE}/api/admin/users/${encodeURIComponent(userId)}`,
    { headers: await authHeader() },
  );
  return adminJson("getAdminUser", response);
}

// ---- User preferences ----------------------------------------------------

export async function getPrefs() {
  // `cache: "no-store"` is belt-and-suspenders alongside the server's
  // Cache-Control: no-store header — guarantees Chromium (browser +
  // Electron renderer) bypasses any heuristic cache when re-hydrating.
  const res = await fetch(`${BASE}/api/me/prefs`, {
    headers: await authHeader(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`getPrefs failed: ${res.status}`);
  const body = await res.json();
  return body.prefs;
}

export async function putPrefs(partial) {
  const res = await fetch(`${BASE}/api/me/prefs`, {
    method: "PUT",
    headers: { ...(await authHeader()), "Content-Type": "application/json" },
    body: JSON.stringify({ prefs: partial }),
  });
  if (!res.ok) throw new Error(`putPrefs failed: ${res.status}`);
}

// ---- MCP servers (#207) ---------------------------------------------------

export async function listMCPServers() {
  const response = await fetch(`${BASE}/api/mcp/servers`, {
    headers: await authHeader(),
  });
  if (!response.ok) throw new Error(`listMCPServers ${response.status}`);
  return response.json();
}

export async function registerMCPServer({
  name,
  url,
  tool_prefix = null,
  auth_type = "oauth_dcr",
  token = null,
}) {
  const response = await fetch(`${BASE}/api/mcp/servers`, {
    method: "POST",
    headers: { ...(await authHeader()), "Content-Type": "application/json" },
    body: JSON.stringify({ name, url, tool_prefix, auth_type, token }),
  });
  if (!response.ok) {
    // Surface the FastAPI error detail so AddMCPServerModal can branch on
    // a machine-readable reason (the dcr_unsupported code that steers the
    // user into the static-token path) rather than string-matching prose.
    //
    // SECURITY: only parse the detail for the oauth_dcr path (token == null).
    // A static-token 422 body can echo the pasted PAT back in
    // ``detail[].input``; parsing it would land the secret on
    // ``ApiError.detail`` where a caller's ``console.error`` could leak it.
    // dcr_unsupported only ever arises on the oauth_dcr path, so we lose
    // nothing by skipping the parse whenever a token was supplied.
    let detail = null;
    if (token == null) {
      try {
        detail = (await response.json()).detail ?? null;
      } catch {
        /* non-JSON error body — status alone must do */
      }
    }
    const err = new ApiError("registerMCPServer", response.status, detail);
    // The dcr_unsupported branch raises detail={code, message}; hoist the
    // code onto the error so callers needn't know the detail shape.
    if (detail && typeof detail === "object") err.code = detail.code;
    throw err;
  }
  return response.json();
}

export async function patchMCPServer(serverId, updates) {
  const response = await fetch(`${BASE}/api/mcp/servers/${serverId}`, {
    method: "PATCH",
    headers: { ...(await authHeader()), "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!response.ok) throw new Error(`patchMCPServer ${response.status}`);
}

export async function deleteMCPServer(serverId) {
  const response = await fetch(`${BASE}/api/mcp/servers/${serverId}`, {
    method: "DELETE",
    headers: await authHeader(),
  });
  if (!response.ok) throw new Error(`deleteMCPServer ${response.status}`);
}

export async function reauthMCPServer(serverId) {
  const response = await fetch(`${BASE}/api/mcp/servers/${serverId}/reauth`, {
    method: "POST",
    headers: await authHeader(),
  });
  if (!response.ok) throw new Error(`reauthMCPServer ${response.status}`);
  return response.json();
}

export async function getChatMCPSettings(chatId) {
  const response = await fetch(`${BASE}/api/chats/${chatId}/mcp`, {
    headers: await authHeader(),
  });
  if (!response.ok) throw new Error(`getChatMCPSettings ${response.status}`);
  return response.json();
}

export async function putChatMCPSettings(chatId, settings) {
  const response = await fetch(`${BASE}/api/chats/${chatId}/mcp`, {
    method: "PUT",
    headers: { ...(await authHeader()), "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!response.ok) throw new Error(`putChatMCPSettings ${response.status}`);
}

// ---- Featured MCP catalog (#405, backend #277) ----------------------------

/**
 * List Channel's curated first-party MCP servers. The SPA renders a
 * one-click "Enable" affordance from this catalog; each entry carries the
 * canonical url, description, docs_url, auth_type, tool_prefix, and default
 * global-enablement. Read-only, no secrets.
 */
export async function getFeaturedServers() {
  const response = await fetch(`${BASE}/api/mcp/featured`, {
    headers: await authHeader(),
  });
  if (!response.ok) throw new Error(`getFeaturedServers ${response.status}`);
  return response.json();
}

/**
 * Enable a featured (curated) MCP server via the one-click path (#405/#277).
 *
 * Registration is server-pinned: passing `featured_id` makes the backend
 * resolve the canonical url, credential type, tool prefix, and default
 * global-enablement from the catalog — the client is NOT the source of
 * truth for any of those. We deliberately do not send `tool_prefix`,
 * `auth_type`, or `globally_enabled`. `name` and `url` are echoed from the
 * fetched catalog entry only because the register endpoint marks them
 * required; they are non-authoritative (the server overwrites `url` with
 * the catalog value for a featured registration — see #277).
 *
 * SECURITY: a `static_token` (PAT) registration always supplies a `token`,
 * and a 422 body can echo the pasted token back in `detail[].input`. So we
 * parse the error body ONLY when no token was supplied — the tokenless
 * (OAuth-DCR featured) path has no secret to leak, so it keeps the
 * machine-readable FastAPI `detail`; the token-bearing path never reads the
 * body at all. Mirrors `registerMCPServer` / #375.
 */
export async function enableFeaturedServer({ featured_id, name, url, token = null }) {
  const response = await fetch(`${BASE}/api/mcp/servers`, {
    method: "POST",
    headers: { ...(await authHeader()), "Content-Type": "application/json" },
    body: JSON.stringify({ featured_id, name, url, token }),
  });
  if (!response.ok) {
    let detail = null;
    if (token == null) {
      try {
        detail = (await response.json()).detail ?? null;
      } catch {
        /* non-JSON error body — status alone must do */
      }
    }
    throw new ApiError("enableFeaturedServer", response.status, detail);
  }
  return response.json();
}

// ---- Admin metrics (#239, epic #233) --------------------------------------
//
// Data source for the admin Dashboard (`/app/admin/dashboard`). Both wrap the
// CloudWatch-backed metrics endpoints (#236) behind the shared `adminJson`
// helper, so a deployed-domain CloudFront ok-but-non-JSON 403 rewrite surfaces
// as unauthorized rather than a JSON parse crash. A 503
// `{"detail": {"error": "metrics_unavailable", ...}}` surfaces as an ApiError
// (status 503) the Dashboard renders as a degraded panel instead of crashing.

export async function getAdminMetricsSummary() {
  // → { "today"|"7d"|"30d": { active_users, metrics: {<19 counters>} } }.
  // "today" is a rolling 24h window; every counter is always present (0.0
  // when no data), so the Dashboard never guards against missing keys.
  const response = await fetch(`${BASE}/api/admin/metrics/summary`, {
    headers: await authHeader(),
  });
  return adminJson("getAdminMetricsSummary", response);
}

export async function getAdminMetricsTimeseries({ metric, window, bucket = null }) {
  // → { metric, window, bucket, start, end, points: [{ t, v }] }. `points`
  // is a dense, ascending, zero-filled, bucket-aligned grid (≤300 points) —
  // render straight into Recharts, no client-side gap-filling. `window` ∈
  // 24h | 7d | 30d; `bucket` is optional (server picks a sane default per
  // window). `metric` must be one of the 19 named counters — an unknown
  // metric or an over-cap bucket/window combo 422s.
  const qs = new URLSearchParams({ metric, window });
  if (bucket) qs.set("bucket", bucket);
  const response = await fetch(`${BASE}/api/admin/metrics/timeseries?${qs}`, {
    headers: await authHeader(),
  });
  return adminJson("getAdminMetricsTimeseries", response);
}

// ---- Assets browse (#328, epic #321) --------------------------------------
//
// Cross-chat browse + single-descriptor fetch for the Artifacts view.
// (`getAssetContent` for the viewer bytes lives with the #325/#327
// per-chat surface above — the Artifacts panel reuses it.) PAGINATION
// RULE: page on ``next_cursor`` (null = exhausted), NEVER on
// ``items.length``. The browse endpoint drops orphaned rows mid-page and
// lazily reaps them, so a page can come back short or even empty while
// ``next_cursor`` is still live. A malformed/foreign cursor is a 400
// (surfaced as ``ApiError`` with ``status === 400``) — restart from page 1.

export async function listAssets({ limit = 50, cursor = null } = {}) {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (cursor) qs.set("cursor", cursor);
  const response = await fetch(`${BASE}/api/assets?${qs}`, {
    headers: await authHeader(),
  });
  if (!response.ok) throw new ApiError("listAssets", response.status);
  return response.json();
}

export async function getAsset(chatId, assetId) {
  // Single card descriptor for a cold deep-link (bookmark/refresh) where
  // the asset isn't on the loaded browse page. The route is per-chat, so
  // callers must carry both ids in the URL.
  const response = await fetch(`${BASE}/api/chats/${chatId}/assets/${assetId}`, {
    headers: await authHeader(),
  });
  if (!response.ok) throw new ApiError("getAsset", response.status);
  return response.json();
}

// ---- Memory records (#475, epic #129) -------------------------------------
//
// The read half of "what Channel remembers" (#479), backing the Customize
// panel. Returns `{groups, summaries, recall_window, withheld_record_count,
// next_cursor}` — `groups` newest-chat-first with each chat's records
// oldest-first, `summaries` the #245 read-only rolling head summaries, and
// `recall_window` the LIVE caps read from `recall.py`. Render every number
// in the panel from that envelope; a hand-copied `5` becomes a lie the first
// time the cap moves.
//
// `limit` counts CHATS per page, not records. PAGINATION RULE: page on
// `next_cursor` (null = exhausted), NEVER on `groups.length` — a chat whose
// events carry no text yields no group, so a page can come back short while
// the cursor is still live. `chatId` scopes the response to one chat and
// always comes back with a null cursor; an id the caller doesn't own is a
// 404 (not 403 — chat existence isn't leaked). A malformed or foreign
// cursor is a 400 (`ApiError` with `status === 400`) — restart from page 1.
//
// Record and summary `text` is DATA: render it as plain text, never through
// `renderMarkdown.jsx` (epic #129 decision 11 — see MemorySection.jsx).

export async function listMemoryRecords({ cursor = null, limit = 10, chatId = null } = {}) {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (cursor) qs.set("cursor", cursor);
  if (chatId) qs.set("chat_id", chatId);
  const response = await fetch(`${BASE}/api/memory/records?${qs}`, {
    headers: await authHeader(),
  });
  if (!response.ok) throw new ApiError("listMemoryRecords", response.status);
  return response.json();
}
