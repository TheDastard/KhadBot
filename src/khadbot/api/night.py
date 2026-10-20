"""
api/night.py

The night summary and the report detail, built from per-pull inputs that a ``ReadSource`` supplies.

The night summary is deterministic aggregates over a whole report, one section per encounter and difficulty, each
with its KPIs, trend, mechanic table and a briefing built from fixed templates. Every value carries the refs it came
from. Every number in a briefing line appears in the records its refs resolve to, counting the refs themselves as
the analyst's verify step does (``analyst/verify.check_finding``): "on N of M pulls" cites N ``death`` refs and M
``pull`` refs. So a line never states a sum or a median, which no single record holds.

Two sources give the same inputs: ``RecordsSource`` reads each pull's records through ``EncounterData`` (local
mode, the default), and ``SqlSource`` reads the read-model tables in Postgres (``db/queries.py``). Everything after
the inputs is shared, so both give the same summary and detail when the tables were projected from the records the
other source would load.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

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
    PullSummary,
    ReportDetail,
    ReportGroup,
    TrendRow,
)
from khadbot.api.views import (
    PULL_CAP,
    ApiContext,
    ApiError,
    cached_findings,
    difficulty_name,
    findings_stale,
    group_pulls,
)
from khadbot.encounter.digest import PullRecords
from khadbot.encounter.models import Pull, make_ref

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from khadbot.db.read_models import DeathRow, MechanicRow, PullRow

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
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FirstDeathFact:
    """The killing blow of a pull's earliest death."""

    ability_id: int | None
    ability_name: str | None
    ref: str  # the death's ``death`` ref


@dataclass(frozen=True)
class MechanicFact:
    """One mechanic's totals in one pull."""

    mechanic_id: str
    name: str | None  # the encounter file's name
    hits: int
    failures: int
    damage_taken: int
    ref: str  # the ``mech`` ref


@dataclass(frozen=True)
class PullFacts:
    """
    What the report detail and night summary need from one pull. Records hold the whole annotated frame, so a
    section keeps these instead and leaves the records to the ``EncounterData`` LRU.
    """

    pull_key: str
    fight_id: int
    encounter_id: int
    encounter_name: str
    difficulty: int | None
    kill: bool
    start_ms: int
    duration_ms: int
    boss_pct: float | None
    boss_hp_pct: float | None
    has_encounter_file: bool
    coverage: Coverage
    phase_reached: str | None
    phase_name: str | None  # the encounter file's name for ``phase_reached``
    deaths: int
    deaths_before_cutoff: int
    anomalies: tuple[str, ...]
    first_death: FirstDeathFact | None
    mechanics: tuple[MechanicFact, ...]  # every mechanic in the encounter file, by mechanic ID
    events_hash: str  # what the records were built from; findings staleness compares against these
    encfile_hash: str

    @property
    def ref(self) -> str:
        return make_ref(self.pull_key, "pull")


@dataclass(frozen=True)
class FindingsState:
    """Whether a pull has cached findings (flagged ones left out), and whether any of them is stale."""

    available: bool
    stale: bool


def facts_from_records(r: PullRecords) -> PullFacts:
    """A pull's inputs from its records; the hashes come from the digest's ``produced_from``."""
    enc = r.enc
    d = r.digest
    phase_names = {x.id: x.name for x in enc.phases} if enc else {}
    mech_names = {x.id: x.name for x in enc.mechanics} if enc else {}
    first = r.deaths[0] if r.deaths else None  # records are in time order
    return PullFacts(
        pull_key=r.pull.pull_key,
        fight_id=r.pull.fight_id,
        encounter_id=r.pull.encounter_id,
        encounter_name=r.pull.encounter_name,
        difficulty=r.pull.difficulty,
        kill=r.pull.kill,
        start_ms=r.pull.start_ms,
        duration_ms=r.pull.duration_ms,
        boss_pct=r.pull.boss_pct,
        boss_hp_pct=r.pull.boss_hp_pct,
        has_encounter_file=enc is not None,
        coverage=d.coverage,
        phase_reached=d.phase_reached,
        phase_name=phase_names.get(d.phase_reached or ""),
        deaths=len(r.deaths),
        deaths_before_cutoff=sum(1 for x in r.deaths if not x.after_cutoff),
        anomalies=tuple(d.anomalies),
        first_death=(
            FirstDeathFact(first.killing_ability_id, first.killing_ability_name, first.ref) if first else None
        ),
        mechanics=tuple(
            MechanicFact(m.mechanic_id, mech_names.get(m.mechanic_id), m.hits, m.failures, m.damage_taken, m.ref)
            for m in sorted(r.mechanics, key=lambda m: m.mechanic_id)
        ),
        events_hash=d.produced_from.get("events_hash", ""),
        encfile_hash=d.produced_from.get("encfile_hash", ""),
    )


def facts_from_rows(pull: PullRow, deaths: Iterable[DeathRow], mechanics: Iterable[MechanicRow]) -> PullFacts:
    """
    A pull's inputs from its read-model rows: its ``pulls`` row, and any subset of its death and mechanic rows
    that holds its first death and every mechanic with a hit or a failure.
    """
    first = min(deaths, key=lambda x: x.n, default=None)
    return PullFacts(
        pull_key=pull.pull_key,
        fight_id=pull.fight_id,
        encounter_id=pull.encounter_id,
        encounter_name=pull.encounter_name,
        difficulty=pull.difficulty,
        kill=pull.kill,
        start_ms=pull.start_ms,
        duration_ms=pull.duration_ms,
        boss_pct=pull.boss_pct,
        boss_hp_pct=pull.boss_hp_pct,
        has_encounter_file=pull.has_encounter_file,
        coverage=cast(Coverage, pull.coverage),
        phase_reached=pull.phase_reached,
        phase_name=pull.phase_name,
        deaths=pull.deaths,
        deaths_before_cutoff=pull.deaths_before_cutoff,
        anomalies=tuple(pull.anomalies),
        first_death=(
            FirstDeathFact(first.killing_ability_id, first.killing_ability_name, first.ref) if first else None
        ),
        mechanics=tuple(
            MechanicFact(m.mechanic_id, m.name, m.hits, m.failures, m.damage_taken, m.ref)
            for m in sorted(mechanics, key=lambda m: m.mechanic_id)
        ),
        events_hash=pull.events_hash,
        encfile_hash=pull.encfile_hash,
    )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class ReadSource(Protocol):
    """Where the report detail and night summary read their inputs."""

    def title(self, code: str) -> str | None:
        """The report title, if known."""
        ...

    def pulls(self, code: str) -> list[PullFacts]:
        """The report's pulls in time order (start, then fight ID). Raises ``ApiError`` ``report_not_found``."""
        ...

    def findings(self, pulls: Sequence[PullFacts]) -> dict[str, FindingsState]:
        """The cached-findings state of each of ``pulls``, by pull key."""
        ...


class RecordsSource:
    """Inputs from each pull's records through ``EncounterData``, and findings from the context's store."""

    def __init__(self, ctx: ApiContext) -> None:
        self.ctx = ctx
        self._pulls: dict[str, Pull] = {}

    def title(self, code: str) -> str | None:
        return self.ctx.title(code)

    def pulls(self, code: str) -> list[PullFacts]:
        pulls = self.ctx.pulls(code)
        self._pulls.update((p.pull_key, p) for p in pulls)
        return [facts_from_records(self.ctx.data.records(p.pull_key)) for p in pulls]

    def findings(self, pulls: Sequence[PullFacts]) -> dict[str, FindingsState]:
        out = {}
        for f in pulls:
            pull = self._pulls.get(f.pull_key) or self.ctx.data.pull(f.pull_key)
            found = cached_findings(self.ctx, pull) if pull is not None else []
            out[f.pull_key] = FindingsState(available=bool(found), stale=any(x.stale for x in found))
        return out


class SqlSource:
    """
    Inputs from the read-model tables and findings from the ``findings`` table, in Postgres. A pull that is not
    projected yet is not in its report, and a report with no projected pull is not found.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def title(self, code: str) -> str | None:
        from khadbot.db import queries  # local mode never imports the database code

        with self.pool.connection() as conn:
            row = queries.report(conn, code)
        return row.title if row is not None else None

    def pulls(self, code: str) -> list[PullFacts]:
        from khadbot.db import queries

        with self.pool.connection() as conn:
            rows = queries.report_rows(conn, code)
        if not rows.pulls:
            raise ApiError(404, "report_not_found", f"Report {code} has not been ingested.")
        deaths: dict[int, list[DeathRow]] = {}
        for d in rows.deaths:
            deaths.setdefault(d.fight_id, []).append(d)
        mechanics: dict[int, list[MechanicRow]] = {}
        for m in rows.mechanics:
            mechanics.setdefault(m.fight_id, []).append(m)
        return [facts_from_rows(p, deaths.get(p.fight_id, []), mechanics.get(p.fight_id, [])) for p in rows.pulls]

    def findings(self, pulls: Sequence[PullFacts]) -> dict[str, FindingsState]:
        from khadbot.db import queries

        with self.pool.connection() as conn:
            stored = queries.findings_hashes(conn, [f.pull_key for f in pulls])
        by_key = {f.pull_key: f for f in pulls}
        out = {key: FindingsState(available=False, stale=False) for key in by_key}
        for s in stored:
            f = by_key[s.pull_key]
            stale = findings_stale(s.events_hash, s.encfile_hash, f.events_hash, f.encfile_hash)
            out[s.pull_key] = FindingsState(available=True, stale=out[s.pull_key].stale or stale)
        return out


def read_source(ctx: ApiContext) -> ReadSource:
    """The context's ``read_source`` when it has one, else its records."""
    return ctx.read_source if ctx.read_source is not None else RecordsSource(ctx)


# ---------------------------------------------------------------------------
# Report detail
# ---------------------------------------------------------------------------


def summarize_pull(f: PullFacts, ordinal: int) -> PullSummary:
    """A pull's row in its group: the fields of what its ``pull`` ref resolves to, plus fight and ordinal."""
    return PullSummary(
        fight=f.fight_id,
        ordinal=ordinal,
        label=f"P{ordinal}",
        outcome="kill" if f.kill else "wipe",
        duration_ms=f.duration_ms,
        boss_pct=f.boss_pct,
        boss_hp_pct=f.boss_hp_pct,
        phase_reached=f.phase_reached,
        deaths=f.deaths,
        deaths_before_cutoff=f.deaths_before_cutoff,
        coverage=f.coverage,
        anomalies=list(f.anomalies),
        ref=f.ref,
    )


def build_report_detail(source: ReadSource, code: str) -> ReportDetail:
    """The report's groups in the order first pulled, each with its pull summaries in time order."""
    groups = [
        ReportGroup(
            encounter_id=eid,
            encounter_name=members[0].encounter_name,
            difficulty=diff,
            difficulty_name=difficulty_name(diff),
            pulls=[summarize_pull(f, i) for i, f in enumerate(members[:PULL_CAP], start=1)],
            truncated=len(members) > PULL_CAP,
            total=len(members),
        )
        for (eid, diff), members in group_pulls(source.pulls(code))
    ]
    return ReportDetail(code=code, title=source.title(code), groups=groups, truncated=False, total=len(groups))


# ---------------------------------------------------------------------------
# Section
# ---------------------------------------------------------------------------


def _first_death_key(r: PullFacts) -> tuple[int | None, str | None] | None:
    """(ability ID, name) of the pull's earliest death, or None without deaths."""
    if r.first_death is None:
        return None
    return r.first_death.ability_id, r.first_death.ability_name


def _best(members: list[tuple[int, PullFacts]]) -> tuple[int, PullFacts] | None:
    """The first kill; otherwise the wipe with the lowest boss health, ties going to the lower fight progress."""
    if not members:
        return None
    kills = [m for m in members if m[1].kill]
    if kills:
        return kills[0]

    def key(m: tuple[int, PullFacts]) -> tuple[float, float, int]:
        p = m[1]
        hp = p.boss_hp_pct if p.boss_hp_pct is not None else float("inf")
        progress = p.boss_pct if p.boss_pct is not None else float("inf")
        return hp, progress, m[0]

    return min(members, key=key)


def build_section(pulls: list[PullFacts], findings: dict[str, FindingsState]) -> NightSection:
    """One section over ``pulls`` (one encounter and difficulty, in time order)."""
    members = list(enumerate(pulls, start=1))
    first = members[0][1]
    pull_refs = [r.ref for _, r in members]
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
            refs=[r.first_death.ref for i, r in members if keys[i] == top_key and r.first_death is not None],
            pull_refs=pull_refs,
        )

    # --- trend ---
    trend = [
        TrendRow(
            ordinal=i,
            label=f"P{i}",
            fight=r.fight_id,
            outcome="kill" if r.kill else "wipe",
            boss_hp_pct=0.0 if r.kill else r.boss_hp_pct,
            boss_pct=r.boss_pct,
            duration_ms=r.duration_ms,
            deaths_before_cutoff=r.deaths_before_cutoff,
            coverage=r.coverage,
            first_death_is_top=top_key is not None and keys[i] == top_key,
            ref=r.ref,
        )
        for i, r in members
    ]

    # --- mechanic table ---
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
                    name=stat.name if stat.name is not None else stat.mechanic_id,
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
        best = BestPull(
            fight=r.fight_id,
            ordinal=i,
            label=f"P{i}",
            outcome="kill" if r.kill else "wipe",
            boss_hp_pct=r.boss_hp_pct,
            boss_pct=r.boss_pct,
            phase_reached=r.phase_reached,
            phase_name=r.phase_name,
            ref=r.ref,
        )

    # --- findings ---
    states = [findings[r.pull_key] for _, r in members]

    coverages = {r.coverage for _, r in members}
    coverage = "none" if not first.has_encounter_file else ("partial" if "partial" in coverages else "full")
    kills = sum(1 for _, r in members if r.kill)
    return NightSection(
        encounter_id=first.encounter_id,
        encounter_name=first.encounter_name,
        difficulty=first.difficulty,
        difficulty_name=difficulty_name(first.difficulty),
        coverage=coverage,
        pulls=m,
        kills=kills,
        wipes=m - kills,
        best_pull=best,
        most_failed_mechanic=most_failed,
        most_common_first_death=first_death,
        trend=trend,
        mechanics=mechanics,
        findings_available=sum(1 for s in states if s.available),
        findings_stale=sum(1 for s in states if s.stale),
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


def build_night(source: ReadSource, code: str) -> NightSummary:
    pulls = source.pulls(code)
    findings = source.findings(pulls)
    kills = sum(1 for p in pulls if p.kill)
    return NightSummary(
        code=code,
        title=source.title(code),
        totals=NightTotals(
            pulls=len(pulls), kills=kills, wipes=len(pulls) - kills, duration_ms=sum(p.duration_ms for p in pulls)
        ),
        sections=[build_section(members, findings) for _, members in group_pulls(pulls)],
    )
