// apps/web/src/components/timeline/marker-tip.tsx
//
// What a marker's tooltip says (hover and keyboard focus). A cooldown's tooltip is the spell tooltip: the spell as an
// AbilityName (Wowhead's script adds the icon and its own spell tooltip when it has loaded; until then a neutral
// square and the name and spell ID), then this use: caster, time, "use n of m".

"use client";

import { AbilityName, IconFallback } from "@/components/shared/ability-name";
import { PlayerName } from "@/components/shared/player-name";
import { useWowheadReady } from "@/components/shared/wowhead";
import { formatClockTenths } from "@/lib/format";

import type { CooldownMark, Marker } from "./markers";

function SpellLine({ m }: { m: CooldownMark }) {
  const ready = useWowheadReady();
  if (ready) return <AbilityName spellId={m.spellId} name={m.spell} />;
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <IconFallback size={16} />
      <b className="font-semibold">{m.spell}</b>
      <span className="font-mono text-[11px] opacity-70">{m.spellId}</span>
    </span>
  );
}

export function MarkerTip({ marker: m, code }: { marker: Marker; code?: string }) {
  switch (m.lane) {
    case "death":
      return (
        <div className="flex flex-col gap-0.5">
          <b className="font-semibold">
            Death {m.n} · {code ? <PlayerName name={m.player} code={code} className="font-semibold" /> : m.player}
          </b>
          <span>
            {formatClockTenths(m.tMs)} · {m.afterCutoff ? "after cutoff" : "before cutoff"}
          </span>
          <span className="opacity-75">Enter opens the death moment</span>
        </div>
      );
    case "mechanic":
      return (
        <div className="flex flex-col gap-0.5">
          <b className="font-semibold">{m.name}</b>
          <span>
            {formatClockTenths(m.tMs)}
            {m.endMs > m.tMs ? ` – ${formatClockTenths(m.endMs)}` : ""}
          </span>
          <span>
            {m.hits} hit{m.hits === 1 ? "" : "s"} · {m.failures === 0 ? "no failures" : `${m.failures} failure${m.failures === 1 ? "" : "s"}`}
          </span>
        </div>
      );
    case "cooldown":
      return (
        <div className="flex flex-col gap-2 text-[13px]">
          <SpellLine m={m} />
          <span className="flex flex-col gap-0.5 border-t border-line pt-1.5 text-xs">
            <span>
              {code ? <PlayerName name={m.caster} code={code} /> : m.caster} · {formatClockTenths(m.tMs)} · use {m.useN}
              {m.useOf ? ` of ${m.useOf}` : ""}
            </span>
            <span className="text-mut">{m.category} cooldown</span>
          </span>
        </div>
      );
    case "window":
      return (
        <div className="flex flex-col gap-0.5">
          <b className="font-semibold">
            {m.name} {m.occurrence}
          </b>
          <span>
            {formatClockTenths(m.tMs)} · {m.covered ? "Covered" : "Missed"}
          </span>
        </div>
      );
  }
}
