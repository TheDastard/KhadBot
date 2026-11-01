"""
api/schemas/reports.py

Reports and their loaded pulls: ``GET /v1/reports`` and ``GET /v1/reports/{code}``.
"""

from __future__ import annotations

from pydantic import BaseModel

from khadbot.api.schemas.common import Coverage, DifficultyName, Outcome


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
