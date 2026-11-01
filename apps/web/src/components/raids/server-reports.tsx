// apps/web/src/components/raids/server-reports.tsx
//
// The Raids page on a server without report lookup (main.py serve): no search, just the reports on the server from
// GET /v1/reports, each as the same night card.

"use client";

import { ErrorState } from "@/components/shared/error-state";
import { SkeletonCard } from "@/components/shared/skeletons";

import { isApiErrorLike } from "./errors";
import { useServerNights } from "./lookup";
import { NightCard } from "./night-card";
import { summaryLine } from "./night";

export function ServerReports() {
  const query = useServerNights();
  if (query.isPending) return <SkeletonCard height={200} />;
  if (query.isError) {
    return (
      <ErrorState
        tone="bad"
        where="Any page"
        code={isApiErrorLike(query.error).code}
        title="KhadBot isn't responding"
        body="Check your connection, then try again."
        action={{ label: "Retry", onClick: () => void query.refetch() }}
      />
    );
  }
  const { nights, total, truncated } = query.data;
  if (nights.length === 0) {
    return (
      <section role="status" className="rounded-sm border border-dashed border-line bg-s1 px-6 py-8 text-center">
        <h2 className="m-0 font-display text-xl">No reports on this server yet</h2>
        <p className="m-0 mt-2 text-sm text-mut">Reports appear here once they have been loaded.</p>
      </section>
    );
  }
  return (
    <section aria-labelledby="raids-server-h" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id="raids-server-h" className="m-0 font-display text-lg leading-[1.3]">
          {summaryLine(nights)}
        </h2>
        <span className="text-[13px] text-mut">
          {truncated ? `Showing ${nights.length} of ${total} reports on this server` : "Reports on this server"}
        </span>
      </div>
      {nights.map((night) => (
        <NightCard key={night.code} night={night} />
      ))}
    </section>
  );
}
