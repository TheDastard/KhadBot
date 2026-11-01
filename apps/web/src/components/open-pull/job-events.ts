// apps/web/src/components/open-pull/job-events.ts
//
// What the job's event stream says, folded into one state, and what a pull's loading screen shows for it. Pure
// functions: the stream is `GET /v1/jobs/{id}/events` (`job.state` first, then `pull.progress` and `job.state` as
// things change, then `job.done` or `error`; src/khadbot/api/jobs.py), and nothing here touches the network.

import type { ApiGet } from "@khadbot/api-client";

export type Job = ApiGet<"/v1/jobs/{job_id}">;
export type JobPull = Job["pulls"][number];
export type JobStep = JobPull["steps"][number];
export type JobFailure = NonNullable<Job["error"]>;
export type StepName = JobStep["name"];
export type StepState = JobStep["state"];

// ----------------------------------------------------------------------------------------------------------------
// The stream folded into one state
// ----------------------------------------------------------------------------------------------------------------

export interface JobStreamState {
  job: Job | null;
  /** When the job last arrived, in ms, so a wait's countdown counts from the moment it was announced. */
  receivedAt: number;
  /** The `error` event: the job failed and the stream closed. */
  failure: JobFailure | null;
  /** The server has no such job (`GET /v1/jobs/{id}` answered 404): it was removed or never existed. */
  gone: boolean;
}

export type JobStreamAction =
  | { type: "state"; job: Job; at: number }
  | { type: "progress"; pull: JobPull }
  | { type: "done"; job: Job; at: number }
  | { type: "failure"; failure: JobFailure }
  | { type: "gone" }
  | { type: "reset" };

export const INITIAL_STREAM: JobStreamState = { job: null, receivedAt: 0, failure: null, gone: false };

export function jobStreamReducer(state: JobStreamState, action: JobStreamAction): JobStreamState {
  switch (action.type) {
    case "state":
    case "done":
      return { job: action.job, receivedAt: action.at, failure: null, gone: false };
    case "progress": {
      if (!state.job) return state;
      const known = state.job.pulls.some((p) => p.pull_key === action.pull.pull_key);
      const pulls = known
        ? state.job.pulls.map((p) => (p.pull_key === action.pull.pull_key ? action.pull : p))
        : [...state.job.pulls, action.pull];
      return { ...state, job: { ...state.job, pulls } };
    }
    case "failure":
      return { ...state, failure: action.failure };
    case "gone":
      return { ...state, gone: true };
    case "reset":
      return INITIAL_STREAM;
  }
}

// ----------------------------------------------------------------------------------------------------------------
// The three steps
// ----------------------------------------------------------------------------------------------------------------

/** The steps a player sees, with the canvas's wording (no "ingest", no stage names). */
export const STEP_COPY: Record<StepName, { name: string; detail: string }> = {
  download: { name: "Download the fight", detail: "From WarcraftLogs" },
  prepare: { name: "Prepare the events", detail: "Puts every event of the fight in order" },
  analyse: { name: "Find what happened", detail: "Phases, mechanics, deaths and cooldowns" },
};

export const STEP_ORDER: readonly StepName[] = ["download", "prepare", "analyse"];

/** The three steps of a pull, in order; every one waiting until the job reports the pull. */
export function stepsOf(pull: JobPull | null): JobStep[] {
  return STEP_ORDER.map(
    (name) => pull?.steps.find((s) => s.name === name) ?? { name, state: "waiting" as const, pct: 0 },
  );
}

/** The pull of the job this page is about (a job may cover more pulls when someone else opened a whole night). */
export function pullOfJob(job: Job | null, fight: number): JobPull | null {
  return job?.pulls.find((p) => p.fight === fight) ?? null;
}

/** True when the pull's last step is done: its digest is written and the pull page can open. */
export function isOpened(job: Job | null, fight: number): boolean {
  if (!job) return false;
  const pull = pullOfJob(job, fight);
  if (pull) return stepsOf(pull).at(-1)?.state === "done" || pull.status === "done";
  return job.state === "done";
}

// ----------------------------------------------------------------------------------------------------------------
// What the screen says
// ----------------------------------------------------------------------------------------------------------------

export type Phase =
  | { kind: "starting" }
  | { kind: "loading" }
  | { kind: "waiting"; etaS: number | null }
  | { kind: "busy"; retryAfterS: number | null }
  | { kind: "failed"; code: string; message: string; step: StepName | null }
  | { kind: "stopped" }
  | { kind: "done" };

/** The phase a job is in, from its state, its typed error and the pull's own steps. */
export function phaseOf(stream: JobStreamState, fight: number): Phase {
  if (stream.gone) return { kind: "stopped" };
  const job = stream.job;
  if (!job) return stream.failure ? failed(stream.failure, null) : { kind: "starting" };
  const pull = pullOfJob(job, fight);
  if (isOpened(job, fight)) return { kind: "done" };
  if (job.state === "cancelled") return { kind: "stopped" };
  const error = job.error ?? stream.failure;
  if (job.state === "failed" || stream.failure || pull?.status === "failed") {
    const failure: JobFailure = error ?? { code: "pulls_failed", message: "This pull could not be loaded." };
    return failed(failure, stepsOf(pull).find((s) => s.state === "failed")?.name ?? null, pull?.message ?? null);
  }
  if (error?.code === "rate_limited") return { kind: "busy", retryAfterS: error.retry_after_s ?? null };
  if (job.state === "waiting_budget") return { kind: "waiting", etaS: error?.eta_s ?? null };
  return { kind: "loading" };
}

function failed(failure: JobFailure, step: StepName | null, pullMessage: string | null = null): Phase {
  const message = failure.code === "pulls_failed" && pullMessage ? pullMessage : failure.message;
  return { kind: "failed", code: failure.code, message, step };
}

/** Seconds left of a wait announced `receivedAt` ms ago, at `now` ms. Null without an estimate; never below zero. */
export function remainingSeconds(totalS: number | null, receivedAt: number, now: number): number | null {
  if (totalS === null) return null;
  return Math.max(0, totalS - (now - receivedAt) / 1000);
}

// ----------------------------------------------------------------------------------------------------------------
// The status line
// ----------------------------------------------------------------------------------------------------------------

export const LOADING_LINE = "Loading this pull from WarcraftLogs. It opens by itself when it’s ready.";

/**
 * The text of the status line (the `aria-live` region). `countdown` is the formatted time left of a wait (m:ss), or
 * null when the server gave no estimate.
 */
export function statusLine(phase: Phase, countdown: string | null): string {
  switch (phase.kind) {
    case "starting":
    case "loading":
      return LOADING_LINE;
    case "waiting":
      return countdown
        ? `Waiting for Warcraft Logs points. This pull starts in ${countdown}.`
        : "Waiting for Warcraft Logs points. This pull starts as soon as they are free.";
    case "busy":
      return "WarcraftLogs is busy. This pull starts again by itself.";
    case "done":
      return "Done. Opening the pull.";
    case "failed":
      return "This pull could not be loaded.";
    case "stopped":
      return "This stopped loading.";
  }
}
