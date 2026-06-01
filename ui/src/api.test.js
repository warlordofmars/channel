// Copyright (c) 2026 John Carter. All rights reserved.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  api,
  createChat,
  getChat,
  listChats,
  listModels,
  patchChat,
  regenerate,
  streamMessage,
} from "./api.js";

describe("api", () => {
  let fetchMock;

  let storage;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = v; },
      removeItem: (k) => { delete storage[k]; },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function mockOk(body, status = 200) {
    fetchMock.mockResolvedValue({
      ok: true,
      status,
      json: () => Promise.resolve(body),
    });
  }

  function mockErr(detail, status = 400) {
    fetchMock.mockResolvedValue({
      ok: false,
      status,
      statusText: "Bad Request",
      json: () => Promise.resolve(detail !== undefined ? { detail } : {}),
    });
  }

  // ---------------------------------------------------------------------------
  // request() core behaviour
  // ---------------------------------------------------------------------------

  it("adds Authorization header when token is in localStorage", async () => {
    localStorage.setItem("starter_mgmt_token", "tok123");
    mockOk({ items: [] });
    await api.listClients();
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer tok123");
  });

  it("omits Authorization header when no token", async () => {
    mockOk({ items: [] });
    await api.listClients();
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
  });

  it("sends JSON body on POST requests", async () => {
    mockOk({ client_id: "c1" });
    await api.createClient({ client_name: "App" });
    const call = fetchMock.mock.calls[0];
    expect(call[1].method).toBe("POST");
    expect(call[1].body).toBe(JSON.stringify({ client_name: "App" }));
  });

  it("omits body on GET requests", async () => {
    mockOk({ items: [] });
    await api.listClients();
    expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
  });

  it("throws error with detail on non-ok response", async () => {
    mockErr("Something bad");
    await expect(api.listClients()).rejects.toThrow("Something bad");
  });

  it("attaches the HTTP status to the thrown error so callers can branch on 429", async () => {
    mockErr("Quota exceeded.", 429);
    try {
      await api.createClient({ client_name: "App" });
      throw new Error("should have thrown");
    } catch (err) {
      expect(err.message).toBe("Quota exceeded.");
      expect(err.status).toBe(429);
    }
  });

  it("falls back to statusText when error json has no detail", async () => {
    mockErr(undefined);
    await expect(api.listClients()).rejects.toThrow("Request failed");
  });

  it("falls back to statusText when error json parse fails", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: () => Promise.reject(new Error("parse fail")),
    });
    await expect(api.listClients()).rejects.toThrow("Internal Server Error");
  });

  it("returns null on 204 response", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204 });
    const result = await api.deleteClient("c1");
    expect(result).toBeNull();
  });

  // ---------------------------------------------------------------------------
  // listClients
  // ---------------------------------------------------------------------------

  it("listClients without cursor", async () => {
    mockOk({ items: [] });
    await api.listClients();
    expect(fetchMock.mock.calls[0][0]).toContain("/api/clients");
    expect(fetchMock.mock.calls[0][0]).not.toContain("cursor=");
  });

  it("listClients with cursor", async () => {
    mockOk({ items: [] });
    await api.listClients({ cursor: "tok" });
    expect(fetchMock.mock.calls[0][0]).toContain("cursor=tok");
  });

  // ---------------------------------------------------------------------------
  // Client CRUD
  // ---------------------------------------------------------------------------

  it("getClient calls correct endpoint", async () => {
    mockOk({ client_id: "c1" });
    await api.getClient("c1");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/clients/c1");
  });

  it("createClient sends POST with body", async () => {
    mockOk({ client_id: "c1" });
    await api.createClient({ client_name: "App" });
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ client_name: "App" });
  });

  it("deleteClient calls DELETE", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204 });
    await api.deleteClient("c2");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/clients/c2");
  });

  // ---------------------------------------------------------------------------
  // Activity & Account stats
  // ---------------------------------------------------------------------------

  it("getAccountStats passes window through", async () => {
    mockOk({ window_days: 30 });
    await api.getAccountStats(30);
    expect(fetchMock.mock.calls[0][0]).toContain("/api/account/stats?window=30");
  });

  it("getAccountStats defaults to 90-day window", async () => {
    mockOk({ window_days: 90 });
    await api.getAccountStats();
    expect(fetchMock.mock.calls[0][0]).toContain("/api/account/stats?window=90");
  });

  it("getActivity with default params", async () => {
    mockOk({ items: [] });
    await api.getActivity();
    expect(fetchMock.mock.calls[0][0]).toContain("days=7");
    expect(fetchMock.mock.calls[0][0]).toContain("limit=100");
  });

  it("getActivity with custom params", async () => {
    mockOk({ items: [] });
    await api.getActivity(30, { limit: 50 });
    expect(fetchMock.mock.calls[0][0]).toContain("days=30");
    expect(fetchMock.mock.calls[0][0]).toContain("limit=50");
  });

  // ---------------------------------------------------------------------------
  // Users
  // ---------------------------------------------------------------------------

  it("getMe calls /api/users/me", async () => {
    mockOk({ user_id: "u1", email: "u@example.com", role: "user" });
    await api.getMe();
    expect(fetchMock.mock.calls[0][0]).toContain("/api/users/me");
    expect(fetchMock.mock.calls[0][1].method).toBe("GET");
  });

  it("listUsers calls /api/users", async () => {
    mockOk({ items: [] });
    await api.listUsers();
    expect(fetchMock.mock.calls[0][0]).toContain("/api/users");
    expect(fetchMock.mock.calls[0][0]).not.toContain("/me");
  });

  it("listUsers passes cursor when provided", async () => {
    mockOk({ items: [] });
    await api.listUsers({ cursor: "c123" });
    expect(fetchMock.mock.calls[0][0]).toContain("cursor=c123");
  });

  it("deleteUser calls DELETE /api/users/{id}", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204 });
    await api.deleteUser("u99");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/users/u99");
  });

  it("updateUserRole calls PATCH /api/users/{id}", async () => {
    mockOk({ user_id: "u1", role: "admin" });
    await api.updateUserRole("u1", "admin");
    expect(fetchMock.mock.calls[0][1].method).toBe("PATCH");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/users/u1");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ role: "admin" });
  });

  it("getUserStats calls GET /api/users/{id}/stats", async () => {
    mockOk({ user_id: "u1" });
    await api.getUserStats("u1");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/users/u1/stats");
    expect(fetchMock.mock.calls[0][1].method).toBe("GET");
  });

  it("getUserLimits calls GET /api/users/{id}/limits", async () => {
    mockOk({ user_id: "u1" });
    await api.getUserLimits("u1");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/users/u1/limits");
    expect(fetchMock.mock.calls[0][1].method).toBe("GET");
  });

  it("updateUserLimits calls PUT /api/users/{id}/limits with body", async () => {
    mockOk({ user_id: "u1" });
    const body = { memory_limit: 100, storage_bytes_limit: 1048576 };
    await api.updateUserLimits("u1", body);
    expect(fetchMock.mock.calls[0][0]).toContain("/api/users/u1/limits");
    expect(fetchMock.mock.calls[0][1].method).toBe("PUT");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(body);
  });

  it("listApiKeys calls GET /api/keys", async () => {
    mockOk([]);
    await api.listApiKeys();
    expect(fetchMock.mock.calls[0][0]).toContain("/api/keys");
    expect(fetchMock.mock.calls[0][1].method).toBe("GET");
  });

  it("createApiKey calls POST /api/keys with name and scope", async () => {
    mockOk({ key_id: "k1", plaintext_key: "starter_sk_abc" });
    await api.createApiKey("My Key", "users:read");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/keys");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ name: "My Key", scope: "users:read" });
  });

  it("deleteApiKey calls DELETE /api/keys/{id}", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204 });
    await api.deleteApiKey("k1");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/keys/k1");
  });

  it("deleteAccount calls DELETE /api/account with confirm body", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204 });
    await api.deleteAccount();
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/account");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ confirm: true });
  });

  // ---------------------------------------------------------------------------
  // 401 handling — clears token and redirects
  // ---------------------------------------------------------------------------

  it("401 response clears mgmt token and redirects to /", async () => {
    storage["starter_mgmt_token"] = "old-token";
    vi.stubGlobal("location", { replace: vi.fn() });
    fetchMock.mockResolvedValue({ ok: false, status: 401, json: () => Promise.resolve({}) });
    const result = await api.listClients();
    expect(result).toBeNull();
    expect(storage["starter_mgmt_token"]).toBeUndefined();
    expect(window.location.replace).toHaveBeenCalledWith("/");
  });

  // ---------------------------------------------------------------------------
  // exportAccount
  // ---------------------------------------------------------------------------

  function mockExportResponse({
    ok = true,
    status = 200,
    blob = new Blob(),
    disposition,
    body,
  } = {}) {
    fetchMock.mockResolvedValue({
      ok,
      status,
      statusText: "Error",
      blob: () => Promise.resolve(blob),
      json: () => Promise.resolve(body ?? {}),
      headers: { get: () => disposition ?? null },
    });
  }

  it("exportAccount sends Authorization header when token present", async () => {
    storage["starter_mgmt_token"] = "user-token";
    mockExportResponse({ disposition: 'attachment; filename="channel-export.json"' });
    await api.exportAccount();
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/account/export");
    expect(opts.headers.Authorization).toBe("Bearer user-token");
  });

  it("exportAccount omits Authorization when no token is stored", async () => {
    mockExportResponse({ disposition: 'attachment; filename="x.json"' });
    await api.exportAccount();
    const opts = fetchMock.mock.calls[0][1];
    expect(opts.headers.Authorization).toBeUndefined();
  });

  it("exportAccount returns blob + filename parsed from Content-Disposition", async () => {
    const blob = new Blob(["{}"], { type: "application/json" });
    mockExportResponse({
      blob,
      disposition: 'attachment; filename="channel-export-user-20260418.json"',
    });
    const result = await api.exportAccount();
    expect(result.blob).toBe(blob);
    expect(result.filename).toBe("channel-export-user-20260418.json");
  });

  it("exportAccount falls back to a default filename when disposition is missing", async () => {
    mockExportResponse({ disposition: null });
    const result = await api.exportAccount();
    expect(result.filename).toBe("channel-export.json");
  });

  it("exportAccount surfaces error detail from JSON body on non-OK responses", async () => {
    mockExportResponse({
      ok: false,
      status: 429,
      body: { detail: "Exports are limited to one per 5 minutes." },
    });
    await expect(api.exportAccount()).rejects.toThrow(
      "Exports are limited to one per 5 minutes.",
    );
  });

  it("exportAccount falls back to statusText when the error body is not JSON", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Server Error",
      json: () => Promise.reject(new Error("not json")),
      headers: { get: () => null },
    });
    await expect(api.exportAccount()).rejects.toThrow("Server Error");
  });

  it("exportAccount surfaces generic 'Request failed' when error body is an empty object", async () => {
    mockExportResponse({ ok: false, status: 500, body: {} });
    await expect(api.exportAccount()).rejects.toThrow("Export failed");
  });

  it("exportAccount clears token and redirects on 401", async () => {
    storage["starter_mgmt_token"] = "old-token";
    const replace = vi.fn();
    vi.stubGlobal("location", { replace });
    fetchMock.mockResolvedValue({
      ok: false,
      status: 401,
      statusText: "Unauthorized",
      headers: { get: () => null },
      blob: () => Promise.resolve(new Blob()),
      json: () => Promise.resolve({}),
    });
    const result = await api.exportAccount();
    expect(result).toBeNull();
    expect(storage["starter_mgmt_token"]).toBeUndefined();
    expect(replace).toHaveBeenCalledWith("/");
  });
});

// ---------------------------------------------------------------------------
// Chats wrappers (named exports)
// ---------------------------------------------------------------------------

describe("chats wrappers", () => {
  let fetchMock;
  let storage;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = v; },
      removeItem: (k) => { delete storage[k]; },
    });
    storage["starter_mgmt_token"] = "tok-abc";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function mockOk(body, status = 200) {
    fetchMock.mockResolvedValue({
      ok: true,
      status,
      json: () => Promise.resolve(body),
    });
  }

  function mockFail(status) {
    fetchMock.mockResolvedValue({
      ok: false,
      status,
      json: () => Promise.resolve({}),
    });
  }

  // ---- createChat ---------------------------------------------------------

  describe("createChat", () => {
    it("POSTs to /api/chats with Authorization + snake-cased body", async () => {
      mockOk({ chat_id: "c1" });
      const result = await createChat({ title: "Hi", modelDefault: "m" });
      expect(result).toEqual({ chat_id: "c1" });
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/chats");
      expect(opts.method).toBe("POST");
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["Content-Type"]).toBe("application/json");
      expect(opts.body).toBe(JSON.stringify({ title: "Hi", model_default: "m" }));
    });

    it("throws on non-ok response", async () => {
      mockFail(500);
      await expect(createChat()).rejects.toThrow(/createChat 500/);
    });

    it("defaults both fields to null when called with no args", async () => {
      mockOk({});
      await createChat();
      const body = JSON.parse(fetchMock.mock.calls[0][1].body);
      expect(body).toEqual({ title: null, model_default: null });
    });

    it("omits Authorization header when no token is stored", async () => {
      delete storage["starter_mgmt_token"];
      mockOk({});
      await createChat();
      expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
    });
  });

  // ---- listChats ----------------------------------------------------------

  describe("listChats", () => {
    it("GETs with default limit=50", async () => {
      mockOk({ items: [], next_cursor: null });
      await listChats();
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats?limit=50");
      expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer tok-abc");
    });

    it("includes cursor in query string when provided", async () => {
      mockOk({ items: [], next_cursor: null });
      await listChats({ limit: 5, cursor: "abc" });
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats?limit=5&cursor=abc");
    });

    it("throws on non-ok", async () => {
      mockFail(401);
      await expect(listChats()).rejects.toThrow(/listChats 401/);
    });
  });

  // ---- getChat ------------------------------------------------------------

  describe("getChat", () => {
    it("GETs by id with default limit=200", async () => {
      mockOk({ chat: {}, messages: [], next_cursor: null });
      await getChat("c1");
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats/c1?limit=200");
    });

    it("includes cursor when provided", async () => {
      mockOk({});
      await getChat("c1", { cursor: "x" });
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats/c1?limit=200&cursor=x");
    });

    it("throws on 404", async () => {
      mockFail(404);
      await expect(getChat("missing")).rejects.toThrow(/getChat 404/);
    });
  });

  // ---- patchChat ----------------------------------------------------------

  describe("patchChat", () => {
    it("PATCHes the chat with the given body", async () => {
      mockOk({});
      await patchChat("c1", { title: "Renamed" });
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats/c1");
      expect(fetchMock.mock.calls[0][1].method).toBe("PATCH");
      expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
        title: "Renamed",
      });
    });

    it("forwards archived flag", async () => {
      mockOk({});
      await patchChat("c1", { archived: true });
      const body = JSON.parse(fetchMock.mock.calls[0][1].body);
      expect(body.archived).toBe(true);
    });

    it("defaults to an empty body when no args are passed", async () => {
      mockOk({});
      await patchChat("c1");
      expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({});
    });

    it("throws on non-ok", async () => {
      mockFail(403);
      await expect(patchChat("c1", { archived: true })).rejects.toThrow(/patchChat 403/);
    });
  });

  // ---- deleteChat ---------------------------------------------------------

  describe("deleteChat", () => {
    it("DELETEs /api/chats/{id} and resolves on 204", async () => {
      const { deleteChat } = await import("./api.js");
      mockOk({});
      await deleteChat("c1");
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats/c1");
      expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    });

    it("throws on non-ok", async () => {
      const { deleteChat } = await import("./api.js");
      mockFail(403);
      await expect(deleteChat("c1")).rejects.toThrow(/deleteChat 403/);
    });
  });

  // ---- streamMessage ------------------------------------------------------

  describe("streamMessage", () => {
    it("POSTs to /api/chats/{id}/messages and returns the bare Response", async () => {
      const fakeResponse = { ok: true, status: 200, body: "stream-handle" };
      fetchMock.mockResolvedValue(fakeResponse);
      const result = await streamMessage("c1", { message: "hi" });
      expect(result).toBe(fakeResponse);
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats/c1/messages");
      expect(fetchMock.mock.calls[0][1].method).toBe("POST");
      expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toBeUndefined();
    });

    it("serialises message/model/effort/attachments into the body", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 200 });
      await streamMessage("c1", {
        message: "hi",
        model: "claude",
        effort: "high",
        attachments: ["a1"],
      });
      const body = JSON.parse(fetchMock.mock.calls[0][1].body);
      expect(body).toEqual({
        message: "hi",
        model: "claude",
        effort: "high",
        attachments: ["a1"],
      });
    });

    it("includes Idempotency-Key when provided", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 200 });
      await streamMessage("c1", { message: "hi", idempotencyKey: "kkk" });
      expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toBe("kkk");
    });

    it("forwards abort signal", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 200 });
      const controller = new AbortController();
      await streamMessage("c1", { message: "hi", signal: controller.signal });
      expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
    });

    it("defaults to an empty body when no args are passed", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 200 });
      await streamMessage("c1");
      const body = JSON.parse(fetchMock.mock.calls[0][1].body);
      expect(body).toEqual({});
    });

    it("throws on non-ok", async () => {
      mockFail(500);
      await expect(streamMessage("c1", { message: "hi" })).rejects.toThrow(
        /streamMessage 500/,
      );
    });
  });

  // ---- listModels ---------------------------------------------------------

  describe("listModels", () => {
    it("GETs /api/models and returns parsed body", async () => {
      mockOk({ models: [{ id: "claude-sonnet-4-6" }] });
      const result = await listModels();
      expect(result.models[0].id).toBe("claude-sonnet-4-6");
      expect(fetchMock.mock.calls[0][0]).toBe("/api/models");
      expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe(
        "Bearer tok-abc",
      );
    });

    it("throws on non-ok", async () => {
      mockFail(500);
      await expect(listModels()).rejects.toThrow(/listModels 500/);
    });
  });

  // ---- regenerate ---------------------------------------------------------

  describe("regenerate", () => {
    it("POSTs to /regenerate and returns the raw Response", async () => {
      const fakeResponse = { ok: true, status: 200, body: "stream" };
      fetchMock.mockResolvedValue(fakeResponse);
      const result = await regenerate("c1", { model: "claude-opus-4-6" });
      expect(result).toBe(fakeResponse);
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats/c1/regenerate");
      expect(fetchMock.mock.calls[0][1].method).toBe("POST");
      expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
        model: "claude-opus-4-6",
      });
    });

    it("forwards abort signal", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 200 });
      const controller = new AbortController();
      await regenerate("c1", { signal: controller.signal });
      expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
    });

    it("defaults to an empty body when no args are passed", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 200 });
      await regenerate("c1");
      expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({});
    });

    it("throws on non-ok", async () => {
      mockFail(500);
      await expect(regenerate("c1", {})).rejects.toThrow(/regenerate 500/);
    });
  });
});
