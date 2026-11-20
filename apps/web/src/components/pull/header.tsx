// apps/web/src/components/pull/header.tsx
//
// The pull header: "Encounter · Difficulty · P2", the fight ID, outcome and coverage badges, the stats (duration,
// fight % left as the primary figure, boss health left, phase reached, deaths), the death-cutoff note and the "Show
// everything" switch for `?full=1`.

"use client";

import { BossPortrait, useBossArt } from "@/components/shared/boss-art";
import { ReviewButton } from "@/components/review";
import { FightPct } from "@/components/shared/fight-pct";
import { StatusBadge } from "@/components/shared/status-badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useFocusActions, useFocusFull } from "@/lib/focus";
import { formatClock, formatPct } from "@/lib/format";
import type { PullDigestData } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { SplitIcon } from "lucide-react";

import { CoverageBadge } from "./coverage-badge";
import { cutoffSentence, cutoffTail } from "./logic";
import type { PullContext } from "./use-pull-context";
import { difficultyLabel } from "./rail";

const FIGHT_PCT_HELP =
  "WarcraftLogs' fight percentage: how much of the whole fight was left, across every stage. Boss health is the main boss's own health.";

function Stat({ label, children, help }: { label: React.ReactNode; children: React.ReactNode; help?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-mut">
        {help ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0} className="cursor-help underline decoration-dotted underline-offset-2">
                {label}
              </span>
            </TooltipTrigger>
            <TooltipContent>{help}</TooltipContent>
          </Tooltip>
        ) : (
          label
        )}
      </dt>
      <dd className="m-0">{children}</dd>
    </div>
  );
}

/** The "Show everything" switch (`?full=1`). */
export function ShowEverythingSwitch({ checked, onChange }: { checked: boolean; onChange: (on: boolean) => void }) {
  return (
    <span className="inline-flex min-h-8 items-center gap-2.5 text-sm">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label="Show everything"
        onClick={() => onChange(!checked)}
        className={cn(
          "relative inline-block h-[22px] w-[38px] shrink-0 cursor-pointer rounded-sm",
          checked ? "bg-info" : "bg-s3 shadow-[inset_0_0_0_1px_var(--line)]",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "absolute top-[3px] size-4 rounded-sm bg-white shadow-[0_1px_2px_rgba(0,0,0,.3)] transition-[left]",
            checked ? "left-[19px]" : "left-[3px]",
          )}
        />
      </button>
      <span aria-hidden className="cursor-pointer" onClick={() => onChange(!checked)}>
        Show everything
      </span>
    </span>
  );
}

export function PullHeader({
  code,
  fight,
  digest,
  context,
}: {
  code: string;
  fight: number;
  digest: PullDigestData;
  context: PullContext;
}) {
  const full = useFocusFull();
  const { setFull } = useFocusActions();
  const { group, summary, names } = context;
  const kill = digest.outcome === "kill";
  const title = [
    group?.encounter_name ?? digest.encounter_name,
    group ? difficultyLabel(group) : "",
    summary?.label ?? "",
  ]
    .filter(Boolean)
    .join(" · ");
  const { portraitSrc } = useBossArt(group?.encounter_id);
  const total = summary?.deaths ?? null;
  const before = summary?.deaths_before_cutoff ?? null;
  const phase = names.phaseShort(digest.phase_reached);

  return (
    <div className="flex flex-col gap-3" data-testid="pull-header">
      <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2">
        <BossPortrait src={portraitSrc} width={96} />
        <h1 className="m-0 text-[28px] leading-tight">{title}</h1>
        <span className="font-mono text-[13px] leading-none font-medium text-mut">fight {fight}</span>
        <StatusBadge status={kill ? "kill" : "wipe"} />
        <CoverageBadge
          code={code}
          fight={fight}
          coverage={digest.coverage}
          digest={digest}
          prefix="Coverage: "
          focusable
        />
        {/* Slot: review-button (components/review/): renders only with `features.reviews`. */}
        <ReviewButton code={code} fight={fight} />
      </div>

      <dl className="m-0 flex flex-wrap gap-x-9 gap-y-3">
        <Stat label="Duration">
          <span className="text-lg font-semibold">{formatClock(digest.duration_ms)}</span>
        </Stat>
        <Stat label="Fight % left" help={FIGHT_PCT_HELP}>
          {digest.boss_pct === null ? (
            <span className="text-lg font-semibold text-mut">—</span>
          ) : (
            <FightPct pct={digest.boss_pct} kill={kill} className="text-lg font-semibold" />
          )}
        </Stat>
        {digest.boss_hp_pct !== null && digest.boss_hp_pct !== undefined ? (
          <Stat label="Boss health left" help={FIGHT_PCT_HELP}>
            <span className="text-[15px] font-medium text-mut">{formatPct(digest.boss_hp_pct)}</span>
          </Stat>
        ) : null}
        <Stat label="Phase reached">
          <span className="text-lg font-semibold" title={names.phase(digest.phase_reached) ?? undefined}>
            {phase ?? "—"}
          </span>
        </Stat>
        <Stat label="Deaths">
          <span className="text-lg font-semibold">{total ?? digest.deaths.length}</span>
          {before !== null && digest.cutoff_ms !== null ? (
            <span className="ml-1.5 text-[13px] text-mut">{before} before cutoff</span>
          ) : null}
        </Stat>
      </dl>

      <div className="flex flex-wrap items-center justify-between gap-2.5 rounded-sm border border-line bg-s1 px-3.5 py-2.5">
        <span className="inline-flex items-center gap-2" data-testid="cutoff-note">
          <SplitIcon className="size-4 shrink-0 text-acc" aria-hidden />
          <span>
            {digest.cutoff_ms !== null
              ? `${cutoffSentence(before, digest.cutoff_ms)} ${cutoffTail(total, before, full)}`
              : "This pull has no death cutoff: every death is analysed."}
          </span>
        </span>
        <ShowEverythingSwitch checked={full} onChange={setFull} />
      </div>
    </div>
  );
}
