// apps/web/e2e/local/global-setup.ts
//
// Before the local suite's tests: put generated test portraits into the data directory the API serves (boss-art.py).
// Playwright starts the API first, but it reads the art listing only when a page asks, which no test has yet.

import { execFileSync } from "node:child_process";
import path from "node:path";

import { REPO_ROOT } from "../support/env";

export default function globalSetup(): void {
  const dataDir = process.env.E2E_LOCAL_DATA_DIR ?? path.join(REPO_ROOT, ".khadbot_fixture_data");
  execFileSync("uv", ["run", "python", path.join(REPO_ROOT, "apps", "web", "e2e", "support", "boss-art.py")], {
    cwd: REPO_ROOT,
    env: { ...process.env, KHADBOT_DATA_DIR: dataDir, KHADBOT_MODE: "local", KHADBOT_STORAGE: "local" },
    stdio: ["ignore", "ignore", "inherit"],
    shell: process.platform === "win32",
  });
}
