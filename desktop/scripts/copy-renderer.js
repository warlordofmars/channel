// Copyright (c) 2026 John Carter. All rights reserved.
/**
 * Copy the SPA build from ../ui/dist/ to ./dist-renderer/ as the
 * renderer payload that electron-builder bundles into the app's asar.
 * Replaces the previous `rsync -a --delete` step, which doesn't work
 * on Windows (Git Bash ships no rsync).
 */
import { rmSync, cpSync, existsSync } from "node:fs";
import { resolve } from "node:path";

const src = resolve("../ui/dist");
const dest = resolve("dist-renderer");

if (!existsSync(src)) {
  console.error(`copy-renderer: source missing: ${src}`);
  console.error(`copy-renderer: run \`npm run build\` in ../ui first`);
  process.exit(1);
}

rmSync(dest, { recursive: true, force: true });
cpSync(src, dest, { recursive: true });
console.log(`copy-renderer: ${src} → ${dest}`);
