// apps/web/src/components/review/review-button.tsx
//
// The "Review this pull" control in the pull header's `review-button` slot (shown only when `features.reviews` is
// on): the button with its token estimate and the reviews left, progress while the review runs, and what went wrong
// when it did not. Findings themselves show in the pull page's Findings section, which refreshes when the job ends.
// Every state has words; nothing is colour alone.

"use client";

import { StatusBadge } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import { useFeature } from "@/lib/capabilities";

import { estimateLine, quotaLine, type ReviewView } from "./logic";
import { useReview } from "./use-review";

/** The control for one view state. Separate from the hook so each state renders in a test. */
export function ReviewControl({ view, onStart }: { view: ReviewView; onStart: () => void }) {
  switch (view.kind) {
    case "loading":
      return (
        <p role="status" className="m-0 text-xs text-mut" data-review="loading">
          Checking reviews…
        </p>
      );
    case "unavailable":
      return null;
    case "signed-out":
      return (
        <p className="m-0 text-xs text-mut" data-review="signed-out">
          Sign in to review this pull.
        </p>
      );
    case "cached":
      return (
        <p className="m-0 flex items-center gap-2 text-xs text-mut" data-review="cached">
          <StatusBadge status="ready">Reviewed</StatusBadge>
          Findings are up to date below. Nothing to run.
        </p>
      );
    case "running":
      return (
        <div className="flex flex-col items-end gap-1" data-review="running">
          <p role="status" className="m-0 text-sm font-medium">
            {view.waiting ? "Review queued…" : `Reviewing… ${Math.round(view.pct)}%`}
          </p>
          <div
            role="progressbar"
            aria-label="Review progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(view.pct)}
            className="h-1.5 w-40 overflow-hidden rounded-sm bg-s3"
          >
            <div className="h-full bg-info transition-[width]" style={{ width: `${view.pct}%` }} />
          </div>
        </div>
      );
    case "failed":
      return (
        <div className="flex flex-col items-end gap-1.5" data-review="failed">
          <p role="status" className="m-0 flex items-center gap-2 text-xs">
            <StatusBadge status="failed" />
            {view.message}
          </p>
          <Button variant="outline" size="sm" onClick={onStart}>
            Try again
          </Button>
        </div>
      );
    case "quota":
      return (
        <p role="status" className="m-0 max-w-[320px] text-right text-xs text-mut" data-review="quota">
          {quotaLine(view.limit, view.used, view.nextSlotAt)}
        </p>
      );
    case "ready":
      return (
        <div className="flex flex-col items-end gap-1" data-review="ready">
          <Button size="md" onClick={onStart}>
            Review this pull
          </Button>
          <p className="m-0 text-xs text-mut">{estimateLine(view.tokens, view.remaining, view.limit)}</p>
        </div>
      );
  }
}

function ReviewButtonInner({ code, fight }: { code: string; fight: number }) {
  const { view, start } = useReview(code, fight);
  return <ReviewControl view={view} onStart={start} />;
}

/** The header slot: nothing unless the server offers reviews. */
export function ReviewButton({ code, fight }: { code: string; fight: number }) {
  const offered = useFeature("reviews");
  if (!offered) return null;
  return (
    <div data-slot="review-button" className="ml-auto">
      <ReviewButtonInner code={code} fight={fight} />
    </div>
  );
}
