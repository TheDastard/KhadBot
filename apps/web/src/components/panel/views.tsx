// apps/web/src/components/panel/views.tsx
//
// One renderer per evidence kind, from the canvas's "Context panel, every ref kind" artboard: the death moment, the
// mechanic card, a cooldown use, a damage window's coverage, an event row and the pull summary, plus the plan, error
// and loading views. The mechanic card is the mechanic's drill-down: what it is (description, abilities as Wowhead
// links), how it is judged (the failure rule in plain words), how it went (tiles, failures by player, each cluster
// with its blame context) and what it cost (the deaths in its window). A spell that belongs to a mechanic stays an
// AbilityName with the mechanic's chip beside it. A renderer takes the record and a ViewContext (the pull's names) and draws a PanelLayout; none of
// them knows the panel's frame (see parts.tsx).

"use client";

import { AbilityName } from "@/components/shared/ability-name";
import { EvidenceChip } from "@/components/shared/evidence-chip";
import { MechanicIcon } from "@/components/shared/mechanic-tip";
import { PlayerName } from "@/components/shared/player-name";
import { StatusBadge } from "@/components/shared/status-badge";
import { UnverifiedTag } from "@/components/pull/unverified-tag";
import { Button } from "@/components/ui/button";
import { spellIdOf, type Names } from "@/components/pull/names";
import { deathNumber } from "@/components/pull/logic";
import { useLazyDigest, useReportAbilities, type EvidenceData } from "@/components/pull/queries";
import { MechanicChip } from "@/components/pull/ref-chip";
import { useFocusActions, windowAround } from "@/lib/focus";
import { formatClockTenths, formatNumber, formatPct } from "@/lib/format";
import { parseRef } from "@/lib/refs";
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
  return <MechanicChip code={code} fight={fight} facts={names.mechanicFacts(id)} />;
}

/** A spell as a Wowhead link and, when it belongs to a mechanic, that mechanic's chip beside it. */
function AbilityWithMechanic({
  id,
  name,
  mechanicId,
  code,
  fight,
  names,
}: {
  id: number | null | undefined;
  name: string | null | undefined;
  /** The mechanic the record names; otherwise the one the ability belongs to. */
  mechanicId?: string | null;
  code: string;
  fight: number;
  names: Names;
}) {
  const mech = mechanicId ?? names.mechanicOfAbility(spellIdOf(id));
  return (
    <span className="inline-flex flex-wrap items-center justify-end gap-x-2 gap-y-1">
      <Ability id={id} name={name} />
      {mech ? <MechChip code={code} fight={fight} id={mech} names={names} /> : null}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Death moment
// ---------------------------------------------------------------------------

export function DeathView({ record: d, evidenceRef, code, fight, names, onClose }: ViewProps<"death">) {
  const { setWindow } = useFocusActions();
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
          <AbilityWithMechanic
            id={d.killing_ability_id}
            name={d.killing_ability_name}
            code={code}
            fight={fight}
            names={names}
          />
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
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <AbilityWithMechanic id={h.ability_id} name={h.ability_name} code={code} fight={fight} names={names} />
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

const BLAME_STATE: Record<string, { label: string; tone: "plain" | "bad" }> = {
  beyond_reach: { label: "Beyond reach", tone: "bad" },
  in_reach: { label: "In reach", tone: "plain" },
  dead: { label: "Dead", tone: "plain" },
  alive: { label: "Alive", tone: "plain" },
  no_position: { label: "No position", tone: "plain" },
  no_holder: { label: "No holder", tone: "plain" },
};

const KIND_LABEL: Record<string, string> = {
  avoidable: "Avoidable",
  soak: "Soak",
  interrupt: "Interrupt",
  dispel: "Dispel",
  debuff: "Debuff",
  unavoidable: "Unavoidable",
};

/** The mechanic's abilities as Wowhead links, named from the report's abilities (or the registry). */
function MechanicAbilities({ code, abilityIds, names }: { code: string; abilityIds: number[]; names: Names }) {
  const abilities = useReportAbilities(code, abilityIds.length > 0);
  if (!abilityIds.length) return null;
  const byId = new Map((abilities.data?.abilities ?? []).map((a) => [a.id, a.name]));
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs" data-testid="mechanic-abilities">
      <span className="text-mut">Abilities</span>
      {abilityIds.map((id) => (
        <span key={id} className="inline-flex items-baseline gap-1.5">
          <AbilityName spellId={spellIdOf(id)} name={byId.get(id) ?? names.spell(id)} />
          <span className="font-mono text-[11px] text-mut">{id}</span>
        </span>
      ))}
    </div>
  );
}

const SUB_HEAD = "flex justify-between gap-2 text-xs text-mut";

export function MechanicView({ record: m, evidenceRef, code, fight, names, onClose }: ViewProps<"mech">) {
  const facts = names.mechanicFacts(m.mechanic_id);
  const description = m.description ?? facts.description;
  const digest = useLazyDigest(code, fight, true, true);
  const deaths = (digest.data?.deaths ?? []).filter((d) => d.mechanic_ids.includes(m.mechanic_id));
  const charged = Object.entries(m.failures_by_player)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const most = charged[0]?.[1] ?? 0;
  const blame = m.cluster_rows.some((c) => c.blamed || c.blame_state);
  const tiles: Array<[string, string, boolean]> = [
    [String(m.failures), "failures", m.failures > 0],
    [String(m.hits), `hits in ${m.clusters} ${m.clusters === 1 ? "cluster" : "clusters"}`, false],
    [String(m.deaths_in_window), "deaths in window", false],
  ];
  return (
    <PanelLayout
      kind="mech"
      evidenceRef={evidenceRef}
      onClose={onClose}
      title={
        <span className="inline-flex items-center gap-1.5">
          <MechanicIcon facts={facts} />
          {facts.name}
        </span>
      }
    >
      {facts.kind || facts.severity ? (
        <div className="flex flex-wrap gap-1.5">
          {facts.kind ? <Tag>{KIND_LABEL[facts.kind] ?? facts.kind}</Tag> : null}
          {facts.severity ? (
            <Tag tone={facts.severity === "high" ? "warn" : "plain"}>Severity {facts.severity}</Tag>
          ) : null}
        </div>
      ) : null}
      {description ? <p className="m-0 text-[13px] leading-normal">{description}</p> : null}
      <MechanicAbilities code={code} abilityIds={facts.abilityIds} names={names} />
      {facts.failureSummary ? (
        <div className="flex flex-col gap-1 rounded-sm bg-s2 px-2.5 py-2 text-xs" data-testid="mechanic-rule">
          <span className="font-semibold">How failures count</span>
          <span>{facts.failureSummary}</span>
        </div>
      ) : null}
      <div className="grid grid-cols-3 gap-2 text-center">
        {tiles.map(([value, label, bad]) => (
          <div key={label} className="rounded-sm bg-s2 p-1.5">
            <b className={cn("block text-lg", bad && "text-bad")}>{value}</b>
            <span className="text-[11px] text-mut">{label}</span>
          </div>
        ))}
      </div>
      {names.mechanicVerified(m.mechanic_id) === false ? (
        <span className="inline-flex items-center gap-1.5 rounded-sm bg-warnt px-2 py-1.5 text-xs text-warn">
          <AlertTriangleIcon className="size-3.5" aria-hidden />
          Mechanic not yet verified: its rule and counts have not been checked by a person
        </span>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <span className={SUB_HEAD}>
          <span>Failures by player</span>
          <span>Damage taken {formatNumber(m.damage_taken)}</span>
        </span>
        {charged.length ? (
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-[13px]" aria-label="Failures by player">
            {charged.map(([name, n]) => (
              <li key={name} className="grid grid-cols-[96px_minmax(0,1fr)_28px] items-center gap-2">
                <span className="truncate">
                  <PlayerName name={name} code={code} />
                </span>
                <span aria-hidden className="h-2 overflow-hidden rounded-sm bg-s3">
                  <span className="block h-full bg-bad" style={{ width: `${(n / most) * 100}%` }} />
                </span>
                <b className="text-right text-bad">{n}</b>
              </li>
            ))}
          </ul>
        ) : (
          <Muted>No failures charged to a player.</Muted>
        )}
      </div>
      {m.cluster_rows.length ? (
        <div className="flex flex-col gap-1.5">
          <span className={SUB_HEAD}>
            <span>Clusters</span>
            {blame ? <span>blame at the first hit</span> : null}
          </span>
          <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Clusters">
            <table className="w-full min-w-[300px] border-collapse text-xs">
              <thead>
                <tr className="text-left text-mut">
                  <th scope="col" className="px-1.5 py-1 font-semibold">#</th>
                  <th scope="col" className="px-1.5 py-1 font-semibold">Time</th>
                  <th scope="col" className="px-1.5 py-1 text-right font-semibold">Hits</th>
                  <th scope="col" className="px-1.5 py-1 text-right font-semibold">Fail</th>
                  {blame ? (
                    <>
                      <th scope="col" className="px-1.5 py-1 font-semibold">Charged to</th>
                      <th scope="col" className="px-1.5 py-1 font-semibold">At the hit</th>
                    </>
                  ) : (
                    <th scope="col" className="px-1.5 py-1 font-semibold">Failed</th>
                  )}
                </tr>
              </thead>
              <tbody>
                {m.cluster_rows.map((c) => {
                  const state = c.blame_state ? BLAME_STATE[c.blame_state] : undefined;
                  return (
                    <tr key={c.cluster} className="border-t border-line align-top">
                      <td className="px-1.5 py-1">{c.cluster}</td>
                      <td className="px-1.5 py-1 font-mono whitespace-nowrap">
                        {formatClockTenths(c.t_ms)}–{formatClockTenths(c.end_ms)}
                      </td>
                      <td className="px-1.5 py-1 text-right">{c.hits}</td>
                      <td className={cn("px-1.5 py-1 text-right font-semibold", c.failures > 0 ? "text-bad" : "text-mut")}>
                        {c.failures}
                      </td>
                      {blame ? (
                        <>
                          <td className="px-1.5 py-1">{c.blamed ? <PlayerName name={c.blamed} code={code} /> : "—"}</td>
                          <td className="px-1.5 py-1">
                            {state ? (
                              <span className="whitespace-nowrap">
                                <Tag tone={state.tone}>{state.label}</Tag>
                              </span>
                            ) : (
                              "—"
                            )}
                            {typeof c.blame_dist_yd === "number" ? (
                              <span className="block text-mut">
                                {c.blame_dist_yd.toFixed(1)} yd
                                {typeof c.blame_reach_yd === "number" ? ` of ${c.blame_reach_yd}` : ""}
                              </span>
                            ) : null}
                          </td>
                        </>
                      ) : (
                        <td className="px-1.5 py-1 text-mut">
                          {c.failed_players.map((p, i) => (
                            <span key={p}>
                              {i > 0 ? ", " : ""}
                              <PlayerName name={p} code={code} />
                            </span>
                          ))}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      {deaths.length ? (
        <div className="flex flex-col gap-1.5" data-testid="mechanic-deaths">
          <span className={SUB_HEAD}>Deaths in its window</span>
          <span className="flex flex-wrap gap-1.5">
            {deaths.map((d) => (
              <EvidenceChip
                key={d.ref}
                evidenceRef={d.ref}
                label={`${d.phase_id ? `${names.phaseShort(d.phase_id)} · ` : ""}death ${deathNumber(d.ref) ?? ""} · ${d.name}`}
              />
            ))}
          </span>
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

export function EventView({ record: e, evidenceRef, code, fight, names, onClose }: ViewProps<"event">) {
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
        {e.mechanic_id ? (
          <Row label="Mechanic">
            <MechChip code={code} fight={fight} id={e.mechanic_id} names={names} />
            <UnverifiedTag verified={names.mechanicVerified(e.mechanic_id)} />
          </Row>
        ) : null}
        {e.amount !== null && e.amount !== undefined ? (
          <Row label="Amount · health after">
            {formatNumber(e.amount)}
            {e.hp_pct !== null && e.hp_pct !== undefined ? ` · ${formatPct(e.hp_pct)}` : ""}
          </Row>
        ) : null}
        {e.overkill ? <Row label="Overkill">{formatNumber(e.overkill)}</Row> : null}
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
    <div role="status" aria-busy="true" aria-label="Loading the evidence" className="flex flex-col gap-2.5" data-panel-kind="loading">
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
