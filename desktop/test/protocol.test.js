// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { readFileSync } from "node:fs";
import { mockElectron } from "./_helpers.js";

vi.mock("electron", () => mockElectron());
vi.mock("node:fs", async () => {
  const actual = await vi.importActual("node:fs");
  return { ...actual, readFileSync: vi.fn() };
});

// Import after mocks
const { registerAppProtocol, registerAppScheme, registerAppHandler, handleAppRequest } = await import("../main/protocol.js");

const RENDERER_ROOT = "/fake/dist-renderer";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("registerAppScheme", () => {
  it("registers app:// as a privileged scheme (no protocol.handle call)", async () => {
    const { protocol } = await import("electron");
    registerAppScheme();
    expect(protocol.registerSchemesAsPrivileged).toHaveBeenCalledWith([
      { scheme: "app", privileges: { standard: true, secure: true, supportFetchAPI: true } },
    ]);
    expect(protocol.handle).not.toHaveBeenCalled();
  });
});

describe("registerAppHandler", () => {
  it("attaches the app:// request handler (no scheme call)", async () => {
    const { protocol } = await import("electron");
    registerAppHandler(RENDERER_ROOT);
    expect(protocol.handle).toHaveBeenCalledWith("app", expect.any(Function));
    expect(protocol.registerSchemesAsPrivileged).not.toHaveBeenCalled();
  });
});

describe("registerAppProtocol", () => {
  it("registers app:// as a privileged scheme before app is ready", async () => {
    const { protocol } = await import("electron");
    registerAppProtocol(RENDERER_ROOT);
    expect(protocol.registerSchemesAsPrivileged).toHaveBeenCalledWith([
      { scheme: "app", privileges: { standard: true, secure: true, supportFetchAPI: true } },
    ]);
  });

  it("registers an app:// request handler", async () => {
    const { protocol } = await import("electron");
    registerAppProtocol(RENDERER_ROOT);
    expect(protocol.handle).toHaveBeenCalledWith("app", expect.any(Function));
  });

  it("the registered handler delegates to handleAppRequest", async () => {
    const { protocol } = await import("electron");
    readFileSync.mockReturnValue(Buffer.from("<html>root</html>"));
    registerAppProtocol(RENDERER_ROOT);
    // Retrieve the handler that was registered and invoke it directly
    const handler = protocol.handle.mock.calls.at(-1)[1];
    const response = handler(new Request("app://-/"));
    expect(response.status).toBe(200);
  });
});

describe("handleAppRequest", () => {
  it("serves index.html for app://-/", async () => {
    readFileSync.mockReturnValue(Buffer.from("<html>root</html>"));
    const response = handleAppRequest(new Request("app://-/"), RENDERER_ROOT);
    expect(readFileSync).toHaveBeenCalledWith(`${RENDERER_ROOT}/index.html`);
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toMatch(/html/);
  });

  it("serves named assets verbatim", async () => {
    readFileSync.mockReturnValue(Buffer.from("console.log(1)"));
    const response = handleAppRequest(new Request("app://-/assets/app.js"), RENDERER_ROOT);
    expect(readFileSync).toHaveBeenCalledWith(`${RENDERER_ROOT}/assets/app.js`);
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toMatch(/javascript/);
  });

  it("falls through SPA deep paths to index.html", async () => {
    readFileSync.mockReturnValue(Buffer.from("<html>root</html>"));
    const response = handleAppRequest(new Request("app://-/app/login"), RENDERER_ROOT);
    expect(readFileSync).toHaveBeenCalledWith(`${RENDERER_ROOT}/index.html`);
    expect(response.status).toBe(200);
  });

  it("rejects path-traversal attempts with 403", async () => {
    // app://-/../etc/passwd is normalised by the Request constructor to
    // app://-/etc/passwd before it reaches the handler, so it cannot be
    // detected as a traversal at runtime (the URL parser has already made
    // it safe). We test with an encoded-slash form (%2F) which IS preserved
    // by the URL parser and reaches the handler with ".." intact.
    const response = handleAppRequest(new Request("app://-/..%2Fetc/passwd"), RENDERER_ROOT);
    expect(response.status).toBe(403);
    expect(readFileSync).not.toHaveBeenCalled();
  });

  it("rejects malformed percent-encoding with 403", async () => {
    const response = handleAppRequest(new Request("app://-/%c0%ae%c0%ae/etc/passwd"), RENDERER_ROOT);
    expect(response.status).toBe(403);
    expect(readFileSync).not.toHaveBeenCalled();
  });

  it("returns 404 when a real asset path doesn't exist", async () => {
    readFileSync.mockImplementation(() => { throw new Error("ENOENT"); });
    const response = handleAppRequest(new Request("app://-/assets/missing.png"), RENDERER_ROOT);
    expect(response.status).toBe(404);
  });

  it("serves unknown-extension assets with octet-stream content-type", async () => {
    readFileSync.mockReturnValue(Buffer.from("binary"));
    const response = handleAppRequest(new Request("app://-/assets/module.wasm"), RENDERER_ROOT);
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/octet-stream");
  });

  it("falls through extension-less paths to index.html (SPA route)", async () => {
    // index.html mock always returns; assertion is that the response resolved
    readFileSync.mockReturnValue(Buffer.from("<html>root</html>"));
    const response = handleAppRequest(new Request("app://-/something/that/looks/like/a/route"), RENDERER_ROOT);
    expect(response.status).toBe(200);
  });
});
