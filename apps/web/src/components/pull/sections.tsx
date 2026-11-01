// apps/web/src/components/pull/sections.tsx
//
// The main column's table sections, as the canvas's "Pull investigation" artboard draws them: Deaths, Mechanics,
// Damage windows and Cooldown uses. Each row ends in an evidence chip that opens its record in the context panel.
// Rows after the death cutoff are muted; the selected row has the accent tint.

"use client";

import { AbilityName } from "@/components/shared/ability-name";
import { PlayerName } from "@/components/shared/player-name";
import { StatusBadge } from "@/components/shared/status-badge";
import { useFocus } from "@/lib/focus";
import { formatClockTenths, formatNumber } from "@/lib/format";
import type { PullDigestData } from "@/lib/queries";
import { cn } from "@/lib/utils";

import { allCooldownUses, deathNumber, topCharged } from "./logic";
import type { Names } from "./names";
import { spellIdOf } from "./names";
import { RefChip } from "./ref-chip";
import { Mono, SectionCard, TableBox, Th } from "./section-card";

interface SectionProps {
  code: string;
  digest: PullDigestData;
  names: Names;
}

// ---------------------------------------------------------------------------
// Deaths
// ---------------------------------------------------------------------------

export function DeathsSection({
  code,
  digest,
  names,
  total,
  before,
  full,
}: SectionProps & { total: number | null; before: number | null; full: boolean }) {
  const { ref: selected } = useFocus();
  const shown = digest.deaths.length;
  const note =
    total === null
      ? `${shown} shown`
      : full
        ? `${total}${before !== null ? ` · ${before} before the cutoff` : ""}`
        : shown < total
          ? `${shown} of ${total} · before the cutoff first`
          : `${total}`;
  return (
    <SectionCard title="Deaths" note={note} testId="section-deaths">
      <TableBox minWidth={560} label="Deaths">
        <thead>
          <tr>
            <Th>#</Th>
            <Th>Time</Th>
            <Th>Player</Th>
            <Th>Killed by</Th>
            <Th>Mechanics in the last 10 s</Th>
            <Th hidden>Evidence</Th>
          </tr>
        </thead>
        <tbody>
          {digest.deaths.map((d) => {
            const isSelected = selected === d.ref;
            return (
              <tr
                key={d.ref}
                data-after-cutoff={d.after_cutoff || undefined}
                aria-selected={isSelected || undefined}
                className={cn("border-t border-line align-top", d.after_cutoff && "text-mut", isSelected && "bg-acct")}
              >
                <td className="px-2 py-1.5 font-mono text-mut">{deathNumber(d.ref) ?? ""}</td>
                <td className="px-2 py-1.5 font-mono">{formatClockTenths(d.t_ms)}</td>
                <td className="px-2 py-1.5 font-medium">
                  <PlayerName name={d.name} code={code} />
                </td>
                <td className="px-2 py-1.5">
                  {d.killing_ability_name ? (
                    <AbilityName spellId={spellIdOf(d.killing_ability_id)} name={d.killing_ability_name} />
                  ) : (
                    "Unknown"
                  )}
                  <span className="block text-xs text-mut">{d.killer_name ?? ""}</span>
                </td>
                <td className="px-2 py-1.5 text-mut">
                  {d.mechanic_ids.length ? d.mechanic_ids.map((m) => names.mechanic(m)).join(", ") : "—"}
                </td>
                <td className="px-2 py-1.5 text-right">
                  <RefChip evidenceRef={d.ref} label="death" />
                </td>
              </tr>
            );
          })}
        </tbody>
      </TableBox>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Mechanics
// ---------------------------------------------------------------------------

export function MechanicsSection({ code, digest, names }: SectionProps) {
  const active = digest.mechanics.filter((m) => m.hits > 0 || m.failures > 0);
  const note = `${active.length}${names.mechanicCount !== null ? ` of ${names.mechanicCount}` : ""} active · by failures, then damage`;
  return (
    <SectionCard title="Mechanics" note={note} testId="section-mechanics">
      <TableBox minWidth={620} label="Mechanics">
        <thead>
          <tr>
            <Th>Mechanic</Th>
            <Th right>Failures</Th>
            <Th right>Hits</Th>
            <Th right>Damage</Th>
            <Th>Most charged</Th>
            <Th hidden>Evidence</Th>
          </tr>
        </thead>
        <tbody>
          {active.map((m) => {
            const top = topCharged(m.failures_by_player);
            return (
              <tr key={m.ref} className="border-t border-line">
                <th scope="row" className="px-2 py-1.5 text-left font-medium">
                  {names.mechanic(m.mechanic_id)}
                </th>
                <td className={cn("px-2 py-1.5 text-right font-semibold", m.failures > 0 ? "text-bad" : "text-mut")}>
                  {m.failures}
                </td>
                <td className="px-2 py-1.5 text-right">{m.hits}</td>
                <td className="px-2 py-1.5 text-right">{formatNumber(m.damage_taken)}</td>
                <td className="px-2 py-1.5 text-mut">
                  {top ? (
                    <>
                      <PlayerName name={top.name} code={code} /> {top.failures}
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-2 py-1.5 text-right">
                  <RefChip evidenceRef={m.ref} label="mech" />
                </td>
              </tr>
            );
          })}
        </tbody>
      </TableBox>
      {active.length === 0 ? <p className="m-0 text-[13px] text-mut">No mechanic was hit on this pull.</p> : null}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Damage windows
// ---------------------------------------------------------------------------

export function WindowsSection({ digest, names }: SectionProps) {
  return (
    <SectionCard title="Damage windows" testId="section-windows">
      {digest.coverage_windows.length === 0 ? (
        <p className="m-0 text-[13px] text-mut">This pull has no damage windows in the encounter file.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col p-0">
          {digest.coverage_windows.map((w) => {
            const active = w.defensive_cooldowns_active.map((id) => names.spell(id));
            const late = (w.defensive_cooldowns_late ?? []).map(
              (l) => `${names.spell(l.spell_id)} was ${(l.late_ms / 1000).toFixed(1)} s late`,
            );
            const detail = [
              active.length ? `${active.join(", ")} active` : "No raid cooldown active",
              `expected ${w.expected_defensive_cooldowns}`,
              ...late,
            ].join(" · ");
            return (
              <li key={w.ref} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-line py-2">
                <Mono className="w-14 text-[13px]">{formatClockTenths(w.t_ms)}</Mono>
                <span className="min-w-[180px] flex-1 basis-[180px] font-medium">
                  {names.window(w.window_id)} #{w.occurrence}
                </span>
                <StatusBadge status={w.covered ? "covered" : "missed"} />
                <span className="min-w-[180px] flex-1 basis-[180px] text-[13px] text-mut">{detail}</span>
                <RefChip evidenceRef={w.ref} label="window" />
              </li>
            );
          })}
        </ul>
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Cooldown uses
// ---------------------------------------------------------------------------

export function CooldownsSection({ code, digest }: SectionProps) {
  const uses = allCooldownUses(digest);
  return (
    <SectionCard title="Cooldown uses" note={`${uses.length} · registry spells only`} testId="section-cooldowns">
      {uses.length === 0 ? (
        <p className="m-0 text-[13px] text-mut">No registry cooldown was used on this pull.</p>
      ) : (
        <div className="@container">
          <ul className="m-0 grid list-none grid-cols-1 gap-x-6 p-0 @[560px]:grid-cols-2">
            {uses.map((c) => (
              <li
                key={c.ref}
                className="grid grid-cols-[58px_minmax(0,1fr)_auto] items-center gap-2 border-t border-line py-1.5 text-[13px]"
              >
                <Mono>{formatClockTenths(c.t_ms)}</Mono>
                <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
                  <AbilityName spellId={spellIdOf(c.spell_id)} name={c.spell_name} className="font-medium" />
                  <PlayerName name={c.caster} code={code} />
                </span>
                <RefChip evidenceRef={c.ref} label="cd" />
              </li>
            ))}
          </ul>
        </div>
      )}
    </SectionCard>
  );
}
