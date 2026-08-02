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
    const onResult = vi.fn();
    const { port, close } = await startLoopback({ state, onResult });
    const res = await getHtml(port, `/callback?token=THE_JWT&state=${state}`);
    expect(res.status).toBe(200);
    expect(res.body).toMatch(/close this window/i);
    // No refresh_token on the redirect: a bypass login, or a fail-soft
    // mint. Still a valid access-token-only session (#297).
    expect(onResult).toHaveBeenCalledWith({ ok: true, token: "THE_JWT", refreshToken: "" });
    await close();
  });

  it("carries the refresh_token off the redirect when the server minted one", async () => {
    const state = generateState();
    const onResult = vi.fn();
    const { port, close } = await startLoopback({ state, onResult });
    const res = await getHtml(
      port,
      `/callback?token=THE_JWT&refresh_token=THE_REFRESH&state=${state}`,
    );
    expect(res.status).toBe(200);
    expect(onResult).toHaveBeenCalledWith({
      ok: true,
      token: "THE_JWT",
      refreshToken: "THE_REFRESH",
    });
    await close();
  });

  it("never surfaces a refresh token on a state mismatch", async () => {
    // The state check gates the whole callback, so a forged loopback hit
    // cannot get a refresh token stored even if it guesses the port.
    const onResult = vi.fn();
    const { port, close } = await startLoopback({ state: "right-state", onResult });
    await getHtml(port, `/callback?token=tok&refresh_token=leak&state=wrong-state`);
    expect(onResult).toHaveBeenCalledWith({ ok: false, code: "STATE_MISMATCH" });
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

describe("login()", () => {
  it("opens external browser at /auth/login with state + desktop_callback", async () => {
    const openExternal = vi.fn().mockResolvedValue(undefined);
    const appOnce = vi.fn();
    let captureOnResult;
    const fakeStartLoopback = vi.fn(({ onResult }) => {
      captureOnResult = onResult;
      return Promise.resolve({ port: 51234, close: vi.fn().mockResolvedValue() });
    });

    const { loginWithDeps } = await import("../main/auth.js");
    const promise = loginWithDeps({
      authBaseUrl: "https://example.test",
      openExternal,
      onAppQuit: appOnce,
      startLoopback: fakeStartLoopback,
      timeoutMs: 1000,
    });
    // Resolve from the loopback side
    queueMicrotask(() => captureOnResult({ ok: true, token: "JWT123", refreshToken: "RT123" }));
    // #297: resolves the pair, so the renderer can persist both together.
    expect(await promise).toEqual({ token: "JWT123", refreshToken: "RT123" });

    expect(openExternal).toHaveBeenCalledTimes(1);
    const url = new URL(openExternal.mock.calls[0][0]);
    expect(url.origin).toBe("https://example.test");
    expect(url.pathname).toBe("/auth/login");
    expect(url.searchParams.get("desktop_callback")).toBe("http://127.0.0.1:51234/callback");
    expect(url.searchParams.get("state")).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("rejects with TIMEOUT after timeoutMs with no callback", async () => {
    const { loginWithDeps } = await import("../main/auth.js");
    await expect(loginWithDeps({
      authBaseUrl: "https://example.test",
      openExternal: vi.fn().mockResolvedValue(),
      onAppQuit: vi.fn(),
      startLoopback: () => Promise.resolve({ port: 1, close: vi.fn().mockResolvedValue() }),
      timeoutMs: 10,
    })).rejects.toThrow("TIMEOUT");
  });

  it("rejects with USER_CANCELLED when loopback signals access_denied", async () => {
    let captureOnResult;
    const { loginWithDeps } = await import("../main/auth.js");
    const promise = loginWithDeps({
      authBaseUrl: "https://example.test",
      openExternal: vi.fn().mockResolvedValue(),
      onAppQuit: vi.fn(),
      startLoopback: ({ onResult }) => { captureOnResult = onResult; return Promise.resolve({ port: 1, close: vi.fn().mockResolvedValue() }); },
      timeoutMs: 1000,
    });
    queueMicrotask(() => captureOnResult({ ok: false, code: "USER_CANCELLED" }));
    await expect(promise).rejects.toThrow("USER_CANCELLED");
  });

  it("coalesces concurrent login() calls into one in-flight promise", async () => {
    let captureOnResult;
    const startLoopback = vi.fn(({ onResult }) => {
      captureOnResult = onResult;
      return Promise.resolve({ port: 1, close: vi.fn().mockResolvedValue() });
    });
    const { loginWithDeps } = await import("../main/auth.js");
    const deps = {
      authBaseUrl: "https://example.test",
      openExternal: vi.fn().mockResolvedValue(),
      onAppQuit: vi.fn(),
      startLoopback,
      timeoutMs: 1000,
    };
    const p1 = loginWithDeps(deps);
    const p2 = loginWithDeps(deps);
    queueMicrotask(() => captureOnResult({ ok: true, token: "T", refreshToken: "RT" }));
    const [a, b] = await Promise.all([p1, p2]);
    expect(a).toEqual({ token: "T", refreshToken: "RT" });
    expect(b).toEqual({ token: "T", refreshToken: "RT" });
    expect(startLoopback).toHaveBeenCalledTimes(1);
  });

  it("closes the loopback on app before-quit", async () => {
    const close = vi.fn().mockResolvedValue();
    let beforeQuitListener;
    const { loginWithDeps } = await import("../main/auth.js");
    void loginWithDeps({
      authBaseUrl: "https://example.test",
      openExternal: vi.fn().mockResolvedValue(),
      onAppQuit: (cb) => { beforeQuitListener = cb; },
      startLoopback: () => Promise.resolve({ port: 1, close }),
      timeoutMs: 60000,
    });
    await Promise.resolve(); // let openExternal fire
    beforeQuitListener();
    await Promise.resolve();
    expect(close).toHaveBeenCalled();
  });

  it("rejects when startLoopback itself rejects", async () => {
    vi.resetModules();
    const { loginWithDeps } = await import("../main/auth.js");
    await expect(loginWithDeps({
      authBaseUrl: "https://example.test",
      openExternal: vi.fn().mockResolvedValue(),
      onAppQuit: vi.fn(),
      startLoopback: () => Promise.reject(new Error("PORT_UNAVAILABLE")),
      timeoutMs: 1000,
    })).rejects.toThrow("PORT_UNAVAILABLE");
  });

  it("settle is idempotent — double-settle does not reject twice", async () => {
    vi.resetModules();
    let captureOnResult;
    const { loginWithDeps } = await import("../main/auth.js");
    const promise = loginWithDeps({
      authBaseUrl: "https://example.test",
      openExternal: vi.fn().mockResolvedValue(),
      onAppQuit: vi.fn(),
      startLoopback: ({ onResult }) => {
        captureOnResult = onResult;
        return Promise.resolve({ port: 1, close: vi.fn().mockResolvedValue() });
      },
      timeoutMs: 1000,
    });
    // Fire onResult twice — second call should be a no-op (settled guard)
    queueMicrotask(() => {
      captureOnResult({ ok: true, token: "X", refreshToken: "RTX" });
      captureOnResult({ ok: false, code: "USER_CANCELLED" }); // should be ignored
    });
    expect(await promise).toEqual({ token: "X", refreshToken: "RTX" });
  });

  it("onAppQuit fires before server is set (server still null)", async () => {
    vi.resetModules();
    let captureQuitCb;
    const { loginWithDeps } = await import("../main/auth.js");
    let captureOnResult;
    const promise = loginWithDeps({
      authBaseUrl: "https://example.test",
      openExternal: vi.fn().mockResolvedValue(),
      onAppQuit: (cb) => { captureQuitCb = cb; },
      // startLoopback that captures onResult but resolves later
      startLoopback: ({ onResult }) => {
        captureOnResult = onResult;
        // Invoke quit callback immediately (before .then fires), simulating
        // app quit arriving before the server resolves
        if (captureQuitCb) captureQuitCb();
        return Promise.resolve({ port: 1, close: vi.fn().mockResolvedValue() });
      },
      timeoutMs: 1000,
    });
    // Fire quit before server resolves (captured before .then callback)
    // Then resolve normally
    queueMicrotask(() => captureOnResult({ ok: true, token: "Y", refreshToken: "RTY" }));
    expect(await promise).toEqual({ token: "Y", refreshToken: "RTY" });
  });
});

describe("login() — production wrapper", () => {
  it("delegates to loginWithDeps using shell.openExternal and app.on", async () => {
    vi.resetModules();
    const electron = await import("electron");
    const { login } = await import("../main/auth.js");
    // login() is async — it awaits import("electron") then calls loginWithDeps
    // which starts a real HTTP server (I/O). Yield to the I/O event loop so
    // the server.listen callback fires and openExternal is invoked.
    const promise = login({ authBaseUrl: "https://example.test" });
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));
    expect(electron.shell.openExternal).toHaveBeenCalledTimes(1);
    const url = new URL(electron.shell.openExternal.mock.calls[0][0]);
    expect(url.pathname).toBe("/auth/login");
    expect(promise).toBeInstanceOf(Promise);
  });
});
