// apps/web/src/app/r/[code]/p/[fight]/slots.tsx
//
// The pull route's timeline slot. The route shell (pull-route.tsx) fills its other two spots directly:
//
//   pull-not-loaded   components/open-pull/   <PullNotLoaded code fight />
//       Shown when the digest isn't written (404) and the server can open pulls (`features.ingest_jobs`). It asks the
//       API to load the pull (POST /v1/reports, after render) and shows the loading steps. When the digest is written
//       it invalidates `queryKeys.pull(code, fight)`; the route shell's digest query then succeeds and swaps in the
//       pull page without a reload, keeping `?ref=`.
//   pull-body         components/pull/, components/panel/   <PullPage code fight digest timeline />
//       The rail, header, main column and context panel. It places `timeline` (the slot below) in the main column,
//       under the header and above the sections, as the canvas draws it.
//   pull-timeline     components/timeline/, components/events/   <PullTimelineSlot code fight digest />
//       The timeline and the zoomed events table.

"use client";

import { PullTimeline } from "@/components/timeline/pull-timeline";
import type { PullDigestData } from "@/lib/queries";

export interface PullDigestSlotProps {
  code: string;
  fight: number;
  digest: PullDigestData;
}

export function PullTimelineSlot(props: PullDigestSlotProps) {
  return <PullTimeline {...props} />;
}
