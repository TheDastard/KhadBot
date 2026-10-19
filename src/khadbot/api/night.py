"""
api/night.py

The night summary: deterministic aggregates over a whole report's digests, one section per encounter and
difficulty, each with its KPIs, trend, mechanic table and a briefing built from fixed templates.

Every value carries the refs it came from. Every number in a briefing line appears in the records its refs
resolve to, counting the refs themselves as the analyst's verify step does (``analyst/verify.check_finding``):
"on N of M pulls" cites N ``death`` refs and M ``pull`` refs. So a line never states a sum or a median, which no
single record holds.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from khadbot.api.schemas import (
    BestPull,
    BriefingLine,
    Coverage,
    FirstDeath,
    MostFailedMechanic,
    NightMechanic,
    NightSection,
    NightSummary,
    NightTotals,
    TrendRow,
)
from khadbot.api.views import ApiContext, cached_findings, difficulty_name, group_pulls
from khadbot.encounter.config_files import EncounterFile
from khadbot.encounter.digest import PullRecords
from khadbot.encounter.models import DeathRecord, MechanicStat, Pull, make_ref

# ---------------------------------------------------------------------------
# Briefing templates
# ---------------------------------------------------------------------------

FIRST_DEATH_TEMPLATE = "{ability} killed the first player on {n} of {m} {pulls_word}."
MOST_FAILED_TEMPLATE = "{mechanic} was failed on {n} of {m} {pulls_word}, most on {label} ({failures} {failure_word})."
BEST_KILL_TEMPLATE = "Best pull: {label}, a kill{phase}."
BEST_WIPE_TEMPLATE = "Best pull: {label}, boss at {hp:.1f}% health{phase}."
BEST_WIPE_NO_HP_TEMPLATE = "Best pull: {label}, a wipe{phase}."
PHASE_CLAUSE = ", reached {phase}"
UNKNOWN_ABILITY = "An unknown ability"


# ---------------------------------------------------------------------------
# Section
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PullFacts:
    """What the night needs from one pull's records. Records hold the whole annotated frame, so a section keeps
    these instead and leaves the records to the ``EncounterData`` LRU."""

    pull: Pull
    enc: EncounterFile | None
    coverage: Coverage
    phase_reached: str | None
    deaths: list[DeathRecord]
    mechanics: list[MechanicStat]

    @classmethod
    def of(cls, r: PullRecords) -> _PullFacts:
        return cls(r.pull, r.enc, r.digest.coverage, r.digest.phase_reached, r.deaths, r.mechanics)


def _first_death_key(r: _PullFacts) -> tuple[int | None, str | None] | None:
    """(ability ID, name) of the pull's earliest death, or None without deaths. Records are in time order."""
    if not r.deaths:
        return None
    d = r.deaths[0]
    return d.killing_ability_id, d.killing_ability_name


def _best(members: list[tuple[int, _PullFacts]]) -> tuple[int, _PullFacts] | None:
    """The first kill; otherwise the wipe with the lowest boss health, ties going to the lower fight progress."""
    if not members:
        return None
    kills = [m for m in members if m[1].pull.kill]
    if kills:
        return kills[0]

    def key(m: tuple[int, _PullFacts]) -> tuple[float, float, int]:
        p = m[1].pull
        hp = p.boss_hp_pct if p.boss_hp_pct is not None else float("inf")
        progress = p.boss_pct if p.boss_pct is not None else float("inf")
        return hp, progress, m[0]

    return min(members, key=key)


def build_section(ctx: ApiContext, pulls: list[Pull]) -> NightSection:
    """One section over ``pulls`` (one encounter and difficulty, in time order)."""
    members = [(i, _PullFacts.of(ctx.data.records(p.pull_key))) for i, p in enumerate(pulls, start=1)]
    first = members[0][1]
    enc = first.enc
    pull_refs = [make_ref(r.pull.pull_key, "pull") for _, r in members]
    m = len(members)

    # --- most common first death ---
    keys = {i: _first_death_key(r) for i, r in members}
    counts = Counter(k for k in keys.values() if k is not None)
    top_key = None
    if counts:
        best_n = max(counts.values())
        top_key = next(k for k in keys.values() if k is not None and counts[k] == best_n)  # earliest pull wins ties
    first_death = None
    if top_key is not None:
        first_death = FirstDeath(
            ability_id=top_key[0],
            ability_name=top_key[1],
            pulls=counts[top_key],
            of_pulls=m,
            refs=[r.deaths[0].ref for i, r in members if keys[i] == top_key],
            pull_refs=pull_refs,
        )

    # --- trend ---
    trend = []
    for i, r in members:
        p = r.pull
        trend.append(
            TrendRow(
                ordinal=i,
                label=f"P{i}",
                fight=p.fight_id,
                outcome="kill" if p.kill else "wipe",
                boss_hp_pct=0.0 if p.kill else p.boss_hp_pct,
                boss_pct=p.boss_pct,
                duration_ms=p.duration_ms,
                deaths_before_cutoff=sum(1 for d in r.deaths if not d.after_cutoff),
                coverage=r.coverage,
                first_death_is_top=top_key is not None and keys[i] == top_key,
                ref=make_ref(p.pull_key, "pull"),
            )
        )

    # --- mechanic table ---
    mech_names = {x.id: x.name for x in enc.mechanics} if enc else {}
    table: dict[str, NightMechanic] = {}
    per_pull_failures: dict[str, list[tuple[int, int, str]]] = {}  # mechanic -> [(ordinal, failures, ref)]
    for i, r in members:
        for stat in r.mechanics:
            if not (stat.hits or stat.failures):
                continue
            row = table.setdefault(
                stat.mechanic_id,
                NightMechanic(
                    mechanic_id=stat.mechanic_id,
                    name=mech_names.get(stat.mechanic_id, stat.mechanic_id),
                    failures=0,
                    hits=0,
                    damage_taken=0,
                    pulls_affected=0,
                    refs=[],
                ),
            )
            row.failures += stat.failures
            row.hits += stat.hits
            row.damage_taken += stat.damage_taken
            row.pulls_affected += 1
            row.refs.append(stat.ref)
            if stat.failures:
                per_pull_failures.setdefault(stat.mechanic_id, []).append((i, stat.failures, stat.ref))
    mechanics = sorted(table.values(), key=lambda x: (-x.failures, -x.damage_taken, x.mechanic_id))

    most_failed = None
    top_mech = next((x for x in mechanics if x.failures), None)
    if top_mech is not None:
        failed = per_pull_failures[top_mech.mechanic_id]
        most_failed = MostFailedMechanic(
            mechanic_id=top_mech.mechanic_id,
            name=top_mech.name,
            failures=top_mech.failures,
            pulls_failed=len(failed),
            refs=[ref for _, _, ref in failed],
        )

    # --- best pull ---
    best = None
    if (picked := _best(members)) is not None:
        i, r = picked
        phase_names = {x.id: x.name for x in enc.phases} if enc else {}
        best = BestPull(
            fight=r.pull.fight_id,
            ordinal=i,
            label=f"P{i}",
            outcome="kill" if r.pull.kill else "wipe",
            boss_hp_pct=r.pull.boss_hp_pct,
            boss_pct=r.pull.boss_pct,
            phase_reached=r.phase_reached,
            phase_name=phase_names.get(r.phase_reached or ""),
            ref=make_ref(r.pull.pull_key, "pull"),
        )

    # --- findings ---
    findings = [cached_findings(ctx, r.pull) for _, r in members]

    coverages = {r.coverage for _, r in members}
    coverage = "none" if enc is None else ("partial" if "partial" in coverages else "full")
    kills = sum(1 for _, r in members if r.pull.kill)
    return NightSection(
        encounter_id=first.pull.encounter_id,
        encounter_name=first.pull.encounter_name,
        difficulty=first.pull.difficulty,
        difficulty_name=difficulty_name(first.pull.difficulty),
        coverage=coverage,
        pulls=m,
        kills=kills,
        wipes=m - kills,
        best_pull=best,
        most_failed_mechanic=most_failed,
        most_common_first_death=first_death,
        trend=trend,
        mechanics=mechanics,
        findings_available=sum(1 for f in findings if f),
        findings_stale=sum(1 for f in findings if any(x.stale for x in f)),
        briefing=briefing(first_death, most_failed, per_pull_failures, best, pull_refs),
    )


# ---------------------------------------------------------------------------
# Briefing
# ---------------------------------------------------------------------------


def _plural(n: int, word: str) -> str:
    return word if n == 1 else f"{word}s"


def briefing(
    first_death: FirstDeath | None,
    most_failed: MostFailedMechanic | None,
    per_pull_failures: dict[str, list[tuple[int, int, str]]],
    best: BestPull | None,
    pull_refs: list[str],
) -> list[BriefingLine]:
    """The section's template lines in fixed order; a line whose value is missing is skipped."""
    lines: list[BriefingLine] = []
    m = len(pull_refs)
    if first_death is not None:
        ability = first_death.ability_name or (
            f"Ability {first_death.ability_id}" if first_death.ability_id else UNKNOWN_ABILITY
        )
        text = FIRST_DEATH_TEMPLATE.format(ability=ability, n=first_death.pulls, m=m, pulls_word=_plural(m, "pull"))
        lines.append(BriefingLine(kind="first_death", text=text, refs=[*first_death.refs, *pull_refs]))
    if most_failed is not None:
        failed = per_pull_failures[most_failed.mechanic_id]
        ordinal, failures, ref = max(failed, key=lambda f: (f[1], -f[0]))  # most failures; earlier pull on ties
        text = MOST_FAILED_TEMPLATE.format(
            mechanic=most_failed.name,
            n=most_failed.pulls_failed,
            m=m,
            pulls_word=_plural(m, "pull"),
            label=f"P{ordinal}",
            failures=failures,
            failure_word=_plural(failures, "failure"),
        )
        # The worst pull's mech ref holds its failure count; the other pulls' refs make up the N.
        lines.append(BriefingLine(kind="most_failed_mechanic", text=text, refs=[*most_failed.refs, *pull_refs]))
    if best is not None:
        phase_text = best.phase_name or best.phase_reached
        phase = PHASE_CLAUSE.format(phase=phase_text) if phase_text else ""
        if best.outcome == "kill":
            text = BEST_KILL_TEMPLATE.format(label=best.label, phase=phase)
        elif best.boss_hp_pct is not None:
            text = BEST_WIPE_TEMPLATE.format(label=best.label, hp=best.boss_hp_pct, phase=phase)
        else:
            text = BEST_WIPE_NO_HP_TEMPLATE.format(label=best.label, phase=phase)
        lines.append(BriefingLine(kind="best_pull", text=text, refs=[best.ref]))
    return lines


# ---------------------------------------------------------------------------
# Night
# ---------------------------------------------------------------------------


def build_night(ctx: ApiContext, code: str) -> NightSummary:
    pulls = ctx.pulls(code)
    kills = sum(1 for p in pulls if p.kill)
    return NightSummary(
        code=code,
        title=ctx.title(code),
        totals=NightTotals(
            pulls=len(pulls), kills=kills, wipes=len(pulls) - kills, duration_ms=sum(p.duration_ms for p in pulls)
        ),
        sections=[build_section(ctx, members) for _, members in group_pulls(pulls)],
    )
