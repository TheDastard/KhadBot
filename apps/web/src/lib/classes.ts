// apps/web/src/lib/classes.ts
//
// Player classes as the players route names them (`Player.class_name`, from the report actors' subType) mapped to
// their colour tokens (--cls-<class> in styles/globals.css). An unknown or missing class has no colour: the name
// shows in the text colour.

import type { Schema } from "@khadbot/api-client";

export type PlayerClass = NonNullable<Schema<"Player">["class_name"]>;

export const CLASS_TEXT: Record<PlayerClass, string> = {
  DeathKnight: "text-cls-deathknight",
  DemonHunter: "text-cls-demonhunter",
  Druid: "text-cls-druid",
  Evoker: "text-cls-evoker",
  Hunter: "text-cls-hunter",
  Mage: "text-cls-mage",
  Monk: "text-cls-monk",
  Paladin: "text-cls-paladin",
  Priest: "text-cls-priest",
  Rogue: "text-cls-rogue",
  Shaman: "text-cls-shaman",
  Warlock: "text-cls-warlock",
  Warrior: "text-cls-warrior",
};

/** The text colour class of a player class, or "" when the class is unknown. */
export function classTextClass(playerClass: string | null | undefined): string {
  if (!playerClass) return "";
  return (CLASS_TEXT as Record<string, string | undefined>)[playerClass] ?? "";
}
