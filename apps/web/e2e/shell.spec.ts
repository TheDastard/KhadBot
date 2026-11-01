// apps/web/e2e/shell.spec.ts
//
// The shell on the hosted API (the dev loop with WCL replay): the top bar signed out and signed in, the theme menu,
// the route shells and their slots, and axe in both themes. SYNTH01 must be seeded (`mise run dev:seed -- --only
// SYNTH01`); its fights 1 and 2 are loaded, fight 999 is not.

import { expectAccessible } from "./support/axe";
import { HOSTED_API } from "./support/env";
import { expect, setTheme, test } from "./support/fixtures";

const LOADED = "/r/SYNTH01/p/1";
const NOT_LOADED = "/r/SYNTH01/p/999";

test.describe("signed out", () => {
  test("the Raids page renders in the shell", async ({ page, blockedHosts }) => {
    await page.goto("/");
    const header = page.getByRole("banner");
    await expect(header.getByRole("link", { name: "KhadBot" })).toHaveAttribute("href", "/");
    await expect(header).toContainText("Encounter review");
    await expect(page.getByTestId("local-label")).toHaveCount(0);
    await expect(header.locator('[data-slot="account"]')).toHaveCount(1);
    await expect(page.locator('[data-slot="raids-page"]')).toBeVisible();
    // Only Wowhead's script was asked for off the machine, and it was refused.
    expect([...new Set(blockedHosts)]).toEqual(["wow.zamimg.com"]);
  });

  test("passes axe in both themes", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator('[data-slot="raids-page"]')).toBeVisible();
    await expectAccessible(page);
  });
});

test.describe("signed in", () => {
  test.use({ user: "alice" });

  test("the session reaches the API", async ({ page }) => {
    await page.goto("/");
    const me = await page.request.get(`${HOSTED_API}/v1/me`);
    expect(me.status()).toBe(200);
    expect((await me.json()).name).toContain("e2e-alice");
  });

  test("a loaded pull shows the pull page's slots", async ({ page }) => {
    await page.goto(LOADED);
    const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
    await expect(crumbs).toContainText("Raids");
    await expect(crumbs).toContainText("SYNTH01");
    await expect(crumbs.getByText("fight 1")).toHaveAttribute("aria-current", "page");
    // The report code is plain text: the night overview isn't built this round.
    await expect(crumbs.getByRole("link")).toHaveCount(1);
    await expect(page.locator('[data-slot="pull-body"]')).toBeVisible();
    await expect(page.locator('[data-slot="pull-timeline"]')).toBeVisible();
  });

  test("a pull that isn't loaded shows the opening slot", async ({ page }) => {
    await page.goto(NOT_LOADED);
    await expect(page.locator('[data-slot="pull-not-loaded"]')).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Opening a pull");
  });

  test("a malformed pull address says so", async ({ page }) => {
    await page.goto("/r/SYNTH01/p/first");
    await expect(page.getByRole("heading", { name: "This isn't a pull's address" })).toBeVisible();
  });

  test("the pull page passes axe in both themes", async ({ page }) => {
    await page.goto(LOADED);
    await expect(page.locator('[data-slot="pull-body"]')).toBeVisible();
    await expectAccessible(page);
  });
});

test.describe("theme menu", () => {
  test("switches by keyboard and is remembered", async ({ page }) => {
    await setTheme(page, "light");
    await page.goto("/");
    const trigger = page.getByRole("button", { name: /^Theme: light/ });
    await trigger.focus();
    await page.keyboard.press("Enter");
    await page.getByRole("menuitemradio", { name: "Dark" }).click();
    await expect(page.locator("html.dark")).toHaveCount(1);
    await expect(page.getByRole("button", { name: /^Theme: dark/ })).toBeVisible();
    await page.reload();
    await expect(page.locator("html.dark")).toHaveCount(1);
  });

  test("System follows the browser", async ({ page }) => {
    await setTheme(page, "system");
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/");
    await expect(page.locator("html.dark")).toHaveCount(1);
    await page.emulateMedia({ colorScheme: "light" });
    await expect(page.locator("html.dark")).toHaveCount(0);
  });
});
