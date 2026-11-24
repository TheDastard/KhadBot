// apps/web/src/components/pull/names.ts
//
// Display names for the IDs the digest and the records carry. The encounter's names lookup gives mechanics, phases
// and damage windows; the cooldown registry gives spells. When a lookup is missing (an encounter without a file, a
// request that failed) the ID itself is shown, made readable ("caustic_waves" -> "Caustic waves"). The lookup also
// gives each mechanic's description, failure rule in plain words and abilities (`mechanicFacts`), and which mechanic
// an ability belongs to (`mechanicOfAbility`: an ability is in one mechanic per difficulty, which the loader checks).

import type { MechanicFacts } from "@/components/shared/mechanic-tip";

import type { CooldownListData, EncounterNamesData } from "./queries";

export interface Names {
  mechanic: (id: string) => string;
  /** False when nobody has yet checked the encounter file's entry against the logs; null when unknown. */
  mechanicVerified: (id: string) => boolean | null;
  /** Everything the lookup says about a mechanic; the name alone without it. */
  mechanicFacts: (id: string) => MechanicFacts;
  /** The mechanic an ability (by spell ID) belongs to, or null. */
  mechanicOfAbility: (abilityId: number | null | undefined) => string | null;
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
  const byAbility = new Map<number, string>();
  for (const [id, info] of Object.entries(encounter?.mechanic_info ?? {})) {
    for (const ability of info.ability_ids) if (!byAbility.has(ability)) byAbility.set(ability, id);
  }
  const mechanic = (id: string) => encounter?.mechanics[id] ?? humanize(id);
  return {
    mechanic,
    mechanicVerified: (id) => encounter?.mechanic_verified[id] ?? null,
    mechanicFacts: (id) => {
      const info = encounter?.mechanic_info?.[id];
      return {
        id,
        name: mechanic(id),
        description: encounter?.mechanic_descriptions?.[id] ?? null,
        failureSummary: info?.failure_summary ?? null,
        abilityIds: info?.ability_ids ?? [],
        iconAbilityId: info?.icon_ability_id ?? null,
        kind: info?.kind ?? null,
        severity: info?.severity ?? null,
      };
    },
    mechanicOfAbility: (abilityId) => (typeof abilityId === "number" ? (byAbility.get(abilityId) ?? null) : null),
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
