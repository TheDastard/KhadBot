// apps/web/src/components/raids/lookup-error.tsx
//
// The lookup's failures as the canvas's "Errors" artboard words them (search and loading cards): report not found or
// private, WarcraftLogs busy (with a retry countdown), KhadBot can't connect to WarcraftLogs, KhadBot isn't responding.

"use client";

import type { ApiError } from "@khadbot/api-client";

import { PrivateLogCard } from "@/components/account";
import { ErrorState } from "@/components/shared/error-state";
import { formatCountdown } from "@/lib/format";
import { useSignedIn } from "@/lib/session";

import { useCountdown } from "./lookup";

export interface LookupErrorProps {
  error: ApiError;
  /** When the failed attempt answered (ms), where the retry countdown starts. */
  failedAt: number;
  onRetry: () => void;
  onEditCode: () => void;
}

export const SIGNED_IN_NOT_FOUND = "Report not found or not shared with your Warcraft Logs account";

export function LookupError({ error, failedAt, onRetry, onEditCode }: LookupErrorProps) {
  const signedIn = useSignedIn();
  const wait = useCountdown(error.code === "budget_exhausted" ? (error.retryAfterS ?? 0) : null, failedAt);

  switch (error.code) {
    case "report_not_found":
      // Signed in: the log isn't theirs to read. Signed out (or not known yet): the private-log card.
      return signedIn ? (
        <ErrorState
          tone="bad"
          where="Search"
          code={error.code}
          error={error}
          title={SIGNED_IN_NOT_FOUND}
          body="Check the code, or ask the report's owner to share it with your account."
          action={{ label: "Edit the code", onClick: onEditCode }}
        />
      ) : (
        <PrivateLogSlot />
      );
    case "budget_exhausted":
      return (
        <ErrorState
          tone="warn"
          where="Loading"
          code={error.code}
          error={error}
          title="WarcraftLogs is busy"
          body="KhadBot can't load more from WarcraftLogs right now. Everything loaded so far is kept."
          action={{
            label: wait > 0 ? `Retry in ${formatCountdown(wait)}` : "Retry",
            onClick: onRetry,
            disabled: wait > 0,
          }}
        />
      );
    case "wcl_unavailable":
    case "wcl_credentials_missing":
      return (
        <ErrorState
          tone="bad"
          where="Loading"
          code={error.code}
          error={error}
          title="KhadBot can't connect to WarcraftLogs"
          body="Something is wrong on our side. Try again later."
          action={{ label: "Try again", onClick: onRetry }}
        />
      );
    case "bad_source":
      // Not on the canvas: the same card shape for text that is neither a report code nor a WarcraftLogs link.
      return (
        <ErrorState
          tone="bad"
          where="Search"
          code={error.code}
          error={error}
          title="That isn't a report code or link"
          body="Paste the code from a WarcraftLogs report address, or the whole address."
          action={{ label: "Edit the code", onClick: onEditCode }}
        />
      );
    default:
      return (
        <ErrorState
          tone="bad"
          where="Any page"
          code={error.code}
          error={error}
          title="KhadBot isn't responding"
          body="Check your connection, then try again."
          action={{ label: "Retry", onClick: onRetry }}
        />
      );
  }
}

/**
 * Slot: private-log-card. A signed-out visitor's 404 shows the sign-in card from components/account/: what signing in
 * allows and the "Sign in with Warcraft Logs" button, returning to this search afterwards.
 */
function PrivateLogSlot() {
  return (
    <div data-slot="private-log-card">
      <PrivateLogCard />
    </div>
  );
}
