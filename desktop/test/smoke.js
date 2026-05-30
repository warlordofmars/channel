// Copyright (c) 2026 John Carter. All rights reserved.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const bin = process.env.CHANNEL_DESKTOP_BIN;
if (!bin || !existsSync(bin)) {
  console.error(`smoke: CHANNEL_DESKTOP_BIN not set or missing: ${bin}`);
  process.exit(2);
}

const proc = spawn(bin, ["--smoke"], { stdio: ["ignore", "inherit", "inherit"] });
const timer = setTimeout(() => {
  console.error("smoke: timed out after 30s");
  proc.kill("SIGKILL");
  process.exit(3);
}, 30_000);

proc.on("exit", (code) => {
  clearTimeout(timer);
  console.log(`smoke: app exited with code ${code}`);
  process.exit(code === 0 ? 0 : 1);
});
