// apps/web/src/lib/quality.ts
//
// The fight % colour scale: WoW item quality by how much of the fight was left (WCL's fight percentage), Artifact
// gold for a kill. The canvas's bands ("Fight % colour" in its component inventory); the thresholds are KhadBot's
// own, WarcraftLogs publishes none:
//
//   Poor 90 to 100 · Common 75 to 90 · Uncommon 50 to 75 · Rare 25 to 50 · Epic 10 to 25 · Legendary under 10
//
// A band includes its lower bound (exactly 90 is Poor, exactly 10 is Epic). Bars fill toward a kill: the filled
// length is 100 minus fight % left, so a kill is a full bar.

import { formatPct } from "./format";

export type Quality = "poor" | "common" | "uncommon" | "rare" | "epic" | "legendary" | "artifact";

const BANDS: ReadonlyArray<readonly [number, Quality]> = [
  [90, "poor"],
  [75, "common"],
  [50, "uncommon"],
  [25, "rare"],
  [10, "epic"],
];

/** The quality band of a pull: `artifact` for a kill, else by fight % left (clamped to 0 to 100). */
export function qualityOf(fightPctLeft: number, kill: boolean): Quality {
  if (kill) return "artifact";
  const pct = clampPct(fightPctLeft);
  for (const [floor, quality] of BANDS) if (pct >= floor) return quality;
  return "legendary";
}

/** The bar's filled share in percent: 100 for a kill, else 100 minus fight % left. */
export function barFill(fightPctLeft: number, kill: boolean): number {
  return kill ? 100 : 100 - clampPct(fightPctLeft);
}

/** The visible value: "Kill", or the fight % left ("5.6%"). */
export function fightPctLabel(fightPctLeft: number, kill: boolean): string {
  return kill ? "Kill" : formatPct(clampPct(fightPctLeft));
}

/** The full description, for aria-label and title: "Kill" or "Wipe with 5.6% of the fight left". */
export function fightPctDescription(fightPctLeft: number, kill: boolean): string {
  return kill ? "Kill" : `Wipe with ${formatPct(clampPct(fightPctLeft))} of the fight left`;
}

function clampPct(value: number): number {
  if (!Number.isFinite(value)) return 100;
  return Math.min(100, Math.max(0, value));
}

/** Tailwind classes for a quality's text and bar colours (tokens in styles/globals.css). */
export const QUALITY_TEXT: Record<Quality, string> = {
  poor: "text-q-poor-text",
  common: "text-q-common-text",
  uncommon: "text-q-uncommon-text",
  rare: "text-q-rare-text",
  epic: "text-q-epic-text",
  legendary: "text-q-legendary-text",
  artifact: "text-q-artifact-text",
};

export const QUALITY_BAR: Record<Quality, string> = {
  poor: "bg-q-poor",
  common: "bg-q-common",
  uncommon: "bg-q-uncommon",
  rare: "bg-q-rare",
  epic: "bg-q-epic",
  legendary: "bg-q-legendary",
  artifact: "bg-q-artifact",
};
