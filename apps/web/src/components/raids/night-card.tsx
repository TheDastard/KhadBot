// apps/web/src/components/raids/night-card.tsx
//
// One night: the title, the report code in mono, "date · N boss pulls", then one encounter row per encounter and
// difficulty. Guild and character results reuse it.

import { EncounterRow } from "./encounter-row";
import { nightMeta, type Night } from "./night";

export function NightCard({ night }: { night: Night }) {
  return (
    <article data-testid="night-card" data-code={night.code} className="rounded-sm border border-line bg-s1">
      <header className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5 px-4 py-2.5">
        <div className="flex min-w-0 flex-1 basis-64 flex-col">
          <span className="flex flex-wrap items-baseline gap-x-2.5">
            {night.title ? <b className="text-[15px] font-semibold">{night.title}</b> : null}
            <span className="font-mono text-xs text-mut">{night.code}</span>
          </span>
          <span className="text-xs text-mut">{nightMeta(night)}</span>
        </div>
        {/* Slot: night-overview. The night overview's button returns here with that screen; nothing is drawn now. */}
      </header>
      {night.groups.map((group) => (
        <EncounterRow key={`${group.encounterId}:${group.difficulty}`} code={night.code} group={group} />
      ))}
    </article>
  );
}
