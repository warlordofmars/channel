// Copyright (c) 2026 John Carter. All rights reserved.
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    globals: true,
    passWithNoTests: true,
    include: ["test/**/*.test.js"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["main/**/*.js", "preload/**/*.js"],
      exclude: ["main/index.js"],
      thresholds: {
        lines: 100,
        functions: 100,
        branches: 100,
        statements: 100,
      },
    },
  },
});
