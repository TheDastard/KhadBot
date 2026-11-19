// apps/web/e2e/review.spec.ts
//
// Pull reviews on the hosted dev loop with the fake model provider (`LLM_PROVIDER=fake` and a delay in
// `FAKE_LLM_DELAY_SECONDS`, in deploy/dev.env): the review button's states on SYNTH01 fight 1, a review followed to
// its findings, a second user seeing the same findings for free, the quota limit and a signed-out visitor (who gets
// 404 for the private test report; the sign-in line is covered by Vitest). Each
// test starts from "not reviewed" (`resetReviews`). The specs in this file share the one pull, so they run in order
// on one worker.

import type { Page } from "@playwright/test";

import { expectAccessible } from "./support/axe";
import { HOSTED_API, USERS } from "./support/env";
import { expect, test } from "./support/fixtures";
import { clearQuota, fillQuota, resetReviews, REVIEWS_PER_DAY } from "./support/review";

test.describe.configure({ mode: "serial", timeout: 120_000 });

const CODE = "SYNTH01";
const URL = `/r/${CODE}/p/1`;
const REVIEW_API = `${HOSTED_API}/v1/reports/${CODE}/pulls/1/review`;
const REVIEW_TIMEOUT_MS = 60_000;

test.beforeEach(() => {
  resetReviews(CODE);
  clearQuota();
});
test.afterAll(() => {
  resetReviews(CODE);
  clearQuota();
});

const slot = (page: Page) => page.locator('[data-slot="review-button"]');

async function userId(page: Page): Promise<string> {
  return ((await (await page.request.get(`${HOSTED_API}/v1/me`)).json()) as { id: string }).id;
}

test.describe("signed in as alice", () => {
  test.use({ user: "alice" });

  test("offers the review with its estimate and the reviews left, then runs it to findings", async ({ page }) => {
    await page.goto(URL);
    const button = slot(page).getByRole("button", { name: "Review this pull" });
    await expect(button).toBeVisible();
    await expect(slot(page)).toContainText(new RegExp(`About [\\d.]+k? tokens · ${REVIEWS_PER_DAY} of ${REVIEWS_PER_DAY} reviews left`));
    await expect(page.getByTestId("section-findings")).toContainText("No cached findings for this pull.");
    await expectAccessible(page, { ready: () => expect(button).toBeVisible() });

    await button.click();
    await expect(slot(page).locator('[data-review="running"]')).toBeVisible();
    await expect(slot(page).getByRole("progressbar", { name: "Review progress" })).toBeVisible();
    await expectAccessible(page, { ready: () => expect(slot(page).locator('[data-review="running"]')).toBeVisible() });

    // The fake model's delay keeps it running for a few seconds; then the findings arrive without a reload.
    await expect(slot(page).locator('[data-review="cached"]')).toBeVisible({ timeout: REVIEW_TIMEOUT_MS });
    await expect(slot(page)).toContainText("Reviewed");
    const findings = page.getByTestId("section-findings");
    await expect(findings.locator("article").first()).toBeVisible();
    await expect(findings).not.toContainText("No cached findings");
    await expect(findings.locator("[data-ref]").first()).toBeVisible(); // evidence chips
    await expectAccessible(page, { ready: () => expect(slot(page).locator('[data-review="cached"]')).toBeVisible() });
  });

  test("a reload while the review runs follows it again", async ({ page }) => {
    await page.goto(URL);
    await slot(page).getByRole("button", { name: "Review this pull" }).click();
    await expect(slot(page).locator('[data-review="running"]')).toBeVisible();
    await page.reload();
    await expect(slot(page).locator('[data-review="running"], [data-review="cached"]')).toBeVisible();
    await expect(slot(page).locator('[data-review="cached"]')).toBeVisible({ timeout: REVIEW_TIMEOUT_MS });
  });

  test("a user who has used their reviews sees the limit and no button", async ({ page }) => {
    fillQuota(await userId(page), REVIEWS_PER_DAY);
    await page.goto(URL);
    await expect(slot(page)).toContainText(`Review limit reached: ${REVIEWS_PER_DAY} of ${REVIEWS_PER_DAY} used`);
    await expect(slot(page).getByRole("button")).toHaveCount(0);
    const refused = await page.request.post(REVIEW_API, { data: {}, headers: { "X-KhadBot-CSRF": "1" } });
    expect(refused.status()).toBe(429);
    expect(((await refused.json()) as { code: string }).code).toBe("quota_exceeded");
    await expectAccessible(page, { ready: () => expect(slot(page)).toContainText("Review limit reached") });
  });
});

test.describe("a second user", () => {
  test("sees the findings another user paid for, free and without a job", async ({ browser }) => {
    const alice = await browser.newContext({ storageState: USERS.alice });
    const carol = await browser.newContext({ storageState: USERS.carol });
    try {
      const a = await alice.newPage();
      await a.goto(URL);
      await slot(a).getByRole("button", { name: "Review this pull" }).click();
      await expect(slot(a).locator('[data-review="cached"]')).toBeVisible({ timeout: REVIEW_TIMEOUT_MS });

      const b = await carol.newPage();
      await b.goto(URL);
      await expect(slot(b).locator('[data-review="cached"]')).toBeVisible({ timeout: REVIEW_TIMEOUT_MS });
      await expect(slot(b).getByRole("button")).toHaveCount(0);
      await expect(b.getByTestId("section-findings").locator("article").first()).toBeVisible();
      const status = (await (await carol.request.get(REVIEW_API)).json()) as {
        cached: boolean;
        quota: { used: number };
      };
      expect(status.cached).toBe(true);
      expect(status.quota.used).toBe(0); // carol used none
    } finally {
      await alice.close();
      await carol.close();
    }
  });
});

test.describe("signed out", () => {
  test("a visitor gets no review button or route for a private report", async ({ page, request }) => {
    // SYNTH01 is private to the test users, so a visitor gets 404 and no page: the API refuses before the button.
    const res = await request.get(REVIEW_API);
    expect(res.status()).toBe(404);
    await page.goto(URL);
    await expect(slot(page)).toHaveCount(0);
  });
});
