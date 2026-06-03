// Copyright (c) 2026 John Carter. All rights reserved.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createChat,
  deleteChat,
  getChat,
  getPrefs,
  listChats,
  listModels,
  logout,
  patchChat,
  putPrefs,
  regenerate,
  streamMessage,
  submitFeedback,
} from "./api.js";

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
      mockOk({});
      await deleteChat("c1");
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats/c1");
      expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    });

    it("throws on non-ok", async () => {
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

  // ---- prefs --------------------------------------------------------------

  describe("getPrefs", () => {
    it("GETs /api/me/prefs and unwraps the envelope", async () => {
      mockOk({ prefs: { theme: "light", accent: "150" } });
      const result = await getPrefs();
      expect(result).toEqual({ theme: "light", accent: "150" });
      expect(fetchMock.mock.calls[0][0]).toBe("/api/me/prefs");
      expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe(
        "Bearer tok-abc",
      );
    });

    it("throws on non-ok", async () => {
      mockFail(500);
      await expect(getPrefs()).rejects.toThrow(/getPrefs failed: 500/);
    });

    it("passes cache: 'no-store' so Chromium bypasses heuristic caching", async () => {
      mockOk({ prefs: { theme: "dark" } });
      await getPrefs();
      expect(fetchMock.mock.calls[0][1].cache).toBe("no-store");
    });
  });

  describe("putPrefs", () => {
    it("PUTs a partial body wrapped in a prefs envelope", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 204 });
      await putPrefs({ theme: "dark", send_on_enter: false });
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/me/prefs");
      expect(opts.method).toBe("PUT");
      expect(opts.headers["Content-Type"]).toBe("application/json");
      expect(JSON.parse(opts.body)).toEqual({
        prefs: { theme: "dark", send_on_enter: false },
      });
    });

    it("throws on non-ok", async () => {
      mockFail(422);
      await expect(putPrefs({ bogus: "x" })).rejects.toThrow(
        /putPrefs failed: 422/,
      );
    });
  });

  // ---- logout -------------------------------------------------------------

  describe("logout", () => {
    it("POSTs /auth/logout with the bearer token and resolves on 204", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 204 });
      await logout();
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/auth/logout");
      expect(opts.method).toBe("POST");
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
    });

    it("throws on non-ok so callers can decide to log+ignore", async () => {
      // The SPA's sign-out wraps logout() in .catch(...) — that's the
      // contract this rejection enables, even though the API client
      // itself doesn't swallow errors.
      mockFail(500);
      await expect(logout()).rejects.toThrow(/logout 500/);
    });

    it("omits Authorization when no token is stored", async () => {
      delete storage["starter_mgmt_token"];
      fetchMock.mockResolvedValue({ ok: true, status: 204 });
      await logout();
      expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
    });
  });

  // ---- submitFeedback -----------------------------------------------------

  describe("submitFeedback", () => {
    it("POSTs to the feedback URL with kind + note in the body", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 204 });
      await submitFeedback("c1", "m1", { kind: "up", note: null });
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/chats/c1/messages/m1/feedback");
      expect(opts.method).toBe("POST");
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["Content-Type"]).toBe("application/json");
      expect(JSON.parse(opts.body)).toEqual({ kind: "up", note: null });
    });

    it("defaults note to null when omitted", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 204 });
      await submitFeedback("c1", "m1", { kind: "down" });
      expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
        kind: "down",
        note: null,
      });
    });

    it("throws on non-ok response so callers can revert optimistic state", async () => {
      mockFail(404);
      await expect(
        submitFeedback("c1", "m-bad", { kind: "up", note: null }),
      ).rejects.toThrow(/submitFeedback 404/);
    });
  });
});
