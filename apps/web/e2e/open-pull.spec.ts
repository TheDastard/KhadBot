// apps/web/e2e/open-pull.spec.ts
//
// Opening a pull that isn't loaded, on the hosted API with WCL replay (no WCL point is spent).
//
// Real flow: needs the dev loop with SYNTH01 seeded and H7z1fqDXjRTFnbtg readable but NOT seeded, so its pulls load
// on request: `E2E_REPORTS=SYNTH01,H7z1fqDXjRTFnbtg mise run test:e2e` (needs `git lfs pull` for its pages). A pull
// that is already loaded skips its test; `mise run stack:reset` and a new seed start again.
//
// State screens (waiting, busy, failed, stopped) mock the job routes, since a state that depends on a simulated point
// limit or a failing worker can't be made to hold still.

import type { Page } from "@playwright/test";

import { expectAccessible } from "./support/axe";
import { E2E_REPORTS, HOSTED_API, WEB_URL } from "./support/env";
import { expect, test } from "./support/fixtures";

const REAL = "H7z1fqDXjRTFnbtg";
const MOCKED = "/r/SYNTH01/p/999"; // never loaded, never opened: the job routes are mocked
// A 2 by 2 opaque PNG, standing in for a boss thumbnail.
const TEST_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGOMspvHwMDAxAAGAA5uAToaGCg+AAAAAElFTkSuQmCC",
  "base64",
);

test.use({ user: "alice" });

async function skipIfLoaded(page: Page, fight: number): Promise<void> {
  test.skip(!E2E_REPORTS.includes(REAL), `${REAL} is not in E2E_REPORTS (it must be readable and not seeded)`);
  const res = await page.request.get(`${HOSTED_API}/v1/reports/${REAL}/pulls/${fight}/digest`);
  test.skip(res.status() === 200, `fight ${fight} of ${REAL} is already loaded: reset the stack to run this`);
}

function stepState(page: Page, step: string) {
  return page.locator(`[data-step="${step}"]`);
}

test.describe("a pull that has to load", () => {
  test("opens by itself, and a step change reaches the screen within a second", { tag: "@budget" }, async ({ page }) => {
    test.slow(); // a report's first load under parallel load outlasts the default 30 s
    await skipIfLoaded(page, 10);
    await page.addInitScript(() => {
      type W = Window & { __events: unknown[]; __dom: unknown[] };
      const w = window as unknown as W;
      w.__events = [];
      w.__dom = [];
      const Original = window.EventSource;
      window.EventSource = class extends Original {
        constructor(url: string | URL, init?: EventSourceInit) {
          super(url, init);
          for (const type of ["job.state", "pull.progress", "job.done"]) {
            this.addEventListener(type, (event) => {
              const data = JSON.parse((event as MessageEvent<string>).data);
              const pull = type === "pull.progress" ? data : data.pulls?.[0];
              w.__events.push({
                type,
                at: Date.now(),
                updated: data.updated_at ?? null,
                steps: Object.fromEntries((pull?.steps ?? []).map((s: { name: string; state: string }) => [s.name, s.state])),
              });
            });
          }
        }
      };
      new MutationObserver((mutations) => {
        for (const m of mutations) {
          const el = m.target as HTMLElement;
          if (el.dataset?.step) w.__dom.push({ step: el.dataset.step, state: el.dataset.state, at: Date.now() });
        }
      }).observe(document, { subtree: true, attributes: true, attributeFilter: ["data-state"] });
    });

    await page.goto(`/r/${REAL}/p/10`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("P");
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Opening a pull");
    await expect(page.locator('[data-slot="pull-not-loaded"]')).toBeVisible();
    await expect(page.getByRole("heading", { name: "Steps" })).toBeVisible();
    await expect(page.locator("[data-step]")).toHaveCount(3);

    // The pull page replaces the loading screen without a reload.
    await expect(page.locator('[data-slot="pull-body"]')).toBeVisible({ timeout: 60_000 });
    expect(page.url()).toContain(`/r/${REAL}/p/10`);

    const { events, dom } = await page.evaluate(() => {
      const w = window as unknown as { __events: unknown[]; __dom: unknown[] };
      return { events: w.__events, dom: w.__dom };
    });
    type Ev = { type: string; at: number; updated: string | null; steps: Record<string, string> };
    type Dom = { step: string; state: string; at: number };
    const render: number[] = [];
    const total: number[] = [];
    for (const change of dom as Dom[]) {
      const cause = (events as Ev[]).filter((e) => e.steps[change.step] === change.state && e.at <= change.at + 1).at(0);
      if (!cause) continue;
      render.push(change.at - cause.at);
      if (cause.updated) total.push(change.at - Date.parse(cause.updated));
    }
    test.info().annotations.push({
      type: "step-change latency (ms)",
      description: `event to screen ${JSON.stringify(render)}; worker write to screen ${JSON.stringify(total)}`,
    });
    console.log("step-change latency", { render, total, events: events.length });
    expect(render.length).toBeGreaterThan(0);
    expect(Math.max(...render)).toBeLessThan(1000);
  });

  test("reloading mid-load picks the job back up", async ({ page }) => {
    test.slow(); // a report's first load under parallel load outlasts the default 30 s
    await skipIfLoaded(page, 20);
    await page.goto(`/r/${REAL}/p/20`);
    await expect(page.locator('[data-slot="pull-not-loaded"]')).toBeVisible();
    await page.reload();
    await expect(page.locator('[data-slot="pull-not-loaded"], [data-slot="pull-body"]').first()).toBeVisible();
    await expect(page.locator('[data-slot="pull-body"]')).toBeVisible({ timeout: 60_000 });
  });

  test("a second visitor follows the same job", async ({ browser, page }) => {
    test.slow(); // a report's first load under parallel load outlasts the default 30 s
    await skipIfLoaded(page, 23);
    const bob = await browser.newContext({ storageState: "e2e/.auth/bob.json", viewport: { width: 1440, height: 900 } });
    try {
      const other = await bob.newPage();
      await page.goto(`/r/${REAL}/p/23`);
      await other.goto(`${WEB_URL}/r/${REAL}/p/23`);
      await expect(page.locator('[data-slot="pull-body"]')).toBeVisible({ timeout: 60_000 });
      await expect(other.locator('[data-slot="pull-body"]')).toBeVisible({ timeout: 60_000 });
    } finally {
      await bob.close();
    }
  });
});

// ----------------------------------------------------------------------------------------------------------------
// State screens, with the job routes mocked
// ----------------------------------------------------------------------------------------------------------------

const CORS = {
  "access-control-allow-origin": WEB_URL,
  "access-control-allow-credentials": "true",
  "access-control-allow-headers": "content-type,x-khadbot-csrf,last-event-id",
  "access-control-allow-methods": "GET,POST,DELETE,OPTIONS",
};

type Steps = Array<["waiting" | "running" | "done" | "failed", number]>;

function jobBody(state: string, steps: Steps, extra: Record<string, unknown> = {}, pullExtra: Record<string, unknown> = {}) {
  const names = ["download", "prepare", "analyse"];
  return {
    id: "mock-job",
    report_code: "SYNTH01",
    state,
    stage: null,
    pct: 10,
    cancel_requested: false,
    can_cancel: true,
    created_at: "2026-11-01T10:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: new Date().toISOString(),
    pulls: [
      {
        pull_key: "SYNTH01:999",
        fight: 999,
        stage: "fetching",
        pct: 10,
        status: "fetching",
        encounter_id: 1,
        encounter_name: "Synthetic Boss",
        difficulty: 5,
        difficulty_name: "mythic",
        steps: steps.map(([s, pct], i) => ({ name: names[i], state: s, pct })),
        ...pullExtra,
      },
    ],
    ...extra,
  };
}

async function mockJob(page: Page, job: Record<string, unknown>, options: { postStatus?: number; postBody?: unknown } = {}) {
  await page.route(/\/v1\/reports$/, async (route) => {
    const req = route.request();
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers: CORS });
    if (req.method() !== "POST") return route.continue();
    return route.fulfill({
      status: options.postStatus ?? 202,
      headers: CORS,
      contentType: "application/json",
      body: JSON.stringify(options.postBody ?? { ready: false, job }),
    });
  });
  await page.route(/\/v1\/jobs\/mock-job$/, (route) =>
    route.fulfill({ status: 200, headers: CORS, contentType: "application/json", body: JSON.stringify(job) }),
  );
  await page.route(/\/v1\/jobs\/mock-job\/events$/, (route) =>
    route.fulfill({
      status: 200,
      headers: CORS,
      contentType: "text/event-stream",
      body: `retry: 600000\nid: 1\nevent: job.state\ndata: ${JSON.stringify(job)}\n\n`,
    }),
  );
}

test.describe("states", () => {
  test("loading: tile, heading, status line and the steps in order", async ({ page }) => {
    await mockJob(page, jobBody("running", [["done", 100], ["running", 60], ["waiting", 0]]));
    await page.goto(MOCKED);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Synthetic Boss · Mythic");
    await expect(page.getByRole("status")).toContainText("It opens by itself when it’s ready");
    await expect(stepState(page, "download").getByTestId("step-state")).toHaveText("Done");
    await expect(stepState(page, "prepare").getByTestId("step-state")).toHaveText("Running");
    await expect(stepState(page, "analyse").getByTestId("step-state")).toHaveText("Waiting");
    await expect(page.getByRole("link", { name: "Back to the search" })).toHaveAttribute("href", "/");
    await expect(page.getByText("it keeps loading")).toBeVisible();
    await expectAccessible(page);
  });

  test("the tile fills with the boss thumbnail, and falls back to the plain square when it can't load", async ({ page }) => {
    // The listing names a thumbnail on this machine (the fixture blocks Blizzard's host): `ok.png` serves a generated
    // PNG, `missing.png` a 404.
    let thumbnail_url = `${WEB_URL}/e2e-art/ok.png`;
    await page.route(/\/v1\/art\/encounters$/, (route) =>
      route.fulfill({
        status: 200,
        headers: CORS,
        contentType: "application/json",
        body: JSON.stringify({
          encounters: [{ encounter_id: 3492, name: "Ula'tek", portrait_url: null, thumbnail_url }],
          attribution: "Boss art is the property of Blizzard Entertainment, Inc.",
        }),
      }),
    );
    await page.route(/\/e2e-art\/ok\.png$/, (route) => route.fulfill({ status: 200, contentType: "image/png", body: TEST_PNG }));
    await page.route(/\/e2e-art\/missing\.png$/, (route) => route.fulfill({ status: 404, body: "" }));
    await mockJob(
      page,
      jobBody("running", [["running", 20], ["waiting", 0], ["waiting", 0]], {}, {
        encounter_id: 3492,
        encounter_name: "Ula'tek",
        difficulty: 4,
        difficulty_name: "heroic",
      }),
    );
    await page.goto(MOCKED);
    const tile = page.getByTestId("pull-tile");
    const thumbnail = tile.getByTestId("boss-thumbnail");
    await expect(thumbnail).toHaveAttribute("data-art", "image");
    await expect
      .poll(() => thumbnail.locator("img").evaluate((el) => (el as HTMLImageElement).naturalWidth))
      .toBeGreaterThan(0);
    expect(await tile.boundingBox()).toMatchObject({ width: 72, height: 72 });
    await expect(page.getByTestId("art-attribution")).toContainText("Blizzard");
    await expectAccessible(page, { ready: () => expect(thumbnail).toHaveAttribute("data-art", "image") });

    thumbnail_url = `${WEB_URL}/e2e-art/missing.png`;
    await page.reload();
    await expect(page.getByTestId("art-attribution")).toBeVisible();
    await expect(thumbnail).toHaveAttribute("data-art", "fallback");
    await expect(thumbnail.locator("img")).toHaveCount(0);
  });

  test("waiting for points counts down with the steps waiting", async ({ page }) => {
    await mockJob(
      page,
      jobBody("waiting_budget", [["waiting", 0], ["waiting", 0], ["waiting", 0]], {
        error: { code: "budget_wait", message: "Waiting for points", eta_s: 90 },
      }),
    );
    await page.goto(MOCKED);
    const status = page.getByRole("status");
    await expect(status).toContainText("Waiting for Warcraft Logs points. This pull starts in 1:");
    const first = await status.textContent();
    await expect.poll(async () => status.textContent(), { timeout: 5000 }).not.toBe(first);
    await expect(page.getByText("Waiting", { exact: true })).toHaveCount(3);
    await expectAccessible(page);
  });

  test("WarcraftLogs is busy shows the retry countdown on a disabled button", async ({ page }) => {
    await mockJob(
      page,
      jobBody("queued", [["running", 20], ["waiting", 0], ["waiting", 0]], {
        error: { code: "rate_limited", message: "Rate limited", retry_after_s: 42 },
      }),
    );
    await page.goto(MOCKED);
    await expect(page.getByRole("heading", { name: "WarcraftLogs is busy" })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Retry in 0:/ })).toBeDisabled();
    await expectAccessible(page);
  });

  test("a failed step shows its message and Start again", async ({ page }) => {
    await mockJob(
      page,
      jobBody(
        "failed",
        [["done", 100], ["failed", 30], ["waiting", 0]],
        { error: { code: "pulls_failed", message: "1 pull failed" } },
        { status: "failed", message: "The events could not be put in order" },
      ),
    );
    await page.goto(MOCKED);
    await expect(page.locator("[data-error-code]")).toContainText("The events could not be put in order");
    await expect(stepState(page, "prepare").getByTestId("step-state")).toHaveText("Failed");
    await expect(page.getByRole("button", { name: "Start again" })).toBeEnabled();
    await expectAccessible(page);
  });

  test("a job the server can't find says it stopped loading", async ({ page }) => {
    const job = jobBody("running", [["running", 5], ["waiting", 0], ["waiting", 0]]);
    await mockJob(page, job);
    await page.unroute(/\/v1\/jobs\/mock-job$/);
    await page.route(/\/v1\/jobs\/mock-job$/, (route) =>
      route.fulfill({
        status: 404,
        headers: CORS,
        contentType: "application/json",
        body: JSON.stringify({ code: "job_not_found", message: "No such job." }),
      }),
    );
    await page.unroute(/\/v1\/jobs\/mock-job\/events$/);
    await page.route(/\/v1\/jobs\/mock-job\/events$/, (route) =>
      route.fulfill({ status: 404, headers: CORS, contentType: "application/json", body: '{"code":"job_not_found","message":"No such job."}' }),
    );
    await page.goto(MOCKED);
    await expect(page.getByRole("heading", { name: "This stopped loading" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Start again" })).toBeVisible();
    await expectAccessible(page);
  });

  test("an API that doesn't answer says KhadBot isn't responding", async ({ page }) => {
    await page.route(/\/v1\/reports$/, (route) =>
      route.request().method() === "POST" ? route.abort("failed") : route.continue(),
    );
    await page.goto(MOCKED);
    await expect(page.getByRole("heading", { name: "KhadBot isn’t responding" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry" })).toBeEnabled();
    await expectAccessible(page);
  });
});
