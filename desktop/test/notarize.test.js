// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { join } from "node:path";

vi.mock("@electron/notarize", () => ({ notarize: vi.fn(() => Promise.resolve()) }));

const { default: notarizeHook } = await import("../scripts/notarize.js");

const ORIG_ENV = { ...process.env };

beforeEach(() => {
  vi.clearAllMocks();
  process.env.APPLE_API_KEY_P8_BASE64 = "ZmFrZQ=="; // "fake"
  process.env.APPLE_API_KEY_ID = "ABCDEFGHIJ";
  process.env.APPLE_API_ISSUER_ID = "00000000-0000-0000-0000-000000000000";
});
afterEach(() => {
  process.env = { ...ORIG_ENV };
});

describe("notarize afterSign hook", () => {
  it("skips when electronPlatformName is not darwin", async () => {
    const { notarize } = await import("@electron/notarize");
    await notarizeHook({
      electronPlatformName: "win32",
      appOutDir: "/tmp",
      packager: { appInfo: { productFilename: "Channel" } },
    });
    expect(notarize).not.toHaveBeenCalled();
  });

  it("skips when APPLE_API_KEY_P8_BASE64 is unset (e.g. local builds)", async () => {
    delete process.env.APPLE_API_KEY_P8_BASE64;
    const { notarize } = await import("@electron/notarize");
    await notarizeHook({
      electronPlatformName: "darwin",
      appOutDir: "/tmp",
      packager: { appInfo: { productFilename: "Channel" } },
    });
    expect(notarize).not.toHaveBeenCalled();
  });

  it("calls notarize with the decoded API key path when env is set", async () => {
    const { notarize } = await import("@electron/notarize");
    await notarizeHook({
      electronPlatformName: "darwin",
      appOutDir: "/tmp/build",
      packager: { appInfo: { productFilename: "Channel" } },
    });
    expect(notarize).toHaveBeenCalledWith(expect.objectContaining({
      // notarize.js uses node:path's `join`, so the expected path uses the
      // platform separator (forward slash on POSIX, backslash on win32).
      appPath: join("/tmp/build", "Channel.app"),
      appleApiKey: expect.stringMatching(/AuthKey_.*\.p8$/),
      appleApiKeyId: "ABCDEFGHIJ",
      appleApiIssuer: "00000000-0000-0000-0000-000000000000",
    }));
  });
});
