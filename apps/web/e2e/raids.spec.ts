// apps/web/e2e/raids.spec.ts
//
// The Raids page and the log search on the hosted API (the dev loop with WCL replay): QkVa6GHRK8yfrTbw is a recorded
// public report the replay answers on the app token; SYNTH01 (seeded) is readable by the test users only. Every chip
// is compared with the lookup's own answer, never with hard-coded numbers.
//
// PUBLIC must never be in E2E_REPORTS: global setup marks every report in that list as readable by the test users
// and not public, which would turn the public lookup into a 404.

import type { components } from "@khadbot/api-client";

import { expectAccessible } from "./support/axe";
import { HOSTED_API } from "./support/env";
import { expect, test } from "./support/fixtures";

type Lookup = components["schemas"]["ReportLookup"];

const PUBLIC = "QkVa6GHRK8yfrTbw";
const PRIVATE = "SYNTH01";
const UNKNOWN = "ZZZZZZZZZZZZZZZZ";

async function lookupOf(request: import("@playwright/test").APIRequestContext, source: string): Promise<Lookup> {
  const res = await request.get(`${HOSTED_API}/v1/lookup/report`, { params: { source } });
  expect(res.status()).toBe(200);
  return (await res.json()) as Lookup;
}

test.describe("before a search", () => {
  test("offers the Log tab and the empty state; Guild and Character follow the flag", async ({ page, request }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Raids", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Find a night of raiding" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Log" })).toHaveAttribute("aria-selected", "true");
    const health = await (await request.get(`${HOSTED_API}/v1/health`)).json();
    await expect(page.getByRole("tab", { name: "Guild" })).toHaveCount(health.features.raid_search ? 1 : 0);
    await expect(page.getByRole("tab", { name: "Character" })).toHaveCount(health.features.raid_search ? 1 : 0);
  });

  test("passes axe in both themes", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Find a night of raiding" })).toBeVisible();
    await expectAccessible(page);
  });
});

test.describe("log search", () => {
  test("every chip matches the lookup, kills first, and links to its pull", async ({ page, request }) => {
    const lookup = await lookupOf(request, PUBLIC);
    await page.goto("/");
    await page.getByLabel("WCL report code or URL").fill(PUBLIC);
    await page.getByRole("button", { name: "Look up" }).click();
    await expect(page).toHaveURL(new RegExp(`source=${PUBLIC}`));

    const pulls = lookup.groups.reduce((n, g) => n + g.pulls.length, 0);
    await expect(page.getByRole("heading", { level: 2, name: new RegExp(`^1 night · ${pulls} boss pulls$`) })).toBeVisible();
    const card = page.getByTestId("night-card");
    await expect(card).toContainText(PUBLIC);
    await expect(card.getByTestId("encounter-row")).toHaveCount(lookup.groups.length);

    for (const [i, group] of lookup.groups.entries()) {
      const row = card.getByTestId("encounter-row").nth(i);
      await expect(row).toContainText(group.encounter_name);
      const chips = row.getByTestId("pull-chip");
      await expect(chips).toHaveCount(group.pulls.length);
      const expected = [...group.pulls.filter((p) => p.outcome === "kill"), ...group.pulls.filter((p) => p.outcome !== "kill")];
      for (const [j, pull] of expected.entries()) {
        const chip = chips.nth(j);
        await expect(chip).toHaveAttribute("href", `/r/${PUBLIC}/p/${pull.fight}`);
        await expect(chip).toHaveAttribute("data-kill", String(pull.outcome === "kill"));
        await expect(chip).toHaveAttribute("data-best", String(pull.fight === group.best_fight));
        await expect(chip).toHaveAttribute("aria-label", new RegExp(`^P${pull.ordinal} .*, fight ${pull.fight}`));
        await expect(chip).toContainText(pull.outcome === "kill" ? "Kill" : `${pull.boss_pct!.toFixed(1)}%`);
      }
    }
  });

  test("reload and Back restore the search", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("WCL report code or URL").fill(PUBLIC);
    await page.getByRole("button", { name: "Look up" }).click();
    await expect(page.getByTestId("night-card")).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("WCL report code or URL")).toHaveValue(PUBLIC);
    await expect(page.getByTestId("night-card")).toBeVisible();
    await page.goBack();
    await expect(page.getByRole("heading", { name: "Find a night of raiding" })).toBeVisible();
    await page.goForward();
    await expect(page.getByTestId("night-card")).toBeVisible();
  });

  test("a URL with #fight= lands on that pull", async ({ page, request }) => {
    const lookup = await lookupOf(request, PUBLIC);
    const fight = lookup.groups[0]!.pulls[0]!.fight;
    await page.goto("/");
    await page.getByLabel("WCL report code or URL").fill(`https://www.warcraftlogs.com/reports/${PUBLIC}#fight=${fight}`);
    await page.getByRole("button", { name: "Look up" }).click();
    await expect(page).toHaveURL(new RegExp(`/r/${PUBLIC}/p/${fight}$`));
  });

  test("results pass axe in both themes", async ({ page }) => {
    await page.goto(`/?source=${PUBLIC}`);
    await expect(page.getByTestId("night-card")).toBeVisible();
    await expectAccessible(page);
  });

  test("the results paint quickly once the lookup answers (cached)", { tag: "@budget" }, async ({ page }, testInfo) => {
    // Warm the API's cache, then load fresh and time answer -> painted card in the page's own clock.
    await page.goto(`/?source=${PUBLIC}`);
    await expect(page.getByTestId("night-card")).toBeVisible();
    await page.addInitScript(() => {
      const w = window as unknown as { __answered?: number; __painted?: number };
      const orig = window.fetch.bind(window);
      window.fetch = async (...args: Parameters<typeof fetch>) => {
        const res = await orig(...args);
        const url = args[0] instanceof Request ? args[0].url : String(args[0]);
        if (url.includes("/v1/lookup/report")) w.__answered = performance.now();
        return res;
      };
      new MutationObserver(() => {
        if (w.__painted === undefined && document.querySelector('[data-testid="pull-chip"]')) {
          requestAnimationFrame(() => requestAnimationFrame(() => (w.__painted ??= performance.now())));
        }
      }).observe(document, { childList: true, subtree: true });
    });
    await page.reload();
    await expect(page.getByTestId("night-card")).toBeVisible();
    await page.waitForFunction(() => (window as unknown as { __painted?: number }).__painted !== undefined);
    const ms = await page.evaluate(() => {
      const w = window as unknown as { __answered: number; __painted: number };
      return Math.round(w.__painted - w.__answered);
    });
    testInfo.annotations.push({ type: "results-paint-ms", description: String(ms) });
    console.log(`results paint after the lookup answered: ${ms} ms`);
    expect(ms).toBeLessThan(1500);
  });
});

test.describe("errors, signed in", () => {
  test.use({ user: "alice" });

  test("a signed-in user's unreadable log says it is not shared with their account", async ({ page }) => {
    await page.goto(`/?source=${UNKNOWN}`);
    await expect(page.locator('[data-slot="private-log-card"]')).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Report not found or not shared with your Warcraft Logs account" })).toBeVisible();
  });
});

test.describe("errors", () => {
  test("a signed-out visitor's unreadable log shows the private-log card", async ({ page }) => {
    await page.goto(`/?source=${PRIVATE}`);
    const spot = page.locator('[data-slot="private-log-card"]');
    await expect(spot).toBeVisible();
    await expect(spot.getByRole("heading", { name: "This log is private" })).toBeVisible();
  });

  test("a busy WarcraftLogs counts down and enables Retry at the end", async ({ page }) => {
    let calls = 0;
    await page.route("**/v1/lookup/report*", async (route) => {
      calls += 1;
      if (calls === 1) {
        await route.fulfill({
          status: 429,
          contentType: "application/json",
          headers: { "access-control-allow-origin": "http://localhost:3000", "access-control-allow-credentials": "true" },
          body: JSON.stringify({ code: "budget_exhausted", message: "No points", wait_s: 3 }),
        });
      } else {
        await route.continue();
      }
    });
    await page.goto(`/?source=${PUBLIC}`);
    await expect(page.getByRole("heading", { name: "WarcraftLogs is busy" })).toBeVisible();
    const retry = page.getByRole("button", { name: /^Retry in 0:0\d$/ });
    await expect(retry).toBeDisabled();
    await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeEnabled({ timeout: 8000 });
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByTestId("night-card")).toBeVisible();
  });

  test("WarcraftLogs being down says KhadBot can't connect", async ({ page }) => {
    await page.route("**/v1/lookup/report*", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        headers: { "access-control-allow-origin": "http://localhost:3000", "access-control-allow-credentials": "true" },
        body: JSON.stringify({ code: "wcl_unavailable", message: "down" }),
      }),
    );
    await page.goto(`/?source=${PUBLIC}`);
    await expect(page.getByRole("heading", { name: "KhadBot can't connect to WarcraftLogs" })).toBeVisible();
  });

  test("a server failure shows its request reference and copies the full ID", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.route("**/v1/lookup/report*", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        headers: {
          "access-control-allow-origin": "http://localhost:3000",
          "access-control-allow-credentials": "true",
          "access-control-expose-headers": "X-Request-ID",
          "x-request-id": "7f3a9c0e5b2d4e61",
        },
        body: JSON.stringify({ code: "wcl_unavailable", message: "down" }),
      }),
    );
    await page.goto(`/?source=${PUBLIC}`);
    const alert = page.getByRole("alert");
    await expect(alert.getByText("Reference: 7f3a9c0e")).toBeVisible();
    await alert.getByRole("button", { name: "Copy the full reference" }).click();
    await expect(alert.getByText("Copied")).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("7f3a9c0e5b2d4e61");
  });

  test("an API that does not answer says KhadBot isn't responding", async ({ page }) => {
    await page.route("**/v1/lookup/report*", (route) => route.abort("failed"));
    await page.goto(`/?source=${PUBLIC}`);
    await expect(page.getByRole("heading", { name: "KhadBot isn't responding" })).toBeVisible();
  });

  test("error cards pass axe in both themes", async ({ page }) => {
    await page.goto(`/?source=${PRIVATE}`);
    await expect(page.locator('[data-slot="private-log-card"]')).toBeVisible();
    await expectAccessible(page);
  });
});
