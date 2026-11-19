// apps/web/src/components/review/logic.ts
//
// What the review button shows, as a pure function of what the API said: the review status (`GET .../review`), the
// job being followed (`GET /v1/jobs/{id}`) and the last failed request. No React, so every state is unit-tested.

import type { ApiError } from "@khadbot/api-client";
import type { ApiGet, Schema } from "@khadbot/api-client";

import { formatCompact } from "@/lib/format";

export type ReviewStatusData = ApiGet<"/v1/reports/{code}/pulls/{fight}/review">;
export type ReviewJob = Schema<"Job">;
export type ReviewQuota = Schema<"ReviewQuota">;

export type ReviewView =
  | { kind: "loading" }
  /** The status could not be read (not a missing pull: that is the pull page's own state). */
  | { kind: "unavailable" }
  /** A visitor: reviews need sign-in. */
  | { kind: "signed-out" }
  /** The findings for this data, prompts and model exist; nothing to run. */
  | { kind: "cached" }
  /** A review is queued or running, started by the viewer or by someone else (the findings are shared). */
  | { kind: "running"; pct: number; waiting: boolean }
  | { kind: "failed"; message: string; code: string | null }
  /** The viewer has used their reviews. `nextSlotAt` is an ISO time when one frees up. */
  | { kind: "quota"; limit: number; used: number; nextSlotAt: string | null }
  | { kind: "ready"; tokens: number; remaining: number; limit: number };

const ACTIVE = new Set(["queued", "running", "waiting_budget"]);

export function isActive(job: ReviewJob | undefined | null): boolean {
  return !!job && ACTIVE.has(job.state);
}

/** The quota a 429 `quota_exceeded` carries, if that is what the error is. */
export function quotaOf(error: ApiError | null): ReviewQuota | null {
  if (!error || error.code !== "quota_exceeded") return null;
  const quota = (error.body as { quota?: ReviewQuota } | undefined)?.quota;
  return quota ?? null;
}

export interface ReviewInputs {
  status: ReviewStatusData | undefined;
  statusError: boolean;
  /** The job being followed: the one a POST returned, or the one the status named. */
  job: ReviewJob | undefined;
  /** The last start request's failure, cleared when the viewer asks again. */
  startError: ApiError | null;
  /** A start request is in flight. */
  starting: boolean;
}

export function reviewView({ status, statusError, job, startError, starting }: ReviewInputs): ReviewView {
  if (job && isActive(job)) {
    return { kind: "running", pct: job.pct, waiting: job.state !== "running" };
  }
  if (starting) return { kind: "running", pct: 0, waiting: true };
  if (job?.state === "failed") {
    return { kind: "failed", message: job.error?.message ?? "The review failed.", code: job.error?.code ?? null };
  }
  const exceeded = quotaOf(startError);
  if (exceeded) {
    return { kind: "quota", limit: exceeded.limit, used: exceeded.used, nextSlotAt: exceeded.next_slot_at ?? null };
  }
  if (startError) return { kind: "failed", message: startErrorMessage(startError), code: startError.code };
  if (!status) return statusError ? { kind: "unavailable" } : { kind: "loading" };
  if (status.cached) return { kind: "cached" };
  const quota = status.quota;
  if (!quota) return { kind: "signed-out" };
  if (quota.remaining <= 0) {
    return { kind: "quota", limit: quota.limit, used: quota.used, nextSlotAt: quota.next_slot_at ?? null };
  }
  return { kind: "ready", tokens: status.estimate.total_tokens, remaining: quota.remaining, limit: quota.limit };
}

function startErrorMessage(error: ApiError): string {
  if (error.code === "not_signed_in") return "Sign in to review this pull.";
  if (error.code === "api_unreachable") return "KhadBot could not be reached. Try again.";
  return error.message || "The review could not be started.";
}

/** "About 12.4k tokens · 7 of 10 reviews left". */
export function estimateLine(tokens: number, remaining: number, limit: number): string {
  return `About ${formatCompact(tokens)} tokens · ${remaining} of ${limit} reviews left`;
}

/** "10 of 10 reviews used in the last 24 hours" plus when the next one frees up, when known. */
export function quotaLine(limit: number, used: number, nextSlotAt: string | null, locale?: string): string {
  const base = `Review limit reached: ${Math.min(used, limit)} of ${limit} used in the last 24 hours.`;
  if (!nextSlotAt) return limit === 0 ? "Reviews are turned off on this server." : base;
  const when = new Date(nextSlotAt);
  if (Number.isNaN(when.getTime())) return base;
  return `${base} The next frees up at ${when.toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" })}.`;
}
