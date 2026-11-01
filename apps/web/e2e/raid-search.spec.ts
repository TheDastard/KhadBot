// apps/web/e2e/raid-search.spec.ts
//
// The Guild and Character tabs on the hosted API (the dev loop with WCL replay): the recorded searches are Death and
// Destruction on Skullcrusher (US) and the character Dastard there. Every number on screen is compared with the
// API's own answer, never with hard-coded numbers. The not-found and empty cards are mocked answers.

import type { components } from "@khadbot/api-client";
import type { Page } from "@playwright/test";

import { expectAccessible } from "./support/axe";
import { HOSTED_API } from "./support/env";
import { expect, setTheme, test } from "./support/fixtures";

type GuildSearch = components["schemas"]["GuildSearch"];
type CharacterSearch = components["schemas"]["CharacterSearch"];

const GUILD = { region: "us", server: "skullcrusher", name: "Death and Destruction" };
const CHARACTER = { region: "us", server: "skullcrusher", name: "Dastard" };
const CORS = { "access-control-allow-origin": "http://localhost:3000", "access-control-allow-credentials": "true" };

const pullsOf = (nights: GuildSearch["nights"]) => nights.reduce((n, night) => n + night.groups.reduce((m, g) => m + g.pulls.length, 0), 0);

async function fillServer(page: Page, label: string, typed: string, option: string) {
  const server = page.getByRole("combobox", { name: "Server" });
  await server.fill(typed);
  await page.getByRole("option", { name: option }).first().click();
  await expect(server).toHaveValue(label);
}

test.describe("server field", () => {
  test("filters as you type, bolds the prefix, keeps the regions as dividers and works by keyboard", async ({ page }) => {
    await page.goto("/?tab=guild");
    const server = page.getByRole("combobox", { name: "Server" });
    await server.fill("Storm");
    const list = page.getByRole("listbox", { name: "Servers" });
    await expect(list).toBeVisible();
    await expect(list.getByRole("group", { name: "United States" })).toBeVisible();
    await expect(list.getByRole("group", { name: "Europe" })).toBeVisible();
    const first = list.getByRole("option").first();
    await expect(first).toContainText("Stormrage");
    await expect(first.locator("b")).toHaveText("Storm");
    await expect(first).toContainText("US");
    await expect(first).toHaveAttribute("aria-selected", "true");
    await expect(server).toHaveAttribute("aria-activedescendant", (await first.getAttribute("id"))!);

    await server.press("ArrowDown");
    await expect(list.getByRole("option").nth(1)).toHaveAttribute("aria-selected", "true");
    await server.press("ArrowUp");
    await server.press("Escape");
    await expect(list).toBeHidden();
    await expect(server).toHaveAttribute("aria-expanded", "false");
    await server.press("ArrowDown");
    await expect(list).toBeVisible();
    await server.press("Enter");
    await expect(server).toHaveValue("Stormrage (US)");
    await expect(list).toBeHidden();
  });

  test("a name two regions share needs a choice", async ({ page }) => {
    await page.goto("/?tab=guild");
    await page.getByRole("combobox", { name: "Server" }).fill("Stormrage");
    await page.getByRole("textbox", { name: "Guild" }).fill("Anything");
    await page.getByRole("button", { name: "Find reports" }).click();
    await expect(page.getByText("Choose a server from the list.")).toBeVisible();
  });
});

test.describe("guild search", () => {
  test("renders the recorded guild's nights as the API answers them", async ({ page, request }) => {
    const res = await request.get(`${HOSTED_API}/v1/search/guild`, { params: GUILD });
    expect(res.status()).toBe(200);
    const search = (await res.json()) as GuildSearch;

    await page.goto("/?tab=guild");
    await expect(page.getByRole("tab", { name: "Guild" })).toHaveAttribute("aria-selected", "true");
    await fillServer(page, "Skullcrusher (US)", "Skull", "Skullcrusher");
    await page.getByRole("textbox", { name: "Guild" }).fill(GUILD.name);
    await page.getByRole("button", { name: "Find reports" }).click();
    await expect(page).toHaveURL(/tab=guild&region=us&server=skullcrusher&name=Death\+and\+Destruction/);

    const header = page.getByTestId("guild-header");
    await expect(header).toContainText(search.guild.name);
    await expect(header).toContainText(`${search.guild.server_name} (${search.guild.region})`);
    if (search.guild.faction) await expect(header).toContainText(search.guild.faction);
    await expect(
      page.getByRole("heading", { level: 2, name: new RegExp(`^Last \\d+ days · ${search.nights.length} nights? · ${pullsOf(search.nights)} boss pulls$`) }),
    ).toBeVisible();
    const cards = page.getByTestId("night-card");
    await expect(cards).toHaveCount(search.nights.length);
    for (const [i, night] of search.nights.entries()) {
      await expect(cards.nth(i)).toHaveAttribute("data-code", night.code);
      await expect(cards.nth(i).getByTestId("pull-chip")).toHaveCount(night.groups.reduce((n, g) => n + g.pulls.length, 0));
    }
    const firstPull = search.nights[0]!.groups[0]!.pulls[0]!;
    await expect(cards.first().locator(`a[href="/r/${search.nights[0]!.code}/p/${firstPull.fight}"]`)).toBeVisible();
    if (search.left_out > 0) await expect(page.getByTestId("result-notes")).toContainText("left out");
  });

  test("a pasted guild URL fills both fields, and reload restores the search", async ({ page }) => {
    await page.goto("/?tab=guild");
    await page.getByRole("textbox", { name: "Guild" }).fill("https://www.warcraftlogs.com/guild/us/skullcrusher/death%20and%20destruction");
    await expect(page.getByRole("combobox", { name: "Server" })).toHaveValue("Skullcrusher (US)");
    await expect(page.getByRole("textbox", { name: "Guild" })).toHaveValue("death and destruction");
    await page.getByRole("button", { name: "Find reports" }).click();
    await expect(page.getByTestId("guild-header")).toBeVisible();
    await page.reload();
    await expect(page.getByRole("combobox", { name: "Server" })).toHaveValue("Skullcrusher (US)");
    await expect(page.getByRole("textbox", { name: "Guild" })).toHaveValue("death and destruction");
    await expect(page.getByTestId("guild-header")).toBeVisible();
  });

  test("results pass axe in both themes", async ({ page }) => {
    await page.goto(`/?tab=guild&region=${GUILD.region}&server=${GUILD.server}&name=${encodeURIComponent(GUILD.name)}`);
    await expect(page.getByTestId("guild-header")).toBeVisible();
    await expectAccessible(page);
  });

  test("the open server list passes axe", async ({ page }) => {
    await setTheme(page, "light");
    await page.goto("/?tab=guild");
    await page.getByRole("combobox", { name: "Server" }).fill("Storm");
    await expect(page.getByRole("listbox", { name: "Servers" })).toBeVisible();
    await expectAccessible(page);
  });
});

test.describe("character search", () => {
  test("renders the recorded character's pulls as the API answers them", async ({ page, request }) => {
    const res = await request.get(`${HOSTED_API}/v1/search/character`, { params: CHARACTER });
    expect(res.status()).toBe(200);
    const search = (await res.json()) as CharacterSearch;

    await page.goto("/?tab=character");
    await fillServer(page, "Skullcrusher (US)", "Skull", "Skullcrusher");
    await page.getByRole("textbox", { name: "Character" }).fill(CHARACTER.name);
    await page.getByRole("button", { name: "Find pulls" }).click();

    const pulls = pullsOf(search.nights);
    await expect(
      page.getByRole("heading", { level: 2, name: `${search.character.name} · ${pulls} pulls on ${search.nights.length} nights` }),
    ).toBeVisible();
    const cards = page.getByTestId("night-card");
    await expect(cards).toHaveCount(search.nights.length);
    await expect(cards.first()).toContainText(`with ${search.character.name}`);
    await expect(page.getByRole("button", { name: /Night overview/ })).toHaveCount(0);

    // The encounter select now knows the encounters seen; choosing one limits the pulls.
    const group = search.nights[0]!.groups[0]!;
    await page.getByLabel("Encounter").selectOption({ label: group.encounter_name });
    await page.getByRole("button", { name: "Find pulls" }).click();
    await expect(page).toHaveURL(new RegExp(`encounter=${group.encounter_id}`));
    const filtered = (await (
      await request.get(`${HOSTED_API}/v1/search/character`, { params: { ...CHARACTER, encounter: group.encounter_id } })
    ).json()) as CharacterSearch;
    await expect(page.getByRole("heading", { level: 2 }).filter({ hasText: group.encounter_name })).toContainText(
      `${pullsOf(filtered.nights)} pull`,
    );
  });

  test("results pass axe in both themes", async ({ page }) => {
    await page.goto(`/?tab=character&region=${CHARACTER.region}&server=${CHARACTER.server}&name=${CHARACTER.name}`);
    await expect(page.getByTestId("night-card").first()).toBeVisible();
    await expectAccessible(page);
  });
});

test.describe("states", () => {
  const error = (status: number, code: string, extra: object = {}) => ({
    status,
    contentType: "application/json",
    headers: CORS,
    body: JSON.stringify({ code, message: code, ...extra }),
  });

  test("guild not found", async ({ page }) => {
    await page.route("**/v1/search/guild*", (route) => route.fulfill(error(404, "guild_not_found")));
    await page.goto(`/?tab=guild&region=us&server=skullcrusher&name=Nobody`);
    await expect(page.getByRole("heading", { name: "Guild not found" })).toBeVisible();
    await expect(page.locator('[data-error-code="guild_not_found"]')).toContainText("Skullcrusher (US)");
    await expectAccessible(page);
    await page.getByRole("button", { name: "Edit the search" }).click();
    await expect(page.getByRole("textbox", { name: "Guild" })).toBeFocused();
  });

  test("character not found", async ({ page }) => {
    await page.route("**/v1/search/character*", (route) => route.fulfill(error(404, "character_not_found")));
    await page.goto(`/?tab=character&region=us&server=skullcrusher&name=Nobody`);
    await expect(page.getByRole("heading", { name: "Character not found" })).toBeVisible();
    await expectAccessible(page);
  });

  test("no raids in the last 30 days", async ({ page }) => {
    await page.route("**/v1/search/guild*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: CORS,
        body: JSON.stringify({
          guild: { id: 1, name: "Quiet Guild", server_name: "Skullcrusher", server_slug: "skullcrusher", region: "US", faction: "Alliance" },
          window_start: "2026-10-02T00:00:00Z",
          window_end: "2026-11-01T00:00:00Z",
          nights: [],
          capped: false,
          left_out: 0,
        }),
      }),
    );
    await page.goto(`/?tab=guild&region=us&server=skullcrusher&name=Quiet`);
    await expect(page.getByTestId("guild-header")).toContainText("Alliance");
    await expect(page.getByRole("heading", { name: "No raids in the last 30 days" })).toBeVisible();
    await expectAccessible(page);
  });

  test("a busy WarcraftLogs counts down", async ({ page }) => {
    await page.route("**/v1/search/guild*", (route) => route.fulfill(error(429, "budget_exhausted", { wait_s: 30 })));
    await page.goto(`/?tab=guild&region=us&server=skullcrusher&name=Busy`);
    await expect(page.getByRole("heading", { name: "WarcraftLogs is busy" })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Retry in 0:/ })).toBeDisabled();
  });
});
