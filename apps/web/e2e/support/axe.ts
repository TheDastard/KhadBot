// apps/web/e2e/support/axe.ts
//
// The accessibility check every screen gets: axe-core in light and in dark at 1440 wide, failing on any serious or
// critical violation (WCAG 2.2 A and AA rules plus axe's best practices). Call it once the screen has settled:
//
//   await expectAccessible(page);                       // the page as it is, in both themes
//   await expectAccessible(page, { include: "main" });  // only part of it
//   await expectAccessible(page, { ready: () => expect(table).toBeVisible() });  // wait for data after each reload
//
// It switches the theme through the stored choice and a reload, so call it last in a test or re-establish state after.

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

export interface A11yOptions {
  /** CSS selector to limit the check to. */
  include?: string;
  /** Rule IDs to skip, each with a reason in the test (prefer fixing the page). */
  disableRules?: string[];
  themes?: Array<"light" | "dark">;
  /**
   * Awaited after each theme's reload, before axe runs: the screen's own "data has arrived" check. `networkidle`
   * alone can fire before the page has asked for its data (the reload is slow under load), and axe would then check
   * a skeleton, or a half-rendered table, instead of the screen.
   */
  ready?: () => Promise<void>;
}

const SERIOUS = new Set(["serious", "critical"]);

async function applyTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  await page.evaluate((value) => window.localStorage.setItem("khadbot-theme", value), theme);
  await page.reload();
  await expect(page.locator("html.dark")).toHaveCount(theme === "dark" ? 1 : 0);
  await page.waitForLoadState("networkidle");
}

export async function expectAccessible(page: Page, options: A11yOptions = {}): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 900 });
  for (const theme of options.themes ?? ["light", "dark"]) {
    await applyTheme(page, theme);
    await options.ready?.();
    let builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]);
    if (options.include) builder = builder.include(options.include);
    if (options.disableRules?.length) builder = builder.disableRules(options.disableRules);
    const { violations } = await builder.analyze();
    const serious = violations
      .filter((v) => SERIOUS.has(v.impact ?? ""))
      .map((v) => `${v.id} (${v.impact}): ${v.help} — ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
    expect(serious, `serious accessibility violations in ${theme}`).toEqual([]);
  }
}
