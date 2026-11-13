// apps/web/e2e/support/global-setup.ts
//
// Before the hosted suite: check the dev loop's API answers and offers sign-in, then sign each test user in through
// a test session (`main.py test-session --storage-state`, via scripts/dev/dev.py so it gets the dev loop's
// environment), with read access to the e2e reports. No Warcraft Logs login and no WCL call is involved. Last, open
// each route once in a browser, so `next dev` compiles them (and the timeline's client-only chunk) before the tests
// start rather than inside the first test of every worker to reach them.

import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";

import { AUTH_DIR, E2E_REPORTS, HOSTED_API, REPO_ROOT, USERS, WEB_URL } from "./env";

/** The app's routes, one address each: the Raids page and the pull page (the first e2e report's first fight). */
const WARM_PATHS = ["/", `/r/${E2E_REPORTS[0] ?? "SYNTH01"}/p/1`];

async function warmRoutes(): Promise<void> {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ storageState: USERS.alice });
    // As in the tests' fixture: nothing leaves the machine.
    await context.route(/^https?:\/\/(?!localhost[:/]|127\.0\.0\.1[:/])/, (route) => route.abort());
    const page = await context.newPage();
    for (const path of WARM_PATHS) {
      await page.goto(`${WEB_URL}${path}`, { waitUntil: "load", timeout: 180_000 });
      await page.waitForLoadState("networkidle", { timeout: 60_000 }).catch(() => undefined);
    }
  } finally {
    await browser.close();
  }
}

async function health(): Promise<{ features?: Record<string, boolean> }> {
  try {
    const res = await fetch(`${HOSTED_API}/v1/health`);
    return (await res.json()) as { features?: Record<string, boolean> };
  } catch (cause) {
    throw new Error(
      `The hosted API isn't answering at ${HOSTED_API}. Start the dev loop first: \`mise run dev:all\`, then ` +
        `\`mise run dev:seed -- --only ${E2E_REPORTS.join(" ")}\` (docs/hosting.md, "The web app").`,
      { cause },
    );
  }
}

export default async function globalSetup(): Promise<void> {
  const info = await health();
  if (!info.features?.sign_in) {
    throw new Error(
      `The API at ${HOSTED_API} has no sign-in: the hosted suite needs the dev loop (\`mise run dev:all\`), not ` +
        "`main.py serve` (that is `mise run test:e2e:local`).",
    );
  }
  mkdirSync(AUTH_DIR, { recursive: true });
  for (const [user, file] of Object.entries(USERS)) {
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "scripts/dev/dev.py",
        "session",
        "--user",
        `e2e-${user}`,
        "--reports",
        ...E2E_REPORTS,
        "--hours",
        "6",
        "--storage-state",
        file,
        "--quiet",
      ],
      { cwd: REPO_ROOT, stdio: ["ignore", "ignore", "inherit"], shell: process.platform === "win32" },
    );
  }
  await warmRoutes();
}
