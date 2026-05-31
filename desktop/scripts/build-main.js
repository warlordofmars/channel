// Copyright (c) 2026 John Carter. All rights reserved.
/**
 * Bundle desktop/main/ + desktop/preload/ via esbuild, baking the API
 * base URL into the bundle at build time.
 *
 * The main process reads `process.env.CHANNEL_API_BASE` in main/index.js
 * with a `?? "https://channel.warlordofmars.net"` fallback. In a packaged
 * Electron app there is no runtime process.env CHANNEL_API_BASE — the
 * user double-clicks the .app with no env set, so the fallback always
 * wins. This script uses esbuild's `--define` to REPLACE the literal
 * `process.env.CHANNEL_API_BASE` with the value chosen at build time, so
 * the fallback collapses to the baked URL.
 *
 * Sources of CHANNEL_API_BASE (in priority order):
 *   1. process.env.CHANNEL_API_BASE — set by CI per branch, or by
 *      `inv desktop-dev` to http://localhost:8001, or by
 *      `inv desktop-build --api-base ...`.
 *   2. Default `https://channel.warlordofmars.net` (prod).
 *
 * Pass `--watch` for incremental rebuilds (used by `npm run dev:main`).
 */
import { build, context } from "esbuild";

const apiBase =
  process.env.CHANNEL_API_BASE ?? "https://channel.warlordofmars.net";
const watch = process.argv.includes("--watch");

const options = {
  entryPoints: ["main/index.js", "preload/index.js"],
  bundle: true,
  platform: "node",
  target: "node20",
  external: ["electron"],
  outdir: "dist-main",
  format: "cjs",
  define: {
    "process.env.CHANNEL_API_BASE": JSON.stringify(apiBase),
  },
};

if (watch) {
  const ctx = await context(options);
  await ctx.watch();
  // eslint-disable-next-line no-console
  console.log(`build-main: watching, CHANNEL_API_BASE=${apiBase}`);
} else {
  await build(options);
  // eslint-disable-next-line no-console
  console.log(`build-main: done, CHANNEL_API_BASE=${apiBase}`);
}
