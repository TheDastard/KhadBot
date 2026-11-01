// apps/web/src/components/shared/player-name.tsx
//
// A player's name in their class colour (--cls-<class>). Pass the class when you have it; otherwise pass the report
// code and the class comes from `GET /v1/reports/{code}/players` (one cached request per report). An unknown class
// shows the name in the text colour.

"use client";

import { classTextClass } from "@/lib/classes";
import { usePlayers } from "@/lib/queries";
import { cn } from "@/lib/utils";

export interface PlayerNameProps {
  name: string;
  /** `Player.class_name`; null when the report doesn't say. Omit it to look the class up by `code`. */
  playerClass?: string | null;
  /** The report, for the lookup when `playerClass` is omitted. */
  code?: string;
  className?: string;
}

export function PlayerName({ name, playerClass, code, className }: PlayerNameProps) {
  const lookup = usePlayers(playerClass === undefined ? code : undefined);
  const resolved =
    playerClass !== undefined ? playerClass : (lookup.data?.players.find((p) => p.name === name)?.class_name ?? null);
  return (
    <span data-class={resolved ?? undefined} className={cn("font-medium", classTextClass(resolved), className)}>
      {name}
    </span>
  );
}
