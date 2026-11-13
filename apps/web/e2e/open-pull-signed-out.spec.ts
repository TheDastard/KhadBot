// apps/web/e2e/open-pull-signed-out.spec.ts
//
// Opening a pull while signed out, on the hosted API with WCL replay (no WCL point is spent). A signed-out visitor
// opens one pull of a public log on KhadBot's own budget and follows it to the pull page, which keeps the label the
// log search gave the pull; a log they can't read shows the private-log card, not the loading screen's sign-in card.
//
// QkVa6GHRK8yfrTbw is the Raids spec's recorded public report: replay answers its metadata on the app token and has
// the pages of the fights recorded under tests/fixtures/encounter/wcl/<code>/<fight>/. It must never be in
// E2E_REPORTS: global setup marks every listed report not public. SYNTH01 (seeded) is readable by the test users
// only. When every recorded pull of the public report is loaded the test skips; `mise run stack:reset` starts again.

import { readdirSync } from "node:fs";
import path from "node:path";

import type { components } from "@khadbot/api-client";

import { HOSTED_API, REPO_ROOT } from "./support/env";
import { expect, test } from "./support/fixtures";

type Lookup = components["schemas"]["ReportLookup"];

const PUBLIC = "QkVa6GHRK8yfrTbw";
const PRIVATE = "SYNTH01";

/** The fights of a report that replay has recorded pages for. */
function recordedFights(code: string): Set<number> {
  const dir = path.join(REPO_ROOT, "tests", "fixtures", "encounter", "wcl", code);
  return new Set(readdirSync(dir).filter((name) => /^\d+$/.test(name)).map(Number));
}

test.describe("signed out", () => {
  test("opens an unloaded pull of a public log and reaches the pull page", async ({ page, request }) => {
    test.slow(); // a report's first load under parallel load outlasts the default 30 s
    const res = await request.get(`${HOSTED_API}/v1/lookup/report`, { params: { source: PUBLIC } });
    expect(res.status()).toBe(200);
    const lookup = (await res.json()) as Lookup;
    const recorded = recordedFights(PUBLIC);
    // The last recorded pull not yet loaded.
    const pull = lookup.groups
      .flatMap((g) => g.pulls)
      .filter((p) => !p.ready && recorded.has(p.fight))
      .at(-1);
    test.skip(!pull, `every recorded pull of ${PUBLIC} is loaded already: reset the stack to run this`);

    const posts: number[] = [];
    page.on("response", (r) => {
      if (r.request().method() === "POST" && r.url().endsWith("/v1/reports")) posts.push(r.status());
    });
    await page.goto(`/r/${PUBLIC}/p/${pull!.fight}`);
    await expect(page.getByRole("link", { name: "Sign in with Warcraft Logs" }).first()).toBeVisible(); // signed out
    await expect(page.locator('[data-slot="pull-not-loaded"]')).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toContainText(pull!.label);
    await expect(page.getByRole("heading", { name: "Sign in to open this pull" })).toHaveCount(0);

    // The pull page replaces the loading screen without a reload, under the same label as in the log search.
    await expect(page.locator('[data-slot="pull-body"]')).toBeVisible({ timeout: 90_000 });
    await expect(page.locator('[data-slot="pull-body"]').getByRole("heading", { level: 1 })).toContainText(pull!.label);
    expect(page.url()).toContain(`/r/${PUBLIC}/p/${pull!.fight}`);
    expect(posts.length).toBeGreaterThan(0);
    expect(posts.every((s) => s === 200 || s === 202)).toBe(true);
  });

  test("a log the visitor can't read shows the private-log card and loads nothing", async ({ page }) => {
    const posts: number[] = [];
    page.on("response", (r) => {
      if (r.request().method() === "POST" && r.url().endsWith("/v1/reports")) posts.push(r.status());
    });
    await page.goto(`/r/${PRIVATE}/p/1`);
    await expect(page.getByRole("heading", { name: "This log is private" })).toBeVisible();
    await expect(page.locator('[data-slot="pull-body"]')).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Sign in to open this pull" })).toHaveCount(0);
    expect(posts.length).toBeGreaterThan(0);
    expect(posts.every((s) => s === 404)).toBe(true); // the same 404 as a missing report: nothing was queued
  });
});
