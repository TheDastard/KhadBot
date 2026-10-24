"""
api/schemas.py

Response models of the dashboard API. They are the source of ``contracts/openapi.json``, from which the frontend's
types are generated, so a field change here is a contract change: regenerate it with
``uv run python scripts/dev/gen_contracts.py``.

Records the encounter pipeline already defines (``PullDigest``, ``PullFindings``) are returned as built. Times are
fight-relative milliseconds, pulls are addressed by WCL fight ID, and every ``ref`` is an evidence ref
(``ev:<report>:<fight>:...``) that ``GET /v1/evidence/{ref}`` resolves.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from khadbot.encounter.models import PullFindings

API_VERSION = "1.0.0"

Coverage = Literal["full", "partial", "none"]
Outcome = Literal["kill", "wipe"]
DifficultyName = Literal["lfr", "normal", "heroic", "mythic"]


class RefKind(StrEnum):
    """The kind of record an evidence ref names (the part after ``ev:<report>:<fight>:``, or ``plan``)."""

    death = "death"
    mech = "mech"
    cd = "cd"
    win = "win"
    event = "event"
    pull = "pull"
    plan = "plan"


# ---------------------------------------------------------------------------
# Errors and health
# ---------------------------------------------------------------------------


class ErrorBody(BaseModel):
    """Every error response: a stable machine code and a message for people."""

    code: str = Field(examples=["report_not_found", "pull_not_found", "unresolvable_ref", "bad_window"])
    message: str


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    data_dir: str
    api_version: str = API_VERSION


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


class GroupInfo(BaseModel):
    """One encounter and difficulty within a report."""

    encounter_id: int
    encounter_name: str
    difficulty: int | None  # WCL difficulty code
    difficulty_name: DifficultyName | None
    pulls: int


class ReportInfo(BaseModel):
    code: str
    title: str | None  # from the cached report metadata, when present
    pulls: int
    groups: list[GroupInfo]  # in the order first pulled


class ReportList(BaseModel):
    reports: list[ReportInfo]
    truncated: bool
    total: int


class PullSummary(BaseModel):
    """One pull in a report group. Every value is in the record its ``ref`` (a ``pull`` ref) resolves to."""

    fight: int
    ordinal: int  # 1-based within the group, time order
    label: str  # "P<ordinal>": a display label only; URLs use ``fight``
    outcome: Outcome
    duration_ms: int
    boss_pct: float | None  # fight progress, 0-100
    boss_hp_pct: float | None  # boss health, 0-100
    phase_reached: str | None
    deaths: int
    deaths_before_cutoff: int
    coverage: Coverage
    anomalies: list[str]
    ref: str


class ReportGroup(BaseModel):
    encounter_id: int
    encounter_name: str
    difficulty: int | None
    difficulty_name: DifficultyName | None
    pulls: list[PullSummary]  # time order
    truncated: bool
    total: int


class ReportDetail(BaseModel):
    code: str
    title: str | None
    groups: list[ReportGroup]  # in the order first pulled
    truncated: bool
    total: int


# ---------------------------------------------------------------------------
# Night summary
# ---------------------------------------------------------------------------


class NightTotals(BaseModel):
    pulls: int
    kills: int
    wipes: int
    duration_ms: int  # summed pull durations


class BestPull(BaseModel):
    """The section's first kill, or else its wipe with the lowest boss health (ties: lower fight progress)."""

    fight: int
    ordinal: int
    label: str
    outcome: Outcome
    boss_hp_pct: float | None
    boss_pct: float | None
    phase_reached: str | None
    phase_name: str | None  # the encounter file's name for ``phase_reached``
    ref: str


class MostFailedMechanic(BaseModel):
    mechanic_id: str
    name: str
    failures: int  # summed over the section's pulls (uncapped records)
    pulls_failed: int  # pulls with at least one failure
    refs: list[str]  # the ``mech`` ref of each pull with a failure


class FirstDeath(BaseModel):
    """The killing ability of the earliest death that is most common across the section's pulls."""

    ability_id: int | None
    ability_name: str | None
    pulls: int  # N in "on N of M pulls"
    of_pulls: int  # M: every pull in the section
    refs: list[str]  # the ``death`` ref of the first death on each of the N pulls
    pull_refs: list[str]  # the ``pull`` ref of each of the M pulls


class TrendRow(BaseModel):
    ordinal: int
    label: str
    fight: int
    outcome: Outcome
    boss_hp_pct: float | None  # 0 on a kill
    boss_pct: float | None
    duration_ms: int
    deaths_before_cutoff: int
    coverage: Coverage
    first_death_is_top: bool  # this pull's first death is the most common first death
    ref: str


class NightMechanic(BaseModel):
    mechanic_id: str
    name: str
    failures: int
    hits: int
    damage_taken: int
    pulls_affected: int  # pulls with a hit or a failure
    refs: list[str]  # the ``mech`` ref of each affected pull


class BriefingLine(BaseModel):
    """A fixed-template sentence; every number in ``text`` appears in the records its ``refs`` resolve to."""

    kind: Literal["first_death", "most_failed_mechanic", "best_pull"]
    text: str
    refs: list[str]


class NightSection(BaseModel):
    """One encounter and difficulty of the report, computed over that group's pulls only."""

    encounter_id: int
    encounter_name: str
    difficulty: int | None
    difficulty_name: DifficultyName | None
    coverage: Coverage  # none: no encounter file for this difficulty; partial: some pull is partial; else full
    pulls: int
    kills: int
    wipes: int
    best_pull: BestPull | None
    most_failed_mechanic: MostFailedMechanic | None
    most_common_first_death: FirstDeath | None
    trend: list[TrendRow]
    mechanics: list[NightMechanic]  # by failures desc, then damage desc
    findings_available: int  # pulls with cached findings, stale or not
    findings_stale: int  # pulls with at least one stale cached finding set
    briefing: list[BriefingLine]


class NightSummary(BaseModel):
    code: str
    title: str | None
    totals: NightTotals
    sections: list[NightSection]  # in the order first pulled


# ---------------------------------------------------------------------------
# Pull views
# ---------------------------------------------------------------------------


class PhaseMarker(BaseModel):
    id: str
    name: str | None
    start_ms: int
    end_ms: int


class DeathMarker(BaseModel):
    t_ms: int
    name: str
    after_cutoff: bool
    ref: str


class MechanicMarker(BaseModel):
    """One cluster of a mechanic's hits. Its ``ref`` is the mechanic's ``mech`` ref (clusters have none)."""

    mechanic_id: str
    name: str | None
    cluster: int
    t_ms: int
    end_ms: int
    hits: int
    failures: int
    ref: str


class CooldownMarker(BaseModel):
    t_ms: int
    spell_id: int
    spell: str
    category: Literal["defensive", "offensive", "utility"]  # the cooldown file the spell is in
    caster: str
    ref: str


class WindowMarker(BaseModel):
    t_ms: int
    window_id: str
    name: str | None
    occurrence: int
    covered: bool
    ref: str


class PullTimeline(BaseModel):
    """Digest-level markers for one pull, built from the same uncapped records the digest uses."""

    pull_key: str
    duration_ms: int
    cutoff_ms: int | None
    ref: str
    phases: list[PhaseMarker]
    deaths: list[DeathMarker]
    mechanics: list[MechanicMarker]
    cooldowns: list[CooldownMarker]
    windows: list[WindowMarker]


class EventsPage(BaseModel):
    """Event rows exactly as their ``event`` refs resolve, capped (see ``truncated`` and ``total``)."""

    rows: list[dict[str, Any]]
    refs: list[str]
    truncated: bool
    total: int
    note: str | None = None


class Evidence(BaseModel):
    """The record a ref names, exactly as the analyst's verify step resolves it."""

    ref: str
    kind: RefKind
    record: dict[str, Any]


class CachedFindings(PullFindings):
    """Cached findings for a pull; ``stale`` when the pull's events or encounter file changed since."""

    stale: bool


class FindingsList(BaseModel):
    findings: list[CachedFindings]  # newest first
    truncated: bool
    total: int
