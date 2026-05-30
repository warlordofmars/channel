// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockElectron } from "./_helpers.js";

vi.mock("electron", () => mockElectron());

const { pickFreePort, generateState, startLoopback } = await import("../main/auth.js");

beforeEach(() => {
  vi.clearAllMocks();
});

describe("pickFreePort", () => {
  it("returns a port in the ephemeral range [49152, 65535]", async () => {
    const port = await pickFreePort();
    expect(port).toBeGreaterThanOrEqual(49152);
    expect(port).toBeLessThanOrEqual(65535);
  });

  it("retries on EADDRINUSE up to 5 times then throws PORT_UNAVAILABLE", async () => {
    // Fail by stubbing net.createServer to always emit EADDRINUSE
    vi.resetModules();
    vi.doMock("node:net", () => ({
      default: {
        createServer: () => ({
          unref: () => {},
          listen: function () {
            queueMicrotask(() => this._err({ code: "EADDRINUSE" }));
            return this;
          },
          once: function (event, cb) { if (event === "error") this._err = cb; return this; },
          close: () => {},
        }),
      },
    }));
    const { pickFreePort: picker } = await import("../main/auth.js");
    await expect(picker()).rejects.toThrow("PORT_UNAVAILABLE");
  });
});

describe("generateState", () => {
  it("returns 43-character base64url (32 bytes encoded, no padding)", () => {
    const s = generateState();
    expect(s).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("returns a different value each call", () => {
    expect(generateState()).not.toBe(generateState());
  });
});

import http from "node:http";

async function getHtml(port, path) {
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port, path }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString() }));
    }).on("error", reject);
  });
}

describe("startLoopback — success path", () => {
  it("resolves onResult with the token when state matches", async () => {
    const state = generateState();
    let resolved;
    const onResult = vi.fn((v) => { resolved = v; });
    const { port, close } = await startLoopback({ state, onResult });
    const res = await getHtml(port, `/callback?token=THE_JWT&state=${state}`);
    expect(res.status).toBe(200);
    expect(res.body).toMatch(/close this window/i);
    expect(onResult).toHaveBeenCalledWith({ ok: true, token: "THE_JWT" });
    await close();
  });

  it("close() is idempotent", async () => {
    const { close } = await startLoopback({ state: "x", onResult: vi.fn() });
    await close();
    await close();   // no throw
  });

  it("STATE_MISMATCH on diverging state", async () => {
    const onResult = vi.fn();
    const { port, close } = await startLoopback({ state: "right-state", onResult });
    const res = await getHtml(port, `/callback?token=tok&state=wrong-state`);
    expect(res.status).toBe(400);
    expect(onResult).toHaveBeenCalledWith({ ok: false, code: "STATE_MISMATCH" });
    await close();
  });

  it("404 on unknown path", async () => {
    const { port, close } = await startLoopback({ state: "S", onResult: vi.fn() });
    const res = await getHtml(port, `/whatever`);
    expect(res.status).toBe(404);
    await close();
  });

  it("treats missing state param as STATE_MISMATCH (defaults to empty)", async () => {
    const onResult = vi.fn();
    const { port, close } = await startLoopback({ state: "right-state", onResult });
    const res = await getHtml(port, `/callback?token=tok`);
    expect(res.status).toBe(400);
    expect(onResult).toHaveBeenCalledWith({ ok: false, code: "STATE_MISMATCH" });
    await close();
  });

});

describe("startLoopback — error paths", () => {
  it("USER_CANCELLED on ?error=access_denied", async () => {
    const onResult = vi.fn();
    const { port, close } = await startLoopback({ state: "S", onResult });
    const res = await getHtml(port, `/callback?error=access_denied&state=S`);
    expect(res.status).toBe(400);
    expect(onResult).toHaveBeenCalledWith({ ok: false, code: "USER_CANCELLED" });
    await close();
  });

  it("INVALID_CALLBACK when token+error both missing", async () => {
    const onResult = vi.fn();
    const { port, close } = await startLoopback({ state: "S", onResult });
    const res = await getHtml(port, `/callback?state=S`);
    expect(res.status).toBe(400);
    expect(onResult).toHaveBeenCalledWith({ ok: false, code: "INVALID_CALLBACK" });
    await close();
  });
});
