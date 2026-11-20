// apps/web/playwright.local.config.ts
//
// The local-mode smoke suite (`mise run test:e2e:local`): the web app against `main.py serve` over the fixture data
// directory, with sign-in off. It starts both servers itself: the API on 127.0.0.1:8001 (so it can run while the dev
// loop holds 8000) and a web dev server on :3000 pointed at it, so stop any other web dev server first (the dev
// loop's, too). Build the data directory once with `mise run gen:fixture-data`.

import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { LOCAL_API, REPO_ROOT, WEB_URL } from "./e2e/support/env";

const dataDir = process.env.E2E_LOCAL_DATA_DIR ?? path.join(REPO_ROOT, ".khadbot_fixture_data");
const port = new URL(LOCAL_API).port || "8001";

export default defineConfig({
  testDir: "./e2e/local",
  globalSetup: "./e2e/local/global-setup.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never", outputFolder: "playwright-report-local" }]] : "list",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: { baseURL: WEB_URL, viewport: { width: 1440, height: 900 }, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: `uv run python main.py serve --port ${port}`,
      cwd: REPO_ROOT,
      url: `${LOCAL_API}/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { KHADBOT_DATA_DIR: dataDir, KHADBOT_MODE: "local", KHADBOT_STORAGE: "local" },
    },
    {
      command: "pnpm dev",
      url: WEB_URL,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { NEXT_PUBLIC_API_BASE: LOCAL_API },
    },
  ],
});
