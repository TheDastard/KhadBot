import { ApiError } from "@khadbot/api-client";
import { describe, expect, it } from "vitest";

import {
  estimateLine,
  quotaLine,
  reviewView,
  type ReviewInputs,
  type ReviewJob,
  type ReviewStatusData,
} from "./logic";

function status(over: Partial<ReviewStatusData> = {}): ReviewStatusData {
  return {
    depth: "standard",
    model: "m",
    cached: false,
    estimate: { digest_tokens: 9000, output_tokens: 1500, total_tokens: 10500 },
    quota: { limit: 10, used: 3, remaining: 7, window_hours: 24 },
    job: null,
    ...over,
  };
}

function job(over: Partial<ReviewJob> = {}): ReviewJob {
  return {
    id: "j1",
    kind: "review",
    report_code: "ABC",
    state: "running",
    stage: "review",
    pct: 40,
    pulls: [],
    error: null,
    points_spent: null,
    cancel_requested: false,
    can_cancel: false,
    created_at: "2026-11-19T10:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-11-19T10:00:00Z",
    ...over,
  };
}

function view(over: Partial<ReviewInputs>) {
  return reviewView({ status: undefined, statusError: false, job: undefined, startError: null, starting: false, ...over });
}

describe("reviewView", () => {
  it("is loading until the status is known, and unavailable if it failed", () => {
    expect(view({}).kind).toBe("loading");
    expect(view({ statusError: true }).kind).toBe("unavailable");
  });

  it("offers the review with the estimate and the reviews left", () => {
    expect(view({ status: status() })).toEqual({ kind: "ready", tokens: 10500, remaining: 7, limit: 10 });
  });

  it("shows a visitor a sign-in line, not a button", () => {
    expect(view({ status: status({ quota: null }) }).kind).toBe("signed-out");
  });

  it("says the findings are up to date when they are cached, even with the quota spent", () => {
    const spent = { limit: 10, used: 10, remaining: 0, window_hours: 24 };
    expect(view({ status: status({ cached: true, quota: spent }) }).kind).toBe("cached");
  });

  it("shows the limit and when a slot frees once the quota is spent", () => {
    const spent = { limit: 10, used: 10, remaining: 0, window_hours: 24, next_slot_at: "2026-11-20T10:00:00Z" };
    expect(view({ status: status({ quota: spent }) })).toEqual({
      kind: "quota",
      limit: 10,
      used: 10,
      nextSlotAt: "2026-11-20T10:00:00Z",
    });
  });

  it("follows a queued or running job, whoever started it", () => {
    expect(view({ status: status(), job: job({ state: "queued", pct: 0 }) })).toEqual({
      kind: "running",
      pct: 0,
      waiting: true,
    });
    expect(view({ status: status(), job: job({ pct: 62.5 }) })).toEqual({ kind: "running", pct: 62.5, waiting: false });
  });

  it("shows a start request in flight as queued", () => {
    expect(view({ status: status(), starting: true }).kind).toBe("running");
  });

  it("shows a failed job with its message", () => {
    const failed = job({ state: "failed", error: { code: "model_mismatch", message: "wrong model" } });
    expect(view({ status: status(), job: failed })).toEqual({ kind: "failed", message: "wrong model", code: "model_mismatch" });
  });

  it("shows quota_exceeded from a start request with the quota in its body", () => {
    const quota = { limit: 1, used: 1, remaining: 0, window_hours: 24, next_slot_at: null };
    const error = new ApiError({ status: 429, code: "quota_exceeded", message: "used", body: { quota } });
    expect(view({ status: status(), startError: error })).toEqual({ kind: "quota", limit: 1, used: 1, nextSlotAt: null });
  });

  it("words other start errors", () => {
    const signedOut = new ApiError({ status: 401, code: "not_signed_in", message: "x" });
    expect(view({ status: status(), startError: signedOut })).toMatchObject({ kind: "failed", message: "Sign in to review this pull." });
  });
});

describe("lines", () => {
  it("words the estimate and the quota", () => {
    expect(estimateLine(10500, 7, 10)).toBe("About 10.5k tokens · 7 of 10 reviews left");
    expect(quotaLine(10, 10, null)).toBe("Review limit reached: 10 of 10 used in the last 24 hours.");
    expect(quotaLine(0, 0, null)).toBe("Reviews are turned off on this server.");
    expect(quotaLine(10, 10, "2026-11-20T10:00:00Z", "en-US")).toContain("The next frees up at");
  });
});
