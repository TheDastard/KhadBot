// apps/web/e2e/pull-page.spec.ts
//
// The pull page on the hosted API (the dev loop with WCL replay): the rail, header, main column sections and the
// context panel. Two targets:
//   - SYNTH01 fight 1 (always seeded): the page renders, the panel opens, axe passes.
//   - H7z1fqDXjRTFnbtg fight 15 (the longest recorded pull; E2E_REPORTS=SYNTH01,H7z1fqDXjRTFnbtg lets the test users
//     read it, and the describe's beforeAll loads the pull through replay if it isn't loaded): every section's rows
//     match the digest response, each chip kind opens its panel, reload and Back restore it, and the budgets are
//     measured (they are printed as test annotations).

import type { APIRequestContext, Page } from "@playwright/test";

import { expectAccessible } from "./support/axe";
import { E2E_REPORTS, HOSTED_API } from "./support/env";
import { expect, test } from "./support/fixtures";
import { ensurePullLoaded, LOAD_TIMEOUT_MS } from "./support/pulls";

test.use({ user: "alice" });

const LONG = { code: "H7z1fqDXjRTFnbtg", fight: 15 };
const hasLong = E2E_REPORTS.includes(LONG.code);

async function getJson<T>(request: APIRequestContext, path: string): Promise<T> {
  const res = await request.get(`${HOSTED_API}${path}`);
  expect(res.status(), path).toBe(200);
  return (await res.json()) as T;
}

const pullUrl = (code: string, fight: number, query = "") => `/r/${code}/p/${fight}${query}`;

async function waitForPage(page: Page) {
  await expect(page.getByTestId("pull-header")).toBeVisible();
  await expect(page.getByTestId("section-deaths")).toBeVisible();
}

test.describe("SYNTH01", () => {
  test("renders the rail, header and sections, and opens a pull summary", async ({ page }) => {
    await page.goto(pullUrl("SYNTH01", 1));
    await waitForPage(page);
    await expect(page.getByRole("complementary", { name: "Pulls" }).locator('[aria-current="page"]')).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("·");
    await expect(page.locator('[data-slot="review-button"]')).toHaveCount(1);
    await expect(page.getByTestId("context-panel")).toContainText("Nothing selected");
    await page.getByRole("complementary", { name: "Pulls" }).locator("a").first().click();
    await expect(page).toHaveURL(/ref=ev%3ASYNTH01%3A\d+%3Apull/);
    await expect(page.getByTestId("context-panel").locator('[data-panel-kind="pull"]')).toBeVisible();
  });

  test("an unresolvable ref and a plan ref show their views", async ({ page }) => {
    await page.goto(pullUrl("SYNTH01", 1, "?ref=ev%3ASYNTH01%3A1%3Adeath%3A999"));
    await expect(page.getByTestId("context-panel").locator('[data-panel-kind="error"]')).toBeVisible();
    await expect(page.getByTestId("context-panel")).toContainText("unresolvable_ref");
    await page.goto(pullUrl("SYNTH01", 1, "?ref=plan%3Aabc%3A0"));
    await expect(page.getByTestId("context-panel")).toContainText("Raid plans aren't available yet");
  });

  test("passes axe in both themes", async ({ page }) => {
    await page.goto(pullUrl("SYNTH01", 1));
    await waitForPage(page);
    await expectAccessible(page);
  });
});

test.describe("H7z1fqDXjRTFnbtg fight 15", () => {
  test.skip(!hasLong, "needs E2E_REPORTS to include H7z1fqDXjRTFnbtg");
  test.beforeAll(async ({ browser }) => {
    test.setTimeout(LOAD_TIMEOUT_MS);
    await ensurePullLoaded(browser, LONG.code, LONG.fight);
  });
  const base = `/v1/reports/${LONG.code}/pulls/${LONG.fight}`;

  test("every section's rows match the digest", async ({ page }) => {
    const digest = await getJson<{
      deaths: unknown[];
      mechanics: Array<{ hits: number; failures: number }>;
      coverage_windows: unknown[];
      defensive_cooldowns: unknown[];
      offensive_cooldowns?: unknown[];
      utility_cooldowns?: unknown[];
      unregistered: unknown[];
      aggro?: unknown[];
    }>(page.request, `${base}/digest`);
    await page.goto(pullUrl(LONG.code, LONG.fight));
    await waitForPage(page);
    const rows = (id: string) => page.getByTestId(id).locator("tbody tr");
    await expect(rows("section-deaths")).toHaveCount(digest.deaths.length);
    await expect(rows("section-mechanics")).toHaveCount(digest.mechanics.filter((m) => m.hits || m.failures).length);
    await expect(page.getByTestId("section-windows").locator("li")).toHaveCount(digest.coverage_windows.length);
    const cds =
      digest.defensive_cooldowns.length + (digest.offensive_cooldowns?.length ?? 0) + (digest.utility_cooldowns?.length ?? 0);
    await expect(page.getByTestId("section-cooldowns").locator("li")).toHaveCount(cds);
    await expect(page.getByTestId("section-aggro").locator("b")).toHaveCount(digest.aggro?.length ?? 0);
  });

  test("each chip kind opens its panel; reload restores it and Back steps back", async ({ page }) => {
    await page.goto(pullUrl(LONG.code, LONG.fight));
    await waitForPage(page);
    const panel = page.getByTestId("context-panel");
    for (const [section, kind] of [
      ["section-deaths", "death"],
      ["section-mechanics", "mech"],
      ["section-windows", "win"],
      ["section-cooldowns", "cd"],
    ] as const) {
      await page.getByTestId(section).locator("a[data-ref]").first().click();
      await expect(panel.locator(`[data-panel-kind="${kind}"]`)).toBeVisible();
    }
    // Back returns to the previous focus (the window chip), reload keeps the current one.
    const url = page.url();
    await page.reload();
    await expect(panel.locator('[data-panel-kind="cd"]')).toBeVisible();
    expect(page.url()).toBe(url);
    await page.goBack();
    await expect(panel.locator('[data-panel-kind="win"]')).toBeVisible();
  });

  test("a rail row selects its pull summary", async ({ page }) => {
    await page.goto(pullUrl(LONG.code, LONG.fight));
    await waitForPage(page);
    await page.locator(`aside[aria-label="Pulls"] a[data-fight="${LONG.fight}"]`).click();
    await expect(page.getByTestId("context-panel").locator('[data-panel-kind="pull"]')).toBeVisible();
  });

  test("Show everything lists the deaths after the cutoff", async ({ page }) => {
    const full = await getJson<{ deaths: Array<{ after_cutoff: boolean }> }>(page.request, `${base}/digest?full=true`);
    await page.goto(pullUrl(LONG.code, LONG.fight));
    await waitForPage(page);
    const rows = page.getByTestId("section-deaths").locator("tbody tr");
    const before = await rows.count();
    await page.getByRole("switch", { name: "Show everything" }).click();
    await expect(page).toHaveURL(/full=1/);
    await expect(rows).toHaveCount(full.deaths.length);
    expect(full.deaths.length).toBeGreaterThan(before);
    await expect(page.locator('tr[data-after-cutoff="true"]').first()).toBeVisible();
  });

  test("the page does not scroll sideways at 1024 px", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 800 });
    await page.goto(pullUrl(LONG.code, LONG.fight));
    await waitForPage(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test("measures first paint and chip to panel", { tag: "@budget" }, async ({ page }, testInfo) => {
    const t0 = Date.now();
    await page.goto(pullUrl(LONG.code, LONG.fight));
    await expect(page.getByTestId("pull-header")).toBeVisible();
    const paint = Date.now() - t0;
    await expect(page.getByTestId("section-deaths").locator("a[data-ref]").first()).toBeVisible();
    const chips = page.getByTestId("section-deaths").locator("a[data-ref]");
    // Measured in the page: from the click event to the panel showing the chip's ref in its record view.
    const timeChip = async (index: number) => {
      const ref = await chips.nth(index).getAttribute("data-ref");
      await page.evaluate((expected) => {
        const w = window as unknown as { __chipMs?: number };
        w.__chipMs = undefined;
        const aside = document.querySelector("[data-testid=context-panel]") as HTMLElement;
        let start = 0;
        document.addEventListener("click", () => (start = performance.now()), { capture: true, once: true });
        const seen = new MutationObserver(() => {
          const loading = aside.querySelector('[data-panel-kind="loading"]');
          if (start && !loading && aside.textContent?.includes(expected as string)) {
            w.__chipMs = performance.now() - start;
            seen.disconnect();
          }
        });
        seen.observe(aside, { childList: true, subtree: true, characterData: true });
      }, ref);
      await chips.nth(index).click();
      await page.waitForFunction(() => (window as unknown as { __chipMs?: number }).__chipMs !== undefined);
      return page.evaluate(() => (window as unknown as { __chipMs: number }).__chipMs);
    };
    const uncached = await timeChip(0);
    await timeChip(1);
    const cached = await timeChip(0);
    testInfo.annotations.push({
      type: "dd11",
      description: `first paint ${paint} ms; chip to panel uncached ${uncached.toFixed(0)} ms, cached ${cached.toFixed(0)} ms`,
    });
    console.log(testInfo.annotations.at(-1)?.description);
  });

  test("passes axe in both themes", async ({ page }) => {
    await page.goto(pullUrl(LONG.code, LONG.fight, `?ref=${encodeURIComponent(`ev:${LONG.code}:${LONG.fight}:death:2`)}`));
    const settled = async () => {
      await waitForPage(page);
      await expect(page.getByTestId("context-panel").locator('[data-panel-kind="death"]')).toBeVisible();
      await expect(page.getByTestId("section-deaths").locator("tbody tr").first()).toBeVisible();
    };
    await settled();
    test.slow(); // the longest pull, reloaded twice and scanned twice by axe: three times the time under load
    await expectAccessible(page, { ready: settled });
  });
});
