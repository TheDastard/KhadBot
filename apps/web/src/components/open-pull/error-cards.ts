// apps/web/src/components/open-pull/error-cards.ts
//
// The error cards of the opening-a-pull screen ("Errors" artboard, the loading cards): which card an API error or a
// failed job gets, in the canvas's words. Pure; opening-pull.tsx draws them with the shared ErrorState.

import type { ErrorTone } from "@/components/shared/error-state";

export type CardAction = "retry" | "start-again" | "back" | "sign-in";

export interface ErrorCard {
  tone: ErrorTone;
  where: string;
  code: string;
  title: string;
  body: string;
  action: CardAction;
}

export interface ErrorInput {
  code: string;
  message: string;
  status?: number;
}

const BUSY: ErrorCard = {
  tone: "warn",
  where: "Loading",
  code: "rate_limited",
  title: "WarcraftLogs is busy",
  body: "KhadBot can’t load more from WarcraftLogs right now. Everything loaded so far is kept.",
  action: "retry",
};

/** The card for a failed request to open the pull, or for a failed job. */
export function errorCard(error: ErrorInput): ErrorCard {
  const { code } = error;
  switch (code) {
    case "api_unreachable":
      return {
        tone: "bad",
        where: "Any page",
        code: "API unreachable",
        title: "KhadBot isn’t responding",
        body: "Check your connection, then try again.",
        action: "retry",
      };
    case "rate_limited":
    case "budget_wait":
    case "budget_exhausted":
      return { ...BUSY, code };
    case "wcl_unavailable":
    case "wcl_unreachable":
    case "wcl_error":
    case "wcl_credentials_missing":
      return {
        tone: "bad",
        where: "Loading",
        code,
        title: "KhadBot can’t connect to WarcraftLogs",
        body: "Something is wrong on our side. Try again later.",
        action: "retry",
      };
    case "visitor_queue_full":
      return {
        tone: "info",
        where: "Loading",
        code,
        title: "Many pulls are loading right now",
        body: "Try again in a moment, or sign in with Warcraft Logs to load this pull now.",
        action: "sign-in",
      };
    case "not_signed_in":
      return {
        tone: "info",
        where: "Loading",
        code,
        title: "Sign in to open this pull",
        body: "This pull has not been loaded yet. Sign in with Warcraft Logs to load it.",
        action: "sign-in",
      };
    case "wcl_not_signed_in":
      return {
        tone: "warn",
        where: "Loading",
        code,
        title: "Sign in to Warcraft Logs again",
        body: "Your Warcraft Logs sign-in ran out. Sign in again, then start this pull again.",
        action: "sign-in",
      };
    case "report_not_found":
      return {
        tone: "bad",
        where: "Loading",
        code,
        title: "Report not found or not shared with your Warcraft Logs account",
        body: "Check the report code, or ask the report’s owner to share it.",
        action: "back",
      };
    case "job_not_found":
    case "stopped":
      return {
        tone: "warn",
        where: "Loading",
        code: "job not found",
        title: "This stopped loading",
        body: "Start again; anything already loaded is kept.",
        action: "start-again",
      };
    default:
      return {
        tone: "bad",
        where: "Loading",
        code,
        title: "This pull couldn’t be loaded",
        body: error.message,
        action: "start-again",
      };
  }
}

/** The card of a failed step: the step's own message, with Start again. */
export function failedStepCard(failure: { code: string; message: string }, stepName: string | null): ErrorCard {
  const base = errorCard(failure);
  if (base.title !== "This pull couldn’t be loaded") return base;
  return { ...base, title: stepName ? `“${stepName}” didn’t finish` : base.title, body: failure.message };
}
