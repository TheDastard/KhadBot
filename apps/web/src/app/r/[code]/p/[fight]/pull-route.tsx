// apps/web/src/app/r/[code]/p/[fight]/pull-route.tsx
//
// The pull route shell: reads the pull's digest and picks what the page shows.
//
//   digest loading         a skeleton the size of the pull page
//   digest written (200)   the pull page: pull-body with pull-timeline inside it
//   not written (404)      pull-not-loaded, when the server can open pulls (`features.ingest_jobs`); otherwise
//                          "Not available on this server yet" (main.py serve serves only what its data directory holds)
//   any other error        an error state with the API's code
//
// The breadcrumb is "Raids / <code> / fight N" (the code is plain text: the night overview isn't built this round),
// and "Raids / Opening a pull" while a pull loads.

"use client";

import { isApiError } from "@khadbot/api-client";
import type { ReactNode } from "react";

import { PullNotLoaded } from "@/components/open-pull/pull-not-loaded";
import { PullPage } from "@/components/pull/pull-page";
import { ErrorState } from "@/components/shared/error-state";
import { NotAvailable } from "@/components/shared/not-available";
import { SkeletonBlock, SkeletonCard } from "@/components/shared/skeletons";
import { Breadcrumb, PageColumn, TopBar } from "@/components/shared/top-bar";
import { useFeature } from "@/lib/capabilities";
import { useFocus } from "@/lib/focus";
import { usePullDigest } from "@/lib/queries";

import { PullTimelineSlot } from "./slots";

const NOT_LOADED = new Set(["pull_not_found", "report_not_found"]);

/** The fight ID of the route's `[fight]` segment, or null unless it is a whole non-negative number. */
export function parseFightParam(text: string): number | null {
  if (!/^\d{1,9}$/.test(text)) return null;
  return Number(text);
}

export function PullRoute({ code, fightParam }: { code: string; fightParam: string }) {
  const fight = parseFightParam(fightParam);
  if (fight === null) {
    return (
      <>
        <TopBar breadcrumb={<Breadcrumb items={[{ label: "Raids", href: "/" }, { label: "Not a pull" }]} />} />
        <PageColumn>
          <ErrorState
            tone="info"
            where="Pull"
            title="This isn't a pull's address"
            body="A pull's address is the report code and the fight number, such as /r/H7z1fqDXjRTFnbtg/p/15."
            action={{ label: "Back to Raids", href: "/" }}
          />
        </PageColumn>
      </>
    );
  }
  return <PullRouteFor code={code} fight={fight} />;
}

function PullRouteFor({ code, fight }: { code: string; fight: number }) {
  const { full } = useFocus();
  const canOpen = useFeature("ingest_jobs");
  const digest = usePullDigest(code, fight, { full });

  const pullCrumbs = (
    <Breadcrumb items={[{ label: "Raids", href: "/" }, { label: code, mono: true }, { label: `fight ${fight}` }]} />
  );

  if (digest.isPending) return <PullRouteSkeleton breadcrumb={pullCrumbs} />;

  if (digest.isError) {
    const error = digest.error;
    if (isApiError(error) && error.status === 404 && NOT_LOADED.has(error.code)) {
      if (canOpen) {
        return (
          <>
            <TopBar breadcrumb={<Breadcrumb items={[{ label: "Raids", href: "/" }, { label: "Opening a pull" }]} />} />
            {/* Slot: pull-not-loaded (components/open-pull/). Keyed so another pull starts afresh. */}
            <PullNotLoaded key={`${code}:${fight}`} code={code} fight={fight} />
          </>
        );
      }
      return (
        <>
          <TopBar breadcrumb={pullCrumbs} />
          <PageColumn>
            <NotAvailable
              area="Opening pulls"
              detail={`Fight ${fight} of ${code} isn't loaded on this server, and this server can't load pulls.`}
            />
          </PageColumn>
        </>
      );
    }
    const code_ = isApiError(error) ? error.code : "unexpected_error";
    const unreachable = code_ === "api_unreachable";
    return (
      <>
        <TopBar breadcrumb={pullCrumbs} />
        <PageColumn>
          <ErrorState
            tone="bad"
            where="Pull"
            code={code_}
            title={
              unreachable
                ? "KhadBot isn't responding"
                : code_ === "wcl_unavailable"
                  ? "KhadBot can't connect to WarcraftLogs"
                  : "This pull couldn't be shown"
            }
            body={unreachable ? "Check your connection, then try again." : (error as Error).message}
            action={{ label: unreachable ? "Retry" : "Try again", onClick: () => void digest.refetch() }}
          />
        </PageColumn>
      </>
    );
  }

  return (
    <>
      <TopBar breadcrumb={pullCrumbs} />
      {/* Slot: pull-body, filled by the pull page (components/pull/), which places `timeline`. */}
      <PullPage
        code={code}
        fight={fight}
        digest={digest.data}
        timeline={
          // Slot: pull-timeline. Replace with the timeline (components/timeline/).
          <PullTimelineSlot code={code} fight={fight} digest={digest.data} />
        }
      />
    </>
  );
}

/** The pull page's shape while its digest loads: rail, header, timeline, sections. */
export function PullRouteSkeleton({ breadcrumb }: { breadcrumb?: ReactNode }) {
  return (
    <>
      <TopBar breadcrumb={breadcrumb} />
      <div aria-busy="true" aria-label="Loading the pull" className="grid grid-cols-[240px_minmax(0,1fr)]">
        <div className="flex min-h-[calc(100vh-57px)] flex-col gap-2 border-r border-line bg-s1 px-3 py-5">
          {Array.from({ length: 6 }, (_, i) => (
            <SkeletonBlock key={i} height={44} />
          ))}
        </div>
        <div className="flex min-w-0 flex-col gap-4 px-7 pt-6">
          <SkeletonCard height={140} />
          <SkeletonBlock height={420} />
          <SkeletonCard height={300} />
        </div>
      </div>
    </>
  );
}
