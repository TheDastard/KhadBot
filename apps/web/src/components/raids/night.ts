// apps/web/src/components/raids/night.ts
//
// What the Raids page shows of a night, as plain data, and the logic that orders and labels it: a night (a report)
// holds encounter groups, a group holds pulls. Both the log lookup and the server's reports list adapt into this
// shape, and guild and character results (components/raids/guild/, components/raids/character/) can too, so the
// night card, encounter row and pull chip are written once.

import type { ApiGet } from "@khadbot/api-client";

import { formatClock, formatPct } from "@/lib/format";
import { pullPagePath } from "@/lib/refs";

export interface NightPull {
  fight: number;
  /** 1-based within its group, in pull order. */
  ordinal: number;
  kill: boolean;
  /** WCL's fight percentage left (0 to 100); null when the report doesn't say. */
  bossPct: number | null;
  durationMs: number;
}

export interface NightGroup {
  encounterId: number;
  name: string;
  /** "Heroic", "Mythic", "LFR"; null when WCL gives no difficulty. */
  difficulty: string | null;
  pulls: NightPull[];
  /** The fight of the group's best pull; worked out from the pulls when the source doesn't say. */
  bestFight: number | null;
}

export interface Night {
  code: string;
  title: string | null;
  /** ISO time the report started; null when the source doesn't say (the server's reports list). */
  startedAt: string | null;
  groups: NightGroup[];
}

// ----------------------------------------------------------------------------
// Adapters from the API's payloads
// ----------------------------------------------------------------------------

export type LookupData = ApiGet<"/v1/lookup/report">;
export type ReportDetailData = ApiGet<"/v1/reports/{code}">;

const DIFFICULTY_LABELS: Record<string, string> = { lfr: "LFR", normal: "Normal", heroic: "Heroic", mythic: "Mythic" };

export function difficultyLabel(name: string | null | undefined): string | null {
  return name ? (DIFFICULTY_LABELS[name] ?? name) : null;
}

export function nightFromLookup(data: LookupData): Night {
  return {
    code: data.code,
    title: data.title ?? null,
    startedAt: data.started_at,
    groups: data.groups.map((g) => ({
      encounterId: g.encounter_id,
      name: g.encounter_name,
      difficulty: difficultyLabel(g.difficulty_name),
      bestFight: g.best_fight,
      pulls: g.pulls.map((p) => ({
        fight: p.fight,
        ordinal: p.ordinal,
        kill: p.outcome === "kill",
        bossPct: p.boss_pct,
        durationMs: p.duration_ms,
      })),
    })),
  };
}

export function nightFromReport(data: ReportDetailData): Night {
  return {
    code: data.code,
    title: data.title ?? null,
    startedAt: null,
    groups: data.groups.map((g) => {
      const pulls = g.pulls.map((p) => ({
        fight: p.fight,
        ordinal: p.ordinal,
        kill: p.outcome === "kill",
        bossPct: p.boss_pct,
        durationMs: p.duration_ms,
      }));
      return {
        encounterId: g.encounter_id,
        name: g.encounter_name,
        difficulty: difficultyLabel(g.difficulty_name),
        bestFight: bestPullOf(pulls)?.fight ?? null,
        pulls,
      };
    }),
  };
}

// ----------------------------------------------------------------------------
// Ordering, best pull, labels
// ----------------------------------------------------------------------------

/** The group's best pull: its first kill, else the wipe with the lowest fight % left (the first of equals). */
export function bestPullOf(pulls: readonly NightPull[]): NightPull | null {
  let best: NightPull | null = null;
  for (const pull of pulls) {
    if (best === null) best = pull;
    else if (pull.kill && !best.kill) best = pull;
    else if (!pull.kill && !best.kill && (pull.bossPct ?? 100) < (best.bossPct ?? 100)) best = pull;
  }
  return best;
}

/** The pull marked best: the source's `bestFight` when it names one of the pulls, else worked out. */
export function bestFightOf(group: Pick<NightGroup, "pulls" | "bestFight">): number | null {
  if (group.bestFight !== null && group.pulls.some((p) => p.fight === group.bestFight)) return group.bestFight;
  return bestPullOf(group.pulls)?.fight ?? null;
}

/** Kills first, then the wipes, each in pull order (the source's order, which is time order). */
export function splitPulls(pulls: readonly NightPull[]): { kills: NightPull[]; wipes: NightPull[] } {
  return { kills: pulls.filter((p) => p.kill), wipes: pulls.filter((p) => !p.kill) };
}

export function pluralize(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

export function bossPullCount(night: Night): number {
  return night.groups.reduce((sum, g) => sum + g.pulls.length, 0);
}

/** "1 night · 24 boss pulls" */
export function summaryLine(nights: readonly Night[]): string {
  const pulls = nights.reduce((sum, n) => sum + bossPullCount(n), 0);
  return `${pluralize(nights.length, "night")} · ${pluralize(pulls, "boss pull")}`;
}

/** "Oct 6, 2026" in UTC; null for a night with no start time. */
export function formatNightDate(iso: string | null): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  return at.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** "Oct 6, 2026 · 15 boss pulls" (the date is left out when the night has none). */
export function nightMeta(night: Night): string {
  return [formatNightDate(night.startedAt), pluralize(bossPullCount(night), "boss pull")].filter(Boolean).join(" · ");
}

/** "Heroic · 7 pulls · best 5.6%" or "... best kill". */
export function groupSub(group: NightGroup): string {
  const best = group.pulls.find((p) => p.fight === bestFightOf(group));
  const bestText = best ? (best.kill ? "best kill" : best.bossPct === null ? null : `best ${formatPct(best.bossPct)}`) : null;
  return [group.difficulty, pluralize(group.pulls.length, "pull"), bestText].filter(Boolean).join(" · ");
}

/** What a chip shows, in reading order: "P1 0:37 96.6%" or "P3 9:39 Kill". */
export function chipText(pull: NightPull): string {
  const pct = pull.kill ? "Kill" : pull.bossPct === null ? "Wipe" : formatPct(pull.bossPct);
  return `P${pull.ordinal} ${formatClock(pull.durationMs)} ${pct}`;
}

/**
 * A pull's description for aria-label and title. It starts with the chip's visible text (so the name contains the
 * label, as WCAG asks), then says the rest: "P1 0:37 96.6%, Ula'tek Heroic, fight 10, wipe with 96.6% left".
 */
export function pullLabel(group: NightGroup, pull: NightPull, best: boolean): string {
  const progress = pull.kill ? [] : [pull.bossPct === null ? "wipe" : `wipe with ${formatPct(pull.bossPct)} left`];
  return [
    chipText(pull),
    [group.name, group.difficulty].filter(Boolean).join(" "),
    `fight ${pull.fight}`,
    ...progress,
    ...(best ? ["best pull"] : []),
  ].join(", ");
}

/** Where a pull chip goes: the pull page, loaded or not. */
export function pullHref(code: string, fight: number): string {
  return pullPagePath(code, fight);
}
