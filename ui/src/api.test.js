// Copyright (c) 2026 John Carter. All rights reserved.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  createChat,
  deleteChat,
  deleteMCPServer,
  finalizeAttachment,
  getAdminMetricsSummary,
  getAdminMetricsTimeseries,
  getAdminUser,
  getAdminUsers,
  getChat,
  getChatMCPSettings,
  getPrefs,
  listChats,
  listMCPServers,
  listModels,
  logout,
  patchChat,
  patchMCPServer,
  presignAttachment,
  putChatMCPSettings,
  putPrefs,
  reauthMCPServer,
  regenerate,
  registerMCPServer,
  sha256Hex,
  streamMessage,
  submitFeedback,
  uploadToPresigned,
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
      mockOk({ chat: {}, messages: [], older_cursor: null });
      await getChat("c1");
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats/c1?limit=200");
    });

    it("includes the before cursor when provided", async () => {
      mockOk({});
      await getChat("c1", { before: "x" });
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats/c1?limit=200&before=x");
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

    it("attaches status + parsed FastAPI detail to the thrown error (#211)", async () => {
      const detail = [
        { type: "string_too_long", loc: ["body", "message"], msg: "too long" },
      ];
      fetchMock.mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail }),
      });
      const err = await streamMessage("c1", { message: "hi" }).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.message).toBe("streamMessage 422");
      expect(err.status).toBe(422);
      expect(err.detail).toEqual(detail);
    });

    it("defaults detail to null when the error body has no detail key (#211)", async () => {
      mockFail(500);
      const err = await streamMessage("c1", { message: "hi" }).catch((e) => e);
      expect(err.status).toBe(500);
      expect(err.detail).toBeNull();
    });

    it("leaves detail null when the error body is not JSON (#211)", async () => {
      fetchMock.mockResolvedValue({
        ok: false,
        status: 413,
        json: () => Promise.reject(new Error("not json")),
      });
      const err = await streamMessage("c1", { message: "hi" }).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(413);
      expect(err.detail).toBeNull();
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

  // ---- attachments: presign + upload + finalize (#177) -------------------

  describe("sha256Hex", () => {
    it("returns the lowercase hex digest of a blob's bytes", async () => {
      // Known SHA-256 of "hello" → 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
      const blob = new Blob(["hello"], { type: "text/plain" });
      const digest = await sha256Hex(blob);
      expect(digest).toBe(
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
      );
    });

    it("rejects when FileReader fires an error", async () => {
      // Stub FileReader so readAsArrayBuffer immediately fires onerror.
      // This exercises the rejection path in sha256Hex without depending
      // on jsdom's actual error conditions.
      class ErroringFileReader {
        constructor() {
          this.error = new Error("read failed");
          this.onload = null;
          this.onerror = null;
        }
        readAsArrayBuffer() {
          // Defer so the caller has a chance to wire up handlers.
          queueMicrotask(() => this.onerror && this.onerror());
        }
      }
      const originalFileReader = globalThis.FileReader;
      vi.stubGlobal("FileReader", ErroringFileReader);
      try {
        await expect(sha256Hex(new Blob(["x"]))).rejects.toThrow(/read failed/);
      } finally {
        // afterEach's vi.unstubAllGlobals would do this too, but we
        // restore explicitly so coverage doesn't depend on order.
        if (originalFileReader) {
          vi.stubGlobal("FileReader", originalFileReader);
        }
      }
    });
  });

  describe("presignAttachment", () => {
    it("POSTs name/mime/size_bytes and returns the presign payload", async () => {
      mockOk({
        att_id: "att-1",
        url: "https://s3.example/upload",
        required_headers: { "Content-Type": "application/pdf" },
        presign_token: "jwt.abc",
      });
      const out = await presignAttachment({
        name: "spec.pdf",
        mime: "application/pdf",
        size_bytes: 1024,
      });
      expect(fetchMock.mock.calls[0][0]).toBe("/api/attachments/presign");
      const opts = fetchMock.mock.calls[0][1];
      expect(opts.method).toBe("POST");
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(JSON.parse(opts.body)).toEqual({
        name: "spec.pdf",
        mime: "application/pdf",
        size_bytes: 1024,
      });
      expect(out.att_id).toBe("att-1");
      expect(out.presign_token).toBe("jwt.abc");
    });

    it("throws on non-ok response", async () => {
      mockFail(400);
      await expect(
        presignAttachment({ name: "x", mime: "evil/bin", size_bytes: 1 }),
      ).rejects.toThrow(/presignAttachment 400/);
    });
  });

  describe("uploadToPresigned", () => {
    it("PUTs the blob with the required headers", async () => {
      fetchMock.mockResolvedValue({ ok: true, status: 200 });
      const blob = new Blob(["bytes"], { type: "application/pdf" });
      await uploadToPresigned(
        "https://s3.example/u",
        {
          "Content-Type": "application/pdf",
          "x-amz-tagging": "unreferenced=1",
        },
        blob,
      );
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("https://s3.example/u");
      expect(opts.method).toBe("PUT");
      expect(opts.headers["Content-Type"]).toBe("application/pdf");
      expect(opts.headers["x-amz-tagging"]).toBe("unreferenced=1");
      expect(opts.body).toBe(blob);
      // No Authorization header — the presigned URL carries its own auth.
      expect(opts.headers.Authorization).toBeUndefined();
    });

    it("throws on non-2xx S3 response", async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 403 });
      const blob = new Blob(["x"], { type: "application/pdf" });
      await expect(
        uploadToPresigned("https://s3.example/u", {}, blob),
      ).rejects.toThrow(/uploadToPresigned 403/);
    });
  });

  describe("finalizeAttachment", () => {
    it("POSTs presign_token + checksum and returns the Attachment", async () => {
      mockOk({
        id: "att-1",
        user_id: "u-1",
        name: "spec.pdf",
        mime: "application/pdf",
        size_bytes: 1024,
        s3_key: "attachments/user/u-1/att-1",
        s3_bucket: "bk",
        checksum_sha256: "sha",
        created_at: "2026-06-04T00:00:00Z",
      });
      const out = await finalizeAttachment({
        presign_token: "jwt.abc",
        checksum_sha256: "sha",
      });
      expect(fetchMock.mock.calls[0][0]).toBe("/api/attachments");
      const opts = fetchMock.mock.calls[0][1];
      expect(opts.method).toBe("POST");
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(JSON.parse(opts.body)).toEqual({
        presign_token: "jwt.abc",
        checksum_sha256: "sha",
      });
      expect(out.id).toBe("att-1");
    });

    it("throws on non-ok response", async () => {
      mockFail(401);
      await expect(
        finalizeAttachment({ presign_token: "bad", checksum_sha256: "x" }),
      ).rejects.toThrow(/finalizeAttachment 401/);
    });
  });
});

describe("MCP API client", () => {
  beforeEach(() => {
    localStorage.setItem("starter_mgmt_token", "test-token");
    global.fetch = vi.fn();
  });

  afterEach(() => {
    localStorage.removeItem("starter_mgmt_token");
    vi.restoreAllMocks();
  });

  it("listMCPServers GETs with auth", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ servers: [] }),
    });
    const data = await listMCPServers();
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/mcp/servers"),
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer test-token" }),
      }),
    );
    expect(data).toEqual({ servers: [] });
  });

  it("listMCPServers throws on non-ok response", async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(listMCPServers()).rejects.toThrow(/listMCPServers 500/);
  });

  it("registerMCPServer POSTs JSON body and returns auth_start_url", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ server_id: "srv-1", auth_start_url: "https://x" }),
    });
    const out = await registerMCPServer({
      name: "Hive",
      url: "https://hive.example.com/mcp",
      tool_prefix: "hive",
    });
    expect(out.auth_start_url).toBe("https://x");
  });

  it("registerMCPServer throws on non-ok response", async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 502 });
    await expect(
      registerMCPServer({ name: "X", url: "https://x/mcp" }),
    ).rejects.toThrow(/registerMCPServer 502/);
  });

  it("patchMCPServer PATCHes a subset of fields", async () => {
    global.fetch.mockResolvedValueOnce({ ok: true, status: 204 });
    await patchMCPServer("srv-1", { globally_enabled: false });
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/mcp/servers/srv-1"),
      expect.objectContaining({ method: "PATCH" }),
    );
  });

  it("patchMCPServer throws on non-ok response", async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 404 });
    await expect(patchMCPServer("srv-1", { name: "X" })).rejects.toThrow(
      /patchMCPServer 404/,
    );
  });

  it("deleteMCPServer DELETEs", async () => {
    global.fetch.mockResolvedValueOnce({ ok: true, status: 204 });
    await deleteMCPServer("srv-1");
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/mcp/servers/srv-1"),
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("deleteMCPServer throws on non-ok response", async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 404 });
    await expect(deleteMCPServer("srv-1")).rejects.toThrow(
      /deleteMCPServer 404/,
    );
  });

  it("reauthMCPServer POSTs and returns auth_start_url", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ server_id: "srv-1", auth_start_url: "https://x" }),
    });
    const out = await reauthMCPServer("srv-1");
    expect(out.auth_start_url).toBe("https://x");
  });

  it("reauthMCPServer throws on non-ok response", async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 404 });
    await expect(reauthMCPServer("srv-1")).rejects.toThrow(
      /reauthMCPServer 404/,
    );
  });

  it("getChatMCPSettings + putChatMCPSettings round trip", async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({ mode: "inherit", explicit_server_ids: [] }),
      })
      .mockResolvedValueOnce({ ok: true, status: 204 });
    const settings = await getChatMCPSettings("chat-1");
    expect(settings.mode).toBe("inherit");
    await putChatMCPSettings("chat-1", {
      mode: "explicit",
      explicit_server_ids: ["srv-1"],
    });
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  it("getChatMCPSettings throws on non-ok response", async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 404 });
    await expect(getChatMCPSettings("chat-1")).rejects.toThrow(
      /getChatMCPSettings 404/,
    );
  });

  it("putChatMCPSettings throws on non-ok response", async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 422 });
    await expect(
      putChatMCPSettings("chat-1", { mode: "inherit", explicit_server_ids: [] }),
    ).rejects.toThrow(/putChatMCPSettings 422/);
  });
});

// ---------------------------------------------------------------------------
// Admin wrappers (#238)
// ---------------------------------------------------------------------------

describe("admin API client", () => {
  // vi.stubGlobal + unstubAllGlobals (not a bare `global.fetch =`
  // assignment) so the mock cannot leak into later suites —
  // vi.restoreAllMocks() does not undo plain global assignments
  // (Copilot review, #343).
  let fetchMock;

  beforeEach(() => {
    localStorage.setItem("starter_mgmt_token", "test-token");
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    localStorage.removeItem("starter_mgmt_token");
    vi.unstubAllGlobals();
  });

  it("getAdminUsers GETs with auth and the default limit only", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ items: [], next_cursor: null }),
    });
    const data = await getAdminUsers();
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/admin/users?limit=50");
    expect(opts.headers.Authorization).toBe("Bearer test-token");
    expect(data).toEqual({ items: [], next_cursor: null });
  });

  it("getAdminUsers passes cursor, limit, and sort through the query", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ items: [], next_cursor: "c2" }),
    });
    await getAdminUsers({ cursor: "c1", limit: 5, sort: "email" });
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/admin/users?limit=5&cursor=c1&sort=email");
  });

  it("getAdminUsers throws ApiError with status + detail on a JSON error", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ detail: "invalid cursor" }),
    });
    const err = await getAdminUsers({ cursor: "bad" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
    expect(err.detail).toBe("invalid cursor");
    expect(err.message).toBe("getAdminUsers 400");
  });

  it("getAdminUsers nulls detail when the error body has none", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: () => Promise.resolve({}),
    });
    const err = await getAdminUsers().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(err.detail).toBeNull();
  });

  it("getAdminUsers nulls detail when the error body is not JSON", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: () => Promise.reject(new Error("not json")),
    });
    const err = await getAdminUsers().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(502);
    expect(err.detail).toBeNull();
  });

  it("getAdminUsers treats an ok-but-non-JSON body as unauthorized (CloudFront 403 rewrite)", async () => {
    // Through the deployed domain, CloudFront rewrites API 403s to a
    // 200 index.html — parsing that as JSON throws, and the wrapper
    // must surface unauthorized rather than a parse error.
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.reject(new SyntaxError("Unexpected token <")),
    });
    const err = await getAdminUsers().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
    expect(err.detail).toMatch(/unauthorized/);
  });

  it("getAdminUser GETs the encoded user id with auth", async () => {
    const body = { user: {}, recent_chats: [], recent_audit_events: [] };
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(body),
    });
    const data = await getAdminUser("amy@ex.com");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/admin/users/amy%40ex.com");
    expect(opts.headers.Authorization).toBe("Bearer test-token");
    expect(data).toEqual(body);
  });

  it("getAdminUser throws ApiError 404 for an unknown user", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: "User not found" }),
    });
    const err = await getAdminUser("ghost@ex.com").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.detail).toBe("User not found");
  });

  it("getAdminMetricsSummary GETs the summary endpoint with auth", async () => {
    const body = { today: {}, "7d": {}, "30d": {} };
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(body),
    });
    const data = await getAdminMetricsSummary();
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/admin/metrics/summary");
    expect(opts.headers.Authorization).toBe("Bearer test-token");
    expect(data).toEqual(body);
  });

  it("getAdminMetricsSummary surfaces a 503 as an ApiError with the detail body", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: () => Promise.resolve({ detail: { error: "metrics_unavailable" } }),
    });
    const err = await getAdminMetricsSummary().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(503);
    expect(err.detail).toEqual({ error: "metrics_unavailable" });
  });

  it("getAdminMetricsTimeseries GETs metric + window and omits an unset bucket", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ points: [] }),
    });
    await getAdminMetricsTimeseries({ metric: "ToolCallSuccesses", window: "7d" });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "/api/admin/metrics/timeseries?metric=ToolCallSuccesses&window=7d",
    );
    expect(opts.headers.Authorization).toBe("Bearer test-token");
  });

  it("getAdminMetricsTimeseries appends the bucket when one is supplied", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ points: [] }),
    });
    await getAdminMetricsTimeseries({
      metric: "MemoryWriteSuccesses",
      window: "24h",
      bucket: "5m",
    });
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "/api/admin/metrics/timeseries?metric=MemoryWriteSuccesses&window=24h&bucket=5m",
    );
  });

  it("getAdminMetricsTimeseries throws ApiError 422 for an unknown metric", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: () => Promise.resolve({ detail: "unknown metric" }),
    });
    const err = await getAdminMetricsTimeseries({
      metric: "NopeCounter",
      window: "7d",
    }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.detail).toBe("unknown metric");
  });
});
