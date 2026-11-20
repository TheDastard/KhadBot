// apps/web/e2e/local/boss-art.spec.ts
//
// Boss art on `main.py serve` over the fixture data directory (playwright.local.config.ts). Global setup uploads
// generated test PNGs through the real upload script, so the API serves a portrait for every registry boss; its
// Blizzard thumbnail URLs are null (no credentials), and the fixture blocks every request off the machine anyway, so
// every thumbnail shows its fallback. QkVa6GHRK8yfrTbw, the one report CI builds into the fixture data, holds
// Ula'tek, a registry boss; pull 14 is one of hers.

import { expectAccessible } from "../support/axe";
import { LOCAL_API } from "../support/env";
import { expect, test } from "../support/fixtures";


const REPORT = "QkVa6GHRK8yfrTbw";
const PULL = `/r/${REPORT}/p/14`;
const BOSS = "Ula'tek";

/** The image has loaded and has a size (not a broken image). */
async function loaded(img: import("@playwright/test").Locator): Promise<boolean> {
  return img.evaluate((el) => (el as HTMLImageElement).complete && (el as HTMLImageElement).naturalWidth > 0);
}

test("the encounter row on the Raids page shows the 48 by 24 portrait from the API", async ({ page }) => {
  await page.goto("/");
  const row = page.getByTestId("night-card").filter({ hasText: REPORT }).getByTestId("encounter-row").filter({ hasText: BOSS }).first();
  const portrait = row.getByTestId("boss-portrait");
  await expect(portrait).toBeVisible();
  await expect(portrait).toHaveAttribute("src", new RegExp(String.raw`^${LOCAL_API}/v1/art/portraits/Ulatek\.png\?v=[0-9a-f]+$`));
  await expect.poll(() => loaded(portrait)).toBe(true);
  expect(await portrait.boundingBox()).toMatchObject({ width: 48, height: 24 });
  await expect(page.getByTestId("art-attribution")).toContainText("Blizzard");
});

test("the pull page shows the 96 by 48 portrait in the heading and a thumbnail fallback in the rail", async ({ page }) => {
  await page.goto(PULL);
  const header = page.getByTestId("pull-header");
  const portrait = header.getByTestId("boss-portrait");
  await expect(portrait).toBeVisible();
  await expect.poll(() => loaded(portrait)).toBe(true);
  expect(await portrait.boundingBox()).toMatchObject({ width: 96, height: 48 });
  await expect(header.getByRole("heading", { level: 1 })).toContainText(BOSS);

  const group = page.locator("#pull-list [data-group]").filter({ hasText: BOSS }).first();
  const thumb = group.getByTestId("boss-thumbnail");
  await expect(thumb).toHaveAttribute("data-art", "fallback");
  expect(await thumb.boundingBox()).toMatchObject({ width: 28, height: 28 });
  await expect(group).toContainText(BOSS);
  await expect(page.getByTestId("art-attribution")).toBeVisible();
  await expectAccessible(page, { ready: () => expect(portrait).toBeVisible() });
});

test("a portrait the API cannot serve leaves the names alone, with no broken image", async ({ page }) => {
  await page.route(`${LOCAL_API}/v1/art/portraits/**`, (route) => route.fulfill({ status: 404, body: "" }));
  await page.goto(PULL);
  await expect(page.getByTestId("pull-header").getByRole("heading", { level: 1 })).toContainText(BOSS);
  await expect(page.getByTestId("boss-portrait")).toHaveCount(0);
  await expect(page.locator("#pull-list [data-group]").filter({ hasText: BOSS }).first()).toBeVisible();
});

test("with the art listing failing every place falls back and the app still works", async ({ page }) => {
  await page.route(`${LOCAL_API}/v1/art/encounters`, (route) => route.fulfill({ status: 500, body: "{}" }));
  await page.goto("/");
  await expect(page.getByTestId("night-card").first()).toBeVisible();
  await expect(page.getByTestId("boss-portrait")).toHaveCount(0);
  await expect(page.getByTestId("art-attribution")).toHaveCount(0);
});
