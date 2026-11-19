// apps/web/e2e/support/review.ts
//
// Review state for the review spec, in the dev loop's Postgres: `resetReviews` deletes a report's review jobs, usage
// rows and cached findings so a run starts from "not reviewed" (the run before it left findings behind), and
// `fillQuota` / `clearQuota` give a user finished review jobs on a placeholder report so they have used their reviews
// without running any (the allowance is `KHADBOT_REVIEWS_PER_DAY`, default 10).

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

import { REPO_ROOT } from "./env";

/** The default `KHADBOT_REVIEWS_PER_DAY`; the dev loop does not set it. */
export const REVIEWS_PER_DAY = 10;

function databaseUrl(): string {
  const text = readFileSync(path.join(REPO_ROOT, "deploy", "dev.env"), "utf8");
  const match = /^DATABASE_URL=(.+)$/m.exec(text);
  if (!match) throw new Error("deploy/dev.env has no DATABASE_URL: run scripts/dev/setup.ps1.");
  return (match[1] ?? "").trim();
}

function run(...args: string[]): void {
  execFileSync("uv", ["run", "python", path.join(REPO_ROOT, "apps", "web", "e2e", "support", "review.py"), ...args], {
    cwd: REPO_ROOT,
    env: { ...process.env, DATABASE_URL: databaseUrl() },
    stdio: ["ignore", "ignore", "inherit"],
    shell: process.platform === "win32",
  });
}

/** Delete `code`'s review jobs, their usage rows and its cached findings. */
export function resetReviews(code: string): void {
  run("reset", code);
}

/** Make `userId` (from `GET /v1/me`) have used `count` reviews today. */
export function fillQuota(userId: string, count: number): void {
  run("fill-quota", userId, String(count));
}

/** Remove what `fillQuota` added, for every user. */
export function clearQuota(): void {
  run("clear-quota");
}
