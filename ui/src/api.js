// Copyright (c) 2026 John Carter. All rights reserved.
/**
 * Channel API client — thin wrapper around fetch.
 * Token is read from localStorage.
 */

const BASE = import.meta.env.VITE_API_BASE ?? "";

function getToken() {
  return localStorage.getItem("starter_mgmt_token") ?? "";
}

function authHeader() {
  const token = getToken();
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
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ title, model_default: modelDefault }),
  });
  if (!response.ok) throw new Error(`createChat ${response.status}`);
  return response.json();
}

export async function listChats({ limit = 50, cursor = null } = {}) {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (cursor) qs.set("cursor", cursor);
  const response = await fetch(`${BASE}/api/chats?${qs}`, {
    headers: authHeader(),
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
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`getChat ${response.status}`);
  return response.json();
}

export async function patchChat(chatId, { title, archived } = {}) {
  const response = await fetch(`${BASE}/api/chats/${chatId}`, {
    method: "PATCH",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ title, archived }),
  });
  if (!response.ok) throw new Error(`patchChat ${response.status}`);
}

export async function deleteChat(chatId) {
  const response = await fetch(`${BASE}/api/chats/${chatId}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`deleteChat ${response.status}`);
}

export async function streamMessage(
  chatId,
  { message, model, effort, attachments, idempotencyKey, signal } = {},
) {
  const headers = { ...authHeader(), "Content-Type": "application/json" };
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
  const response = await fetch(`${BASE}/api/models`, { headers: authHeader() });
  if (!response.ok) throw new Error(`listModels ${response.status}`);
  return response.json();
}

export async function regenerate(chatId, { model, effort, signal } = {}) {
  const response = await fetch(`${BASE}/api/chats/${chatId}/regenerate`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
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
      headers: { ...authHeader(), "Content-Type": "application/json" },
      body: JSON.stringify({ kind, note }),
    },
  );
  if (!response.ok) throw new Error(`submitFeedback ${response.status}`);
}

// ---- Auth ---------------------------------------------------------------
//
// POST /auth/logout records an immutable audit-log entry on the server
// (event_type=auth.logout) so a stolen-laptop scenario has a server-side
// signal for remediation. The endpoint does NOT invalidate the JWT (no
// JTI denylist today — see #114); the caller is responsible for
// clearing the local token. Best-effort by design — the SPA wraps the
// call in `.catch(...)` so transient API outages don't strand a
// signed-in user.

export async function logout() {
  const res = await fetch(`${BASE}/auth/logout`, {
    method: "POST",
    headers: authHeader(),
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
    headers: { ...authHeader(), "Content-Type": "application/json" },
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
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ presign_token, checksum_sha256 }),
  });
  if (!response.ok) throw new Error(`finalizeAttachment ${response.status}`);
  return response.json();
}

// ---- User preferences ----------------------------------------------------

export async function getPrefs() {
  // `cache: "no-store"` is belt-and-suspenders alongside the server's
  // Cache-Control: no-store header — guarantees Chromium (browser +
  // Electron renderer) bypasses any heuristic cache when re-hydrating.
  const res = await fetch(`${BASE}/api/me/prefs`, {
    headers: authHeader(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`getPrefs failed: ${res.status}`);
  const body = await res.json();
  return body.prefs;
}

export async function putPrefs(partial) {
  const res = await fetch(`${BASE}/api/me/prefs`, {
    method: "PUT",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ prefs: partial }),
  });
  if (!res.ok) throw new Error(`putPrefs failed: ${res.status}`);
}

// ---- MCP servers (#207) ---------------------------------------------------

export async function listMCPServers() {
  const response = await fetch(`${BASE}/api/mcp/servers`, {
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`listMCPServers ${response.status}`);
  return response.json();
}

export async function registerMCPServer({ name, url, tool_prefix = null }) {
  const response = await fetch(`${BASE}/api/mcp/servers`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ name, url, tool_prefix }),
  });
  if (!response.ok) throw new Error(`registerMCPServer ${response.status}`);
  return response.json();
}

export async function patchMCPServer(serverId, updates) {
  const response = await fetch(`${BASE}/api/mcp/servers/${serverId}`, {
    method: "PATCH",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!response.ok) throw new Error(`patchMCPServer ${response.status}`);
}

export async function deleteMCPServer(serverId) {
  const response = await fetch(`${BASE}/api/mcp/servers/${serverId}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`deleteMCPServer ${response.status}`);
}

export async function reauthMCPServer(serverId) {
  const response = await fetch(`${BASE}/api/mcp/servers/${serverId}/reauth`, {
    method: "POST",
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`reauthMCPServer ${response.status}`);
  return response.json();
}

export async function getChatMCPSettings(chatId) {
  const response = await fetch(`${BASE}/api/chats/${chatId}/mcp`, {
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`getChatMCPSettings ${response.status}`);
  return response.json();
}

export async function putChatMCPSettings(chatId, settings) {
  const response = await fetch(`${BASE}/api/chats/${chatId}/mcp`, {
    method: "PUT",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!response.ok) throw new Error(`putChatMCPSettings ${response.status}`);
}
