// apps/web/src/components/raids/guild/guild-header.tsx
//
// The guild header (canvas component "Guild header"): the guild's name, "Server (Region)" and its faction in the
// faction's colour. The crest is not drawn: no game art is served, so the header has none (the canvas's fallback).

import type { ApiGet } from "@khadbot/api-client";

import { cn } from "@/lib/utils";

type Guild = ApiGet<"/v1/search/guild">["guild"];

const FACTION_COLOUR: Record<string, string> = { horde: "text-faction-horde", alliance: "text-faction-alliance" };

export function GuildHeader({ guild }: { guild: Guild }) {
  const faction = guild.faction;
  return (
    <div data-testid="guild-header" className="flex items-center gap-3 rounded-sm border border-line bg-s1 px-4 py-3">
      <span className="flex min-w-0 flex-col">
        <b className="font-display text-lg leading-tight font-semibold">{guild.name}</b>
        <span className="text-[13px] text-mut">
          {guild.server_name} ({guild.region})
          {faction ? (
            <>
              {" · "}
              <span className={cn("font-semibold", FACTION_COLOUR[faction.toLowerCase()] ?? "text-ink")}>{faction}</span>
            </>
          ) : null}
        </span>
      </span>
    </div>
  );
}
