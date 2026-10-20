import { defineConfig } from "@playwright/test";

const port = 4173;

export default defineConfig({
  testDir: ".",
  workers: 1,
  timeout: 240_000,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: "chrome",
    viewport: { width: 1920, height: 1200 },
    deviceScaleFactor: Number(process.env.E2E_DSF ?? 1),
    launchOptions: {
      args: ["--js-flags=--expose-gc", "--enable-precise-memory-info",
        ...(process.env.CI ? ["--enable-unsafe-swiftshader"] : [])],
    },
  },
  webServer: {
    command: `npm run build && npx vite preview --port ${port} --strictPort --host 127.0.0.1`,
    cwd: "../../frontend",
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
