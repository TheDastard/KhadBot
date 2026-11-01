"""
api/schemas/raids.py

The Raids page (hosted mode): ``GET /v1/lookup/report``, a report's boss pulls before any is loaded, and the body
of a 429 that says how long until the WCL budget has points.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from khadbot.api.schemas.common import DifficultyName, ErrorBody, Outcome


class BudgetWaitBody(ErrorBody):
    """The answer when the WCL budget has no points for a request now: how long until it would."""

    wait_s: float = Field(description="Seconds until the budget would have the points; try again then")


class LookupZone(BaseModel):
    """The raid zone WCL files the report under."""

    id: int
    name: str


class LookupPull(BaseModel):
    """One boss pull of a looked-up report, from the report's metadata; nothing about it has to be loaded."""

    fight: int = Field(description="WCL fight ID; the pull page is /r/{code}/p/{fight}")
    ordinal: int = Field(description="1-based within its group, time order")
    label: str = Field(description='"P<ordinal>": a display label only')
    outcome: Outcome
    duration_ms: int
    boss_pct: float | None = Field(description="WCL's fight percentage left, 0-100; the primary figure")
    boss_hp_pct: float | None = Field(description="The boss's health left, 0-100")
    ready: bool = Field(description="Whether the pull's digest is already written, so the pull page opens at once")


class LookupGroup(BaseModel):
    """One encounter and difficulty of the night."""

    encounter_id: int
    encounter_name: str
    difficulty: int | None
    difficulty_name: DifficultyName | None
    pulls: list[LookupPull] = Field(description="Time order; ordering kills first is the client's job")
    best_fight: int = Field(description="The group's best pull: its first kill, else the one with the lowest fight %")


class ReportLookup(BaseModel):
    """A report's boss pulls, grouped by encounter and difficulty, as WCL lists them now."""

    code: str
    title: str | None
    started_at: datetime = Field(description="When the report was started (UTC)")
    zone: LookupZone | None
    focus_fight: int | None = Field(description="The fight a URL's #fight= named, when it names a boss pull here")
    groups: list[LookupGroup] = Field(description="In the order first pulled")
