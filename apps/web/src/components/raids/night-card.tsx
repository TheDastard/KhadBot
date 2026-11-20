// apps/web/src/components/raids/night-card.tsx
//
// One night: the title, the report code in mono, "date · N boss pulls", then one encounter row per encounter and
// difficulty. Guild and character results reuse it.

"use client";

import { useBossArt } from "@/components/shared/boss-art";

import { EncounterRow } from "./encounter-row";
import { nightMeta, type Night, type NightGroup } from "./night";

function GroupRow({ code, group }: { code: string; group: NightGroup }) {
  return <EncounterRow code={code} group={group} portraitSrc={useBossArt(group.encounterId).portraitSrc} />;
}

/** `meta` replaces the "date · N boss pulls" line (the character search words it "… pulls with <name>"). */
export function NightCard({ night, meta }: { night: Night; meta?: string }) {
  return (
    <article data-testid="night-card" data-code={night.code} className="rounded-sm border border-line bg-s1">
      <header className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5 px-4 py-2.5">
        <div className="flex min-w-0 flex-1 basis-64 flex-col">
          <span className="flex flex-wrap items-baseline gap-x-2.5">
            {night.title ? <b className="text-[15px] font-semibold">{night.title}</b> : null}
            <span className="font-mono text-xs text-mut">{night.code}</span>
          </span>
          <span className="text-xs text-mut">{meta ?? nightMeta(night)}</span>
        </div>
        {/* Slot: night-overview. The night overview's button returns here with that screen; nothing is drawn now. */}
      </header>
      {night.groups.map((group) => (
        <GroupRow key={`${group.encounterId}:${group.difficulty}`} code={night.code} group={group} />
      ))}
    </article>
  );
}
