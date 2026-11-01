// apps/web/playwright.config.ts
//
// The hosted end-to-end suite (`mise run test:e2e`): the web app against the dev loop's hosted API with WCL replay
// on and test sessions, never a real Warcraft Logs login or a WCL point. Start the dev loop first
// (`mise run dev:all`, which also starts the web dev server; `mise run dev:seed -- --only SYNTH01`). When nothing
// listens on :3000, Playwright starts `next dev` itself. docs/hosting.md, "The web app", has the steps.

import { defineConfig, devices } from "@playwright/test";

import { HOSTED_API, WEB_URL } from "./e2e/support/env";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: ["local/**"],
  globalSetup: "./e2e/support/global-setup.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: WEB_URL,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  // The tests tagged @budget measure latency, so they run apart from the rest: `pnpm e2e` (e2e/run.mjs) runs the
  // chromium project with parallel workers, then this one on a single worker. `dependencies` keeps the order when
  // the config is used directly (`playwright test`).
  projects: [
    {
      name: "chromium",
      grepInvert: /@budget/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "budgets",
      grep: /@budget/,
      dependencies: ["chromium"],
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command: "pnpm dev",
    url: WEB_URL,
    reuseExistingServer: true,
    timeout: 120_000,
    env: { NEXT_PUBLIC_API_BASE: HOSTED_API },
  },
});
