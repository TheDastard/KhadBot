// apps/web/src/components/shared/fight-pct.tsx
//
// WCL's fight percentage, the primary progress figure everywhere (review answer 5):
// - FightPct: the value ("5.6%") in its quality colour, or "Kill" in Artifact gold;
// - FightPctBar: a bar that fills toward a kill (100 minus fight % left), a full gold bar for a kill.
// Both carry the full description ("Wipe with 5.6% of the fight left") as their accessible name, so colour never
// carries the meaning. The bands are in lib/quality.ts.

import { cn } from "@/lib/utils";
import {
  barFill,
  fightPctDescription,
  fightPctLabel,
  QUALITY_BAR,
  QUALITY_TEXT,
  qualityOf,
} from "@/lib/quality";

export interface FightPctProps {
  /** WCL's fight percentage left (Pull `boss_pct`): 0 to 100. */
  pct: number;
  kill: boolean;
  className?: string;
}

export function FightPct({ pct, kill, className }: FightPctProps) {
  const quality = qualityOf(pct, kill);
  const description = fightPctDescription(pct, kill);
  return (
    <span
      data-quality={quality}
      aria-label={description}
      title={description}
      className={cn("font-medium tabular-nums", kill && "font-semibold", QUALITY_TEXT[quality], className)}
    >
      {fightPctLabel(pct, kill)}
    </span>
  );
}

export interface FightPctBarProps extends FightPctProps {
  /** Track height in px: 8 in the rail, 3 along a pull chip, 6 on loading rows. */
  height?: number;
  /** Hide it from assistive tech when a FightPct beside it already says the same. */
  decorative?: boolean;
}

export function FightPctBar({ pct, kill, height = 8, decorative = false, className }: FightPctBarProps) {
  const quality = qualityOf(pct, kill);
  const fill = barFill(pct, kill);
  const description = fightPctDescription(pct, kill);
  return (
    <span
      role={decorative ? undefined : "img"}
      aria-hidden={decorative || undefined}
      aria-label={decorative ? undefined : description}
      title={decorative ? undefined : description}
      data-quality={quality}
      data-fill={fill.toFixed(2)}
      className={cn("relative block overflow-hidden rounded-sm bg-s3", className)}
      style={{ height }}
    >
      <span
        className={cn("absolute inset-y-0 left-0 rounded-sm", QUALITY_BAR[quality])}
        style={{ width: `${fill}%` }}
      />
    </span>
  );
}
