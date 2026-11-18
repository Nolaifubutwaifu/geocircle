import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  use: { baseURL: "http://localhost:3100", viewport: { width: 1000, height: 700 } },
  webServer: { command: "pnpm build && pnpm start -p 3100", url: "http://localhost:3100", timeout: 240_000, reuseExistingServer: true },
});
