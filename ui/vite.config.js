import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** Vite plugin that resolves ?raw imports for files outside the project root. */
function rawOutsideRoot() {
  return {
    name: "raw-outside-root",
    load(id) {
      if (!id.endsWith("?raw")) return;
      const filePath = id.slice(0, -4); // strip ?raw
      if (fs.existsSync(filePath)) {
        const content = fs.readFileSync(filePath, "utf-8");
        return `export default ${JSON.stringify(content)}`;
      }
    },
  };
}

export default defineConfig({
  plugins: [rawOutsideRoot(), react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8001",
      "/auth": "http://localhost:8001",
      "/oauth": "http://localhost:8001",
      "/mcp": "http://localhost:8000",
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/setupTests.js",
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["src/**/*.{js,jsx}"],
      exclude: ["src/main.jsx", "src/setupTests.js"],
      thresholds: {
        lines: 100,
        functions: 100,
        branches: 100,
        statements: 100,
      },
    },
  },
});
