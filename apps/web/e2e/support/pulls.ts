// apps/web/e2e/support/pulls.ts
//
// Loading a pull before a spec reads it. The pull page and timeline specs read H7z1fqDXjRTFnbtg fight 15, which a
// fresh stack has not loaded (the open-pull spec needs that report readable but not seeded). Visiting the page would
// start the load, but the first load of a report takes longer than a test's timeout under parallel load, so the
// spec's `beforeAll` opens the pull through the API as a test user and waits for its digest:
//
//   test.beforeAll(async ({ browser }) => {
//     test.setTimeout(LOAD_TIMEOUT_MS);
//     await ensurePullLoaded(browser, "H7z1fqDXjRTFnbtg", 15);
//   });
//
// A pull that is loading already is followed, not started twice (the API answers with the running job).

import { expect, type Browser } from "@playwright/test";

import { HOSTED_API, USERS, type TestUser } from "./env";

/** Long enough for a report's first metadata fetch and one pull's three steps under parallel load. */
export const LOAD_TIMEOUT_MS = 180_000;

export async function ensurePullLoaded(browser: Browser, code: string, fight: number, user: TestUser = "alice"): Promise<void> {
  const context = await browser.newContext({ storageState: USERS[user] });
  try {
    const digest = `${HOSTED_API}/v1/reports/${code}/pulls/${fight}/digest`;
    if ((await context.request.get(digest)).status() === 200) return;
    const opened = await context.request.post(`${HOSTED_API}/v1/reports`, {
      data: { source: code, fights: [fight] },
      headers: { "X-KhadBot-CSRF": "1" },
    });
    expect([200, 202], `opening ${code} fight ${fight}`).toContain(opened.status());
    await expect
      .poll(async () => (await context.request.get(digest)).status(), {
        message: `${code} fight ${fight} never finished loading`,
        timeout: LOAD_TIMEOUT_MS - 10_000,
        intervals: [1_000],
      })
      .toBe(200);
  } finally {
    await context.close();
  }
}
