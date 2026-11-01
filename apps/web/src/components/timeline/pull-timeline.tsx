// apps/web/src/components/timeline/pull-timeline.tsx
//
// The pull page's timeline section and, under it, the zoomed events. It reads the timeline (one request), loads the
// chart in the browser only (next/dynamic, ssr: false, behind a placeholder of the chart's height), and ties the
// chart to the URL through useFocus: the selected marker is `?ref=`, a brushed or keyed window is `?t=`.
//
// The panel (components/panel/) reads `?ref=` and may set `t`; this component only reads and writes the same two
// parameters, so the two never talk directly.

"use client";

import { isApiError } from "@khadbot/api-client";
import dynamic from "next/dynamic";
import { useMemo } from "react";

import { ZoomedEvents } from "@/components/events/zoomed-events";
import { ErrorState } from "@/components/shared/error-state";
import { SkeletonBlock } from "@/components/shared/skeletons";
import { useFocus } from "@/lib/focus";
import type { PullDigestData } from "@/lib/queries";

import { buildTimeline } from "./markers";
import { usePullTimeline } from "./queries";
import { fitWindow, zoomWindowAround } from "./scale";

// 358 px: the chart with eight mechanic rows, so the section doesn't jump when the chart arrives.
const CHART_HEIGHT = 358;

const TimelineChart = dynamic(() => import("./timeline-chart"), {
  ssr: false,
  loading: () => <SkeletonBlock height={CHART_HEIGHT} />,
});

export interface PullTimelineProps {
  code: string;
  fight: number;
  digest: PullDigestData;
}

export function PullTimeline({ code, fight }: PullTimelineProps) {
  const timeline = usePullTimeline(code, fight);
  const focus = useFocus();
  const data = timeline.data;
  const model = useMemo(() => (data ? buildTimeline(data) : null), [data]);
  const zoom = useMemo(() => (data && focus.t ? fitWindow(focus.t, data.duration_ms) : null), [data, focus.t]);
  // A selected death names the player the events table starts filtered to.
  const actor = useMemo(() => {
    if (!model || !focus.ref || !zoom) return null;
    const death = model.deaths.find((d) => d.ref === focus.ref);
    return death && death.tMs >= zoom.startMs && death.tMs <= zoom.endMs ? death.player : null;
  }, [model, focus.ref, zoom]);

  return (
    <>
      <section
        aria-labelledby="timeline-heading"
        data-slot="pull-timeline"
        className="flex min-w-0 flex-col gap-2 rounded-sm border border-line bg-s1 px-4 py-3.5"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1.5">
          <h2 id="timeline-heading" className="m-0 text-base leading-[1.3]">
            Timeline
          </h2>
          <span id="timeline-keys" className="text-xs text-mut">
            ← → step through markers · Enter selects · Esc clears · Z zooms to 10 s · drag to zoom (30 s max)
          </span>
        </div>
        {timeline.isError ? (
          <ErrorState
            tone="bad"
            where="Timeline"
            code={isApiError(timeline.error) ? timeline.error.code : "unexpected_error"}
            title="The timeline couldn't be shown"
            body={(timeline.error as Error).message}
            action={{ label: "Try again", onClick: () => void timeline.refetch() }}
          />
        ) : !data || !model ? (
          <div aria-busy="true" aria-label="Loading the timeline">
            <SkeletonBlock height={CHART_HEIGHT} />
          </div>
        ) : (
          <TimelineChart
            code={code}
            model={model}
            durationMs={data.duration_ms}
            cutoffMs={data.cutoff_ms}
            phases={data.phases}
            selectedRef={focus.ref}
            zoom={zoom}
            onSelect={focus.setRef}
            onZoom={focus.setWindow}
            onZoomAround={(at) => focus.setWindow(zoomWindowAround(at, data.duration_ms))}
          />
        )}
      </section>
      {zoom && <ZoomedEvents code={code} fight={fight} window={zoom} actor={actor} onClear={() => focus.setWindow(null)} />}
    </>
  );
}
