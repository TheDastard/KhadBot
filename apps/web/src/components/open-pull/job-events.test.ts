import { describe, expect, it } from "vitest";

import { errorCard } from "./error-cards";
import {
  INITIAL_STREAM,
  isOpened,
  jobStreamReducer,
  phaseOf,
  remainingSeconds,
  statusLine,
  stepsOf,
  type Job,
  type JobPull,
  type JobStreamAction,
  type JobStreamState,
} from "./job-events";
import { formatCountdown } from "@/lib/format";

function pull(fight: number, steps: [string, number][], over: Partial<JobPull> = {}): JobPull {
  const names = ["download", "prepare", "analyse"] as const;
  return {
    pull_key: `ABC:${fight}`,
    fight,
    stage: "fetching",
    pct: 0,
    status: "fetching",
    steps: names.map((name, i) => ({
      name,
      state: (steps[i]?.[0] ?? "waiting") as "waiting" | "running" | "done" | "failed" | "skipped",
      pct: steps[i]?.[1] ?? 0,
    })),
    ...over,
  } as JobPull;
}

function job(state: Job["state"], pulls: JobPull[], over: Partial<Job> = {}): Job {
  return {
    id: "j1",
    report_code: "ABC",
    state,
    stage: null,
    pct: 0,
    pulls,
    cancel_requested: false,
    created_at: "2026-11-01T10:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-11-01T10:00:00Z",
    ...over,
  } as Job;
}

function fold(actions: JobStreamAction[]): JobStreamState {
  return actions.reduce(jobStreamReducer, INITIAL_STREAM);
}

describe("job stream reducer over a recorded event sequence", () => {
  const first = job("queued", [pull(12, [])]);
  const events: JobStreamAction[] = [
    { type: "state", job: first, at: 1000 },
    { type: "progress", pull: pull(12, [["running", 40]]) },
    { type: "progress", pull: pull(12, [["done", 100], ["running", 60]]) },
    { type: "progress", pull: pull(12, [["done", 100], ["done", 100], ["running", 30]], { status: "processing" }) },
  ];

  it("follows the pull step by step", () => {
    const stages = events.map((_, i) => stepsOf(fold(events.slice(0, i + 1)).job?.pulls[0] ?? null).map((s) => s.state));
    expect(stages).toEqual([
      ["waiting", "waiting", "waiting"],
      ["running", "waiting", "waiting"],
      ["done", "running", "waiting"],
      ["done", "done", "running"],
    ]);
  });

  it("opens when the last step is done", () => {
    const done = jobStreamReducer(fold(events), {
      type: "progress",
      pull: pull(12, [["done", 100], ["done", 100], ["done", 100]], { status: "done" }),
    });
    expect(isOpened(done.job, 12)).toBe(true);
    expect(phaseOf(done, 12).kind).toBe("done");
    expect(isOpened(fold(events).job, 12)).toBe(false);
  });

  it("ignores progress before the job arrives, and adds a pull it did not know", () => {
    expect(jobStreamReducer(INITIAL_STREAM, { type: "progress", pull: pull(12, []) })).toBe(INITIAL_STREAM);
    const added = jobStreamReducer(fold(events.slice(0, 1)), { type: "progress", pull: pull(13, []) });
    expect(added.job?.pulls.map((p) => p.fight)).toEqual([12, 13]);
  });

  it("follows a job that covers more pulls", () => {
    const state = fold([{ type: "state", job: job("running", [pull(10, [["done", 100], ["done", 100], ["done", 100]]), pull(12, [["running", 5]])]), at: 0 }]);
    expect(phaseOf(state, 12).kind).toBe("loading");
    expect(phaseOf(state, 10).kind).toBe("done");
  });
});

describe("phases", () => {
  it("starts before a job is known", () => {
    expect(phaseOf(INITIAL_STREAM, 12)).toEqual({ kind: "starting" });
  });

  it("waits for points with the ETA, and for a rate limit with the retry time", () => {
    const waiting = fold([
      { type: "state", at: 0, job: job("waiting_budget", [pull(12, [])], { error: { code: "budget_wait", message: "w", eta_s: 90 } }) },
    ]);
    expect(phaseOf(waiting, 12)).toEqual({ kind: "waiting", etaS: 90 });
    const busy = fold([
      { type: "state", at: 0, job: job("queued", [pull(12, [])], { error: { code: "rate_limited", message: "r", retry_after_s: 42 } }) },
    ]);
    expect(phaseOf(busy, 12)).toEqual({ kind: "busy", retryAfterS: 42 });
  });

  it("names the failed step and uses the pull's message", () => {
    const failed = fold([
      {
        type: "state",
        at: 0,
        job: job("failed", [pull(12, [["done", 100], ["failed", 10]], { status: "failed", message: "Events were not in order" })], {
          error: { code: "pulls_failed", message: "1 pull failed" },
        }),
      },
    ]);
    expect(phaseOf(failed, 12)).toEqual({
      kind: "failed",
      code: "pulls_failed",
      message: "Events were not in order",
      step: "prepare",
    });
  });

  it("stops for a cancelled job and for a job the server does not have", () => {
    expect(phaseOf(fold([{ type: "state", at: 0, job: job("cancelled", [pull(12, [])]) }]), 12).kind).toBe("stopped");
    expect(phaseOf(fold([{ type: "gone" }]), 12).kind).toBe("stopped");
  });

  it("fails on the stream's error event before any state", () => {
    const state = fold([{ type: "failure", failure: { code: "interrupted", message: "stopped" } }]);
    expect(phaseOf(state, 12)).toMatchObject({ kind: "failed", code: "interrupted" });
  });
});

describe("waiting time", () => {
  it("counts down from when it was announced and never goes below zero", () => {
    expect(remainingSeconds(90, 1000, 31_000)).toBe(60);
    expect(remainingSeconds(10, 0, 60_000)).toBe(0);
    expect(remainingSeconds(null, 0, 1)).toBeNull();
  });

  it("formats as m:ss, rounding up", () => {
    expect(formatCountdown(42)).toBe("0:42");
    expect(formatCountdown(59.2)).toBe("1:00");
    expect(formatCountdown(125)).toBe("2:05");
  });

  it("words the status line with the countdown", () => {
    expect(statusLine({ kind: "waiting", etaS: 42 }, "0:42")).toBe(
      "Waiting for Warcraft Logs points. This pull starts in 0:42.",
    );
    expect(statusLine({ kind: "loading" }, null)).toContain("It opens by itself");
  });
});

describe("error cards", () => {
  it("maps the API's codes to the canvas's cards", () => {
    expect(errorCard({ code: "rate_limited", message: "" }).title).toBe("WarcraftLogs is busy");
    expect(errorCard({ code: "wcl_unavailable", message: "" }).title).toBe("KhadBot can’t connect to WarcraftLogs");
    expect(errorCard({ code: "api_unreachable", message: "" }).title).toBe("KhadBot isn’t responding");
    expect(errorCard({ code: "job_not_found", message: "" }).title).toBe("This stopped loading");
    expect(errorCard({ code: "report_not_found", message: "" }).title).toBe(
      "Report not found or not shared with your Warcraft Logs account",
    );
    expect(errorCard({ code: "pulls_failed", message: "Because." }).body).toBe("Because.");
  });
});
