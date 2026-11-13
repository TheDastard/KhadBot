// apps/web/e2e/run.mjs
//
// Runs the hosted suite in two passes (`pnpm e2e`, `mise run test:e2e`): the functional tests with parallel workers,
// then the tests tagged @budget on one worker. A budget test measures latency in the page (first render, hover,
// click, a step change), and parallel workers share one CPU, one dev server and one API, so a budget measured beside
// other tests measures the load, not the app. Both passes run even when the first fails; the exit code is the worse
// of the two. Extra arguments (a file filter, --headed, ...) go to both passes.
//
// The functional pass runs on E2E_WORKERS workers (default 4; playwright.config.ts and e2e/support/env.ts), not
// Playwright's default of half the logical CPUs, because every worker shares one dev server and one API. The budget
// pass stays on one worker whatever the arguments say.

import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const cli = createRequire(import.meta.url).resolve("@playwright/test/cli");
const extra = process.argv.slice(2);

const pass = (args, after = []) =>
  spawnSync(process.execPath, [cli, "test", ...args, ...extra, ...after], { stdio: "inherit" }).status ?? 1;

const functional = pass(["--project=chromium"]);
const budgets = pass(["--project=budgets", "--no-deps", "--pass-with-no-tests"], ["--workers=1"]);
process.exit(Math.max(functional, budgets));
