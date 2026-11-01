// apps/web/e2e/support/watch.ts
//
// A live watch for a test user. A test session has no Warcraft Logs token, so `POST /v1/reports/{code}/watch` answers
// 409 `wcl_sign_in_required` for it (a watch polls as its owner). The account menu's "Following live" only reads and
// stops watches, so a test that needs one puts the row in the dev loop's Postgres directly, as an active watch with no
// poll scheduled, and removes it afterwards. Stopping it goes through the real `DELETE /v1/reports/{code}/watch`.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

import { REPO_ROOT } from "./env";

function databaseUrl(): string {
  const text = readFileSync(path.join(REPO_ROOT, "deploy", "dev.env"), "utf8");
  const match = /^DATABASE_URL=(.+)$/m.exec(text);
  if (!match) throw new Error("deploy/dev.env has no DATABASE_URL: run scripts/dev/setup.ps1.");
  return (match[1] ?? "").trim();
}

function run(action: "add" | "remove", code: string, userId: string): void {
  execFileSync("uv", ["run", "python", path.join(REPO_ROOT, "apps", "web", "e2e", "support", "watch.py"), action, code, userId], {
    cwd: REPO_ROOT,
    env: { ...process.env, DATABASE_URL: databaseUrl() },
    stdio: ["ignore", "ignore", "inherit"],
    shell: process.platform === "win32",
  });
}

/** Make `userId` (from `GET /v1/me`) follow `code` live, replacing any watch of that report. */
export function addWatch(code: string, userId: string): void {
  run("add", code, userId);
}

/** Delete every watch of `code`. */
export function removeWatches(code: string): void {
  run("remove", code, "-");
}
