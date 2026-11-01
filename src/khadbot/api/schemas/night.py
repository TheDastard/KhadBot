"""
api/schemas/night.py

The night summary of one report (``GET /v1/reports/{code}/night``): per encounter and difficulty.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from khadbot.api.schemas.common import Coverage, DifficultyName, Outcome


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
