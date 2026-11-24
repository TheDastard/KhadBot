// apps/web/src/components/timeline/marker-tip.tsx
//
// What a marker's tooltip says (hover and keyboard focus). A cooldown's tooltip is the spell tooltip: the spell as an
// AbilityName (Wowhead's script adds the icon and its own spell tooltip when it has loaded; until then a neutral
// square and the name and spell ID), then this use: caster, time, "use n of m". A mechanic marker's tooltip is
// KhadBot's card for the mechanic (icon, name, kind and severity, its description) above this occurrence (time and
// phase, hits and failures); without a description it is the compact ink tooltip with the occurrence only.

"use client";

import { AbilityName, IconFallback } from "@/components/shared/ability-name";
import { MechanicIcon, type MechanicFacts } from "@/components/shared/mechanic-tip";
import { PlayerName } from "@/components/shared/player-name";
import { useWowheadReady } from "@/components/shared/wowhead";
import { formatClockTenths } from "@/lib/format";

import type { CooldownMark, Marker, MechanicMark } from "./markers";

/** What a marker's tooltip may add from outside the timeline: the mechanic's facts and the marker's phase. */
export interface MarkerContext {
  mechanic?: MechanicFacts;
  phase?: string | null;
}

/** True when a mechanic marker gets the card (it has a description to show). */
export function mechanicCard(mechanic: MechanicFacts | undefined): mechanic is MechanicFacts {
  return !!mechanic?.description;
}

const KIND: Record<string, string> = {
  avoidable: "Avoidable",
  soak: "Soak",
  interrupt: "Interrupt",
  dispel: "Dispel",
  debuff: "Debuff",
  unavoidable: "Unavoidable",
};

function Occurrence({ m, phase }: { m: MechanicMark; phase?: string | null }) {
  return (
    <>
      <span className="font-mono">
        {formatClockTenths(m.tMs)}
        {m.endMs > m.tMs ? ` – ${formatClockTenths(m.endMs)}` : ""}
        {phase ? ` · ${phase}` : ""}
      </span>
      <span>
        {m.hits} hit{m.hits === 1 ? "" : "s"} ·{" "}
        {m.failures === 0 ? "no failures" : `${m.failures} failure${m.failures === 1 ? "" : "s"}`}
      </span>
    </>
  );
}

const TAG = "rounded-sm border border-line bg-s2 px-2 text-xs";

function MechanicCard({ m, mechanic, phase }: { m: MechanicMark; mechanic: MechanicFacts; phase?: string | null }) {
  return (
    <div className="flex flex-col gap-[7px] text-[13px]" data-mechanic-tip={mechanic.id}>
      <span className="flex items-center gap-1.5 font-semibold">
        <MechanicIcon facts={mechanic} />
        {mechanic.name}
        {m.clusterOf > 1 ? (
          <span className="ml-auto text-xs font-normal text-mut">
            {m.clusterN} of {m.clusterOf}
          </span>
        ) : null}
      </span>
      {mechanic.kind || mechanic.severity ? (
        <span className="flex flex-wrap gap-1">
          {mechanic.kind ? <span className={TAG}>{KIND[mechanic.kind] ?? mechanic.kind}</span> : null}
          {mechanic.severity ? (
            <span className={mechanic.severity === "high" ? "rounded-sm bg-warnt px-2 text-xs font-medium text-warn" : TAG}>
              Severity {mechanic.severity}
            </span>
          ) : null}
        </span>
      ) : null}
      <span className="leading-snug">{mechanic.description}</span>
      <span className="flex flex-col gap-0.5 border-t border-line pt-[7px] text-xs">
        <Occurrence m={m} phase={phase} />
        <span className="text-mut">Enter opens the mechanic</span>
      </span>
    </div>
  );
}

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

export function MarkerTip({ marker: m, code, context }: { marker: Marker; code?: string; context?: MarkerContext }) {
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
      if (mechanicCard(context?.mechanic)) return <MechanicCard m={m} mechanic={context.mechanic} phase={context.phase} />;
      return (
        <div className="flex flex-col gap-0.5">
          <b className="font-semibold">{m.name}</b>
          <Occurrence m={m} phase={context?.phase} />
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
