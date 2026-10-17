import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const testsDir = path.resolve(here, "../tests").replaceAll("\\", "/");
const anchor = path.join(here, "src/main.tsx");

// Specs in ../tests have no node_modules of their own; resolve their bare imports from frontend/.
const resolveTestDeps: Plugin = {
  name: "resolve-test-deps",
  enforce: "pre",
  resolveId(id, importer, options) {
    if (!importer?.replaceAll("\\", "/").startsWith(testsDir)) return null;
    if (/^[./\0]|^[a-zA-Z]:/.test(id)) return null;
    return this.resolve(id, anchor, { ...options, skipSelf: true });
  },
};

const daemon = "http://127.0.0.1:8765";
const proxy = {
  "/api": daemon,
  "/ws": { target: daemon, ws: true },
};

export default defineConfig({
  plugins: [react(), tailwindcss(), resolveTestDeps],
  server: { proxy, fs: { allow: [".."] } },
  preview: { proxy },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts?(x)", "../tests/frontend/**/*.test.ts?(x)"],
  },
});
