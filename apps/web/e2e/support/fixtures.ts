// apps/web/e2e/support/fixtures.ts
//
// The `test` every spec imports. On top of Playwright's own it:
// - keeps the browser offline: any request to a host other than localhost or 127.0.0.1 is aborted, so no test
//   reaches Warcraft Logs, Wowhead or a font host, and the app is tested with every third-party image and script
//   missing (Wowhead's script then never loads, and ability names show their fallbacks). `blockedHosts` lists them.
// - signs in: `test.use({ user: "alice" })` loads that test user's session (made by global-setup.ts); the default
//   is signed out.
// - `setTheme(page, "dark")` picks a theme choice before the page loads.
//
//   import { expect, test } from "./support/fixtures";
//   test.use({ user: "alice" });
//   test("...", async ({ page, blockedHosts }) => { ... });

import { test as base, type Page } from "@playwright/test";

import { USERS, type TestUser } from "./env";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

interface Fixtures {
  user: TestUser | null;
  blockedHosts: string[];
}

export const test = base.extend<Fixtures>({
  user: [null, { option: true }],
  storageState: async ({ user }, use) => {
    await use(user ? USERS[user] : { cookies: [], origins: [] });
  },
  blockedHosts: async ({ context }, use) => {
    const blocked: string[] = [];
    await context.route(
      (url) => !LOCAL_HOSTS.has(url.hostname),
      async (route) => {
        blocked.push(new URL(route.request().url()).hostname);
        await route.abort("blockedbyclient");
      },
    );
    await use(blocked);
  },
  page: async ({ page, blockedHosts: _ }, use) => {
    // Depending on blockedHosts installs the offline guard for every test, even one that doesn't ask for the list.
    await use(page);
  },
});

export { expect } from "@playwright/test";

/**
 * The theme choice the page starts with (the theme menu's stored value). Call before `page.goto`. It only sets a
 * browser with no choice yet, so a choice made in the test (the theme menu) survives reloads.
 */
export async function setTheme(page: Page, choice: "system" | "light" | "dark"): Promise<void> {
  await page.addInitScript((value) => {
    try {
      if (window.localStorage.getItem("khadbot-theme") === null) window.localStorage.setItem("khadbot-theme", value);
    } catch {
      // ignore
    }
  }, choice);
}
