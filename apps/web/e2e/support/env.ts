// apps/web/e2e/support/env.ts
//
// Where the end-to-end tests find things. The hosted suite (playwright.config.ts) runs against the dev loop: the API
// on http://localhost:8000 (`mise run dev:all`) and the web dev server on http://localhost:3000, which Playwright
// starts when nothing is listening there. The local suite (playwright.local.config.ts) starts `main.py serve` on
// 8001 over the fixture data directory itself.

import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** The repository root (apps/web/e2e/support -> ../../../..). */
export const REPO_ROOT = path.resolve(here, "../../../..");

/** The web app's own directory. */
export const WEB_ROOT = path.resolve(here, "../..");

export const WEB_URL = process.env.E2E_WEB_URL ?? "http://localhost:3000";

/** The hosted API. `localhost`, not 127.0.0.1: the test session's cookie is for `localhost`, as sign-in's is. */
export const HOSTED_API = process.env.E2E_API_BASE ?? "http://localhost:8000";

/** The local-mode API the local suite starts (main.py serve). */
export const LOCAL_API = process.env.E2E_LOCAL_API_BASE ?? "http://127.0.0.1:8001";

/** Playwright storage-state files: live session cookies, gitignored (apps/web/e2e/.auth/). */
export const AUTH_DIR = path.join(WEB_ROOT, "e2e", ".auth");

/**
 * The fixture reports the hosted suite expects seeded (`mise run dev:seed -- --only SYNTH01`) and readable by the
 * test users. SYNTH01 is small and seeds in seconds; add codes here when a test needs another report.
 */
export const E2E_REPORTS = (process.env.E2E_REPORTS ?? "SYNTH01").split(",").filter(Boolean);

/** Test users with a session each (`main.py test-session`), and their storage-state files. */
export const USERS = {
  alice: path.join(AUTH_DIR, "alice.json"),
  bob: path.join(AUTH_DIR, "bob.json"),
} as const;

export type TestUser = keyof typeof USERS;
