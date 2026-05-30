// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, expect, it } from "vitest";
import { TOKEN_KEY, isTokenValid, parseToken } from "./auth.js";

function makeToken({ expOffsetSeconds = 3600, role = "user", email = "u@example.com", sub = "user-1" } = {}) {
  const exp = Math.floor(Date.now() / 1000) + expOffsetSeconds;
  const payload = btoa(JSON.stringify({ exp, sub, role, email }));
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("TOKEN_KEY", () => {
  it("matches the legacy starter_mgmt_token localStorage key", () => {
    expect(TOKEN_KEY).toBe("starter_mgmt_token");
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
