"""
api/schemas/pulls.py

One pull's views: the timeline markers, event pages, evidence and cached findings.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, RootModel

from khadbot.encounter.evidence import EventRecord, MechanicRecord, PullRecord
from khadbot.encounter.models import CooldownUse, DeathRecord, PullFindings, WindowCoverage


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


class DeathEvidence(BaseModel):
    """A ``death`` ref: one player death with its window, top hits, killer and defensives."""

    kind: Literal["death"]
    ref: str
    record: DeathRecord


class MechanicEvidence(BaseModel):
    """A ``mech`` ref: the mechanic's stat and one row per cluster of its hits."""

    kind: Literal["mech"]
    ref: str
    record: MechanicRecord


class CooldownEvidence(BaseModel):
    """A ``cd`` ref: one cast of a cooldown-file spell."""

    kind: Literal["cd"]
    ref: str
    record: CooldownUse


class WindowEvidence(BaseModel):
    """A ``win`` ref: one occurrence of a damage window and the defensive raid cooldowns that covered it."""

    kind: Literal["win"]
    ref: str
    record: WindowCoverage


class EventEvidence(BaseModel):
    """An ``event`` ref: one annotated event."""

    kind: Literal["event"]
    ref: str
    record: EventRecord


class PullEvidence(BaseModel):
    """A ``pull`` ref: the pull summary."""

    kind: Literal["pull"]
    ref: str
    record: PullRecord


class Evidence(
    RootModel[
        Annotated[
            DeathEvidence | MechanicEvidence | CooldownEvidence | WindowEvidence | EventEvidence | PullEvidence,
            Field(discriminator="kind"),
        ]
    ]
):
    """
    The record a ref names, exactly as the analyst's verify step resolves it, typed by ``kind``. Plan refs have no
    member: the route answers them 501 ``plan_refs_unavailable`` until raid plans exist.
    """


class CachedFindings(PullFindings):
    """Cached findings for a pull; ``stale`` when the pull's events or encounter file changed since."""

    stale: bool


class FindingsList(BaseModel):
    findings: list[CachedFindings]  # newest first
    truncated: bool
    total: int
