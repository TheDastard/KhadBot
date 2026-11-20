// apps/web/src/components/pull/rail.tsx
//
// The pull rail (240 px): the report's pulls grouped under their encounter and difficulty, kills first, a divider,
// then wipes in pull order. Each row is a link that opens the pull with its pull ref selected (review answer 7); the
// open pull has aria-current. A group header is the boss's 28 px thumbnail (a neutral square without one), framed by a
// 1 px line as the canvas draws it, and name.

"use client";

import Link from "next/link";

import { BossThumbnail, useBossArt } from "@/components/shared/boss-art";
import { FightPct, FightPctBar } from "@/components/shared/fight-pct";
import { StatusBadge } from "@/components/shared/status-badge";
import { focusHref, useFocusFull } from "@/lib/focus";
import { pullPagePath } from "@/lib/refs";
import { cn } from "@/lib/utils";

import { CoverageBadge } from "./coverage-badge";
import { splitKillsAndWipes } from "./logic";
import { useReportDetail, type ReportDetailData } from "./queries";
import { SkeletonBlock } from "@/components/shared/skeletons";

type Group = ReportDetailData["groups"][number];
type PullSummary = Group["pulls"][number];

const DIFFICULTY: Record<string, string> = { lfr: "LFR", normal: "Normal", heroic: "Heroic", mythic: "Mythic" };

export function difficultyLabel(group: Pick<Group, "difficulty" | "difficulty_name">): string {
  if (group.difficulty_name) return DIFFICULTY[group.difficulty_name] ?? group.difficulty_name;
  return group.difficulty === null ? "" : `Difficulty ${group.difficulty}`;
}

function Row({ code, pull, current, full }: { code: string; pull: PullSummary; current: boolean; full: boolean }) {
  const kill = pull.outcome === "kill";
  const href = focusHref(pullPagePath(code, pull.fight), new URLSearchParams(), { ref: pull.ref, full });
  return (
    <Link
      href={href}
      scroll={false}
      aria-current={current ? "page" : undefined}
      data-fight={pull.fight}
      className={cn(
        "grid min-h-11 grid-cols-[30px_minmax(0,1fr)_50px] items-center gap-x-2 gap-y-1 rounded-sm p-2 text-ink no-underline hover:bg-s2",
        current && "bg-s2 shadow-[inset_3px_0_0_var(--acc)]",
      )}
    >
      <span className="font-mono text-[13px] leading-none font-semibold text-mut">{pull.label}</span>
      <FightPctBar pct={pull.boss_pct ?? 100} kill={kill} height={8} decorative />
      <span className="text-right text-[13px]">
        <FightPct pct={pull.boss_pct ?? 100} kill={kill} />
      </span>
      <span className="col-span-2 col-start-2 flex items-center gap-1.5 text-xs">
        <StatusBadge status={kill ? "kill" : "wipe"} />
        <CoverageBadge code={code} fight={pull.fight} coverage={pull.coverage} />
      </span>
    </Link>
  );
}

function GroupHeader({ group }: { group: Group }) {
  const { thumbnailSrc } = useBossArt(group.encounter_id);
  return (
    <div className="flex items-center gap-2 border-b border-line px-2 pb-1.5">
      <BossThumbnail src={thumbnailSrc} size={28} className="border border-line" />
      <span className="min-w-0 flex-1 text-[13px] leading-tight font-semibold">{group.encounter_name}</span>
      <span className="text-xs text-mut">{difficultyLabel(group)}</span>
    </div>
  );
}

export function PullRail({ code, fight }: { code: string; fight: number }) {
  const detail = useReportDetail(code);
  const full = useFocusFull();
  return (
    <aside
      id="pull-list"
      aria-label="Pulls"
      className="flex flex-col gap-[18px] border-r border-line bg-s1 px-3 py-5"
    >
      {detail.isPending ? (
        Array.from({ length: 5 }, (_, i) => <SkeletonBlock key={i} height={44} />)
      ) : detail.isError ? (
        <p className="m-0 px-2 text-[13px] text-mut">The report&apos;s pulls couldn&apos;t be listed.</p>
      ) : (
        detail.data.groups.map((group) => {
          const { kills, wipes } = splitKillsAndWipes(group.pulls);
          return (
            <div key={`${group.encounter_id}-${group.difficulty}`} className="flex flex-col gap-1" data-group={group.encounter_id}>
              <GroupHeader group={group} />
              {kills.map((p) => (
                <Row key={p.fight} code={code} pull={p} current={p.fight === fight} full={full} />
              ))}
              {kills.length > 0 && wipes.length > 0 ? <hr aria-hidden className="mx-2 my-1 border-0 border-t border-line" /> : null}
              {wipes.map((p) => (
                <Row key={p.fight} code={code} pull={p} current={p.fight === fight} full={full} />
              ))}
              {group.truncated ? (
                <p className="m-0 px-2 text-xs text-mut">
                  Showing {group.pulls.length} of {group.total} pulls.
                </p>
              ) : null}
            </div>
          );
        })
      )}
    </aside>
  );
}
