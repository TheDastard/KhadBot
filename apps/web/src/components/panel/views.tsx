// apps/web/src/components/panel/views.tsx
//
// One renderer per evidence kind, from the canvas's "Context panel, every ref kind" artboard: the death moment, the
// mechanic card, a cooldown use, a damage window's coverage, an event row and the pull summary, plus the plan, error
// and loading views. A renderer takes the record and a ViewContext (the pull's names) and draws a PanelLayout; none of
// them knows the panel's frame (see parts.tsx).

"use client";

import { AbilityName } from "@/components/shared/ability-name";
import { EvidenceChip } from "@/components/shared/evidence-chip";
import { PlayerName } from "@/components/shared/player-name";
import { StatusBadge } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import { spellIdOf, type Names } from "@/components/pull/names";
import { useLazyDigest, type EvidenceData } from "@/components/pull/queries";
import { windowAround, useFocus } from "@/lib/focus";
import { formatClockTenths, formatNumber, formatPct } from "@/lib/format";
import { buildRef, parseRef } from "@/lib/refs";
import { cn } from "@/lib/utils";
import { SkeletonBlock } from "@/components/shared/skeletons";
import { AlertTriangleIcon } from "lucide-react";

import { Muted, PanelLayout, Row, Rows, Tag } from "./parts";

export type EvidenceKindName = EvidenceData["kind"];
export type RecordOf<K extends EvidenceKindName> = Extract<EvidenceData, { kind: K }>["record"];

/** What every renderer receives besides its record. */
export interface ViewProps<K extends EvidenceKindName> {
  record: RecordOf<K>;
  evidenceRef: string;
  /** The report and fight the ref belongs to. */
  code: string;
  fight: number;
  names: Names;
  onClose?: () => void;
}

/** m:ss.mmm, for a single event's exact time. */
export function formatClockMillis(ms: number): string {
  const total = Math.max(0, Math.round(ms));
  const minutes = Math.floor(total / 60_000);
  const seconds = Math.floor((total % 60_000) / 1000);
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(total % 1000).padStart(3, "0")}`;
}

function Ability({ id, name }: { id: number | null | undefined; name: string | null | undefined }) {
  return name ? <AbilityName spellId={spellIdOf(id)} name={name} /> : <span>Unknown ability</span>;
}

function MechChip({ code, fight, id, names }: { code: string; fight: number; id: string; names: Names }) {
  return (
    <EvidenceChip
      evidenceRef={buildRef({ kind: "mech", report: code, fight, key: id })}
      label={names.mechanic(id)}
    />
  );
}

// ---------------------------------------------------------------------------
// Death moment
// ---------------------------------------------------------------------------

export function DeathView({ record: d, evidenceRef, code, fight, names, onClose }: ViewProps<"death">) {
  const { setWindow } = useFocus();
  const n = /:death:(\d+)$/.exec(evidenceRef)?.[1];
  const seconds = (d.window_ms ?? 10_000) / 1000;
  return (
    <PanelLayout
      kind="death"
      evidenceRef={evidenceRef}
      onClose={onClose}
      title={
        <>
          Death {n} · <PlayerName name={d.name} code={code} />
        </>
      }
    >
      <div className="flex flex-wrap gap-1.5">
        <Tag>{formatClockTenths(d.t_ms)}</Tag>
        {d.phase_id ? <Tag>{names.phaseShort(d.phase_id)}</Tag> : null}
        <Tag tone={d.after_cutoff ? "plain" : "bad"}>{d.after_cutoff ? "After cutoff" : "Before cutoff"}</Tag>
      </div>
      <Rows>
        <Row label="Killed by" strong>
          <Ability id={d.killing_ability_id} name={d.killing_ability_name} />
        </Row>
        {d.killer_name ? <Row label="Killer">{d.killer_name}</Row> : null}
        {d.overkill !== null ? <Row label="Overkill">{formatNumber(d.overkill)}</Row> : null}
        {d.time_to_die_ms !== null ? <Row label="Time to die">{(d.time_to_die_ms / 1000).toFixed(1)} s</Row> : null}
        <Row label={`Damage taken, last ${seconds} s`}>{formatNumber(d.window_damage)}</Row>
        {d.window_healing !== null ? (
          <Row label={`Healing received, last ${seconds} s`}>{formatNumber(d.window_healing)}</Row>
        ) : null}
        <Row label="Defensives active">
          {d.active_defensives.length ? d.active_defensives.map((id) => names.spell(id)).join(", ") : "None"}
        </Row>
      </Rows>
      {d.top_hits.length ? (
        <div className="flex flex-col gap-1">
          <Muted>Top hits</Muted>
          {d.top_hits.map((h, i) => (
            <div key={i} className="grid grid-cols-[52px_minmax(0,1fr)_auto] gap-2 border-t border-line py-1 text-[13px]">
              <span className="font-mono">{formatClockTenths(h.t_ms)}</span>
              <span>
                <Ability id={h.ability_id} name={h.ability_name} />{" "}
                <span className="text-mut">{h.source_name ?? ""}</span>
              </span>
              <span className="text-right font-medium">{formatNumber(h.amount)}</span>
            </div>
          ))}
        </div>
      ) : null}
      {d.mechanic_ids.length ? (
        <div className="flex flex-col gap-1.5">
          <Muted>
            Mechanics that hit <PlayerName name={d.name} code={code} /> in the last {seconds} s
          </Muted>
          <span className="flex flex-wrap gap-1.5">
            {d.mechanic_ids.map((m) => (
              <MechChip key={m} code={code} fight={fight} id={m} names={names} />
            ))}
          </span>
        </div>
      ) : null}
      <Button variant="secondary" size="sm" className="self-start" onClick={() => setWindow(windowAround(d.t_ms))}>
        Zoom the timeline to 10 s around this death
      </Button>
    </PanelLayout>
  );
}

// ---------------------------------------------------------------------------
// Mechanic card
// ---------------------------------------------------------------------------

export function MechanicView({ record: m, evidenceRef, code, names, onClose }: ViewProps<"mech">) {
  const charged = Object.entries(m.failures_by_player)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const tiles: Array<[string, string, boolean]> = [
    [String(m.failures), "failures", m.failures > 0],
    [String(m.hits), `hits in ${m.clusters} ${m.clusters === 1 ? "cluster" : "clusters"}`, false],
    [String(m.deaths_in_window), "deaths in window", false],
  ];
  return (
    <PanelLayout kind="mech" evidenceRef={evidenceRef} onClose={onClose} title={names.mechanic(m.mechanic_id)}>
      <div className="grid grid-cols-3 gap-2 text-center">
        {tiles.map(([value, label, bad]) => (
          <div key={label} className="rounded-sm bg-s2 p-1.5">
            <b className={cn("block text-lg", bad && "text-bad")}>{value}</b>
            <span className="text-[11px] text-mut">{label}</span>
          </div>
        ))}
      </div>
      <Muted>
        Damage taken {formatNumber(m.damage_taken)}
        {charged.length ? " · Failures charged: " : ""}
        {charged.map(([name, n], i) => (
          <span key={name}>
            {i > 0 ? ", " : ""}
            <PlayerName name={name} code={code} /> {n}
          </span>
        ))}
      </Muted>
      {m.cluster_rows.length ? (
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Clusters">
          <table className="w-full min-w-[300px] border-collapse text-xs">
            <thead>
              <tr className="text-left text-mut">
                <th scope="col" className="px-1.5 py-1 font-semibold">#</th>
                <th scope="col" className="px-1.5 py-1 font-semibold">Time</th>
                <th scope="col" className="px-1.5 py-1 text-right font-semibold">Hits</th>
                <th scope="col" className="px-1.5 py-1 text-right font-semibold">Fail</th>
                <th scope="col" className="px-1.5 py-1 font-semibold">Failed</th>
              </tr>
            </thead>
            <tbody>
              {m.cluster_rows.map((c) => (
                <tr key={c.cluster} className="border-t border-line align-top">
                  <td className="px-1.5 py-1">{c.cluster}</td>
                  <td className="px-1.5 py-1 font-mono whitespace-nowrap">
                    {formatClockTenths(c.t_ms)}–{formatClockTenths(c.end_ms)}
                  </td>
                  <td className="px-1.5 py-1 text-right">{c.hits}</td>
                  <td className={cn("px-1.5 py-1 text-right font-semibold", c.failures > 0 ? "text-bad" : "text-mut")}>
                    {c.failures}
                  </td>
                  <td className="px-1.5 py-1 text-mut">
                    {c.failed_players.map((p, i) => (
                      <span key={p}>
                        {i > 0 ? ", " : ""}
                        <PlayerName name={p} code={code} />
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </PanelLayout>
  );
}

// ---------------------------------------------------------------------------
// Cooldown use
// ---------------------------------------------------------------------------

export function CooldownView({ record: c, evidenceRef, code, fight, names, onClose }: ViewProps<"cd">) {
  const parsed = parseRef(evidenceRef);
  const thisUse = parsed?.kind === "cd" ? parsed.n : null;
  const digest = useLazyDigest(code, fight, true, true);
  const all = [
    ...(digest.data?.defensive_cooldowns ?? []),
    ...(digest.data?.offensive_cooldowns ?? []),
    ...(digest.data?.utility_cooldowns ?? []),
  ].filter((u) => u.spell_id === c.spell_id && u.ref !== evidenceRef);
  const verified = names.spellVerified(c.spell_id);
  return (
    <PanelLayout
      kind="cd"
      evidenceRef={evidenceRef}
      onClose={onClose}
      title={`${c.spell_name}${thisUse ? ` · use ${thisUse}` : ""}`}
    >
      <Rows>
        <Row label="Caster" strong>
          <PlayerName name={c.caster} code={code} />
        </Row>
        <Row label="Time · phase">
          {formatClockTenths(c.t_ms)}
          {c.phase_id ? ` · ${names.phaseShort(c.phase_id)}` : ""}
        </Row>
        <Row label="Spell">
          <AbilityName spellId={spellIdOf(c.spell_id)} name={c.spell_name} />{" "}
          <span className="font-mono text-[11px] text-mut">{c.spell_id}</span>
        </Row>
      </Rows>
      {verified === false ? (
        <span className="inline-flex items-center gap-1.5 rounded-sm bg-warnt px-2 py-1.5 text-xs text-warn">
          <AlertTriangleIcon className="size-3.5" aria-hidden />
          Registry entry not yet verified
        </span>
      ) : null}
      {all.length ? (
        <Muted>
          Other uses of this spell:{" "}
          {all
            .map((u) => {
              const p = parseRef(u.ref);
              return `${formatClockTenths(u.t_ms)}${p?.kind === "cd" ? ` (use ${p.n})` : ""}`;
            })
            .join(", ")}
        </Muted>
      ) : null}
    </PanelLayout>
  );
}

// ---------------------------------------------------------------------------
// Damage window coverage
// ---------------------------------------------------------------------------

export function WindowView({ record: w, evidenceRef, code, fight, names, onClose }: ViewProps<"win">) {
  const digest = useLazyDigest(code, fight, true, true);
  const others = (digest.data?.coverage_windows ?? []).filter((o) => o.window_id === w.window_id && o.ref !== evidenceRef);
  return (
    <PanelLayout kind="win" evidenceRef={evidenceRef} onClose={onClose} title={`${names.window(w.window_id)} · ${w.occurrence}`}>
      <span className="self-start">
        <StatusBadge status={w.covered ? "covered" : "missed"} />
      </span>
      <Rows>
        <Row label="Starts">{formatClockTenths(w.t_ms)}</Row>
        <Row label="Raid cooldowns active">
          {w.defensive_cooldowns_active.length ? w.defensive_cooldowns_active.map((id) => names.spell(id)).join(", ") : "None"}
        </Row>
        <Row label="Expected">{w.expected_defensive_cooldowns}</Row>
        {(w.defensive_cooldowns_late ?? []).map((l) => (
          <Row key={l.spell_id} label={`${names.spell(l.spell_id)} was late`}>
            {(l.late_ms / 1000).toFixed(1)} s
          </Row>
        ))}
      </Rows>
      {others.length ? (
        <Muted>
          Other occurrences:{" "}
          {others
            .map((o) => `${o.occurrence} (${formatClockTenths(o.t_ms)}) ${o.covered ? "covered" : "missed"}`)
            .join(" · ")}
        </Muted>
      ) : null}
    </PanelLayout>
  );
}

// ---------------------------------------------------------------------------
// Event row
// ---------------------------------------------------------------------------

export function EventView({ record: e, evidenceRef, code, names, onClose }: ViewProps<"event">) {
  const who = (name: string | null | undefined) => (name ? <PlayerName name={name} code={code} /> : "—");
  return (
    <PanelLayout
      kind="event"
      evidenceRef={evidenceRef}
      onClose={onClose}
      title={e.ability ? <Ability id={e.ability_id} name={e.ability} /> : `${e.type} event`}
    >
      <Rows>
        <Row label="Time · phase">
          {formatClockMillis(e.t_ms)}
          {e.phase_id ? ` · ${names.phaseShort(e.phase_id)}` : ""}
        </Row>
        <Row label="Type">{e.type}</Row>
        <Row label="Source → target">
          {e.source ?? "—"} → {who(e.target)}
        </Row>
        {e.ability ? (
          <Row label="Ability">
            <Ability id={e.ability_id} name={e.ability} />{" "}
            {e.ability_id ? <span className="font-mono text-[11px] text-mut">{e.ability_id}</span> : null}
          </Row>
        ) : null}
        {e.amount !== null && e.amount !== undefined ? (
          <Row label="Amount · health after">
            {formatNumber(e.amount)}
            {e.hp_pct !== null && e.hp_pct !== undefined ? ` · ${formatPct(e.hp_pct)}` : ""}
          </Row>
        ) : null}
        {e.overkill ? <Row label="Overkill">{formatNumber(e.overkill)}</Row> : null}
        {e.mechanic_id ? <Row label="Mechanic">{names.mechanic(e.mechanic_id)}</Row> : null}
      </Rows>
      {e.failure ? (
        <Tag tone="bad">
          Failure{e.blamed ? " · charged to " : ""}
          {e.blamed ? who(e.blamed) : null}
        </Tag>
      ) : null}
    </PanelLayout>
  );
}

// ---------------------------------------------------------------------------
// Pull summary
// ---------------------------------------------------------------------------

export function PullView({ record: p, evidenceRef, code, names, onClose }: ViewProps<"pull">) {
  const kill = p.outcome === "kill";
  return (
    <PanelLayout kind="pull" evidenceRef={evidenceRef} onClose={onClose} title={`${p.encounter_name} · pull summary`}>
      <Rows>
        <Row label="Outcome · duration">
          {kill ? "Kill" : "Wipe"} · {formatClockTenths(p.duration_ms)}
        </Row>
        <Row label="Fight % · boss health">
          {p.boss_pct === null ? "—" : formatPct(p.boss_pct, 2)} · {p.boss_hp_pct === null ? "—" : formatPct(p.boss_hp_pct, 2)}
        </Row>
        <Row label="Phase reached">{names.phaseShort(p.phase_reached) ?? "—"}</Row>
        <Row label="Cutoff">{p.cutoff_ms === null ? "None" : formatClockTenths(p.cutoff_ms)}</Row>
        <Row label="Deaths · before cutoff">
          {p.deaths} · {p.deaths_before_cutoff}
        </Row>
        <Row label="Coverage · anomalies">
          {p.coverage[0]?.toUpperCase()}
          {p.coverage.slice(1)} · {p.anomalies.length}
        </Row>
      </Rows>
      {p.aggro.length ? (
        <Muted>
          Aggro:{" "}
          {p.aggro.map((a, i) => (
            <span key={a.npc}>
              {i > 0 ? " · " : ""}
              {a.npc} {a.swings} swings
              {a.targets.map((t) => (
                <span key={t.name}>
                  , <PlayerName name={t.name} code={code} /> {formatPct(t.share_pct)}
                </span>
              ))}
            </span>
          ))}
        </Muted>
      ) : null}
    </PanelLayout>
  );
}

// ---------------------------------------------------------------------------
// Plan, error, loading, nothing selected
// ---------------------------------------------------------------------------

export function PlanView({ evidenceRef, onClose }: { evidenceRef: string; onClose?: () => void }) {
  return (
    <PanelLayout kind="plan" title="Raid plans aren't available yet" evidenceRef={evidenceRef} onClose={onClose}>
      <p className="m-0 text-[13px]">
        This link points at a raid-plan entry. Comparing a plan with the pull arrives in a later release, so there&apos;s
        nothing to show yet.
      </p>
    </PanelLayout>
  );
}

export function ErrorView({
  evidenceRef,
  code,
  message,
  unresolvable,
  onRetry,
  onClose,
}: {
  evidenceRef: string;
  code: string;
  message: string;
  unresolvable: boolean;
  onRetry?: () => void;
  onClose?: () => void;
}) {
  return (
    <PanelLayout
      kind="error"
      title={unresolvable ? "This evidence can't be found" : "This evidence couldn't be loaded"}
      evidenceRef={evidenceRef}
      onClose={onClose}
    >
      <div role="alert" className="rounded-sm bg-badt px-2.5 py-2 text-[13px] text-ink">
        <b className="font-semibold text-bad">{code}</b> · {message}
      </div>
      {unresolvable ? (
        <p className="m-0 text-[13px]">The link may be mistyped, or come from data that has since changed.</p>
      ) : onRetry ? (
        <Button variant="secondary" size="sm" className="self-start" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </PanelLayout>
  );
}

export function LoadingView() {
  return (
    <div aria-busy="true" aria-label="Loading the evidence" className="flex flex-col gap-2.5" data-panel-kind="loading">
      <span className="font-mono text-[11px] font-medium tracking-[0.06em] text-acc uppercase">loading</span>
      <SkeletonBlock width="60%" height={20} />
      <SkeletonBlock width="80%" height={12} />
      <SkeletonBlock height={12} />
      <SkeletonBlock width="90%" height={12} />
      <SkeletonBlock width="70%" height={12} />
    </div>
  );
}

export function EmptyView() {
  return (
    <PanelLayout kind="evidence" title="Nothing selected">
      <p className="m-0 text-[13px] text-mut">
        Choose a chip in a table, a pull in the list or a marker on the timeline to see its evidence here.
      </p>
    </PanelLayout>
  );
}
