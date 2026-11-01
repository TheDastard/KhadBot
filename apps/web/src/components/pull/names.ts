// apps/web/src/components/pull/names.ts
//
// Display names for the IDs the digest and the records carry. The encounter's names lookup gives mechanics, phases
// and damage windows; the cooldown registry gives spells. When a lookup is missing (an encounter without a file, a
// request that failed) the ID itself is shown, made readable ("caustic_waves" -> "Caustic waves").

import type { CooldownListData, EncounterNamesData } from "./queries";

export interface Names {
  mechanic: (id: string) => string;
  window: (id: string) => string;
  /** The phase's full name, or its ID made readable. */
  phase: (id: string | null | undefined) => string | null;
  /** The phase's short name ("Stage Three"). */
  phaseShort: (id: string | null | undefined) => string | null;
  /** A registry spell's name, or "Spell <id>". */
  spell: (id: number) => string;
  /** False when the registry says the spell's entry has not been checked against the game; null when unknown. */
  spellVerified: (id: number) => boolean | null;
  /** How many mechanics the encounter file defines; null without the lookup. */
  mechanicCount: number | null;
}

/** "caustic_waves" -> "Caustic waves". */
export function humanize(id: string): string {
  const text = id.replace(/[_-]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : id;
}

export function buildNames(
  encounter: EncounterNamesData | undefined,
  cooldowns: CooldownListData | undefined,
  extraSpells: Record<number, string> = {},
): Names {
  return {
    mechanic: (id) => encounter?.mechanics[id] ?? humanize(id),
    window: (id) => encounter?.damage_windows[id] ?? humanize(id),
    phase: (id) => (id ? (encounter?.phases[id]?.name ?? humanize(id)) : null),
    phaseShort: (id) =>
      id ? (encounter?.phases[id]?.short_name ?? encounter?.phases[id]?.name ?? humanize(id)) : null,
    spell: (id) => cooldowns?.cooldowns[String(id)]?.name ?? extraSpells[id] ?? `Spell ${id}`,
    spellVerified: (id) => {
      const entry = cooldowns?.cooldowns[String(id)];
      return entry ? entry.verified : null;
    },
    mechanicCount: encounter ? Object.keys(encounter.mechanics).length : null,
  };
}

/** WCL's melee is ability 1 and has no spell page; 0 is unknown. Only real spell IDs link to Wowhead. */
export function spellIdOf(abilityId: number | null | undefined): number | null {
  return typeof abilityId === "number" && abilityId > 1 ? abilityId : null;
}
