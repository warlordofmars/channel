// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  AUTH_FILE_NAME,
  TAG_ENCRYPTED,
  TAG_PLAINTEXT,
  createTokenStorage,
} from "../main/token-storage.js";

// Real files in a real temp directory rather than a mocked fs: the tag
// byte, the 0600 mode and the "written on one machine, read on another"
// cases are all properties of what lands on disk, and a stubbed fs would
// only assert that we called it the way we said we would.
let dir;
let filePath;
let warn;

/**
 * Stub `safeStorage`. `encryptString` is a reversible non-identity
 * transform (byte-flip) so a test can tell ciphertext from plaintext by
 * looking at the file, without pulling in a real keychain.
 */
function fakeSafeStorage({ available = true } = {}) {
  return {
    isEncryptionAvailable: vi.fn(() => available),
    encryptString: vi.fn((plain) =>
      Buffer.from(Buffer.from(plain, "utf8").map((b) => b ^ 0xff)),
    ),
    decryptString: vi.fn((buf) =>
      Buffer.from(buf.map((b) => b ^ 0xff)).toString("utf8"),
    ),
  };
}

function storage(safeStorage) {
  return createTokenStorage({ filePath, safeStorage, warn });
}

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "channel-token-storage-"));
  filePath = join(dir, AUTH_FILE_NAME);
  warn = vi.fn();
});

afterEach(async () => {
  await rm(dir, { recursive: true, force: true });
});

describe("token storage — encrypted branch", () => {
  it("round-trips a session through safeStorage", async () => {
    const safeStorage = fakeSafeStorage();
    const store = storage(safeStorage);

    await store.write({ refresh_token: "rt-1" });
    expect(await store.read()).toEqual({ refresh_token: "rt-1" });

    expect(safeStorage.encryptString).toHaveBeenCalledWith(
      JSON.stringify({ refresh_token: "rt-1" }),
    );
    expect(safeStorage.decryptString).toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
  });

  it("tags the file encrypted and never leaves the token in clear text", async () => {
    const store = storage(fakeSafeStorage());
    await store.write({ refresh_token: "super-secret-value" });

    const raw = await readFile(filePath);
    expect(raw[0]).toBe(TAG_ENCRYPTED);
    expect(raw.includes(Buffer.from("super-secret-value", "utf8"))).toBe(false);
  });

  it("writes the file owner-only", async () => {
    const store = storage(fakeSafeStorage());
    await store.write({ refresh_token: "rt" });
    expect((await stat(filePath)).mode & 0o777).toBe(0o600);
  });

  it("replaces the previous session rather than appending", async () => {
    const store = storage(fakeSafeStorage());
    await store.write({ refresh_token: "old" });
    await store.write({ refresh_token: "new" });
    expect(await store.read()).toEqual({ refresh_token: "new" });
  });
});

describe("token storage — clear-text fallback (no libsecret)", () => {
  it("still persists, tagged plaintext, and warns loudly", async () => {
    const safeStorage = fakeSafeStorage({ available: false });
    const store = storage(safeStorage);

    await store.write({ refresh_token: "rt-plain" });

    expect(safeStorage.encryptString).not.toHaveBeenCalled();
    const raw = await readFile(filePath);
    expect(raw[0]).toBe(TAG_PLAINTEXT);
    expect(raw.subarray(1).toString("utf8")).toBe(
      JSON.stringify({ refresh_token: "rt-plain" }),
    );

    expect(warn).toHaveBeenCalledTimes(1);
    const [message] = warn.mock.calls[0];
    expect(message).toContain("CLEAR TEXT");
    expect(message).toContain(filePath);
  });

  it("reads a plaintext file back without touching safeStorage", async () => {
    const safeStorage = fakeSafeStorage({ available: false });
    const store = storage(safeStorage);
    await store.write({ refresh_token: "rt-plain" });

    expect(await store.read()).toEqual({ refresh_token: "rt-plain" });
    expect(safeStorage.decryptString).not.toHaveBeenCalled();
  });

  it("upgrades a plaintext file to encrypted on the next write", async () => {
    // The Linux user who installs gnome-keyring after signing in. The tag
    // byte is what lets the same file be read either way, so the upgrade
    // needs no migration step — the hourly rotation does it.
    await storage(fakeSafeStorage({ available: false })).write({ refresh_token: "rt" });
    expect((await readFile(filePath))[0]).toBe(TAG_PLAINTEXT);

    const encrypting = storage(fakeSafeStorage());
    await encrypting.write({ refresh_token: "rt-rotated" });

    expect((await readFile(filePath))[0]).toBe(TAG_ENCRYPTED);
    expect(await encrypting.read()).toEqual({ refresh_token: "rt-rotated" });
  });

  it("reads an encrypted file even after encryption stopped being available", async () => {
    // Availability is a property of the machine, not of the file: a
    // reader must key off the tag, not off its own isEncryptionAvailable.
    await storage(fakeSafeStorage()).write({ refresh_token: "rt" });

    const degraded = fakeSafeStorage({ available: false });
    expect(await storage(degraded).read()).toEqual({ refresh_token: "rt" });
    expect(degraded.decryptString).toHaveBeenCalled();
  });
});

describe("token storage — read failure modes all answer null", () => {
  it("returns null when the file does not exist", async () => {
    expect(await storage(fakeSafeStorage()).read()).toBeNull();
  });

  it("returns null for a zero-length file", async () => {
    await writeFile(filePath, Buffer.alloc(0));
    expect(await storage(fakeSafeStorage()).read()).toBeNull();
  });

  it("returns null and warns when the ciphertext cannot be decrypted", async () => {
    await writeFile(filePath, Buffer.from([TAG_ENCRYPTED, 1, 2, 3]));
    const safeStorage = fakeSafeStorage();
    safeStorage.decryptString.mockImplementation(() => {
      throw new Error("keychain entry revoked");
    });

    expect(await storage(safeStorage).read()).toBeNull();
    expect(warn.mock.calls[0][0]).toContain("could not be decrypted");
  });

  it("returns null when the decrypted payload is not JSON", async () => {
    await writeFile(filePath, Buffer.concat([Buffer.from([TAG_PLAINTEXT]), Buffer.from("{oops")]));
    expect(await storage(fakeSafeStorage()).read()).toBeNull();
  });

  it.each([
    ["a JSON array", "[]"],
    ["a JSON string", '"rt"'],
    ["JSON null", "null"],
  ])("returns null for %s, which is valid JSON but not a session", async (_label, json) => {
    await writeFile(filePath, Buffer.concat([Buffer.from([TAG_PLAINTEXT]), Buffer.from(json)]));
    expect(await storage(fakeSafeStorage()).read()).toBeNull();
  });
});

describe("token storage — write input guard", () => {
  it.each([
    ["null", null],
    ["a string", "rt"],
    ["an array", ["rt"]],
  ])("refuses to persist %s", async (_label, value) => {
    await expect(storage(fakeSafeStorage()).write(value)).rejects.toThrow(TypeError);
    await expect(readFile(filePath)).rejects.toThrow();
  });

  it("propagates I/O failures rather than swallowing them", async () => {
    // A silent write failure is a session that dies at the next expiry
    // with no signal — the caller has just burned its predecessor.
    const store = createTokenStorage({
      filePath: join(dir, "no-such-directory", AUTH_FILE_NAME),
      safeStorage: fakeSafeStorage(),
      warn,
    });
    await expect(store.write({ refresh_token: "rt" })).rejects.toThrow();
  });
});

describe("token storage — clear", () => {
  it("deletes the stored session", async () => {
    const store = storage(fakeSafeStorage());
    await store.write({ refresh_token: "rt" });
    await store.clear();

    expect(await store.read()).toBeNull();
    await expect(readFile(filePath)).rejects.toThrow();
  });

  it("is a no-op when there is nothing stored", async () => {
    const store = storage(fakeSafeStorage());
    await expect(store.clear()).resolves.toBeUndefined();
    await expect(store.clear()).resolves.toBeUndefined();
  });
});
