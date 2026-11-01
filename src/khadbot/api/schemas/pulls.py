"""
api/schemas/pulls.py

One pull's views: the timeline markers, event pages, evidence and cached findings.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel

from khadbot.encounter.models import PullFindings


class RefKind(StrEnum):
    """The kind of record an evidence ref names (the part after ``ev:<report>:<fight>:``, or ``plan``)."""

    death = "death"
    mech = "mech"
    cd = "cd"
    win = "win"
    event = "event"
    pull = "pull"
    plan = "plan"


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
