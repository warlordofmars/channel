// Copyright (c) 2026 John Carter. All rights reserved.
/**
 * OS-keychain-backed persistence for the desktop session (#297, epic #241).
 *
 * The web SPA keeps its refresh token in the `channel_refresh` HttpOnly
 * cookie, which JavaScript cannot read. Electron has no such transport:
 * the loopback redirect hands the renderer a plaintext refresh token
 * (#292) and something has to hold it across restarts. `localStorage` is
 * the wrong place — in a packaged app it is an unencrypted LevelDB store
 * under `userData`, readable by any process running as the user — so
 * this module puts it behind `safeStorage`, which wraps the platform
 * keychain (Keychain on macOS, DPAPI on Windows, libsecret on Linux).
 *
 * File format — `userData/auth.bin`
 * ---------------------------------
 * One tag byte, then the payload:
 *
 *     0x01 <safeStorage ciphertext>   encrypted
 *     0x00 <utf-8 JSON>               clear-text fallback
 *
 * The tag is what makes the two forms interchangeable *at rest*.
 * `isEncryptionAvailable()` is a property of the machine at a moment in
 * time, not of the file — a Linux user who installs gnome-keyring after
 * first sign-in flips it from false to true — so a reader cannot infer
 * the payload's form from its own environment and has to be told. It
 * also makes the upgrade automatic and free: {@link write} re-decides on
 * every call, and desktop rotates its refresh token roughly hourly, so a
 * clear-text file becomes an encrypted one on the next rotation with no
 * migration step.
 *
 * Failure posture
 * ---------------
 * {@link read} answers `null` for *every* unusable state — absent,
 * truncated, undecryptable, not JSON. All of them mean the same thing to
 * the caller ("no stored session, sign in again"), and a throw here would
 * surface as an unhandled rejection inside an `ipcMain.handle` callback
 * rather than as anything a user could act on. {@link write} does the
 * opposite and lets I/O errors propagate: its caller has just been handed
 * a rotated refresh token whose predecessor is already dead server-side,
 * so a silent write failure is a session that dies at the next expiry
 * with no signal.
 */
import { readFile, rm, writeFile } from "node:fs/promises";

/** Filename under `app.getPath("userData")`. */
export const AUTH_FILE_NAME = "auth.bin";

/** Leading byte: payload is `safeStorage` ciphertext. */
export const TAG_ENCRYPTED = 0x01;

/** Leading byte: payload is clear-text UTF-8 JSON. */
export const TAG_PLAINTEXT = 0x00;

/**
 * Owner-only permissions on the credential file.
 *
 * Applied on create — which is the case that matters, since nothing but
 * this module ever creates it. It is the only protection the clear-text
 * fallback has, and it is worth having on the encrypted branch too:
 * ciphertext an attacker cannot read is still ciphertext they cannot
 * delete or swap for their own.
 */
const FILE_MODE = 0o600;

/**
 * Whether `value` is something we are willing to serialise as a session.
 *
 * The renderer is the only sender (`registerIpc` rejects any other
 * `event.sender`), but "only our own renderer" is not "only well-formed
 * input" — a bug in the SPA, or script running in it, reaches this
 * function through the same channel. Arrays and primitives round-trip
 * through JSON perfectly well and would be stored happily, then come
 * back out of `read()` as a shape no caller expects.
 */
function isSessionObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Build the `{read, write, clear}` surface over one file.
 *
 * Dependencies are injected rather than imported so the module stays
 * testable against a real temp directory and a stub `safeStorage` —
 * matching how `main/auth.js` and `main/window.js` are wired. The
 * electron-backed instance is assembled in `main/index.js`.
 *
 * @param {object}  deps
 * @param {string}  deps.filePath      absolute path to `auth.bin`
 * @param {object}  deps.safeStorage   electron's `safeStorage` (or a stub)
 * @param {Function} deps.warn         sink for the clear-text warning
 */
export function createTokenStorage({ filePath, safeStorage, warn }) {
  /**
   * The stored session object, or `null` when there isn't a usable one.
   */
  async function read() {
    let raw;
    try {
      raw = await readFile(filePath);
    } catch {
      // Absent (the normal pre-login state), or unreadable. Neither is
      // distinguishable from "no session" to the caller.
      return null;
    }
    // A zero-length file has no tag byte to read. Reachable if a write
    // was interrupted, or if something else truncated the file.
    if (raw.length === 0) return null;

    const payload = raw.subarray(1);
    let json;
    try {
      json =
        raw[0] === TAG_ENCRYPTED
          ? safeStorage.decryptString(payload)
          : payload.toString("utf8");
    } catch {
      // Encrypted payload we can no longer decrypt: the keychain entry
      // was revoked, the file moved between machines, or encryption
      // stopped being available. Re-login is the only recovery.
      warn(
        "[channel] stored desktop credentials could not be decrypted — " +
          "signing in again will replace them.",
      );
      return null;
    }
    try {
      const parsed = JSON.parse(json);
      return isSessionObject(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }

  /**
   * Replace the stored session.
   *
   * Encrypts when the platform can, and says so loudly when it cannot —
   * a Linux box with no libsecret provider gets clear text on disk, and
   * that is a fact the operator deserves to see rather than a silent
   * downgrade. It is still better than the alternatives: refusing to
   * persist would sign the user out every hour (the exact bug this
   * change exists to fix), and the file is `0600` either way.
   */
  async function write(session) {
    if (!isSessionObject(session)) {
      throw new TypeError("tokenStorage.write: expected a session object");
    }
    const json = JSON.stringify(session);
    const encrypted = safeStorage.isEncryptionAvailable();
    if (!encrypted) {
      warn(
        `[channel] OS encryption is unavailable — writing desktop credentials to ${filePath} ` +
          "in CLEAR TEXT. Install a libsecret-compatible keyring (gnome-keyring, kwallet) " +
          "and sign in again to store them encrypted.",
      );
    }
    const payload = encrypted
      ? safeStorage.encryptString(json)
      : Buffer.from(json, "utf8");
    const tagged = Buffer.concat([
      Buffer.from([encrypted ? TAG_ENCRYPTED : TAG_PLAINTEXT]),
      payload,
    ]);
    await writeFile(filePath, tagged, { mode: FILE_MODE });
  }

  /**
   * Delete the stored session.
   *
   * `force` swallows ENOENT, so signing out twice — or signing out of a
   * session that never persisted anything — is not an error.
   */
  async function clear() {
    await rm(filePath, { force: true });
  }

  return { read, write, clear };
}
