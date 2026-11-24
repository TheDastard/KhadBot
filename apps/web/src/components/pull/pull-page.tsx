// apps/web/src/components/pull/pull-page.tsx
//
// The pull page, filling the route's `pull-body` slot: the 240 px pull rail, then the main column (header, the
// timeline node the route passes in, then the sections beside the 380 px context panel). The panel is sticky, so it
// stays in view while the sections scroll. Tables and the timeline scroll in their own boxes, so the page itself
// never scrolls sideways, down to about 1024 px.

"use client";

import type { ReactNode } from "react";

import { ContextPanel } from "@/components/panel/context-panel";
import { ArtFooter } from "@/components/shared/boss-art";
import { FocusSync, useFocusFull } from "@/lib/focus";
import type { PullDigestData } from "@/lib/queries";

import { PullHeader } from "./header";
import { PullRail } from "./rail";
import { usePullFindings } from "./queries";
import { AggroSection, AnomaliesSection, FindingsSection, UnregisteredSection } from "./sections-more";
import { CooldownsSection, DeathsSection, MechanicsSection, WindowsSection } from "./sections";
import { usePullContext } from "./use-pull-context";

export function PullPage({
  code,
  fight,
  digest,
  timeline,
}: {
  code: string;
  fight: number;
  digest: PullDigestData;
  /** The timeline and zoomed events (components/timeline/), placed under the header. */
  timeline: ReactNode;
}) {
  const full = useFocusFull();
  const context = usePullContext(code, fight);
  const findings = usePullFindings(code, fight);
  const { names, summary } = context;
  const section = { code, fight, digest, names };

  return (
    <div data-slot="pull-body" className="grid min-h-[calc(100vh-57px)] grid-cols-[240px_minmax(0,1fr)] items-stretch">
      <FocusSync />
      <PullRail code={code} fight={fight} />
      <main className="flex min-w-0 flex-col gap-4 px-7 pt-6 pb-16">
        <PullHeader code={code} fight={fight} digest={digest} context={context} />
        {timeline}
        <div className="grid grid-cols-[minmax(0,1fr)_380px] items-start gap-4">
          <div className="flex min-w-0 flex-col gap-4">
            <DeathsSection {...section} total={summary?.deaths ?? null} before={summary?.deaths_before_cutoff ?? null} full={full} />
            <MechanicsSection {...section} />
            <WindowsSection {...section} />
            <CooldownsSection {...section} />
            <div className="@container">
              <div className="grid grid-cols-1 items-start gap-4 @[640px]:grid-cols-2">
                <AggroSection code={code} digest={digest} />
                <UnregisteredSection digest={digest} />
              </div>
            </div>
            <AnomaliesSection digest={digest} names={names} />
            <FindingsSection findings={findings.data} loading={findings.isPending} />
          </div>
          <ContextPanel code={code} fight={fight} />
        </div>
        <ArtFooter />
      </main>
    </div>
  );
}
