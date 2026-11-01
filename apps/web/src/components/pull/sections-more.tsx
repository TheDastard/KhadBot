// apps/web/src/components/pull/sections-more.tsx
//
// The rest of the main column: Aggro and Unregistered abilities (side by side), Anomalies (one folded line per kind,
// review answer 6) and the pull's cached findings with their confidence and "Out of date" (review answer 12).

"use client";

import { AbilityName } from "@/components/shared/ability-name";
import { PlayerName } from "@/components/shared/player-name";
import { StatusBadge } from "@/components/shared/status-badge";
import { formatNumber, formatPct } from "@/lib/format";
import type { PullDigestData } from "@/lib/queries";
import { parseRef, refShortLabel } from "@/lib/refs";

import { describeAnomaly, foldAnomalies } from "./logic";
import type { Names } from "./names";
import { spellIdOf } from "./names";
import type { FindingsData } from "./queries";
import { RefChip } from "./ref-chip";
import { Mono, SectionCard } from "./section-card";

// ---------------------------------------------------------------------------
// Aggro and unregistered abilities
// ---------------------------------------------------------------------------

export function AggroSection({ code, digest }: { code: string; digest: PullDigestData }) {
  const aggro = digest.aggro ?? [];
  return (
    <SectionCard title="Aggro" testId="section-aggro">
      {aggro.length === 0 ? (
        <p className="m-0 text-[13px] text-mut">No enemy swings were recorded on a player.</p>
      ) : (
        aggro.map((a) => (
          <div key={a.npc} className="flex flex-col gap-0.5 border-t border-line pt-1.5 text-[13px]">
            <b className="font-semibold">
              {a.npc} · {a.swings} swings
            </b>
            <span>
              {a.targets.map((t, i) => (
                <span key={t.name}>
                  {i > 0 ? " · " : ""}
                  <PlayerName name={t.name} code={code} /> {formatPct(t.share_pct)}
                </span>
              ))}
            </span>
            {Object.keys(a.taunts).length > 0 ? (
              <span className="text-mut">
                Taunts:{" "}
                {Object.entries(a.taunts).map(([name, n], i) => (
                  <span key={name}>
                    {i > 0 ? ", " : ""}
                    <PlayerName name={name} code={code} /> {n}
                  </span>
                ))}
              </span>
            ) : null}
          </div>
        ))
      )}
    </SectionCard>
  );
}

export function UnregisteredSection({ digest }: { digest: PullDigestData }) {
  return (
    <SectionCard title="Unregistered abilities" testId="section-unregistered">
      <span className="text-xs text-mut">Damage taken that no encounter-file entry claims</span>
      {digest.unregistered.length === 0 ? (
        <p className="m-0 text-[13px] text-mut">Nothing: every damage source on this pull is claimed.</p>
      ) : (
        digest.unregistered.map((u) => (
          <div
            key={u.ability_id}
            className="grid grid-cols-[minmax(0,1fr)_auto_52px] gap-2 border-t border-line py-1 text-[13px]"
          >
            <span>
              <AbilityName spellId={spellIdOf(u.ability_id)} name={u.name ?? "Unknown ability"} />{" "}
              <Mono className="text-[11px] text-mut">{u.ability_id}</Mono>
            </span>
            <span className="text-right">{formatNumber(u.damage)}</span>
            <span className="text-right text-mut">{formatPct(u.share_pct)}</span>
          </div>
        ))
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Anomalies
// ---------------------------------------------------------------------------

export function AnomaliesSection({ digest, names }: { digest: PullDigestData; names: Names }) {
  const groups = foldAnomalies(digest.anomalies);
  const spellNames: Record<number, string> = {};
  for (const c of [...digest.defensive_cooldowns, ...(digest.offensive_cooldowns ?? []), ...(digest.utility_cooldowns ?? [])]) {
    spellNames[c.spell_id] = c.spell_name;
  }
  return (
    <SectionCard title="Anomalies" testId="section-anomalies">
      {groups.length === 0 ? (
        <p className="m-0 text-[13px] text-mut">None: this pull&apos;s data looks complete.</p>
      ) : (
        groups.map((g) => {
          const text = describeAnomaly(g);
          const spells = text.spells
            ? g.args.map((a) => {
                const id = Number(a);
                return Number.isFinite(id) ? `${names.spell(id) !== `Spell ${id}` ? names.spell(id) : (spellNames[id] ?? `Spell ${id}`)} (${id})` : a;
              })
            : [];
          return (
            <details key={g.kind} data-anomaly={g.kind} className="text-sm">
              <summary className="cursor-pointer">
                <b className="font-semibold">{text.title}</b>{" "}
                <Mono className="text-xs text-mut">{g.kind}</Mono>
              </summary>
              <p className="mt-1.5 mb-0 text-mut">
                {text.body}
                {spells.length ? ` ${spells.join(", ")}.` : ""}
              </p>
            </details>
          );
        })
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Findings
// ---------------------------------------------------------------------------

export function FindingsSection({ findings, loading }: { findings: FindingsData | undefined; loading: boolean }) {
  const entries = (findings?.findings ?? []).filter((f) => f.findings.length > 0);
  return (
    <SectionCard title="Findings" testId="section-findings">
      {entries.length === 0 ? (
        <p className="m-0 text-[13px] text-mut">{loading ? "Looking for cached findings…" : "No cached findings for this pull."}</p>
      ) : (
        entries.map((entry) => (
          <div key={entry.cache_key} className="flex flex-col gap-2">
            {entry.stale ? (
              <p className="m-0 flex flex-wrap items-center gap-2 text-[13px] text-mut">
                <StatusBadge status="out-of-date" />
                Out of date: the data or encounter file changed since this was cached.
              </p>
            ) : null}
            {entry.findings.map((f) => (
              <article
                key={f.id}
                className="flex flex-col gap-1.5 rounded-sm border border-line bg-s2 px-3 py-2.5 text-sm"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={`confidence-${f.confidence}`} />
                  {f.scope === "cross_pull" ? <span className="text-xs text-mut">Across pulls</span> : null}
                </div>
                <p className="m-0">{f.claim}</p>
                <div className="flex flex-wrap gap-1.5">
                  {f.refs.map((r) => {
                    const parsed = parseRef(r);
                    return <RefChip key={r} evidenceRef={r} label={parsed ? refShortLabel(parsed) : r} />;
                  })}
                </div>
              </article>
            ))}
            {entry.unknowns.length ? (
              <p className="m-0 text-xs text-mut">Not known: {entry.unknowns.join(" · ")}</p>
            ) : null}
          </div>
        ))
      )}
    </SectionCard>
  );
}
