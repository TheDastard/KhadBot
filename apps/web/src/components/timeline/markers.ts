// apps/web/src/components/timeline/markers.ts
//
// The timeline's markers, built from the timeline payload: one flat list in time order (the order the arrow keys and
// the "Markers as a list" disclosure follow, across lanes), the mechanic rows, and where each cooldown icon sits.
// Pure functions; nothing here draws.
//
// Lanes, top to bottom: phases (bands, not markers), one row per mechanic, deaths, cooldowns, damage windows.

import { formatClockTenths } from "@/lib/format";
import { parseRef } from "@/lib/refs";

import type { PullTimelineData } from "./queries";

export type Lane = "mechanic" | "death" | "cooldown" | "window";

/** Time ties break by lane in this order, then by id, so the order never depends on the payload's. */
export const LANE_RANK: Record<Lane, number> = { mechanic: 0, death: 1, cooldown: 2, window: 3 };

interface MarkerBase {
  /** Unique within the timeline (a mechanic's ref is shared by its clusters). */
  id: string;
  lane: Lane;
  tMs: number;
  /** The end of a span (a mechanic cluster); equal to `tMs` for a point. */
  endMs: number;
  /** The evidence ref Enter selects. */
  ref: string;
  /** The marker's accessible name. */
  label: string;
  /** The line "Markers as a list" shows after the time. */
  listText: string;
}

export interface MechanicMark extends MarkerBase {
  lane: "mechanic";
  mechanicId: string;
  name: string;
  failures: number;
  hits: number;
  /** This cluster's place among its mechanic's clusters on the pull (1-based), and how many there are. */
  clusterN: number;
  clusterOf: number;
}
export interface DeathMark extends MarkerBase {
  lane: "death";
  n: number;
  player: string;
  afterCutoff: boolean;
}
export interface CooldownMark extends MarkerBase {
  lane: "cooldown";
  spellId: number;
  spell: string;
  caster: string;
  category: "defensive" | "offensive" | "utility";
  /** 0 is the upper tier; casts closer together than an icon's width drop to 1. */
  tier: 0 | 1;
  useN: number;
  useOf: number;
}
export interface WindowMark extends MarkerBase {
  lane: "window";
  name: string;
  short: string;
  occurrence: number;
  covered: boolean;
}

export type Marker = MechanicMark | DeathMark | CooldownMark | WindowMark;

export interface MechanicRow {
  mechanicId: string;
  name: string;
  failures: number;
  marks: MechanicMark[];
}

export interface TimelineModel {
  /** Every marker in time order. */
  ordered: Marker[];
  rows: MechanicRow[];
  deaths: DeathMark[];
  cooldowns: CooldownMark[];
  windows: WindowMark[];
}

/** An encounter-file ID as words when the file gave no name ("caustic_waves" -> "Caustic waves"). */
export function humanize(id: string): string {
  const words = id.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : id;
}

const SMALL_WORDS = new Set(["of", "the", "and", "to", "a", "in"]);

/** "Rage of the Shackled" becomes "RotS": capitals for the words that matter, lower case for the small ones. */
export function abbreviate(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  if (words.length < 2) return name;
  return words
    .map((w) => (SMALL_WORDS.has(w.toLowerCase()) ? w.charAt(0).toLowerCase() : w.charAt(0).toUpperCase()))
    .join("");
}

export function compareMarkers(a: Marker, b: Marker): number {
  return a.tMs - b.tMs || LANE_RANK[a.lane] - LANE_RANK[b.lane] || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
}

/** Markers in the order the arrow keys follow: time, then lane, then id. */
export function orderMarkers<T extends Marker>(markers: readonly T[]): T[] {
  return [...markers].sort(compareMarkers);
}

/**
 * Which tier each cast (given in time order) sits in. A cast goes in the upper tier unless one is within `gapMs` of
 * it; then in the lower tier unless one is there too; then in whichever tier's last cast is older.
 */
export function assignCooldownTiers(times: readonly number[], gapMs: number): Array<0 | 1> {
  const last: [number, number] = [-Infinity, -Infinity];
  return times.map((t) => {
    let tier: 0 | 1;
    if (t - last[0] >= gapMs) tier = 0;
    else if (t - last[1] >= gapMs) tier = 1;
    else tier = last[0] <= last[1] ? 0 : 1;
    last[tier] = t;
    return tier;
  });
}

/** An icon is about 22 px on a 1000 px axis; casts closer than this share the lower tier. */
export const COOLDOWN_GAP_FRACTION = 0.022;

export function buildTimeline(timeline: PullTimelineData): TimelineModel {
  const rowsById = new Map<string, MechanicRow>();
  for (const m of timeline.mechanics) {
    const name = m.name ?? humanize(m.mechanic_id);
    const row = rowsById.get(m.mechanic_id) ?? { mechanicId: m.mechanic_id, name, failures: 0, marks: [] };
    row.failures += m.failures;
    const failures = m.failures === 0 ? "no failures" : `${m.failures} failure${m.failures === 1 ? "" : "s"}`;
    row.marks.push({
      id: `${m.ref}#${m.cluster}`,
      lane: "mechanic",
      tMs: m.t_ms,
      endMs: Math.max(m.end_ms, m.t_ms),
      ref: m.ref,
      mechanicId: m.mechanic_id,
      name,
      failures: m.failures,
      hits: m.hits,
      clusterN: 0,
      clusterOf: 0,
      label: `${name}, ${formatClockTenths(m.t_ms)}, ${failures}`,
      listText: `${name} cluster, ${failures}`,
    });
    rowsById.set(m.mechanic_id, row);
  }
  const hitsOf = (row: MechanicRow) => row.marks.reduce((sum, x) => sum + x.hits, 0);
  const rows = [...rowsById.values()].sort(
    (a, b) => b.failures - a.failures || hitsOf(b) - hitsOf(a) || a.name.localeCompare(b.name),
  );
  for (const row of rows) {
    row.marks = orderMarkers(row.marks);
    row.marks.forEach((mark, i) => {
      mark.clusterN = i + 1;
      mark.clusterOf = row.marks.length;
    });
  }

  const deaths: DeathMark[] = timeline.deaths.map((d, i) => {
    const parsed = parseRef(d.ref);
    const n = parsed?.kind === "death" ? parsed.n : i + 1;
    const after = d.after_cutoff ? ", after cutoff" : "";
    return {
      id: d.ref,
      lane: "death",
      tMs: d.t_ms,
      endMs: d.t_ms,
      ref: d.ref,
      n,
      player: d.name,
      afterCutoff: d.after_cutoff,
      label: `Death ${n}, ${d.name}, ${formatClockTenths(d.t_ms)}${after}`,
      listText: `Death ${n}, ${d.name}${d.after_cutoff ? " (after cutoff)" : ""}`,
    };
  });

  const uses = new Map<number, number>();
  for (const c of timeline.cooldowns) {
    const parsed = parseRef(c.ref);
    const n = parsed?.kind === "cd" ? parsed.n : 0;
    uses.set(c.spell_id, Math.max(uses.get(c.spell_id) ?? 0, n));
  }
  const casts = [...timeline.cooldowns].sort((a, b) => a.t_ms - b.t_ms || a.ref.localeCompare(b.ref));
  const tiers = assignCooldownTiers(
    casts.map((c) => c.t_ms),
    timeline.duration_ms * COOLDOWN_GAP_FRACTION,
  );
  const cooldowns: CooldownMark[] = casts.map((c, i) => {
    const parsed = parseRef(c.ref);
    const useN = parsed?.kind === "cd" ? parsed.n : 0;
    return {
      id: c.ref,
      lane: "cooldown",
      tMs: c.t_ms,
      endMs: c.t_ms,
      ref: c.ref,
      spellId: c.spell_id,
      spell: c.spell,
      caster: c.caster,
      category: c.category,
      tier: tiers[i]!,
      useN,
      useOf: Math.max(uses.get(c.spell_id) ?? 0, useN),
      label: `${c.spell}, ${c.caster}, ${formatClockTenths(c.t_ms)}`,
      listText: `${c.spell}, ${c.caster}`,
    };
  });

  const windows: WindowMark[] = timeline.windows.map((w) => {
    const name = w.name ?? humanize(w.window_id);
    const state = w.covered ? "covered" : "missed";
    return {
      id: w.ref,
      lane: "window",
      tMs: w.t_ms,
      endMs: w.t_ms,
      ref: w.ref,
      name,
      short: abbreviate(name),
      occurrence: w.occurrence,
      covered: w.covered,
      label: `${name} ${w.occurrence}, ${formatClockTenths(w.t_ms)}, ${state}`,
      listText: `${name} ${w.occurrence}, ${state}`,
    };
  });

  const ordered = orderMarkers<Marker>([...rows.flatMap((r) => r.marks), ...deaths, ...cooldowns, ...windows]);
  return { ordered, rows, deaths, cooldowns, windows };
}

/** The index of the first marker whose ref is `ref`, or -1. */
export function indexOfRef(ordered: readonly Marker[], ref: string | null): number {
  return ref ? ordered.findIndex((m) => m.ref === ref) : -1;
}

export type StepKey = "ArrowRight" | "ArrowDown" | "ArrowLeft" | "ArrowUp" | "Home" | "End";

/** Where a stepping key moves from `index` among `count` markers: no wrap, and -1 (nothing focused yet) starts at an end. */
export function stepIndex(count: number, index: number, key: StepKey): number {
  if (count <= 0) return -1;
  switch (key) {
    case "Home":
      return 0;
    case "End":
      return count - 1;
    case "ArrowRight":
    case "ArrowDown":
      return index < 0 ? 0 : Math.min(count - 1, index + 1);
    case "ArrowLeft":
    case "ArrowUp":
      return index < 0 ? count - 1 : Math.max(0, index - 1);
  }
}

export function isStepKey(key: string): key is StepKey {
  return ["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "Home", "End"].includes(key);
}
