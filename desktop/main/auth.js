// Copyright (c) 2026 John Carter. All rights reserved.
import { randomBytes, timingSafeEqual } from "node:crypto";
import net from "node:net";
import http from "node:http";

const CLOSE_PAGE_HTML = `<!doctype html><meta charset="utf-8"><title>Signed in</title>
<style>body{font:14px system-ui;margin:40px;color:#222}</style>
<h2>You can close this window.</h2>
<p>Channel Desktop is signed in.</p>`;

const PORT_MIN = 49152;
const PORT_MAX = 65535;
const PORT_RETRIES = 5;

function statesEqual(a, b) {
  const A = Buffer.from(a);
  const B = Buffer.from(b);
  if (A.length !== B.length) return false;
  return timingSafeEqual(A, B);
}

export function generateState() {
  return randomBytes(32).toString("base64url");
}

export function pickFreePort() {
  return new Promise((resolve, reject) => {
    let attempts = 0;

    const tryPort = () => {
      attempts += 1;
      const port = PORT_MIN + Math.floor(Math.random() * (PORT_MAX - PORT_MIN + 1));
      const server = net.createServer();
      server.unref();
      server.once("error", (err) => {
        if (err.code === "EADDRINUSE" && attempts < PORT_RETRIES) {
          tryPort();
        } else {
          reject(new Error("PORT_UNAVAILABLE"));
        }
      });
      server.listen(port, "127.0.0.1", () => {
        server.close(() => resolve(port));
      });
    };

    tryPort();
  });
}

export function startLoopback({ state, onResult }) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (url.pathname !== "/callback") {
        res.writeHead(404); return res.end("not found");
      }
      const got = url.searchParams.get("state") ?? "";
      const tok = url.searchParams.get("token") ?? "";
      if (!statesEqual(got, state)) {
        res.writeHead(400); res.end("state mismatch");
        onResult({ ok: false, code: "STATE_MISMATCH" });
        return;
      }
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      res.end(CLOSE_PAGE_HTML);
      onResult({ ok: true, token: tok });
    });

    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      let closed = false;
      const close = () => new Promise((res) => {
        if (closed) return res();
        closed = true;
        server.close(() => res());
      });
      resolve({ port, close });
    });
    server.on("error", reject);
  });
}
