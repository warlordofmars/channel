// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mockElectron } from "./_helpers.js";

vi.mock("electron", () => mockElectron());

const { pickFreePort, generateState } = await import("../main/auth.js");

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
