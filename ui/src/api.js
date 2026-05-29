// Copyright (c) 2026 John Carter. All rights reserved.
/**
 * Channel API client — thin wrapper around fetch.
 * Token is read from localStorage.
 */

const BASE = import.meta.env.VITE_API_BASE ?? "";

function getToken() {
  return localStorage.getItem("starter_mgmt_token") ?? "";
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
