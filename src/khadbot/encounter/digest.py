"""
encounter/digest.py

Per-pull digest: the deterministic summary the analyst reads first (plan section 6.4).

``build_digest`` annotates a stored pull, runs the analysis functions, applies the caps, and records
coverage and anomalies. ``render_digest`` turns it into compact text for the LLM. Times are rendered
as seconds with one decimal (``12.4s``) and numbers with thousands separators, so the verify step
can match them back to the records.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from khadbot.config import EncounterConfig
from khadbot.encounter.analysis.aggro import aggro_summaries
from khadbot.encounter.analysis.cooldowns import cooldown_uses, window_coverage
from khadbot.encounter.analysis.deaths import death_records
from khadbot.encounter.analysis.mechanics import mechanic_stats
from khadbot.encounter.annotate import Annotated, AnnotationCache, annotate_pull
from khadbot.encounter.config_files import (
    CooldownRegistry,
    EncounterFile,
    load_encounter_view,
    load_registry,
    registry_hash,
)
from khadbot.encounter.models import (
    Actor,
    CooldownUse,
    DeathRecord,
    MechanicStat,
    Pull,
    PullDigest,
    UnregisteredAbility,
    WindowCoverage,
    make_ref,
)
from khadbot.encounter.store import EventStore, split_pull_key

DEATH_CAP = 10
MECHANIC_CAP = 8
COOLDOWN_CAP = 30
UNREGISTERED_CAP = 5
FULL_COVERAGE_MAX_PCT = 10.0  # unregistered share of friendly damage taken below which coverage is "full"

# Data types every real pull has rows for; zero rows means the fetch is missing, not that nothing happened.
REQUIRED_DATA_TYPES = ("DamageTaken", "Casts", "Healing")


def default_annotation_cache() -> AnnotationCache:
    return AnnotationCache(Path(EncounterConfig().data_dir) / "annotated")


# ---------------------------------------------------------------------------
# Unregistered damage
# ---------------------------------------------------------------------------


def unregistered_damage(
    a: Annotated, enc: EncounterFile | None, abilities: Mapping[int, str]
) -> tuple[list[UnregisteredAbility], float]:
    """
    Friendly damage taken from abilities the encounter file does not mention, by damage desc.

    Returns every unregistered ability and their combined share (0-100) of friendly damage taken.
    Without an encounter file all damage is unregistered.
    """
    hits = a.hits.with_columns(pl.col("amount").fill_null(0))
    total = int(hits["amount"].sum() or 0)
    if not total:
        return [], 0.0
    known = sorted(enc.registered_ability_ids()) if enc else []
    rows = (
        hits.filter(~pl.col("ability_id").is_in(known) & pl.col("ability_id").is_not_null())
        .group_by("ability_id")
        .agg(pl.col("amount").sum().alias("damage"))
        .sort(["damage", "ability_id"], descending=[True, False])
        .rows()
    )
    out = [
        UnregisteredAbility(
            ability_id=ability_id,
            name=abilities.get(ability_id),
            damage=int(damage),
            share_pct=round(damage / total * 100, 1),
        )
        for ability_id, damage in rows
    ]
    return out, round(sum(d for _, d in rows) / total * 100, 1)


# ---------------------------------------------------------------------------
# build_digest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PullRecords:
    """Every analysis record for one pull (uncapped), plus the capped digest built from them."""

    pull: Pull
    annotated: Annotated
    enc: EncounterFile | None
    actors: list[Actor]
    abilities: dict[int, str]
    deaths: list[DeathRecord]  # every player death, time order (``death:<n>`` refs)
    mechanics: list[MechanicStat]  # every mechanic in the encounter file, file order
    cooldowns: list[CooldownUse]  # every registry cooldown cast, time order
    windows: list[WindowCoverage]
    digest: PullDigest


def build_pull_records(
    pull_key: str,
    store: EventStore,
    enc_root: Path | None = None,
    registry: CooldownRegistry | None = None,
    *,
    cache: AnnotationCache | None = None,
    cutoff_fraction: float | None = None,
    unregistered_cap: int = UNREGISTERED_CAP,
) -> PullRecords:
    """
    Analysis records and digest for one ingested pull. The encounter file is looked up by the pull's
    ``encounter_id``. Evidence refs resolve against these uncapped records.

    ``unregistered_cap`` widens the unregistered list for encounter-file authoring (``--unregistered``).
    """
    code, fight = split_pull_key(pull_key)
    pull = next((p for p in store.pulls(code) if p.fight_id == fight), None)
    if pull is None:
        raise KeyError(f"pull {pull_key} is not in the event store; ingest it first")
    registry = registry or load_registry()
    enc = load_encounter_view(pull.encounter_id, pull.difficulty, enc_root)
    fraction = EncounterConfig().cutoff_fraction if cutoff_fraction is None else cutoff_fraction
    a = annotate_pull(pull_key, store, enc, cache=cache, cutoff_fraction=fraction)
    actors = store.actors(code)
    abilities = store.abilities(code)

    deaths = death_records(a, actors, registry, abilities)
    mechanics = mechanic_stats(a, enc, actors, deaths)
    cooldowns = cooldown_uses(a, registry, actors)
    windows = window_coverage(a, enc, registry)
    unregistered, unregistered_pct = unregistered_damage(a, enc, abilities)

    if enc is None:
        coverage = "none"
    else:
        coverage = "full" if unregistered_pct < FULL_COVERAGE_MAX_PCT else "partial"

    anomalies = [f"truncated:{t}" for t in store.truncated(pull_key)]
    present = set(a.frame["data_type"].cast(pl.Utf8).unique().to_list())
    anomalies += [f"missing_data_type:{t}" for t in REQUIRED_DATA_TYPES if t not in present]
    if a.frame["hp_pct"].null_count() == a.frame.height:
        anomalies.append("no_hp_data")
    # An early wipe legitimately never reaches later phases; only flag a trigger the pull should have hit.
    if a.unmatched_phase and (pull.kill or (pull.last_phase or 0) > len(a.phases)):
        anomalies.append(f"phase_trigger_unmatched:{a.unmatched_phase}")
    unverified = sorted({c.spell_id for c in cooldowns if not (e := registry.get(c.spell_id)) or not e.verified})
    anomalies += [f"cooldown_unverified:{s}" for s in unverified]
    if enc is not None and unregistered_pct >= FULL_COVERAGE_MAX_PCT:
        anomalies.append(f"unregistered_damage:{unregistered_pct:.1f}")

    active_mechanics = [m for m in mechanics if m.hits or m.failures]
    active_mechanics.sort(key=lambda m: (-m.failures, -m.damage_taken))

    digest = PullDigest(
        pull_key=pull_key,
        encounter_name=pull.encounter_name,
        outcome="kill" if pull.kill else "wipe",
        duration_ms=pull.duration_ms,
        phase_reached=a.phases[-1].phase_id if a.phases else None,
        boss_pct=pull.boss_pct,
        boss_hp_pct=pull.boss_hp_pct,
        cutoff_ms=a.cutoff_ms,
        deaths=sorted(deaths, key=lambda d: (d.after_cutoff, d.t_ms))[:DEATH_CAP],
        mechanics=active_mechanics[:MECHANIC_CAP],
        cooldowns=cooldowns[:COOLDOWN_CAP],
        coverage_windows=windows,
        unregistered=unregistered[:unregistered_cap],
        aggro=aggro_summaries(a, actors),
        coverage=coverage,
        anomalies=anomalies,
        produced_from={
            "events_hash": store.events_hash(pull_key) or "",
            "encfile_hash": a.encfile_hash,
            "registry_hash": registry_hash(registry),
        },
    )
    return PullRecords(
        pull=pull,
        annotated=a,
        enc=enc,
        actors=actors,
        abilities=abilities,
        deaths=deaths,
        mechanics=mechanics,
        cooldowns=cooldowns,
        windows=windows,
        digest=digest,
    )


def build_digest(
    pull_key: str,
    store: EventStore,
    enc_root: Path | None = None,
    registry: CooldownRegistry | None = None,
    *,
    cache: AnnotationCache | None = None,
    cutoff_fraction: float | None = None,
    unregistered_cap: int = UNREGISTERED_CAP,
) -> PullDigest:
    """
    Digest for one ingested pull. The encounter file is looked up by the pull's ``encounter_id``.

    ``unregistered_cap`` widens the unregistered list for encounter-file authoring (``--unregistered``).
    """
    return build_pull_records(
        pull_key,
        store,
        enc_root,
        registry,
        cache=cache,
        cutoff_fraction=cutoff_fraction,
        unregistered_cap=unregistered_cap,
    ).digest


# ---------------------------------------------------------------------------
# render_digest
# ---------------------------------------------------------------------------


def _s(ms: int | None) -> str:
    return "?" if ms is None else f"{ms / 1000:.1f}s"


def _n(value: int | None) -> str:
    return "?" if value is None else f"{value:,}"


def render_digest(d: PullDigest) -> str:
    """Compact text rendering for the LLM; every record line carries its evidence ref."""
    head = [
        f"PULL {d.pull_key} {d.encounter_name}",
        d.outcome,
        _s(d.duration_ms),
        f"reached {d.phase_reached}" if d.phase_reached else "phase unknown",
    ]
    if d.boss_pct is not None:
        head.append(f"fight {d.boss_pct:.1f}%")
    if d.boss_hp_pct is not None:
        head.append(f"boss HP {d.boss_hp_pct:.1f}%")
    if d.cutoff_ms is not None:
        head.append(f"cutoff {_s(d.cutoff_ms)}")
    head.append(f"coverage {d.coverage}")
    lines = [" | ".join(head), f"ref {make_ref(d.pull_key, 'pull')}"]

    lines.append(f"DEATHS ({len(d.deaths)})")
    for x in d.deaths:
        killer = x.killing_ability_name or "unknown"
        if x.killing_ability_id is not None:
            killer += f" ({x.killing_ability_id})"
        parts = [f"{_s(x.t_ms)} {x.name} ({x.phase_id or '-'}){' AFTER CUTOFF' if x.after_cutoff else ''}"]
        parts.append(f"killed by {killer}, overkill {_n(x.overkill)}")
        parts.append(f"ttd {_s(x.time_to_die_ms)}")
        healed = f", healed {_n(x.window_healing)}" if x.window_healing is not None else ""
        parts.append(f"last {x.window_ms // 1000}s: taken {_n(x.window_damage)}{healed}")
        if x.mechanic_ids:
            parts.append("mechanics " + ",".join(x.mechanic_ids))
        if x.active_defensives:
            parts.append("defensives " + ",".join(str(s) for s in x.active_defensives))
        if x.top_hits:
            hits = "; ".join(f"{_s(h.t_ms)} {h.ability_name or h.ability_id} {_n(h.amount)}" for h in x.top_hits)
            parts.append(f"top hits: {hits}")
        lines.append(f"- [{x.ref}] " + "; ".join(parts))

    if d.mechanics:
        lines.append("MECHANICS")
    for m in d.mechanics:
        line = (
            f"- [{m.ref}] {m.mechanic_id}: {m.failures} failures, {m.hits} hits in {m.clusters} clusters, "
            f"{_n(m.damage_taken)} damage, in {m.deaths_in_window} death windows"
        )
        if m.failures_by_player:
            line += "; failed: " + ", ".join(f"{name} {n}" for name, n in m.failures_by_player.items())
        lines.append(line)

    if d.cooldowns:
        lines.append("COOLDOWNS")
    for c in d.cooldowns:
        lines.append(f"- [{c.ref}] {_s(c.t_ms)} {c.spell_name} ({c.spell_id}) by {c.caster} ({c.phase_id or '-'})")

    if d.coverage_windows:
        lines.append("DAMAGE WINDOWS")
    for w in d.coverage_windows:
        active = ",".join(str(s) for s in w.cooldowns_active) or "none"
        status = "covered" if w.covered else "NOT covered"
        lines.append(f"- [{w.ref}] {w.window_id} #{w.occurrence} at {_s(w.t_ms)}: {status} (active: {active})")

    if d.aggro:
        lines.append(f"AGGRO (melee swings by target, taunts landed) [{make_ref(d.pull_key, 'pull')}]")
    for g in d.aggro:
        targets = ", ".join(f"{t.name} {t.swings} ({t.share_pct:.1f}%)" for t in g.targets)
        taunts = ", ".join(f"{name} {n}" for name, n in g.taunts.items()) or "none"
        lines.append(f"- {g.npc}: {g.swings} swings; {targets}; taunts: {taunts}")

    if d.unregistered:
        lines.append("UNREGISTERED DAMAGE")
    for u in d.unregistered:
        lines.append(f"- {u.ability_id} {u.name or 'unknown'}: {_n(u.damage)} ({u.share_pct:.1f}%)")

    if d.anomalies:
        lines.append("ANOMALIES " + ", ".join(d.anomalies))
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """Rough token count used for the digest size budget (plan 6.4)."""
    return len(text) // 4
