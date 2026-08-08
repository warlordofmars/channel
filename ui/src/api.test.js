// Copyright (c) 2026 John Carter. All rights reserved.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  createChat,
  deleteChat,
  deleteMCPServer,
  enableFeaturedServer,
  finalizeAttachment,
  getAdminMetricsSummary,
  getAdminMetricsTimeseries,
  getAdminUser,
  getAdminUsers,
  getAsset,
  getAssetContent,
  getChat,
  getChatMCPSettings,
  getFeaturedServers,
  getPrefs,
  listAssets,
  listChatAssets,
  listChats,
  listMCPServers,
  listMemoryRecords,
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
import { LEGACY_TOKEN_KEY, TOKEN_KEY, parseToken } from "./lib/auth.js";

// A stored session that is nowhere near expiry, so `authHeader` takes the
// fast path and never reaches the silent-refresh branch. Suites that mean
// to exercise refreshing build their own session (see "silent refresh").
function liveSession(token) {
  return JSON.stringify({ access_token: token, expires_at: Date.now() + 3_600_000 });
}

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
    storage[TOKEN_KEY] = liveSession("tok-abc");
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
      delete storage[TOKEN_KEY];
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

  // ---- listChatAssets (#327) ---------------------------------------------

  describe("listChatAssets", () => {
    it("GETs the per-chat assets surface with Authorization", async () => {
      mockOk({ items: [{ asset_id: "a1" }] });
      const result = await listChatAssets("c1");
      expect(result).toEqual({ items: [{ asset_id: "a1" }] });
      expect(fetchMock.mock.calls[0][0]).toBe("/api/chats/c1/assets");
      expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe(
        "Bearer tok-abc",
      );
    });

    it("throws an ApiError carrying the status on non-ok", async () => {
      mockFail(404);
      const err = await listChatAssets("c1").catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(404);
    });
  });

  // ---- getAssetContent (#327) --------------------------------------------

  describe("getAssetContent", () => {
    it("GETs the content endpoint and returns the raw Response", async () => {
      const response = { ok: true, status: 200, blob: () => Promise.resolve() };
      fetchMock.mockResolvedValue(response);
      const result = await getAssetContent("c1", "a1");
      expect(result).toBe(response);
      expect(fetchMock.mock.calls[0][0]).toBe(
        "/api/chats/c1/assets/a1/content",
      );
      expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe(
        "Bearer tok-abc",
      );
    });

    it("throws ApiError 404 when the object is gone", async () => {
      mockFail(404);
      const err = await getAssetContent("c1", "a1").catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(404);
    });

    it("throws ApiError 502 on other S3 failures", async () => {
      mockFail(502);
      const err = await getAssetContent("c1", "a1").catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(502);
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
      delete storage[TOKEN_KEY];
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
    localStorage.setItem(TOKEN_KEY, liveSession("test-token"));
    global.fetch = vi.fn();
  });

  afterEach(() => {
    localStorage.removeItem(TOKEN_KEY);
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
    // Body defaults auth_type to oauth_dcr with a null token.
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.auth_type).toBe("oauth_dcr");
    expect(body.token).toBeNull();
  });

  it("registerMCPServer sends auth_type + token for the static-token path", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ server_id: "srv-static", auth_start_url: null }),
    });
    // Synthetic non-secret value (no GitHub PAT prefix) so secret
    // scanners don't flag the dummy fixture.
    const fakeBearer = "dummy-" + "bearer-value";
    const out = await registerMCPServer({
      name: "GitHub",
      url: "https://api.githubcopilot.com/mcp/",
      auth_type: "static_token",
      token: fakeBearer,
    });
    expect(out.auth_start_url).toBeNull();
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.auth_type).toBe("static_token");
    expect(body.token).toBe(fakeBearer);
  });

  it("registerMCPServer throws on non-ok response", async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 502 });
    await expect(
      registerMCPServer({ name: "X", url: "https://x/mcp" }),
    ).rejects.toThrow(/registerMCPServer 502/);
  });

  it("registerMCPServer surfaces the dcr_unsupported code from the error body", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: () =>
        Promise.resolve({
          detail: { code: "dcr_unsupported", message: "no DCR here" },
        }),
    });
    await expect(
      registerMCPServer({ name: "GitHub", url: "https://api.githubcopilot.com/mcp/" }),
    ).rejects.toMatchObject({ code: "dcr_unsupported", status: 400 });
  });

  it("registerMCPServer defaults detail to null when the body has no detail key", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: () => Promise.resolve({}),
    });
    let caught;
    try {
      await registerMCPServer({ name: "X", url: "https://x/mcp" });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught.detail).toBeNull();
    expect(caught.code).toBeUndefined();
  });

  it("registerMCPServer never parses/propagates detail when a token was supplied (no PAT echo)", async () => {
    // A static-token 422 body can echo the pasted PAT back in
    // detail[].input. With a token supplied, registerMCPServer must NOT
    // parse the body at all — the secret must not land on the error.
    const pastedSecret = "dummy-" + "pasted-secret";
    const jsonSpy = vi.fn(() =>
      Promise.resolve({ detail: [{ msg: "too short", input: pastedSecret }] }),
    );
    global.fetch.mockResolvedValueOnce({ ok: false, status: 422, json: jsonSpy });
    let caught;
    try {
      await registerMCPServer({
        name: "GitHub",
        url: "https://api.githubcopilot.com/mcp/",
        auth_type: "static_token",
        token: pastedSecret,
      });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    // Body was never read, so the secret can't be on the error object.
    expect(jsonSpy).not.toHaveBeenCalled();
    expect(caught.detail).toBeNull();
    expect(caught.code).toBeUndefined();
    expect(JSON.stringify(caught)).not.toContain(pastedSecret);
  });

  it("registerMCPServer tolerates a string detail (no code hoisted)", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ detail: "A static-token registration requires a token" }),
    });
    let caught;
    try {
      await registerMCPServer({ name: "X", url: "https://x/mcp" });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught.code).toBeUndefined();
    expect(caught.detail).toBe("A static-token registration requires a token");
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

  it("getFeaturedServers GETs the catalog with auth", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ servers: [{ featured_id: "github" }] }),
    });
    const data = await getFeaturedServers();
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/mcp/featured"),
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer test-token" }),
      }),
    );
    expect(data.servers[0].featured_id).toBe("github");
  });

  it("getFeaturedServers throws on non-ok response", async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(getFeaturedServers()).rejects.toThrow(/getFeaturedServers 500/);
  });

  it("enableFeaturedServer POSTs featured_id/name/url/token only (no prefix/enablement)", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ server_id: "srv-feat", auth_start_url: null }),
    });
    const fakeBearer = "dummy-" + "bearer-value";
    const out = await enableFeaturedServer({
      featured_id: "github",
      name: "GitHub",
      url: "https://api.githubcopilot.com/mcp/",
      token: fakeBearer,
    });
    expect(out.server_id).toBe("srv-feat");
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.method).toBe("POST");
    const body = JSON.parse(opts.body);
    // Exactly the four keys the server needs — never tool_prefix /
    // auth_type / globally_enabled (server pins those from the catalog).
    expect(Object.keys(body).sort()).toEqual(["featured_id", "name", "token", "url"]);
    expect(body.featured_id).toBe("github");
    expect(body.token).toBe(fakeBearer);
  });

  it("enableFeaturedServer defaults token to null when omitted", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ server_id: "srv-dcr", auth_start_url: "https://a" }),
    });
    const out = await enableFeaturedServer({
      featured_id: "acme",
      name: "Acme",
      url: "https://acme.example.com/mcp",
    });
    expect(out.auth_start_url).toBe("https://a");
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.token).toBeNull();
  });

  it("enableFeaturedServer throws ApiError WITHOUT reading the body when a token was supplied (no PAT echo)", async () => {
    // A static_token 422 can echo the pasted token in detail[].input; the
    // wrapper must never read the body when a token was supplied.
    const jsonSpy = vi.fn(() => Promise.resolve({ detail: "should-not-be-read" }));
    global.fetch.mockResolvedValueOnce({ ok: false, status: 422, json: jsonSpy });
    let caught;
    try {
      await enableFeaturedServer({
        featured_id: "github",
        name: "GitHub",
        url: "https://api.githubcopilot.com/mcp/",
        token: "dummy-" + "secret",
      });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught.status).toBe(422);
    expect(caught.detail).toBeNull();
    expect(jsonSpy).not.toHaveBeenCalled();
  });

  it("enableFeaturedServer preserves FastAPI detail on the tokenless (OAuth) path", async () => {
    // No token supplied → no secret to leak → the machine-readable detail
    // is preserved (mirrors registerMCPServer's oauth branch).
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: () =>
        Promise.resolve({ detail: { code: "unknown_featured_server" } }),
    });
    const err = await enableFeaturedServer({
      featured_id: "acme",
      name: "Acme",
      url: "https://acme.example.com/mcp",
    }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
    expect(err.detail).toEqual({ code: "unknown_featured_server" });
  });

  it("enableFeaturedServer nulls detail on the tokenless path when the body has no detail key", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: () => Promise.resolve({}),
    });
    const err = await enableFeaturedServer({
      featured_id: "acme",
      name: "Acme",
      url: "https://acme.example.com/mcp",
    }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.detail).toBeNull();
  });

  it("enableFeaturedServer nulls detail on the tokenless path when the body is not JSON", async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: () => Promise.reject(new Error("not JSON")),
    });
    const err = await enableFeaturedServer({
      featured_id: "acme",
      name: "Acme",
      url: "https://acme.example.com/mcp",
    }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(502);
    expect(err.detail).toBeNull();
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
    localStorage.setItem(TOKEN_KEY, liveSession("test-token"));
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    localStorage.removeItem(TOKEN_KEY);
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

// ---------------------------------------------------------------------------
// Assets API client (#328)
// ---------------------------------------------------------------------------

describe("assets API client", () => {
  let fetchMock;

  beforeEach(() => {
    localStorage.setItem(TOKEN_KEY, liveSession("test-token"));
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    localStorage.removeItem(TOKEN_KEY);
    vi.unstubAllGlobals();
  });

  describe("listAssets", () => {
    it("GETs /api/assets with auth and the default limit only", async () => {
      fetchMock.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ items: [], next_cursor: null }),
      });
      const data = await listAssets();
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/assets?limit=50");
      expect(opts.headers.Authorization).toBe("Bearer test-token");
      expect(data).toEqual({ items: [], next_cursor: null });
    });

    it("includes the cursor in the query string when provided", async () => {
      fetchMock.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ items: [], next_cursor: null }),
      });
      await listAssets({ limit: 10, cursor: "abc" });
      expect(fetchMock.mock.calls[0][0]).toBe("/api/assets?limit=10&cursor=abc");
    });

    it("throws ApiError carrying the status on a malformed cursor (400)", async () => {
      fetchMock.mockResolvedValueOnce({
        ok: false,
        status: 400,
        json: () => Promise.resolve({ detail: "invalid cursor" }),
      });
      const err = await listAssets({ cursor: "bad" }).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(400);
    });
  });

  describe("getAsset", () => {
    it("GETs the per-chat descriptor with auth", async () => {
      const cardBody = { asset_id: "as-1", chat_id: "ch-1", kind: "code" };
      fetchMock.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(cardBody),
      });
      const data = await getAsset("ch-1", "as-1");
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/chats/ch-1/assets/as-1");
      expect(opts.headers.Authorization).toBe("Bearer test-token");
      expect(data).toEqual(cardBody);
    });

    it("throws ApiError 404 when the descriptor is gone", async () => {
      fetchMock.mockResolvedValueOnce({
        ok: false,
        status: 404,
        json: () => Promise.resolve({ detail: "Asset not found" }),
      });
      const err = await getAsset("ch-1", "missing").catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(404);
    });
  });

  describe("getAssetContent", () => {
    it("GETs the content route and returns the bare Response", async () => {
      const response = { ok: true, blob: async () => new Blob(["x"]) };
      fetchMock.mockResolvedValueOnce(response);
      const result = await getAssetContent("ch-1", "as-1");
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("/api/chats/ch-1/assets/as-1/content");
      expect(opts.headers.Authorization).toBe("Bearer test-token");
      expect(result).toBe(response);
    });

    it("throws ApiError with the status on a content failure (502)", async () => {
      fetchMock.mockResolvedValueOnce({
        ok: false,
        status: 502,
        json: () => Promise.resolve({ detail: "Asset content unavailable" }),
      });
      const err = await getAssetContent("ch-1", "as-1").catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(502);
    });
  });
});

// ---------------------------------------------------------------------------
// Silent refresh (#295, epic #241)
// ---------------------------------------------------------------------------
//
// `api.js` keeps two pieces of module-level state — the single-flight slot
// and the post-failure cooldown — so each test here imports a FRESH module
// instance via `vi.resetModules()` rather than reaching into the module to
// reset it. That keeps the reset machinery out of production code.

describe("silent refresh", () => {
  const HOUR_MS = 3_600_000;
  let storage;
  let fetchMock;
  let assign;

  function jwt(expOffsetSeconds) {
    const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
    return `eyJhbGciOiJIUzI1NiJ9.${btoa(JSON.stringify({ exp, sub: "u@example.com" }))}.sig`;
  }

  // What /auth/refresh hands back. Must be structurally a JWT — `saveSession`
  // refuses to persist anything else rather than writing an unvalidated
  // response body into browser storage.
  const ROTATED = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJyb3RhdGVkIn0.sig";

  /** Store a session whose access token expires in `ms` from now. */
  function storeSession(token, ms) {
    storage[TOKEN_KEY] = JSON.stringify({ access_token: token, expires_at: Date.now() + ms });
  }

  function refreshCalls() {
    return fetchMock.mock.calls.filter(([url]) => url === "/auth/refresh");
  }

  function apiCalls() {
    return fetchMock.mock.calls.filter(([url]) => url !== "/auth/refresh");
  }

  /** Answer /auth/refresh from `refreshResponse`; everything else 200 {}. */
  function route(refreshResponse) {
    fetchMock.mockImplementation((url) =>
      url === "/auth/refresh"
        ? Promise.resolve(refreshResponse)
        : Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }),
    );
  }

  function refreshOk(body) {
    return { ok: true, status: 200, json: () => Promise.resolve(body) };
  }

  async function freshApi() {
    vi.resetModules();
    return import("./api.js");
  }

  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = v; },
      removeItem: (k) => { delete storage[k]; },
    });
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    assign = vi.fn();
    vi.stubGlobal("location", { assign });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("does not refresh while the token is comfortably alive", async () => {
    storeSession("live", HOUR_MS);
    route(refreshOk({}));
    const api = await freshApi();
    await api.listModels();
    expect(refreshCalls()).toHaveLength(0);
    expect(apiCalls()[0][1].headers.Authorization).toBe("Bearer live");
  });

  it("refreshes inside the 5-minute skew window and sends the new token", async () => {
    storeSession("stale", 60_000);
    route(refreshOk({ access_token: ROTATED, token_type: "bearer", expires_in: 3600 }));
    const api = await freshApi();
    await api.listModels();

    expect(refreshCalls()).toHaveLength(1);
    const [, init] = refreshCalls()[0];
    expect(init.method).toBe("POST");
    // Non-empty CSRF header; the value itself is never inspected server-side.
    expect(init.headers["X-Channel-Refresh"]).toBeTruthy();
    // No body — the refresh token rides the HttpOnly cookie, which JS
    // cannot read and therefore cannot send explicitly.
    expect(init.body).toBeUndefined();
    expect(init.credentials).toBe("include");

    expect(apiCalls()[0][1].headers.Authorization).toBe(`Bearer ${ROTATED}`);
    const saved = JSON.parse(storage[TOKEN_KEY]);
    expect(saved.access_token).toBe(ROTATED);
    expect(saved.expires_at).toBeGreaterThan(Date.now() + 3_000_000);
  });

  it("collapses concurrent callers onto ONE rotation", async () => {
    // The load-bearing test: #290 hard-rotates on every use and reads a
    // re-presented token as a reuse breach that kills the device family,
    // so a second concurrent refresh would sign the user out.
    storeSession("stale", 60_000);
    route(refreshOk({ access_token: ROTATED, expires_in: 3600 }));
    const api = await freshApi();

    await Promise.all([api.listModels(), api.listModels(), api.listModels(), api.listModels()]);

    expect(refreshCalls()).toHaveLength(1);
    expect(apiCalls()).toHaveLength(4);
    for (const [, init] of apiCalls()) {
      expect(init.headers.Authorization).toBe(`Bearer ${ROTATED}`);
    }
  });

  it("refreshes again once the stored deadline has moved on", async () => {
    storeSession("stale", 60_000);
    route(refreshOk({ access_token: ROTATED, expires_in: 3600 }));
    const api = await freshApi();
    await api.listModels();
    await api.listModels();
    // Second call reads the refreshed deadline, so it takes the fast path.
    expect(refreshCalls()).toHaveLength(1);
  });

  it("migrates a legacy-key session onto the new key when it refreshes", async () => {
    storage[LEGACY_TOKEN_KEY] = jwt(60);
    route(refreshOk({ access_token: ROTATED, expires_in: 3600 }));
    const api = await freshApi();
    await api.listModels();

    expect(refreshCalls()).toHaveLength(1);
    expect(JSON.parse(storage[TOKEN_KEY]).access_token).toBe(ROTATED);
    expect(storage[LEGACY_TOKEN_KEY]).toBeUndefined();
  });

  it("keeps a still-valid token when the refresh is refused", async () => {
    // Bypass logins, and desktop sessions signed in before #297, have no
    // refresh credential; /auth/refresh answers 401 for them by design.
    // Signing them out here would evict them EARLIER than doing nothing.
    const token = jwt(60);
    storeSession(token, 60_000);
    route({ ok: false, status: 401, json: () => Promise.resolve({}) });
    const api = await freshApi();
    await api.listModels();

    expect(apiCalls()[0][1].headers.Authorization).toBe(`Bearer ${token}`);
    expect(assign).not.toHaveBeenCalled();
    expect(storage[TOKEN_KEY]).toBeDefined();
  });

  it("ends the session when the refresh is refused and the token is dead", async () => {
    storeSession(jwt(-60), -60_000);
    storage[LEGACY_TOKEN_KEY] = "leftover";
    route({ ok: false, status: 401, json: () => Promise.resolve({}) });
    const api = await freshApi();
    await api.listModels();

    expect(storage[TOKEN_KEY]).toBeUndefined();
    expect(storage[LEGACY_TOKEN_KEY]).toBeUndefined();
    expect(assign).toHaveBeenCalledWith("/app/login");
    expect(apiCalls()[0][1].headers.Authorization).toBeUndefined();
  });

  it("refuses to persist a malformed access_token from a 200", async () => {
    // Storage-poisoning guard: the response body is off-device input, so
    // a non-JWT value is rejected rather than written and then replayed
    // as a credential on every later request.
    const token = jwt(60);
    storeSession(token, 60_000);
    route(refreshOk({ access_token: "<script>", expires_in: 3600 }));
    const api = await freshApi();
    await api.listModels();

    expect(JSON.parse(storage[TOKEN_KEY]).access_token).toBe(token);
    expect(apiCalls()[0][1].headers.Authorization).toBe(`Bearer ${token}`);
    expect(assign).not.toHaveBeenCalled();
  });

  it("keeps local state when a 200 carries no access_token", async () => {
    // A malformed body is a broken server, not an authoritative "your
    // credential is no good" — only a 4xx refusal ends the session.
    const token = jwt(-60);
    storeSession(token, -60_000);
    route(refreshOk({ token_type: "bearer" }));
    const api = await freshApi();
    await api.listModels();
    expect(assign).not.toHaveBeenCalled();
    expect(storage[TOKEN_KEY]).toBeDefined();
  });

  it("does NOT sign out on a 429 — a rate limit is not a credential verdict", async () => {
    // #294 is about to add rate limiting to /auth/refresh. A 429 arrives
    // in bursts by definition, so reading any 4xx as "signed out" would
    // turn throttling into a mass logout. Only 401 is a verdict.
    const token = jwt(-60);
    storeSession(token, -60_000);
    route({ ok: false, status: 429, json: () => Promise.resolve({}) });
    const api = await freshApi();
    await api.listModels();

    expect(assign).not.toHaveBeenCalled();
    expect(JSON.parse(storage[TOKEN_KEY]).access_token).toBe(token);
  });

  it("does NOT sign out on a 403 — that means our CSRF header was missing", async () => {
    const token = jwt(-60);
    storeSession(token, -60_000);
    route({ ok: false, status: 403, json: () => Promise.resolve({}) });
    const api = await freshApi();
    await api.listModels();
    expect(assign).not.toHaveBeenCalled();
  });

  it("keeps local state when the refresh endpoint cannot be reached", async () => {
    // A transient blip that happens to straddle expiry must not destroy a
    // session whose refresh cookie is still perfectly good.
    const token = jwt(-60);
    storeSession(token, -60_000);
    fetchMock.mockImplementation((url) =>
      url === "/auth/refresh"
        ? Promise.reject(new TypeError("Failed to fetch"))
        : Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }),
    );
    const api = await freshApi();
    await api.listModels();

    expect(assign).not.toHaveBeenCalled();
    expect(JSON.parse(storage[TOKEN_KEY]).access_token).toBe(token);
    expect(apiCalls()[0][1].headers.Authorization).toBe(`Bearer ${token}`);
  });

  it("falls back to the token's exp claim when expires_in is missing", async () => {
    // Never `Date.now() + 0`: that is truthy, so it would win the fallback
    // and store an already-dead deadline, re-rotating once per API call.
    const rotated = jwt(3600);
    storeSession("stale", 60_000);
    route(refreshOk({ access_token: rotated }));
    const api = await freshApi();
    await api.listModels();
    expect(JSON.parse(storage[TOKEN_KEY]).expires_at).toBe(parseToken(rotated).exp * 1000);
  });

  it("stops retrying for a cooldown after a refusal", async () => {
    const token = jwt(60);
    storeSession(token, 60_000);
    route({ ok: false, status: 401, json: () => Promise.resolve({}) });
    const api = await freshApi();

    await api.listModels();
    await api.listModels();
    await api.listModels();

    // One attempt, not one per call — #294 rate-limits this endpoint.
    expect(refreshCalls()).toHaveLength(1);
    expect(apiCalls()).toHaveLength(3);
  });

  it("rotates again once the cooldown lapses — the single-flight slot is released", async () => {
    // The slot holds one *attempt*, not one page's worth. A settled promise
    // left in it would answer every later caller with the first attempt's
    // verdict, so a session that recovers from a transient blip would never
    // renew again — the failure mode the release exists to prevent.
    vi.useFakeTimers();
    storeSession(jwt(3600), 60_000);
    let seen = 0;
    fetchMock.mockImplementation((url) => {
      if (url !== "/auth/refresh") {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
      }
      seen += 1;
      return seen === 1
        ? Promise.reject(new TypeError("Failed to fetch"))
        : Promise.resolve(refreshOk({ access_token: ROTATED, expires_in: 3600 }));
    });
    const api = await freshApi();

    await api.listModels();
    expect(refreshCalls()).toHaveLength(1);

    vi.setSystemTime(Date.now() + 31_000); // past the 30s cooldown
    await api.listModels();

    expect(refreshCalls()).toHaveLength(2);
    expect(apiCalls()[1][1].headers.Authorization).toBe(`Bearer ${ROTATED}`);
  });

  it("never refreshes when there is no session at all", async () => {
    route(refreshOk({}));
    const api = await freshApi();
    await api.listModels();
    // AuthGate owns the redirect for a cold no-token load; competing with
    // it here would race two navigations.
    expect(refreshCalls()).toHaveLength(0);
    expect(assign).not.toHaveBeenCalled();
    expect(apiCalls()[0][1].headers.Authorization).toBeUndefined();
  });

  // -------------------------------------------------------------------------
  // Cross-document single-flight (#495)
  // -------------------------------------------------------------------------
  //
  // The suite above proves single-flight WITHIN a document. Two tabs are two
  // module instances sharing one cookie jar, so `vi.resetModules()` twice is
  // a faithful stand-in for two documents: separate module state, one shared
  // `localStorage`, one shared `navigator.locks`.
  //
  // What is NOT reproducible here — and stays a human check — is the real
  // thing: two live tabs whose access tokens cross the 5-minute skew window
  // at the same moment against a server that hard-rotates.
  describe("cross-document lock (#495)", () => {
    /** Mirrors `REFRESH_LOCK_WAIT_MS` in api.js, which is not exported. */
    const LOCK_WAIT_MS = 8_000;

    /** Let every pending microtask AND macrotask drain. */
    function settle() {
      return new Promise((resolve) => { setTimeout(resolve, 0); });
    }

    /**
     * Minimal exclusive-mode `navigator.locks` stand-in — jsdom ships none,
     * so the lock path is otherwise unreachable.
     *
     * Models only what `api.js` uses: exclusive mode, FIFO grant order,
     * release when the callback settles, and an `AbortSignal` that drops a
     * request still queued. Faithful on the property under test — a waiter's
     * abort releases only its own queue slot, never the live holder's.
     */
    function installWebLocks() {
      const tails = new Map();
      vi.stubGlobal("navigator", {
        locks: {
          request(name, options, callback) {
            const predecessor = tails.get(name) ?? Promise.resolve();
            let release;
            const held = new Promise((resolve) => { release = resolve; });
            tails.set(name, predecessor.then(() => held));

            const abandoned = new Promise((_, reject) => {
              options.signal.addEventListener("abort", () => {
                reject(Object.assign(new Error("lock request aborted"), { name: "AbortError" }));
              });
            });

            return Promise.race([predecessor, abandoned]).then(
              () => Promise.resolve(callback()).finally(release),
              (error) => { release(); throw error; },
            );
          },
        },
      });
    }

    /** Answer /auth/refresh differently per call; everything else 200 {}. */
    function routeInTurn(...responses) {
      let seen = 0;
      fetchMock.mockImplementation((url) => {
        if (url !== "/auth/refresh") {
          return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
        }
        const answer = responses[seen];
        seen += 1;
        return answer();
      });
    }

    function authHeaders() {
      return apiCalls().map(([, init]) => init.headers.Authorization);
    }

    it("serialises two documents onto ONE rotation, and the loser reuses the winner's token", async () => {
      // The load-bearing test. Without the lock both documents present the
      // same refresh token; #290 reads the second as an OAuth 2.1 reuse
      // breach and revokes the whole device family.
      installWebLocks();
      storeSession("stale", 60_000);

      let answerRefresh;
      const heldOpen = new Promise((resolve) => { answerRefresh = resolve; });
      routeInTurn(() => heldOpen.then(() => refreshOk({ access_token: ROTATED, expires_in: 3600 })));

      const tabA = await freshApi();
      const tabB = await freshApi();
      const a = tabA.listModels();
      const b = tabB.listModels();
      // Both documents are now queued on the lock; A holds it, B waits.
      await settle();
      answerRefresh();
      await Promise.all([a, b]);

      expect(refreshCalls()).toHaveLength(1);
      // B did not fall back to its stale token either — it read the session
      // A wrote while holding the lock.
      expect(authHeaders()).toEqual([`Bearer ${ROTATED}`, `Bearer ${ROTATED}`]);
      expect(assign).not.toHaveBeenCalled();
    });

    it("leaves no pending timer behind once the lock is released", async () => {
      // The wait bound is a timer armed on every rotation. Left uncleared it
      // would fire ~8s later against an AbortController nobody is watching —
      // harmless in itself, but one stray timer per API-call burst.
      vi.useFakeTimers();
      installWebLocks();
      storeSession("stale", 60_000);
      route(refreshOk({ access_token: ROTATED, expires_in: 3600 }));
      const api = await freshApi();
      await api.listModels();

      expect(refreshCalls()).toHaveLength(1);
      expect(vi.getTimerCount()).toBe(0);
    });

    it("disarms the wait timer the moment the lock is granted, so it can never reach the hold", async () => {
      // The bound is on the WAIT. Leaving the timer armed while we hold the
      // lock would point it at our own in-flight rotation — and the spec's
      // "aborting after grant is a no-op" is the wrong thing to be relying
      // on for a property this module states outright.
      vi.useFakeTimers();
      installWebLocks();
      storeSession("stale", 60_000);
      let answerRefresh;
      const heldOpen = new Promise((resolve) => { answerRefresh = resolve; });
      routeInTurn(() => heldOpen.then(() => refreshOk({ access_token: ROTATED, expires_in: 3600 })));

      const api = await freshApi();
      const call = api.listModels();
      await vi.advanceTimersByTimeAsync(0); // lock granted; rotation in flight

      expect(refreshCalls()).toHaveLength(1);
      expect(vi.getTimerCount()).toBe(0);

      answerRefresh();
      await call;
      expect(authHeaders()).toEqual([`Bearer ${ROTATED}`]);
    });

    it("rotates twice across two documents WITHOUT Web Locks — the gap this closes", async () => {
      // Pins what the lock is actually buying: identical setup to the test
      // above, minus `navigator.locks`, and the second document presents a
      // token the first has already consumed.
      vi.stubGlobal("navigator", {});
      storeSession("stale", 60_000);
      route(refreshOk({ access_token: ROTATED, expires_in: 3600 }));

      const tabA = await freshApi();
      const tabB = await freshApi();
      await Promise.all([tabA.listModels(), tabB.listModels()]);

      expect(refreshCalls()).toHaveLength(2);
    });

    it("keeps per-document single-flight when Web Locks are unavailable", async () => {
      // Absent in non-secure contexts and older browsers. Degrading must
      // land on the pre-#495 behaviour, never on no single-flight at all.
      vi.stubGlobal("navigator", {});
      storeSession("stale", 60_000);
      route(refreshOk({ access_token: ROTATED, expires_in: 3600 }));
      const api = await freshApi();

      await Promise.all([
        api.listModels(),
        api.listModels(),
        api.listModels(),
        api.listModels(),
      ]);

      expect(refreshCalls()).toHaveLength(1);
      expect(authHeaders()).toEqual(Array(4).fill(`Bearer ${ROTATED}`));
    });

    it("keeps per-document single-flight when there is no navigator at all", async () => {
      vi.stubGlobal("navigator", undefined);
      storeSession("stale", 60_000);
      route(refreshOk({ access_token: ROTATED, expires_in: 3600 }));
      const api = await freshApi();

      await Promise.all([api.listModels(), api.listModels()]);

      expect(refreshCalls()).toHaveLength(1);
    });

    it("falls back to an unguarded rotation when the lock machinery itself fails", async () => {
      vi.useFakeTimers();
      vi.stubGlobal("navigator", {
        locks: { request: () => Promise.reject(new Error("lock manager unavailable")) },
      });
      storeSession("stale", 60_000);
      route(refreshOk({ access_token: ROTATED, expires_in: 3600 }));
      const api = await freshApi();
      await api.listModels();

      expect(refreshCalls()).toHaveLength(1);
      expect(authHeaders()).toEqual([`Bearer ${ROTATED}`]);
      // Never granted, so only the outer cleanup can have disarmed the timer.
      expect(vi.getTimerCount()).toBe(0);
    });

    it("abandons the wait for a stalled holder rather than wedging behind it", async () => {
      // An unbounded lock is a worse failure than the bug it fixes: one tab
      // on a stalled connection would freeze every other tab's API calls.
      // The holder is never cut off — only the waiter gives up.
      vi.useFakeTimers();
      installWebLocks();
      storeSession("stale", 60_000);
      routeInTurn(
        () => new Promise(() => {}), // holder: never answers
        () => Promise.resolve(refreshOk({ access_token: ROTATED, expires_in: 3600 })),
      );

      const tabA = await freshApi();
      const tabB = await freshApi();
      const stalled = tabA.listModels(); // deliberately never settles
      const b = tabB.listModels();

      await vi.advanceTimersByTimeAsync(LOCK_WAIT_MS);
      await b;

      expect(refreshCalls()).toHaveLength(2);
      expect(authHeaders()).toEqual([`Bearer ${ROTATED}`]);
      // The stalled document is still stalled — the waiter escaped, it did
      // not cancel anyone else's in-flight rotation.
      await expect(Promise.race([stalled, Promise.resolve("pending")])).resolves.toBe("pending");
    });

    it("reuses a session written while it waited, instead of rotating on escape", async () => {
      // Several documents queued behind one stalled holder each escape on
      // their own timer. Without this re-read that is a *cascade* of reuse
      // breaches rather than one: up to 8s have passed since the caller last
      // looked, so whoever escaped first may already have rotated.
      vi.useFakeTimers();
      installWebLocks();
      storeSession("stale", 60_000);
      routeInTurn(() => new Promise(() => {})); // holder: never answers

      const tabA = await freshApi();
      const tabB = await freshApi();
      tabA.listModels(); // deliberately never settles
      const b = tabB.listModels();

      await vi.advanceTimersByTimeAsync(LOCK_WAIT_MS / 2);
      storeSession(ROTATED, HOUR_MS); // another document finished its rotation
      await vi.advanceTimersByTimeAsync(LOCK_WAIT_MS / 2);
      await b;

      // Only the stalled holder's request was ever issued.
      expect(refreshCalls()).toHaveLength(1);
      expect(authHeaders()).toEqual([`Bearer ${ROTATED}`]);
    });

    it("releases the lock when the rotation fails, so the next document can retry", async () => {
      const token = jwt(60);
      installWebLocks();
      storeSession(token, 60_000);
      routeInTurn(
        () => Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) }),
        () => Promise.resolve(refreshOk({ access_token: ROTATED, expires_in: 3600 })),
      );

      const tabA = await freshApi();
      const tabB = await freshApi();
      await Promise.all([tabA.listModels(), tabB.listModels()]);

      // A's 401 propagated as a failure (rather than being retried outside
      // the lock) and released the lock; B then got its own attempt.
      expect(refreshCalls()).toHaveLength(2);
      expect(authHeaders()).toEqual([`Bearer ${token}`, `Bearer ${ROTATED}`]);
      // 401 + a still-usable access token is not a sign-out (#295).
      expect(assign).not.toHaveBeenCalled();
    });
  });

  it("endSession clears both keys and routes to the login page", async () => {
    storage[TOKEN_KEY] = JSON.stringify({ access_token: "a", expires_at: 1 });
    storage[LEGACY_TOKEN_KEY] = "b";
    const api = await freshApi();
    api.endSession();
    expect(storage[TOKEN_KEY]).toBeUndefined();
    expect(storage[LEGACY_TOKEN_KEY]).toBeUndefined();
    expect(assign).toHaveBeenCalledWith("/app/login");
  });

  // -------------------------------------------------------------------------
  // Soft session-end redirect (#483)
  // -------------------------------------------------------------------------
  //
  // Every test here asserts BOTH halves: that the intended redirect fired
  // AND that the other one did not. "A redirect happened" alone would pass
  // against the pre-#483 code, which always hard-navigated — the whole
  // defect is *which* mechanism runs, since only `location.assign` tears
  // the document down (the Electron white flash + focus steal).

  describe("session-end redirect", () => {
    it("routes through a registered navigator and never touches the document", async () => {
      const navigate = vi.fn();
      storage[TOKEN_KEY] = JSON.stringify({ access_token: "a", expires_at: 1 });
      const api = await freshApi();
      api.setSessionEndNavigator(navigate);

      api.endSession();

      expect(navigate).toHaveBeenCalledWith("/app/login", { replace: true });
      // The load-bearing half: no full-document navigation. `replace`
      // matters too — a destroyed session must not sit on the history
      // stack for Back to return to.
      expect(assign).not.toHaveBeenCalled();
    });

    it("clears local state BEFORE it navigates", async () => {
      // Ordering is unchanged from the hard-navigation version, and a
      // router-aware gate can re-render synchronously inside `navigate`;
      // it must never observe a session that is already over as live.
      let storedAtNavigate;
      const navigate = vi.fn(function captureStorage() {
        storedAtNavigate = storage[TOKEN_KEY];
      });
      storage[TOKEN_KEY] = JSON.stringify({ access_token: "a", expires_at: 1 });
      storage[LEGACY_TOKEN_KEY] = "b";
      const api = await freshApi();
      api.setSessionEndNavigator(navigate);

      api.endSession();

      expect(navigate).toHaveBeenCalled();
      expect(storedAtNavigate).toBeUndefined();
      expect(storage[LEGACY_TOKEN_KEY]).toBeUndefined();
    });

    it("gives up softly on the real path — a dead token plus a refused refresh", async () => {
      // The reported symptom arrives here, not via a direct `endSession`
      // call: `authHeader` -> `ensureAccessToken` hits the 401-only rule
      // and gives up mid-session. #291's 1h TTL makes it hourly.
      const navigate = vi.fn();
      storeSession(jwt(-60), -60_000);
      route({ ok: false, status: 401, json: () => Promise.resolve({}) });
      const api = await freshApi();
      api.setSessionEndNavigator(navigate);

      await api.listModels();

      expect(navigate).toHaveBeenCalledWith("/app/login", { replace: true });
      expect(assign).not.toHaveBeenCalled();
      expect(storage[TOKEN_KEY]).toBeUndefined();
    });

    it("hard-navigates when nothing has registered", async () => {
      // Load-bearing fallback: `endSession` can fire before any component
      // mounts. Reaching the login page the ugly way beats not reaching it.
      storage[TOKEN_KEY] = JSON.stringify({ access_token: "a", expires_at: 1 });
      const api = await freshApi();

      api.endSession();

      expect(assign).toHaveBeenCalledWith("/app/login");
    });

    it("hard-navigates again once the navigator is unregistered", async () => {
      // The registrant unregisters on unmount, because `api.js` cannot
      // tell a live `navigate` from one belonging to a torn-down router.
      const navigate = vi.fn();
      const api = await freshApi();
      api.setSessionEndNavigator(navigate);
      api.setSessionEndNavigator(null);

      api.endSession();

      expect(navigate).not.toHaveBeenCalled();
      expect(assign).toHaveBeenCalledWith("/app/login");
    });

    it("degrades to the document when the registration is not callable", async () => {
      // Nothing validates the registration, because the fallback below
      // already covers it: a bad registrant loses the soft redirect, not
      // the redirect.
      const api = await freshApi();
      api.setSessionEndNavigator("/app/login");

      expect(() => api.endSession()).not.toThrow();
      expect(assign).toHaveBeenCalledWith("/app/login");
    });

    it("falls back to the document when the navigator throws", async () => {
      // `ensureAccessToken` and `useChatStream` both treat `endSession` as
      // infallible, so an escaping error would skip the redirect AND break
      // the caller. A torn-down router must degrade, not detonate.
      const navigate = vi.fn(function deadRouter() {
        throw new Error("router unmounted");
      });
      const api = await freshApi();
      api.setSessionEndNavigator(navigate);

      expect(() => api.endSession()).not.toThrow();
      expect(navigate).toHaveBeenCalled();
      expect(assign).toHaveBeenCalledWith("/app/login");
    });
  });

  it("sends the refresh cookie with the logout revoke", async () => {
    storeSession("live", HOUR_MS);
    fetchMock.mockResolvedValue({ ok: true, status: 204 });
    const api = await freshApi();
    await api.logout();
    expect(fetchMock.mock.calls[0][1].credentials).toBe("include");
  });

  it("logs out without refreshing first, and dispatches in the same tick", async () => {
    // Sidebar.signOut fires logout() and then navigates synchronously. If
    // logout awaited a refresh, the navigation would win and the request
    // would never leave — silently losing the family revoke and the jti
    // denylist write, which is the half of logout that ends the session.
    // Rotating a family one instant before revoking it is waste anyway.
    const token = jwt(60);
    storeSession(token, 60_000);
    route({ ok: true, status: 204, json: () => Promise.resolve({}) });
    const api = await freshApi();

    const pending = api.logout();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/auth/logout");
    await pending;
    expect(refreshCalls()).toHaveLength(0);
    expect(apiCalls()[0][1].headers.Authorization).toBe(`Bearer ${token}`);
  });

  // -------------------------------------------------------------------------
  // Desktop body transport (#297)
  // -------------------------------------------------------------------------

  describe("desktop body transport", () => {
    let keychain;

    function installKeychain(stored) {
      keychain = {
        read: vi.fn().mockResolvedValue(stored),
        write: vi.fn().mockResolvedValue(undefined),
        clear: vi.fn().mockResolvedValue(undefined),
      };
      vi.stubGlobal("channelDesktop", { isDesktop: true, tokenStorage: keychain });
      return keychain;
    }

    it("sends the keychain's refresh token in the request body", async () => {
      // Electron cannot present the HttpOnly cookie, so the token the OS
      // keychain is holding travels in the body instead. This is the whole
      // reason a desktop session stops dying at the 1h access-token expiry.
      installKeychain({ refresh_token: "rt-desktop" });
      storeSession("stale", 60_000);
      route(refreshOk({ access_token: ROTATED, expires_in: 3600, refresh_token: "rt-next" }));
      const api = await freshApi();
      await api.listModels();

      const [, init] = refreshCalls()[0];
      expect(JSON.parse(init.body)).toEqual({ refresh_token: "rt-desktop" });
      expect(init.headers["Content-Type"]).toBe("application/json");
      expect(init.headers["X-Channel-Refresh"]).toBeTruthy();
      expect(apiCalls()[0][1].headers.Authorization).toBe(`Bearer ${ROTATED}`);
    });

    it("persists the rotated successor back to the keychain", async () => {
      const k = installKeychain({ refresh_token: "rt-desktop" });
      storeSession("stale", 60_000);
      route(refreshOk({ access_token: ROTATED, expires_in: 3600, refresh_token: "rt-next" }));
      const api = await freshApi();
      await api.listModels();

      expect(k.write).toHaveBeenCalledWith({ refresh_token: "rt-next" });
      // ...and never into localStorage, where any script in the renderer
      // could read a 30-day credential.
      expect(JSON.stringify(storage)).not.toContain("rt-next");
    });

    it("stores the successor BEFORE the access token", async () => {
      // `saveSession` throws on a malformed access token. The presented
      // refresh token is already dead server-side by then, so doing the
      // access token first would strand a session whose only remaining
      // long-lived credential was never written anywhere.
      const k = installKeychain({ refresh_token: "rt-desktop" });
      storeSession("stale", 60_000);
      route(refreshOk({ access_token: "not-a-jwt", expires_in: 3600, refresh_token: "rt-next" }));
      const api = await freshApi();
      await api.listModels();

      expect(k.write).toHaveBeenCalledWith({ refresh_token: "rt-next" });
    });

    it("clears the keychain when a rotation comes back without a successor", async () => {
      const k = installKeychain({ refresh_token: "rt-desktop" });
      storeSession("stale", 60_000);
      route(refreshOk({ access_token: ROTATED, expires_in: 3600 }));
      const api = await freshApi();
      await api.listModels();

      expect(k.clear).toHaveBeenCalled();
      expect(k.write).not.toHaveBeenCalled();
    });

    it("falls back to the bodyless cookie shape when the keychain is empty", async () => {
      // A desktop session signed in before #297, or one minted by a
      // bypass login that deliberately mints no refresh family.
      installKeychain(null);
      storeSession("stale", 60_000);
      route(refreshOk({ access_token: ROTATED, expires_in: 3600 }));
      const api = await freshApi();
      await api.listModels();

      const [, init] = refreshCalls()[0];
      expect(init.body).toBeUndefined();
      expect(init.headers["Content-Type"]).toBeUndefined();
    });

    it("still collapses concurrent callers onto ONE rotation", async () => {
      // Desktop reads the keychain before rotating, which puts an extra
      // await ahead of the fetch — but the single-flight promise is
      // assigned synchronously, so nothing can slip in behind it. #290
      // would read a second rotation as a reuse breach and revoke the
      // whole device family.
      installKeychain({ refresh_token: "rt-desktop" });
      storeSession("stale", 60_000);
      route(refreshOk({ access_token: ROTATED, expires_in: 3600, refresh_token: "rt-next" }));
      const api = await freshApi();

      await Promise.all([api.listModels(), api.listModels(), api.listModels()]);
      expect(refreshCalls()).toHaveLength(1);
    });
  });
});

// ---------------------------------------------------------------------------
// Memory records (#475, epic #129) — backs the "What Channel remembers" panel
// ---------------------------------------------------------------------------

describe("memory records API client", () => {
  let fetchMock;

  beforeEach(() => {
    localStorage.setItem(TOKEN_KEY, liveSession("test-token"));
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    localStorage.removeItem(TOKEN_KEY);
    vi.unstubAllGlobals();
  });

  const emptyPage = {
    groups: [],
    summaries: [],
    recall_window: {
      max_sessions: 5,
      events_per_session: 2,
      text_truncate: 120,
      ordering: "recency",
      enabled: true,
    },
    withheld_record_count: 0,
    next_cursor: null,
  };

  it("GETs /api/memory/records with auth and the default chats-per-page limit", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(emptyPage) });
    const data = await listMemoryRecords();
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/memory/records?limit=10");
    expect(opts.headers.Authorization).toBe("Bearer test-token");
    expect(data).toEqual(emptyPage);
  });

  it("carries the cursor and the chat scope in the query string", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(emptyPage) });
    await listMemoryRecords({ limit: 25, cursor: "cur-1", chatId: "chat-7" });
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/memory/records?limit=25&cursor=cur-1&chat_id=chat-7",
    );
  });

  it("throws ApiError carrying the status on a malformed cursor (400)", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ detail: "invalid cursor" }),
    });
    const err = await listMemoryRecords({ cursor: "bad" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });

  it("throws ApiError 404 when the chat scope names a chat the caller doesn't own", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: "Chat not found" }),
    });
    const err = await listMemoryRecords({ chatId: "someone-elses" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });
});
