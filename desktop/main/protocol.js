// Copyright (c) 2026 John Carter. All rights reserved.
import { protocol } from "electron";
import { readFileSync } from "node:fs";
import { extname, posix } from "node:path";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js":   "application/javascript; charset=utf-8",
  ".mjs":  "application/javascript; charset=utf-8",
  ".css":  "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg":  "image/svg+xml",
  ".png":  "image/png",
  ".jpg":  "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif":  "image/gif",
  ".ico":  "image/x-icon",
  ".woff2":"font/woff2",
};

function mimeFor(p) {
  return MIME[extname(p).toLowerCase()] ?? "application/octet-stream";
}

/**
 * Resolve an app:// URL to a path inside renderer root, returning null on
 * traversal attempts. The URL hostname is ignored (use "-" by convention).
 *
 * Note: the browser's URL parser normalises `/../` segments before we can
 * inspect them via `new URL(url).pathname`, so we detect traversal by
 * checking the raw URL string for `..` segments before parsing.
 */
function resolveRendererPath(url, rendererRoot) {
  // Reject any URL that contains a ".." path segment before URL normalisation
  // strips it. Encoded variants (%2e%2e, %2E%2E, etc.) are also rejected.
  let decoded;
  try {
    decoded = decodeURIComponent(url);
  } catch {
    return null; // malformed percent-encoding → treat as hostile, falls through to 403
  }
  if (decoded.includes("..")) return null;

  const u = new URL(url);
  const rawPath = u.pathname; // e.g. "/assets/app.js"
  const normalized = posix.normalize(rawPath);
  return rendererRoot + normalized;
}

export function handleAppRequest(request, rendererRoot) {
  const resolved = resolveRendererPath(request.url, rendererRoot);
  if (resolved === null) return new Response("forbidden", { status: 403 });

  // Root request → index.html
  if (resolved === rendererRoot + "/") {
    const body = readFileSync(rendererRoot + "/index.html");
    return new Response(body, { status: 200, headers: { "content-type": "text/html; charset=utf-8" } });
  }

  // Asset request — try to serve verbatim
  if (extname(resolved)) {
    try {
      const body = readFileSync(resolved);
      return new Response(body, { status: 200, headers: { "content-type": mimeFor(resolved) } });
    } catch {
      return new Response("not found", { status: 404 });
    }
  }

  // Extension-less path = SPA route → fall through to index.html
  const body = readFileSync(rendererRoot + "/index.html");
  return new Response(body, { status: 200, headers: { "content-type": "text/html; charset=utf-8" } });
}

/**
 * Register the app:// scheme as privileged. MUST be called before
 * app.whenReady() — Electron requires privileged-scheme registration
 * before the default session is created.
 */
export function registerAppScheme() {
  protocol.registerSchemesAsPrivileged([
    { scheme: "app", privileges: { standard: true, secure: true, supportFetchAPI: true } },
  ]);
}

/**
 * Attach the request handler. MUST be called inside or after
 * app.whenReady() — protocol.handle reads the default session.
 */
export function registerAppHandler(rendererRoot) {
  protocol.handle("app", (request) => handleAppRequest(request, rendererRoot));
}

/**
 * Convenience: both phases in one call. Only safe AFTER app.whenReady().
 * Kept for tests; production callers should call the two phases separately.
 */
export function registerAppProtocol(rendererRoot) {
  registerAppScheme();
  registerAppHandler(rendererRoot);
}
