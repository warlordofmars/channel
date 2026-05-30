// Copyright (c) 2026 John Carter. All rights reserved.
import { randomBytes } from "node:crypto";
import net from "node:net";

const PORT_MIN = 49152;
const PORT_MAX = 65535;
const PORT_RETRIES = 5;

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
