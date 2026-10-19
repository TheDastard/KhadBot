"""
encounter/digest.py

Per-pull digest: the deterministic summary the analyst reads first.

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
from khadbot.encounter.analysis.friendly_damage import UNCLASSIFIED, classified_hits, friendly_damage
from khadbot.encounter.analysis.mechanics import mechanic_stats
from khadbot.encounter.annotate import Annotated, AnnotationCache, annotate_pull, unmatched_phases
from khadbot.encounter.config_files import (
    CooldownRegistry,
    EncounterFile,
    FriendlyDamageRegistry,
    check_cooldown_ids,
    cooldowns_hash,
    friendly_damage_hash,
    load_cooldowns,
    load_encounter_view,
    load_friendly_damage,
)
from khadbot.encounter.models import (
    Actor,
    BlameState,
    CooldownUse,
    DeathRecord,
    FriendlyDamage,
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
DEFENSIVE_COOLDOWN_CAP = 30
# Long pulls cast 30+ offensive cooldowns; 15 kept the recorded 10-minute pulls under the 3k-token digest ceiling.
# Which casts survive the cap: see ``capped_offensive``.
OFFENSIVE_COOLDOWN_CAP = 15
# Pulls cast up to 70 utility cooldowns, most of them personal movement. The section holds one cast, the
# top-ranked raid or enemy-side one; cooldown_timeline lists the rest. With it the largest recorded digest is
# about 3,001 tokens, under the 3.1k ceiling. Which cast survives the cap: see ``capped_utility``.
UTILITY_COOLDOWN_CAP = 1
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
    a: Annotated, enc: EncounterFile | None, abilities: Mapping[int, str], friendly: FriendlyDamageRegistry
) -> tuple[list[UnregisteredAbility], float]:
    """
    Damage players took from abilities the encounter file does not mention, by damage desc. Friendly damage
    (registry abilities, and anything a friendly player or pet dealt) is never unregistered.

    Returns every unregistered ability and their combined share (0-100) of friendly damage taken.
    Without an encounter file all non-friendly damage is unregistered.
    """
    hits = classified_hits(a, enc, friendly)
    total = int(hits["amount"].sum() or 0)
    if not total:
        return [], 0.0
    known = sorted(enc.registered_ability_ids()) if enc else []
    rows = (
        hits.filter(
            pl.col("friendly_class").is_null() & ~pl.col("ability_id").is_in(known) & pl.col("ability_id").is_not_null()
        )
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


def _friendly_anomalies(
    a: Annotated, enc: EncounterFile | None, friendly: FriendlyDamageRegistry, fd: FriendlyDamage
) -> list[str]:
    """
    ``friendly_damage_unverified:<id>`` per unverified registry ability that hit a player, and
    ``unclassified_friendly_damage:<pct>`` when friendly-source damage the registry doesn't class reaches the
    coverage threshold (with an encounter file), since that damage no longer lowers coverage.
    """
    hits = a.hits
    hit_ids = set(hits["ability_id"].drop_nulls().to_list())
    out = [
        f"friendly_damage_unverified:{e.ability_id}"
        for e in friendly.abilities
        if not e.verified and e.ability_id in hit_ids
    ]
    total = int(hits["amount"].fill_null(0).sum() or 0)
    unclassified = fd.by_class.get(UNCLASSIFIED, 0)
    if enc is not None and total and (pct := round(unclassified / total * 100, 1)) >= FULL_COVERAGE_MAX_PCT:
        out.append(f"unclassified_friendly_damage:{pct:.1f}")
    return out


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
    cooldowns: list[CooldownUse]  # every cooldown cast, of every category, time order
    windows: list[WindowCoverage]
    digest: PullDigest


def capped_offensive(uses: list[CooldownUse], offensive: CooldownRegistry) -> list[CooldownUse]:
    """
    At most ``OFFENSIVE_COOLDOWN_CAP`` offensive casts, in time order. Raid-wide casts are kept first, so a late
    Bloodlust is never cut; then each spell's first cast, then each one's second, and so on, earliest first, so
    one spell cast often (Power Infusion) can't crowd out the rest.
    """
    return _capped(uses, offensive, OFFENSIVE_COOLDOWN_CAP, {"raid": 0})


# Utility tiers: raid-wide casts, then enemy control and externals, then the caster's own (mostly movement).
_UTILITY_TIERS = {"raid": 0, "enemy": 1, "enemies": 1, "external": 1, "personal": 2}


def capped_utility(uses: list[CooldownUse], utility: CooldownRegistry) -> list[CooldownUse]:
    """
    At most ``UTILITY_COOLDOWN_CAP`` utility casts, in time order. Raid-wide casts are kept first, then casts on
    enemies or another player, then the caster's own, so a pull's many personal movement casts (Death's Advance,
    Divine Steed) fill the section only when nothing else does. Within a tier each spell's first cast comes
    first, then each one's second, and so on, earliest first.
    """
    return _capped(uses, utility, UTILITY_COOLDOWN_CAP, _UTILITY_TIERS)


def _capped(
    uses: list[CooldownUse], registry: CooldownRegistry, cap: int, tiers: Mapping[str, int]
) -> list[CooldownUse]:
    """The first ``cap`` casts by (tier of the spell's target, nth cast of the spell, time), in time order.
    A target missing from ``tiers``, or a spell missing from ``registry``, ranks after every tier."""
    last = max(tiers.values(), default=0) + 1
    nth: dict[int, int] = {}
    ranked = []
    for c in sorted(uses, key=lambda c: c.t_ms):
        nth[c.spell_id] = nth.get(c.spell_id, 0) + 1
        entry = registry.get(c.spell_id)
        tier = tiers.get(entry.target, last) if entry is not None else last
        ranked.append(((tier, nth[c.spell_id], c.t_ms), c))
    kept = [c for _, c in sorted(ranked, key=lambda r: r[0])[:cap]]
    return sorted(kept, key=lambda c: (c.t_ms, c.ref))


def build_pull_records(
    pull_key: str,
    store: EventStore,
    enc_root: Path | None = None,
    defensive: CooldownRegistry | None = None,
    *,
    offensive: CooldownRegistry | None = None,
    utility: CooldownRegistry | None = None,
    friendly: FriendlyDamageRegistry | None = None,
    cache: AnnotationCache | None = None,
    cutoff_fraction: float | None = None,
    unregistered_cap: int = UNREGISTERED_CAP,
) -> PullRecords:
    """
    Analysis records and digest for one ingested pull. The encounter file is looked up by the pull's
    ``encounter_id``. Evidence refs resolve against these uncapped records.

    ``unregistered_cap`` widens the unregistered and unclassified friendly-damage lists for authoring
    the encounter file and the friendly-damage registry (``--unregistered``).
    """
    code, fight = split_pull_key(pull_key)
    pull = next((p for p in store.pulls(code) if p.fight_id == fight), None)
    if pull is None:
        raise KeyError(f"pull {pull_key} is not in the event store; ingest it first")
    defensive = defensive or load_cooldowns("defensive")
    offensive = offensive or load_cooldowns("offensive")
    utility = utility or load_cooldowns("utility")
    check_cooldown_ids(defensive, offensive, utility)
    friendly = friendly or load_friendly_damage()
    enc = load_encounter_view(pull.encounter_id, pull.difficulty, enc_root)
    fraction = EncounterConfig().cutoff_fraction if cutoff_fraction is None else cutoff_fraction
    a = annotate_pull(pull_key, store, enc, cache=cache, cutoff_fraction=fraction)
    actors = store.actors(code)
    abilities = store.abilities(code)

    deaths = death_records(a, actors, defensive, abilities, friendly=friendly)
    mechanics = mechanic_stats(a, enc, actors, deaths)
    cooldowns = cooldown_uses(a, [defensive, offensive, utility], actors)
    windows = window_coverage(a, enc, defensive)
    unregistered, unregistered_pct = unregistered_damage(a, enc, abilities, friendly)
    friendly_dmg = friendly_damage(a, enc, friendly, actors, abilities, unclassified_cap=unregistered_cap)

    if enc is None:
        coverage = "none"
    else:
        coverage = "full" if unregistered_pct < FULL_COVERAGE_MAX_PCT else "partial"

    anomalies = [f"truncated:{t}" for t in store.truncated(pull_key)]
    present = set(a.frame["data_type"].cast(pl.Utf8).unique().to_list())
    anomalies += [f"missing_data_type:{t}" for t in REQUIRED_DATA_TYPES if t not in present]
    if a.frame["hp_pct"].null_count() == a.frame.height:
        anomalies.append("no_hp_data")
    # An early wipe legitimately never reaches later phases; only flag a phase WCL's transitions say it reached.
    if enc is not None:
        anomalies += [f"phase_trigger_unmatched:{p}" for p in unmatched_phases(pull, a.phases, enc)]
    # Utility casts are left out: nothing reads a utility entry's cooldown or duration, and a pull casts a dozen
    # different ones.
    unverified = sorted(
        {
            c.spell_id
            for c in cooldowns
            if c.category != "utility"
            and (not (e := defensive.get(c.spell_id) or offensive.get(c.spell_id)) or not e.verified)
        }
    )
    anomalies += [f"cooldown_unverified:{s}" for s in unverified]
    if enc is not None and unregistered_pct >= FULL_COVERAGE_MAX_PCT:
        anomalies.append(f"unregistered_damage:{unregistered_pct:.1f}")
    anomalies += _friendly_anomalies(a, enc, friendly, friendly_dmg)

    active_mechanics = [m for m in mechanics if m.hits or m.failures]
    active_mechanics.sort(key=lambda m: (-m.failures, -m.damage_taken))

    digest = PullDigest(
        pull_key=pull_key,
        encounter_name=pull.encounter_name,
        outcome="kill" if pull.kill else "wipe",
        duration_ms=pull.duration_ms,
        # The encounter file's phase when there is one; otherwise WCL's name for the last phase transition.
        phase_reached=a.phases[-1].phase_id if a.phases else pull.wcl_phase_reached(),
        boss_pct=pull.boss_pct,
        boss_hp_pct=pull.boss_hp_pct,
        cutoff_ms=a.cutoff_ms,
        deaths=sorted(deaths, key=lambda d: (d.after_cutoff, d.t_ms))[:DEATH_CAP],
        mechanics=active_mechanics[:MECHANIC_CAP],
        # Each category has its own cap, so a raid's many offensive casts never push defensives out.
        defensive_cooldowns=[c for c in cooldowns if c.category == "defensive"][:DEFENSIVE_COOLDOWN_CAP],
        offensive_cooldowns=capped_offensive([c for c in cooldowns if c.category == "offensive"], offensive),
        utility_cooldowns=capped_utility([c for c in cooldowns if c.category == "utility"], utility),
        coverage_windows=windows,
        unregistered=unregistered[:unregistered_cap],
        friendly_damage=friendly_dmg,
        aggro=aggro_summaries(a, actors),
        coverage=coverage,
        anomalies=anomalies,
        produced_from={
            "events_hash": store.events_hash(pull_key) or "",
            "positions_hash": store.positions_hash(pull_key) or "",
            "encfile_hash": a.encfile_hash,
            "defensive_cooldowns_hash": cooldowns_hash(defensive),
            "offensive_cooldowns_hash": cooldowns_hash(offensive),
            "utility_cooldowns_hash": cooldowns_hash(utility),
            "friendly_damage_hash": friendly_damage_hash(friendly),
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
    defensive: CooldownRegistry | None = None,
    *,
    offensive: CooldownRegistry | None = None,
    utility: CooldownRegistry | None = None,
    friendly: FriendlyDamageRegistry | None = None,
    cache: AnnotationCache | None = None,
    cutoff_fraction: float | None = None,
    unregistered_cap: int = UNREGISTERED_CAP,
) -> PullDigest:
    """
    Digest for one ingested pull. The encounter file is looked up by the pull's ``encounter_id``.

    ``unregistered_cap`` widens the unregistered and unclassified friendly-damage lists (``--unregistered``).
    """
    return build_pull_records(
        pull_key,
        store,
        enc_root,
        defensive,
        offensive=offensive,
        utility=utility,
        friendly=friendly,
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


# Blame states in the order the MECHANICS line lists them, with their wording.
_BLAME_STATE_WORDS: dict[BlameState, str] = {
    "beyond_reach": "beyond reach",
    "in_reach": "in reach",
    "alive": "alive",
    "dead": "dead",
    "no_position": "no position",
    "no_holder": "none",
}


def _blame_clause(m: MechanicStat) -> str:
    """``blamed: 2 beyond reach (31–55 yd), 1 dead, 1 none``: where the charged players were at the hits."""
    parts = []
    for state, words in _BLAME_STATE_WORDS.items():
        if not (n := m.blame_states.get(state)):
            continue
        part = f"{n} {words}"
        if (span := m.blame_dist_yd.get(state)) is not None:
            lo, hi = (f"{v:.0f}" for v in span)
            part += f" ({lo} yd)" if lo == hi else f" ({lo}–{hi} yd)"
        parts.append(part)
    return "blamed: " + ", ".join(parts)


def _render_cooldowns(title: str, uses: list[CooldownUse]) -> list[str]:
    """A cooldown section: one line per cast; nothing when there were none."""
    lines = [title] if uses else []
    for c in uses:
        lines.append(f"- [{c.ref}] {_s(c.t_ms)} {c.spell_name} ({c.spell_id}) by {c.caster} ({c.phase_id or '-'})")
    return lines


def _render_friendly(d: PullDigest) -> list[str]:
    """The SELF DAMAGE section: friendly damage by class, then self-inflicted damage per player."""
    fd = d.friendly_damage
    if not fd.damage:
        return []
    classes = ", ".join(f"{c.replace('_', ' ')} {_n(v)}" for c, v in fd.by_class.items())
    lines = [f"SELF DAMAGE [{make_ref(d.pull_key, 'pull')}] friendly {_n(fd.damage)} ({fd.share_pct:.1f}%): {classes}"]
    for p in fd.self_inflicted:
        hits = ", ".join(f"{x.name or x.ability_id} {_n(x.damage)}" for x in p.abilities)
        lines.append(f"- {p.name} {_n(p.damage)}: {hits}")
    return lines


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
        own = f", self-inflicted {_n(x.window_self_damage)}" if x.window_self_damage else ""
        parts.append(f"last {x.window_ms // 1000}s: taken {_n(x.window_damage)}{healed}{own}")
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
        if m.blame_states:
            line += "; " + _blame_clause(m)
        lines.append(line)

    lines += _render_cooldowns("DEFENSIVE COOLDOWNS", d.defensive_cooldowns)

    if d.coverage_windows:
        lines.append("DAMAGE WINDOWS")
    for w in d.coverage_windows:
        active = ",".join(str(s) for s in w.defensive_cooldowns_active) or "none"
        status = "covered" if w.covered else "NOT covered"
        late = ", ".join(f"{c.spell_id} +{_s(c.late_ms)}" for c in w.defensive_cooldowns_late)
        late = f"; late: {late}" if late else ""
        lines.append(f"- [{w.ref}] {w.window_id} #{w.occurrence} at {_s(w.t_ms)}: {status} (active: {active}{late})")

    lines += _render_cooldowns("OFFENSIVE COOLDOWNS", d.offensive_cooldowns)
    lines += _render_cooldowns("UTILITY COOLDOWNS", d.utility_cooldowns)

    if d.aggro:
        lines.append(f"AGGRO (melee swings by target, taunts landed) [{make_ref(d.pull_key, 'pull')}]")
    for g in d.aggro:
        targets = ", ".join(f"{t.name} {t.swings} ({t.share_pct:.1f}%)" for t in g.targets)
        taunts = ", ".join(f"{name} {n}" for name, n in g.taunts.items()) or "none"
        lines.append(f"- {g.npc}: {g.swings} swings; {targets}; taunts: {taunts}")

    lines += _render_friendly(d)

    if d.unregistered:
        lines.append("UNREGISTERED DAMAGE")
    for u in d.unregistered:
        lines.append(f"- {u.ability_id} {u.name or 'unknown'}: {_n(u.damage)} ({u.share_pct:.1f}%)")

    if d.friendly_damage.unclassified:
        lines.append("UNCLASSIFIED FRIENDLY DAMAGE")
    for u in d.friendly_damage.unclassified:
        lines.append(f"- {u.ability_id} {u.name or 'unknown'}: {_n(u.damage)} ({u.share_pct:.1f}%)")

    if d.anomalies:
        lines.append("ANOMALIES " + ", ".join(d.anomalies))
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """Rough token count (``len(text) // 4``) used for the digest size budget."""
    return len(text) // 4
