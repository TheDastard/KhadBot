// apps/web/src/components/pull/use-pull-context.ts
//
// What the page and the panel need to name things for one pull: its place in the report (encounter, difficulty, the
// pull summary) and the display-name lookups. Everything is cached, so the panel reusing it costs no requests.

"use client";

import { useMemo } from "react";

import { buildNames, type Names } from "./names";
import { useCooldownNames, useEncounterNames, useReportDetail, type ReportDetailData } from "./queries";

type Group = ReportDetailData["groups"][number];
type PullSummary = Group["pulls"][number];

export interface PullContext {
  group: Group | undefined;
  summary: PullSummary | undefined;
  names: Names;
  loading: boolean;
}

export function findPull(
  detail: ReportDetailData | undefined,
  fight: number,
): { group: Group | undefined; summary: PullSummary | undefined } {
  for (const group of detail?.groups ?? []) {
    const summary = group.pulls.find((p) => p.fight === fight);
    if (summary) return { group, summary };
  }
  return { group: undefined, summary: undefined };
}

export function usePullContext(code: string, fight: number, extraSpells?: Record<number, string>): PullContext {
  const detail = useReportDetail(code);
  const { group, summary } = useMemo(() => findPull(detail.data, fight), [detail.data, fight]);
  const encounter = useEncounterNames(group?.encounter_id, group?.difficulty);
  const cooldowns = useCooldownNames();
  const names = useMemo(
    () => buildNames(encounter.data, cooldowns.data, extraSpells),
    [encounter.data, cooldowns.data, extraSpells],
  );
  return { group, summary, names, loading: detail.isPending };
}
