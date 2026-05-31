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

async function request(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (res.status === 401) {
    localStorage.removeItem("starter_mgmt_token");
    globalThis.location.replace("/");
    return null;
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    // Attach the HTTP status so callers can branch on 429 (quota / rate
    // limit) without scraping the message — generic JS Error has no
    // status field of its own.
    const message = err.detail ?? "Request failed";
    const wrapped = new Error(message);
    wrapped.status = res.status;
    throw wrapped;
  }

  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  // Clients
  listClients: ({ limit = 50, cursor } = {}) => {
    const params = new URLSearchParams({ limit });
    if (cursor) params.set("cursor", cursor);
    return request("GET", `/api/clients?${params}`);
  },
  getClient: (id) => request("GET", `/api/clients/${id}`),
  createClient: (body) => request("POST", "/api/clients", body),
  deleteClient: (id) => request("DELETE", `/api/clients/${id}`),

  // Activity
  getActivity: (days = 7, { limit = 100 } = {}) =>
    request("GET", `/api/activity?days=${days}&limit=${limit}`),
  getAccountStats: (windowDays = 90) =>
    request("GET", `/api/account/stats?window=${windowDays}`),

  // Users
  getMe: () => request("GET", "/api/users/me"),
  listUsers: ({ limit = 50, cursor } = {}) => {
    const params = new URLSearchParams({ limit });
    if (cursor) params.set("cursor", cursor);
    return request("GET", `/api/users?${params}`);
  },
  updateUserRole: (id, role) => request("PATCH", `/api/users/${id}`, { role }),
  getUserStats: (id) => request("GET", `/api/users/${id}/stats`),
  getUserLimits: (id) => request("GET", `/api/users/${id}/limits`),
  updateUserLimits: (id, body) => request("PUT", `/api/users/${id}/limits`, body),
  deleteUser: (id) => request("DELETE", `/api/users/${id}`),

  // API Keys
  listApiKeys: () => request("GET", "/api/keys"),
  createApiKey: (name, scope) => request("POST", "/api/keys", { name, scope }),
  deleteApiKey: (id) => request("DELETE", `/api/keys/${id}`),

  // Account
  deleteAccount: () => request("DELETE", "/api/account", { confirm: true }),
  exportAccount: async () => {
    const token = getToken();
    const headers = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${BASE}/api/account/export`, { headers });
    if (res.status === 401) {
      localStorage.removeItem("starter_mgmt_token");
      globalThis.location.replace("/");
      return null;
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? "Export failed");
    }
    const blob = await res.blob();
    const disposition = res.headers.get("content-disposition") ?? "";
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : "channel-export.json";
    return { blob, filename };
  },
};

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

export async function getChat(chatId, { limit = 200, cursor = null } = {}) {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (cursor) qs.set("cursor", cursor);
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
  if (!response.ok) throw new Error(`streamMessage ${response.status}`);
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
