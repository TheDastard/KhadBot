// apps/web/src/components/raids/encounter-row.tsx
//
// One encounter and difficulty of a night: the journal portrait (48 by 24, shown only when a URL is given; none is
// today, so the name stands alone), the name, "Difficulty · N pulls · best X", and the pull chips: kills first, a thin
// divider, then the wipes in pull order.

import { bestFightOf, groupSub, splitPulls, type NightGroup } from "./night";
import { PullChip } from "./pull-chip";

export function EncounterRow({ code, group, portraitSrc }: { code: string; group: NightGroup; portraitSrc?: string }) {
  const best = bestFightOf(group);
  const { kills, wipes } = splitPulls(group.pulls);
  const chip = (fight: number, pull: NightGroup["pulls"][number]) => (
    <PullChip key={fight} code={code} group={group} pull={pull} best={fight === best} />
  );
  return (
    <div
      data-testid="encounter-row"
      className="grid grid-cols-[230px_minmax(0,1fr)] items-start gap-4 border-t border-line px-4 py-3"
    >
      <div className="flex min-w-0 items-center gap-2.5">
        {portraitSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={portraitSrc} alt="" width={48} height={24} className="h-6 w-12 flex-none object-contain" />
        ) : null}
        <span className="flex min-w-0 flex-col">
          <b className="text-[13px] font-semibold">{group.name}</b>
          <span className="text-xs text-mut">{groupSub(group)}</span>
        </span>
      </div>
      <div role="group" aria-label={`${group.name} ${group.difficulty ?? ""} pulls`.replace("  ", " ")} className="flex flex-wrap gap-1.5">
        {kills.map((p) => chip(p.fight, p))}
        {kills.length > 0 && wipes.length > 0 ? (
          <span aria-hidden className="mx-1.5 w-px flex-none self-stretch bg-line" />
        ) : null}
        {wipes.map((p) => chip(p.fight, p))}
      </div>
    </div>
  );
}
