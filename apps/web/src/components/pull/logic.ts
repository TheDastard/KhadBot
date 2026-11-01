// apps/web/src/components/pull/logic.ts
//
// The pull page's pure logic, apart from React so Vitest can test it: the rail's ordering, folding anomaly codes into
// one line per kind with their explanations, the cutoff note and the digest's cooldown lists merged.

import type { PullDigestData } from "@/lib/queries";
import { formatClockTenths } from "@/lib/format";

// ---------------------------------------------------------------------------
// Rail
// ---------------------------------------------------------------------------

/** Kills first (in pull order), then wipes in pull order; the rail draws a divider between them. */
export function splitKillsAndWipes<T extends { outcome: "kill" | "wipe" }>(pulls: readonly T[]) {
  return {
    kills: pulls.filter((p) => p.outcome === "kill"),
    wipes: pulls.filter((p) => p.outcome !== "kill"),
  };
}

// ---------------------------------------------------------------------------
// Anomalies
// ---------------------------------------------------------------------------

export interface AnomalyGroup {
  /** The code before the colon ("cooldown_unverified"). */
  kind: string;
  /** What follows the colon on each code ("871"), in the order given; empty strings for codes with no argument. */
  args: string[];
  /** How many codes of this kind. */
  count: number;
}

/** One group per anomaly kind, in order of first appearance, each holding its arguments (review answer 6). */
export function foldAnomalies(codes: readonly string[]): AnomalyGroup[] {
  const groups = new Map<string, AnomalyGroup>();
  for (const code of codes) {
    const at = code.indexOf(":");
    const kind = at === -1 ? code : code.slice(0, at);
    const arg = at === -1 ? "" : code.slice(at + 1);
    let group = groups.get(kind);
    if (!group) {
      group = { kind, args: [], count: 0 };
      groups.set(kind, group);
    }
    group.args.push(arg);
    group.count += 1;
  }
  return [...groups.values()];
}

export interface AnomalyText {
  /** The one-line title ("8 cooldowns not yet verified"). */
  title: string;
  /** The explanation in plain words. */
  body: string;
  /** Whether the arguments are spell IDs the page can name. */
  spells: boolean;
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** The title and explanation of a group. Unknown kinds are shown as they are. */
export function describeAnomaly(group: AnomalyGroup): AnomalyText {
  const n = group.count;
  const first = group.args[0] ?? "";
  switch (group.kind) {
    case "cooldown_unverified":
      return {
        title: `${n} ${plural(n, "cooldown", "cooldowns")} not yet verified`,
        body: "These registry entries haven't been checked against the game yet, so their durations may be off. Their uses still count.",
        spells: true,
      };
    case "friendly_damage_unverified":
      return {
        title: `${n} friendly-damage ${plural(n, "entry", "entries")} not yet verified`,
        body: "These friendly-damage registry entries haven't been checked against the game yet, so how their damage is classed may be off.",
        spells: true,
      };
    case "truncated":
      return {
        title: `${group.args.join(", ")} events cut short`,
        body: "Some of this pull's events were not all fetched, so counts built from them may be low.",
        spells: false,
      };
    case "missing_data_type":
      return {
        title: `No ${group.args.join(", ")} events`,
        body: "These kinds of events are missing for this pull, so anything that depends on them is left out.",
        spells: false,
      };
    case "no_hp_data":
      return {
        title: "No health data",
        body: "The pull's events carry no health values, so health along the fight can't be shown.",
        spells: false,
      };
    case "phase_trigger_unmatched":
      return {
        title: `${n} ${plural(n, "phase", "phases")} reached by WarcraftLogs but not matched`,
        body: "WarcraftLogs says the pull reached these phases, but the events never matched the encounter file's trigger for them, so phase timing may be off.",
        spells: false,
      };
    case "unregistered_damage":
      return {
        title: `${first}% of damage taken is unregistered`,
        body: "That share of the damage players took isn't claimed by any encounter-file entry, so coverage is partial.",
        spells: false,
      };
    case "unclassified_friendly_damage":
      return {
        title: `${first}% of friendly damage is unclassified`,
        body: "That share of the damage players took from friendly sources isn't classed by the friendly-damage registry.",
        spells: false,
      };
    default:
      return { title: group.kind, body: group.args.filter(Boolean).join(", "), spells: false };
  }
}

// ---------------------------------------------------------------------------
// Cutoff note
// ---------------------------------------------------------------------------

/** 1 -> "1st", 11 -> "11th", 22 -> "22nd". */
export function ordinal(n: number): string {
  const rest = n % 100;
  if (rest >= 11 && rest <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}

/** "Analysis stops at the 11th death, 9:37.8, when half the raid had died." */
export function cutoffSentence(deathsBeforeCutoff: number | null, cutoffMs: number): string {
  const when = formatClockTenths(cutoffMs);
  return deathsBeforeCutoff
    ? `Analysis stops at the ${ordinal(deathsBeforeCutoff)} death, ${when}, when half the raid had died.`
    : `Analysis stops at ${when}, when half the raid had died.`;
}

/** What the switch changes, in a sentence: "14 later deaths are hidden." or "Showing all 25 deaths; ...". */
export function cutoffTail(total: number | null, before: number | null, full: boolean): string {
  if (total === null || before === null) return "";
  const after = Math.max(0, total - before);
  if (full) return `Showing all ${total} ${plural(total, "death", "deaths")}; the ${after} after the cutoff are muted.`;
  return after === 0 ? "" : `${after} later ${plural(after, "death is", "deaths are")} hidden.`;
}

// ---------------------------------------------------------------------------
// Cooldown uses
// ---------------------------------------------------------------------------

export type CooldownUseRow = PullDigestData["defensive_cooldowns"][number];

/** Every category of cooldown use in the digest, merged in time order. */
export function allCooldownUses(digest: PullDigestData): CooldownUseRow[] {
  return [
    ...digest.defensive_cooldowns,
    ...(digest.offensive_cooldowns ?? []),
    ...(digest.utility_cooldowns ?? []),
  ].sort((a, b) => a.t_ms - b.t_ms || a.spell_id - b.spell_id);
}

/** The death number a death ref carries (`ev:CODE:15:death:2` -> 2), the "#" column. */
export function deathNumber(ref: string): number | null {
  const match = /:death:(\d+)$/.exec(ref);
  return match ? Number(match[1]) : null;
}

/** The top charged player of a mechanic: most failures, ties by name. */
export function topCharged(failuresByPlayer: Record<string, number>): { name: string; failures: number } | null {
  const entries = Object.entries(failuresByPlayer).filter(([, n]) => n > 0);
  if (entries.length === 0) return null;
  entries.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const [name, failures] = entries[0] as [string, number];
  return { name, failures };
}
