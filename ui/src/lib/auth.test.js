// Copyright (c) 2026 John Carter. All rights reserved.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  LEGACY_TOKEN_KEY,
  TOKEN_KEY,
  clearSession,
  isTokenValid,
  loadSession,
  parseToken,
  readToken,
  saveSession,
} from "./auth.js";

function makeToken({ expOffsetSeconds = 3600, role = "user", email = "u@example.com", sub = "user-1" } = {}) {
  const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
  const payload = btoa(JSON.stringify({ exp, sub, role, email }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("TOKEN_KEY", () => {
  it("is the de-branded channel_mgmt_token key (#260)", () => {
    expect(TOKEN_KEY).toBe("channel_mgmt_token");
  });

  it("keeps the pre-rename key available for read-only fallback", () => {
    expect(LEGACY_TOKEN_KEY).toBe("starter_mgmt_token");
  });
});

describe("parseToken", () => {
  it("parses a valid token into its claims object", () => {
    const t = makeToken({ role: "admin" });
    const claims = parseToken(t);
    expect(claims.role).toBe("admin");
    expect(claims.exp).toBeGreaterThan(Math.floor(Date.now() / 1000));
  });

  it("returns null for null/undefined/empty inputs", () => {
    expect(parseToken(null)).toBeNull();
    expect(parseToken(undefined)).toBeNull();
    expect(parseToken("")).toBeNull();
  });

  it("returns null for a malformed token", () => {
    expect(parseToken("not.a.jwt")).toBeNull();
    expect(parseToken("only-one-segment")).toBeNull();
  });
});

describe("isTokenValid", () => {
  it("returns true for a future-exp token", () => {
    expect(isTokenValid(makeToken({ expOffsetSeconds: 3600 }))).toBe(true);
  });
  it("returns false for a past-exp token", () => {
    expect(isTokenValid(makeToken({ expOffsetSeconds: -3600 }))).toBe(false);
  });
  it("returns false for null/undefined/empty/malformed inputs", () => {
    expect(isTokenValid(null)).toBe(false);
    expect(isTokenValid(undefined)).toBe(false);
    expect(isTokenValid("")).toBe(false);
    expect(isTokenValid("not.a.jwt")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Session envelope + #260 key migration (#295)
// ---------------------------------------------------------------------------

describe("session storage", () => {
  let storage;

  beforeEach(() => {
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

  it("returns an empty session when nothing is stored", () => {
    expect(loadSession()).toEqual({ access_token: "", expires_at: 0 });
    expect(readToken()).toBe("");
  });

  it("reads the {access_token, expires_at} envelope", () => {
    const at = Date.now() + 3_600_000;
    storage[TOKEN_KEY] = JSON.stringify({ access_token: "tok", expires_at: at });
    expect(loadSession()).toEqual({ access_token: "tok", expires_at: at });
    expect(readToken()).toBe("tok");
  });

  it("derives expires_at from the JWT exp claim when the envelope omits it", () => {
    const token = makeToken({ expOffsetSeconds: 600 });
    storage[TOKEN_KEY] = JSON.stringify({ access_token: token });
    const { expires_at: expiresAt } = loadSession();
    expect(expiresAt).toBe(parseToken(token).exp * 1000);
  });

  it("reads a bare-JWT value written before the envelope existed", () => {
    // The shape `/auth/callback`'s login-completion page still writes.
    const token = makeToken({ expOffsetSeconds: 1800 });
    storage[TOKEN_KEY] = token;
    expect(loadSession()).toEqual({
      access_token: token,
      expires_at: parseToken(token).exp * 1000,
    });
  });

  it("reports expires_at 0 when neither the envelope nor the token dates it", () => {
    // Reads as "already expired" so the refresh wrapper renews rather
    // than trusting an undatable token.
    storage[TOKEN_KEY] = "not-a-jwt";
    expect(loadSession()).toEqual({ access_token: "not-a-jwt", expires_at: 0 });
  });

  it("treats a corrupt envelope as no session instead of throwing", () => {
    storage[TOKEN_KEY] = "{ this is not json";
    expect(loadSession()).toEqual({ access_token: "", expires_at: 0 });
  });

  it("treats an envelope with no access_token as no session", () => {
    storage[TOKEN_KEY] = JSON.stringify({ expires_at: Date.now() + 1000 });
    expect(loadSession()).toEqual({ access_token: "", expires_at: 0 });
  });

  // -- #260 read-both / write-new migration ---------------------------------

  it("falls back to the legacy key so a pre-rename session survives", () => {
    const token = makeToken();
    storage[LEGACY_TOKEN_KEY] = token;
    expect(readToken()).toBe(token);
  });

  it("falls back to the legacy key when the new key holds an unusable value", () => {
    // A corrupt or access-token-less envelope under the new key must not
    // force a re-login while a good pre-rename session sits beside it —
    // that is precisely what read-both exists to prevent.
    const token = makeToken();
    for (const junk of ["{ not json", JSON.stringify({ expires_at: 1 }), ""]) {
      storage[TOKEN_KEY] = junk;
      storage[LEGACY_TOKEN_KEY] = token;
      expect(readToken()).toBe(token);
    }
  });

  it("prefers the new key when both are present", () => {
    storage[TOKEN_KEY] = JSON.stringify({ access_token: "new", expires_at: 1 });
    storage[LEGACY_TOKEN_KEY] = "old";
    expect(readToken()).toBe("new");
  });

  it("writes the new key and deletes the legacy one", () => {
    const token = makeToken();
    storage[LEGACY_TOKEN_KEY] = "old";
    saveSession(token, 12_345);
    expect(JSON.parse(storage[TOKEN_KEY])).toEqual({
      access_token: token,
      expires_at: 12_345,
    });
    expect(storage[LEGACY_TOKEN_KEY]).toBeUndefined();
  });

  it("accepts real base64url tokens, including `-` and `_` in every segment", () => {
    // Regression guard: base64url's alphabet is `A-Za-z0-9-_`, so a class
    // that dropped `-` would reject most real signatures and lock every
    // user out. Padded standard base64 (`+/=`) is accepted too, because
    // btoa-based fixtures and some encoders emit it.
    const real =
      "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" +
      ".eyJzdWIiOiJqb2huQGV4LmNvbSIsImV4cCI6MTc2NzIyNTYwMH0" +
      ".q-3xY_9zAbC-dEfG_hIjKlMnOpQrStUvWxYz012";
    for (const token of [real, "ab-cd_ef.gh-ij_kl.mn-op_qr", "a-b.c-d.e-f", "a+b.c/d.e=f"]) {
      expect(() => saveSession(token)).not.toThrow();
      expect(readToken()).toBe(token);
    }
  });

  it("refuses to store anything that isn't structurally a JWT", () => {
    // `saveSession`'s inputs come from off-device: a /auth/refresh
    // response body and the desktop loopback. Writing an unvalidated
    // value here would persist it as a credential replayed on every
    // later request, so the write is guarded rather than the callers.
    const rejected = [
      "", "not-a-jwt", "two.segments", "a..b", "abc.def.gh i", 'abc.def.gh"i',
      "<script>alert(1)</script>", null, undefined, 42, { access_token: "x" },
    ];
    for (const bad of rejected) {
      expect(() => saveSession(bad)).toThrow(TypeError);
    }
    expect(storage[TOKEN_KEY]).toBeUndefined();
  });

  it("leaves an existing session untouched when a bad write is refused", () => {
    const good = makeToken();
    saveSession(good);
    expect(() => saveSession("garbage")).toThrow(TypeError);
    expect(JSON.parse(storage[TOKEN_KEY]).access_token).toBe(good);
  });

  it("falls back to the token's exp when saved without an explicit expiry", () => {
    const token = makeToken({ expOffsetSeconds: 900 });
    saveSession(token);
    expect(JSON.parse(storage[TOKEN_KEY]).expires_at).toBe(parseToken(token).exp * 1000);
  });

  it("clears both keys so a legacy value cannot resurrect the session", () => {
    storage[TOKEN_KEY] = JSON.stringify({ access_token: makeToken(), expires_at: 1 });
    storage[LEGACY_TOKEN_KEY] = "b";
    clearSession();
    expect(storage[TOKEN_KEY]).toBeUndefined();
    expect(storage[LEGACY_TOKEN_KEY]).toBeUndefined();
    expect(readToken()).toBe("");
  });
});
