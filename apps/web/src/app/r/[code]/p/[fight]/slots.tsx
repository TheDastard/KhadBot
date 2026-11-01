// apps/web/src/app/r/[code]/p/[fight]/slots.tsx
//
// The pull route's three slots, each a placeholder of the right size until its area fills it. An area replaces its
// placeholder's use in pull-route.tsx with its own component taking the same props (a few lines at the marked spot):
//
//   pull-not-loaded   components/open-pull/   <PullNotLoadedSlot code fight />
//       Shown when the digest isn't written (404) and the server can open pulls (`features.ingest_jobs`). It asks the
//       API to load the pull (POST /v1/reports, after render) and shows the loading steps. When the digest is written
//       it invalidates `queryKeys.pull(code, fight)`; the route shell's digest query then succeeds and swaps in the
//       pull page without a reload, keeping `?ref=`.
//   pull-body         components/pull/, components/panel/   <PullBodySlot code fight digest timeline />
//       The rail, header, main column and context panel. It places `timeline` (the next slot) in the main column,
//       under the header and above the sections, as the canvas draws it.
//   pull-timeline     components/timeline/, components/events/   <PullTimelineSlot code fight digest />
//       The timeline and the zoomed events table.

"use client";

import type { ReactNode } from "react";

import { SlotPlaceholder } from "@/components/shared/slot-placeholder";
import { PullTimeline } from "@/components/timeline/pull-timeline";
import type { PullDigestData } from "@/lib/queries";

export interface PullSlotProps {
  code: string;
  fight: number;
}

export interface PullDigestSlotProps extends PullSlotProps {
  digest: PullDigestData;
}

export function PullNotLoadedSlot(_props: PullSlotProps) {
  return (
    <main className="mx-auto flex w-full max-w-[1040px] flex-col gap-6 px-8 pt-8 pb-16">
      <SlotPlaceholder slot="pull-not-loaded" label="Opening a pull" minHeight={360} />
    </main>
  );
}

export function PullTimelineSlot(props: PullDigestSlotProps) {
  return <PullTimeline {...props} />;
}

/** The pull page's layout: a 240 px rail, then the main column; under the header, the sections and a 380 px panel. */
export function PullBodySlot({ timeline }: PullDigestSlotProps & { timeline: ReactNode }) {
  return (
    <div data-slot="pull-body" className="grid min-h-[calc(100vh-57px)] grid-cols-[240px_minmax(0,1fr)] items-stretch">
      <aside aria-label="Pulls" className="border-r border-line bg-s1 px-3 py-5">
        <SlotPlaceholder slot="pull-rail" label="Pulls" minHeight={320} />
      </aside>
      <main className="flex min-w-0 flex-col gap-4 px-7 pt-6 pb-16">
        <SlotPlaceholder slot="pull-header" label="Pull header" minHeight={140} />
        {timeline}
        <div className="grid grid-cols-[minmax(0,1fr)_380px] items-start gap-4">
          <SlotPlaceholder slot="pull-sections" label="Deaths, mechanics, windows, cooldowns" minHeight={480} />
          <SlotPlaceholder slot="pull-panel" label="Context panel" minHeight={480} />
        </div>
      </main>
    </div>
  );
}
