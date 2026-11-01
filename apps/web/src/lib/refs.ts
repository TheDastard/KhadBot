// apps/web/src/lib/refs.ts
//
// Evidence refs, parsed and built the way the API does (src/khadbot/encounter/evidence.py, parse_ref):
//
//   ev:<report>:<fight>:death:<n>            n >= 1
//   ev:<report>:<fight>:mech:<mechanic_id>
//   ev:<report>:<fight>:cd:<spell_id>:<n>    n >= 1
//   ev:<report>:<fight>:win:<window_id>:<n>  n >= 1
//   ev:<report>:<fight>:event:<seq>          seq >= 0
//   ev:<report>:<fight>:pull
//   plan:<plan_id>:<n>                       n >= 0
//
// A string that doesn't have one of these shapes parses to null; the API answers it `unresolvable_ref`.

import type { Schema } from "@khadbot/api-client";

/** The ref kinds, matching the API's RefKind. `plan` has no record yet (the API answers 501). */
export type RefKind = "death" | "mech" | "cd" | "win" | "event" | "pull" | "plan";

/** The kinds the evidence route resolves, from the generated union (`Evidence.kind`). */
export type EvidenceKind = Schema<"Evidence">["kind"];

export type ParsedRef =
  | { kind: "death"; report: string; fight: number; n: number }
  | { kind: "mech"; report: string; fight: number; key: string }
  | { kind: "cd"; report: string; fight: number; spellId: number; n: number }
  | { kind: "win"; report: string; fight: number; key: string; n: number }
  | { kind: "event"; report: string; fight: number; n: number }
  | { kind: "pull"; report: string; fight: number }
  | { kind: "plan"; key: string; n: number };

const SHAPES: Record<Exclude<RefKind, "plan">, number> = { death: 1, mech: 1, cd: 2, win: 2, event: 1, pull: 0 };

function toInt(text: string, minimum: number): number | null {
  if (!/^-?\d+$/.test(text)) return null;
  const value = Number(text);
  return Number.isSafeInteger(value) && value >= minimum ? value : null;
}

/** Parse a ref; null when it has no known shape. */
export function parseRef(ref: string): ParsedRef | null {
  const parts = ref.trim().split(":");
  if (parts[0] === "plan") {
    if (parts.length !== 3 || !parts[1]) return null;
    const n = toInt(parts[2] ?? "", 0);
    return n === null ? null : { kind: "plan", key: parts[1], n };
  }
  if (parts[0] !== "ev" || parts.length < 4 || !parts[1]) return null;
  const [, report, fightText, kind, ...rest] = parts as [string, string, string, string, ...string[]];
  if (!(kind in SHAPES)) return null;
  const k = kind as keyof typeof SHAPES;
  if (rest.length !== SHAPES[k] || rest.some((p) => p === "")) return null;
  const fight = toInt(fightText, 0);
  if (fight === null) return null;
  const [a = "", b = ""] = rest;
  switch (k) {
    case "death": {
      const n = toInt(a, 1);
      return n === null ? null : { kind: k, report, fight, n };
    }
    case "mech":
      return { kind: k, report, fight, key: a };
    case "cd": {
      const spellId = toInt(a, 0);
      const n = toInt(b, 1);
      return spellId === null || n === null ? null : { kind: k, report, fight, spellId, n };
    }
    case "win": {
      const n = toInt(b, 1);
      return n === null ? null : { kind: k, report, fight, key: a, n };
    }
    case "event": {
      const n = toInt(a, 0);
      return n === null ? null : { kind: k, report, fight, n };
    }
    case "pull":
      return { kind: k, report, fight };
  }
}

/** The ref string of a parsed ref (the inverse of parseRef). */
export function buildRef(ref: ParsedRef): string {
  switch (ref.kind) {
    case "plan":
      return `plan:${ref.key}:${ref.n}`;
    case "death":
    case "event":
      return `ev:${ref.report}:${ref.fight}:${ref.kind}:${ref.n}`;
    case "mech":
      return `ev:${ref.report}:${ref.fight}:mech:${ref.key}`;
    case "cd":
      return `ev:${ref.report}:${ref.fight}:cd:${ref.spellId}:${ref.n}`;
    case "win":
      return `ev:${ref.report}:${ref.fight}:win:${ref.key}:${ref.n}`;
    case "pull":
      return `ev:${ref.report}:${ref.fight}:pull`;
  }
}

/** The pull ref of a fight: what a rail row selects. */
export function pullRef(report: string, fight: number): string {
  return buildRef({ kind: "pull", report, fight });
}

/** The pull page a ref belongs to (`/r/<code>/p/<fight>`), or null for a plan ref. */
export function pullPath(ref: ParsedRef): string | null {
  return ref.kind === "plan" ? null : pullPagePath(ref.report, ref.fight);
}

/** The pull page of a report's fight. */
export function pullPagePath(report: string, fight: number): string {
  return `/r/${encodeURIComponent(report)}/p/${fight}`;
}

/** A short visible label for a chip ("death 2", "mech", "cd", "win 2", "event", "pull", "plan"). */
export function refShortLabel(ref: ParsedRef): string {
  switch (ref.kind) {
    case "death":
    case "win":
      return `${ref.kind} ${ref.n}`;
    default:
      return ref.kind;
  }
}
