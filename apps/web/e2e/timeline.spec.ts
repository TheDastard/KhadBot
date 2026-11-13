// apps/web/e2e/timeline.spec.ts
//
// The pull timeline and the zoomed events on the longest fixture pull, H7z1fqDXjRTFnbtg:15 (618 s), on the hosted
// API with WCL replay. The report must be readable by the test users; the beforeAll below loads the pull through
// replay if it isn't loaded yet:
//
//   E2E_REPORTS=SYNTH01,H7z1fqDXjRTFnbtg mise run test:e2e -- timeline
//
// Screen text is compared with the API's own answers, never with fixed numbers. The last test measures the budgets
// (hover, click, keyboard step, first render) in the page, prints them as `BUDGET name=ms` and enforces them.

import { expectAccessible } from "./support/axe";
import { HOSTED_API } from "./support/env";
import { expect, test } from "./support/fixtures";
import { ensurePullLoaded, LOAD_TIMEOUT_MS } from "./support/pulls";

const CODE = "H7z1fqDXjRTFnbtg";
const FIGHT = 15;
const PULL = `/r/${CODE}/p/${FIGHT}`;

test.use({ user: "alice" });
// The chart is client-only and draws about 980 markers on this pull; beside the parallel suite on a dev build its first
// render can take far longer than when the spec runs alone, so the waits for it and the tests' timeout are longer.
test.describe.configure({ timeout: 90_000 });

test.beforeAll(async ({ browser }) => {
  test.setTimeout(LOAD_TIMEOUT_MS);
  await ensurePullLoaded(browser, CODE, FIGHT);
});

async function open(page: import("@playwright/test").Page) {
  await page.goto(PULL);
  await expect(page.locator("[data-marker]").first()).toBeVisible({ timeout: 30_000 });
}

test("clicking a death marker selects it in the URL", async ({ page }) => {
  await open(page);
  // The last death stands alone; earlier ones can sit within a pixel of each other.
  const death = page.locator("[data-lane=death]").last();
  const ref = await death.getAttribute("data-marker");
  await death.click();
  await expect(page).toHaveURL(new RegExp(`ref=${encodeURIComponent(ref!).replace(/%/g, "%")}`));
  await expect(death).toHaveAttribute("aria-pressed", "true");
});

test("arrow keys step between markers, Enter selects, Escape clears", async ({ page }) => {
  await open(page);
  const group = page.getByRole("group", { name: "Timeline markers" });
  const first = group.locator("[data-marker][tabindex='0']");
  await first.focus();
  const before = await first.getAttribute("data-marker");
  await page.keyboard.press("ArrowRight");
  const focused = page.locator("[data-marker]:focus");
  await expect(focused).toHaveCount(1);
  expect(await focused.getAttribute("data-marker")).not.toBe(before);
  // The group is one tab stop.
  await expect(group.locator("[data-marker][tabindex='0']")).toHaveCount(1);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/ref=/);
  await page.keyboard.press("Escape");
  await expect(page).not.toHaveURL(/ref=/);
});

test("Z zooms to 10 s and the events table matches the events response", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: /^Death 2,/ }).focus();
  await page.keyboard.press("z");
  await expect(page).toHaveURL(/t=\d+-\d+/);
  const [, from, to] = /t=(\d+)-(\d+)/.exec(page.url())!;
  expect(Number(to) - Number(from)).toBe(10_000);
  const region = page.getByTestId("zoomed-events");
  await expect(region.getByRole("heading", { name: /^Events / })).toBeVisible();

  const api = await page.request.get(
    `${HOSTED_API}/v1/reports/${CODE}/pulls/${FIGHT}/events?t_from_ms=${from}&t_to_ms=${to}&types=damage&types=death`,
  );
  expect(api.status()).toBe(200);
  const body = (await api.json()) as { rows: unknown[]; total: number };
  await expect(region.getByTestId("row-count")).toHaveText(`${body.rows.length} of ${body.total} row${body.total === 1 ? "" : "s"}`);
  await expect(region.locator("tbody tr")).toHaveCount(body.rows.length);

  await region.getByRole("button", { name: "Clear zoom" }).click();
  await expect(page).not.toHaveURL(/t=/);
  await expect(region).toHaveCount(0);
});

test("brushing a window zooms to it, 30 s at most", async ({ page }) => {
  await open(page);
  const group = page.getByRole("group", { name: "Timeline markers" });
  const box = (await group.boundingBox())!;
  const y = box.y + 15; // the phases lane has no markers
  await page.mouse.move(box.x + box.width * 0.3, y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.5, y, { steps: 8 });
  await page.mouse.up();
  await expect(page).toHaveURL(/t=\d+-\d+/);
  const [, from, to] = /t=(\d+)-(\d+)/.exec(page.url())!;
  expect(Number(to) - Number(from)).toBeLessThanOrEqual(30_000);
  await expect(page.getByTestId("zoomed-events")).toBeVisible();
});

test("a window in the URL shows its events on load", async ({ page }) => {
  await page.goto(`${PULL}?t=420341-430341`);
  await expect(page.getByTestId("zoomed-events").getByRole("heading", { name: "Events 7:00.3 – 7:10.3" })).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByTestId("zoom-window")).toBeVisible();
});

test("the timeline passes axe in both themes, with the events table open", async ({ page }) => {
  await page.goto(`${PULL}?t=420341-430341`);
  await expect(page.getByTestId("zoomed-events").locator("tbody tr").first()).toBeVisible();
  await expectAccessible(page, { include: "[data-slot='pull-timeline'], [data-testid='zoomed-events']" });
});

test("budgets: first render, hover, click and step", { tag: "@budget" }, async ({ page }) => {
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, number>;
    new MutationObserver(() => {
      if (w.__first === undefined && document.querySelector("[data-marker]")) w.__first = performance.now();
    }).observe(document, { childList: true, subtree: true });
  });
  await page.goto(PULL);
  await expect(page.locator("[data-marker]").first()).toBeVisible({ timeout: 30_000 });
  const first = await page.evaluate(() => (window as unknown as Record<string, number>).__first!);

  // Time from an input event's own timestamp to the DOM showing its result.
  const measure = (kind: "hover" | "click" | "step") =>
    page.evaluate(
      (k) =>
        new Promise<number>((resolve) => {
          let start = 0;
          const stamp = (e: Event) => (start = e.timeStamp || performance.now());
          const done = () => resolve(performance.now() - start);
          const check = () => {
            if (k === "hover" && document.querySelector("[data-slot='tooltip-content']")) return true;
            if (k === "click" && document.querySelector("[data-lane='death'][aria-pressed='true']")) return true;
            if (k === "step") {
              const el = document.activeElement as HTMLElement | null;
              return !!el?.dataset.marker && el.dataset.marker !== (window as unknown as { __from?: string }).__from;
            }
            return false;
          };
          const type = k === "hover" ? "pointermove" : k === "click" ? "click" : "keydown";
          document.addEventListener(type, stamp, { capture: true, once: true });
          const poll = () => {
            if (start && check()) done();
            else requestAnimationFrame(poll);
          };
          requestAnimationFrame(poll);
        }),
      kind,
    );

  const death = page.locator("[data-lane=death]").last();
  const hoverP = measure("hover");
  await page.waitForTimeout(100); // let the listener arm before the input
  await death.hover();
  const hover = await hoverP;

  const clickP = measure("click");
  await page.waitForTimeout(100); // let the listener arm before the input
  await death.click();
  const click = await clickP;

  await death.focus();
  await page.evaluate(() => {
    (window as unknown as { __from?: string }).__from = (document.activeElement as HTMLElement).dataset.marker;
  });
  const stepP = measure("step");
  await page.waitForTimeout(100); // let the listener arm before the input
  await page.keyboard.press("ArrowLeft");
  const step = await stepP;

  const fixed = (n: number) => Math.round(n);
  console.log(`BUDGET first_render_ms=${fixed(first)} hover_ms=${fixed(hover)} click_ms=${fixed(click)} step_ms=${fixed(step)}`);
  expect(first).toBeLessThan(1500);
  expect(hover).toBeLessThan(100);
  expect(click).toBeLessThan(100);
  expect(step).toBeLessThan(100);
});
