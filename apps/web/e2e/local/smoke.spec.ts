// apps/web/e2e/local/smoke.spec.ts
//
// The app on `main.py serve` over the fixture data directory (playwright.local.config.ts): no sign-in, so the header
// says "Local" and has no account control; reports open without a session; an area whose flag is off shows the
// "Not available on this server yet" notice.

import { expectAccessible } from "../support/axe";
import { expect, test } from "../support/fixtures";

const LOADED = "/r/QkVa6GHRK8yfrTbw/p/14";

test("the shell says Local and offers no sign-in", async ({ page }) => {
  await page.goto("/");
  const header = page.getByRole("banner");
  await expect(page.getByTestId("local-label")).toHaveText("Local");
  await expect(header.locator('[data-slot="account"]')).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Raids", level: 1 })).toBeVisible();
});

test("the Raids page lists the reports on the server, with no search", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.getByTestId("night-card").first()).toBeVisible();
  await expect(page.getByTestId("night-card").first().getByTestId("pull-chip").first()).toHaveAttribute("href", /^\/r\/[^/]+\/p\/\d+$/);
});

test("a loaded pull opens without a session", async ({ page }) => {
  await page.goto(LOADED);
  await expect(page.locator('[data-slot="pull-body"]')).toBeVisible();
});

test("opening a pull that isn't loaded is not available here", async ({ page }) => {
  await page.goto("/r/QkVa6GHRK8yfrTbw/p/999");
  await expect(page.getByRole("heading", { name: "Not available on this server yet" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to Raids" })).toHaveAttribute("href", "/");
  await expect(page.locator('[data-slot="pull-not-loaded"]')).toHaveCount(0);
});

test("passes axe in both themes", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Raids", level: 1 })).toBeVisible();
  await expectAccessible(page);
});
