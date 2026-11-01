// apps/web/src/components/raids/pull-chip.tsx
//
// The pull chip (canvas "Pull chip"): 72 by 44, ordinal and duration on top in small mono, fight % (or Kill) in its
// quality colour, a 3 px bar along the bottom filling toward a kill, and a 2 px accent line on top for the group's
// best pull. The full description is the link's aria-label and title. It always links to the pull page, loaded or
// not: opening an unloaded pull is that page's job, and nothing here says which pulls are loaded.

import Link from "next/link";

import { FightPctBar } from "@/components/shared/fight-pct";
import { formatClock } from "@/lib/format";
import { QUALITY_TEXT, qualityOf } from "@/lib/quality";
import { cn } from "@/lib/utils";

import { chipText, pullHref, pullLabel, type NightGroup, type NightPull } from "./night";

export interface PullChipProps {
  code: string;
  group: NightGroup;
  pull: NightPull;
  best: boolean;
}

export function PullChip({ code, group, pull, best }: PullChipProps) {
  const label = pullLabel(group, pull, best);
  const pct = pull.bossPct ?? 100;
  const quality = qualityOf(pct, pull.kill);
  const text = chipText(pull).split(" ").slice(2).join(" ");
  return (
    <Link
      href={pullHref(code, pull.fight)}
      aria-label={label}
      title={label}
      data-testid="pull-chip"
      data-fight={pull.fight}
      data-kill={pull.kill}
      data-best={best}
      className={cn(
        "relative flex h-11 w-[72px] flex-col justify-between overflow-hidden rounded-sm border border-line bg-s2 px-1.5 pt-1 pb-[7px] text-ink no-underline hover:bg-s3",
        best && "shadow-[inset_0_2px_0_var(--acc)]",
      )}
    >
      <span className="flex justify-between gap-1 font-mono text-[10px] leading-none font-medium text-mut" aria-hidden>
        <span>P{pull.ordinal}</span>{" "}
        <span>{formatClock(pull.durationMs)}</span>
      </span>{" "}
      <span aria-hidden className={cn("text-sm leading-none font-semibold tabular-nums", QUALITY_TEXT[quality])}>
        {text}
      </span>
      <FightPctBar pct={pct} kill={pull.kill} height={3} decorative className="absolute inset-x-0 bottom-0 rounded-none" />
    </Link>
  );
}
