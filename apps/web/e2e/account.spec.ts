// apps/web/e2e/account.spec.ts
//
// Sign-in and the account menu on the hosted API (the dev loop with WCL replay): the signed-out top bar and the
// private-log card, the signed-in menu (name, points, followed logs, Sign out) driven by keyboard, and a session that
// ends mid-visit. Test sessions stand in for a Warcraft Logs login; SYNTH01 (seeded) is readable by the test users
// only, so a signed-out lookup of it is the private log.

import AxeBuilder from "@axe-core/playwright";
import type { components } from "@khadbot/api-client";
import type { Page } from "@playwright/test";

import { expectAccessible } from "./support/axe";
import { HOSTED_API } from "./support/env";
import { expect, test } from "./support/fixtures";
import { addWatch, removeWatches } from "./support/watch";

type Budget = components["schemas"]["BudgetResponse"];

const PRIVATE = "SYNTH01";

/** Axe over the whole page with the account menu open, in each theme. */
async function expectMenuAccessible(page: Page): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 900 });
  for (const theme of ["light", "dark"] as const) {
    await page.evaluate((value) => window.localStorage.setItem("khadbot-theme", value), theme);
    await page.reload();
    await expect(page.locator("html.dark")).toHaveCount(theme === "dark" ? 1 : 0);
    await page.getByTestId("account-button").click();
    await expect(page.getByRole("dialog", { name: "Account" })).toBeVisible();
    const { violations } = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"])
      .analyze();
    const serious = violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id}: ${v.help} — ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
    expect(serious, `serious accessibility violations in ${theme}`).toEqual([]);
  }
}

test.describe("signed out", () => {
  test("the top bar offers sign-in that returns to the page being viewed", async ({ page, request }) => {
    await page.goto("/");
    const signIn = page.getByRole("banner").getByRole("link", { name: "Sign in with Warcraft Logs" });
    const first = new URL((await signIn.getAttribute("href")) ?? "");
    expect(first.origin + first.pathname).toBe(`${HOSTED_API}/v1/auth/login`);
    expect(first.searchParams.get("next")).toBe("/");

    await page.goto(`/?source=${PRIVATE}`);
    const href = (await signIn.getAttribute("href")) ?? "";
    expect(new URL(href).searchParams.get("next")).toBe(`/?source=${PRIVATE}`);

    // The API sends the browser on to Warcraft Logs (never followed here); no session exists yet.
    const res = await request.get(href, { maxRedirects: 0 });
    expect(res.status()).toBe(303);
    expect(res.headers()["location"]).toContain("warcraftlogs.com");
  });

  test("a private log shows the sign-in card, and the card returns to that log", async ({ page }) => {
    await page.goto(`/?source=${PRIVATE}`);
    const card = page.getByTestId("private-log-card");
    await expect(card.getByRole("heading", { name: "This log is private" })).toBeVisible();
    await expect(card.getByRole("listitem")).toHaveCount(3);
    await expect(card).toContainText("You come straight back to this log afterwards.");
    await expect(card).not.toContainText("less waiting");
    const link = card.getByRole("link", { name: "Sign in with Warcraft Logs" });
    expect(new URL((await link.getAttribute("href")) ?? "").searchParams.get("next")).toBe(`/?source=${PRIVATE}`);
    await expectAccessible(page);
  });
});

test.describe("signed in", () => {
  test.use({ user: "alice" });

  test("the menu shows the account, the points and Sign out, and works by keyboard", async ({ page }) => {
    const budget = (await (await page.request.get(`${HOSTED_API}/v1/budget`)).json()) as Budget;
    await page.goto("/");
    const button = page.getByTestId("account-button");
    await expect(button).toContainText("e2e-alice");
    await expect(page.getByRole("link", { name: "Sign in with Warcraft Logs" })).toHaveCount(0);

    await button.focus();
    await page.keyboard.press("Enter");
    const menu = page.getByRole("dialog", { name: "Account" });
    await expect(menu).toContainText("e2e-alice");
    await expect(menu.getByRole("heading", { name: "Warcraft Logs points" })).toBeVisible();
    const left = Math.floor(Math.max(0, budget.allowance.left));
    await expect(page.getByTestId("points-left")).toContainText(`${left.toLocaleString("en-US")} of`);
    await expect(page.getByRole("progressbar", { name: "Points left this hour" })).toHaveAttribute(
      "aria-valuenow",
      String(left),
    );
    await expect(page.getByTestId("points-reset")).toContainText(/resets \d/);
    await expect(menu.getByText("Following live")).toHaveCount(0); // nothing followed
    await expect(menu).not.toContainText("less waiting");

    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    await expect(button).toBeFocused();
    await expectMenuAccessible(page);
  });

  test("a session that ends mid-visit becomes signed out with a notice", async ({ page }) => {
    await page.route(`${HOSTED_API}/v1/budget`, (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ code: "not_signed_in", message: "Sign in with Warcraft Logs first." }),
      }),
    );
    await page.goto("/");
    await page.getByTestId("account-button").click();
    const header = page.getByRole("banner");
    await expect(header.getByRole("status")).toContainText("Your session ended");
    await expect(header.getByRole("link", { name: "Sign in with Warcraft Logs" })).toBeVisible();
    await expect(page.getByTestId("account-button")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Raids", level: 1 })).toBeVisible();
  });
});

// Bob's session is the one these tests end, so they run one after another and alice's stay valid.
test.describe("signed in, a followed log and signing out", () => {
  test.use({ user: "bob" });
  test.describe.configure({ mode: "serial" });

  test("a followed log shows with Stop, and Stop ends it", async ({ page }) => {
    const me = (await (await page.request.get(`${HOSTED_API}/v1/me`)).json()) as { id: string };
    addWatch(PRIVATE, me.id);
    try {
      await page.goto("/");
      await page.getByTestId("account-button").click();
      const log = page.getByTestId("followed-log");
      await expect(log).toHaveCount(1);
      await expect(log).toContainText(PRIVATE);
      await expectMenuAccessible(page); // leaves the menu open
      await page.getByRole("button", { name: /^Stop following/ }).click();
      await expect(page.getByTestId("followed-log")).toHaveCount(0);
      const list = await (await page.request.get(`${HOSTED_API}/v1/watches`)).json();
      expect(list.watches).toEqual([]);
    } finally {
      removeWatches(PRIVATE);
    }
  });

  test("Sign out returns to Raids, signed out", async ({ page }) => {
    await page.goto(`/?source=${PRIVATE}`);
    await expect(page.getByTestId("night-card")).toBeVisible();
    await page.getByTestId("account-button").click();
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: "Raids", level: 1 })).toBeVisible();
    await expect(page.getByRole("banner").getByRole("link", { name: "Sign in with Warcraft Logs" })).toBeVisible();
    await expect(page.getByTestId("account-button")).toHaveCount(0);
    await expect(page.getByRole("banner")).not.toContainText("session ended");
    expect((await page.request.get(`${HOSTED_API}/v1/me`)).status()).toBe(401);
  });
});
