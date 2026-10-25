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

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from khadbot.encounter.models import PullFindings
from khadbot.errors import ErrorBody as ErrorBody  # defined at the foundation so sign-in shares it

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


class Features(BaseModel):
    """
    The optional areas of the API and whether this server offers each. A flag is true only when the area's routes are
    mounted, so a client that hides what is off never links to a route that answers 404.
    """

    sign_in: bool = Field(default=False, description="Warcraft Logs sign-in: /v1/auth/* and /v1/me")
    ingest_jobs: bool = Field(
        default=False, description="Ingest jobs and their event streams: POST /v1/reports and /v1/jobs/*"
    )
    live_watch: bool = Field(default=False, description="Live report watch")
    budget: bool = Field(default=False, description="The WCL point budget: /v1/budget")
    reviews: bool = Field(default=False, description="Pull reviews on the queue")
    chat: bool = Field(default=False, description="Chat with the conversation agent")


class Health(BaseModel):
    """
    Status, the data directory and what this server offers. ``mode`` is the entry point's label: ``local`` for
    ``main.py serve`` (read routes over a data directory, no sign-in) and ``hosted`` for the hosted API. It is shown to
    people; clients branch on ``features``, never on ``mode``.
    """

    status: Literal["ok"] = "ok"
    data_dir: str
    api_version: str = API_VERSION
    mode: Literal["local", "hosted"] = "local"
    features: Features = Field(default_factory=Features)  # type: ignore[arg-type]


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


# ---------------------------------------------------------------------------
# Ingest jobs (hosted mode)
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    """Start an ingest. ``source`` is a report code or a Warcraft Logs report URL."""

    source: str = Field(
        min_length=1,
        max_length=512,
        description="A report code, or a WCL report URL (…/reports/<code>). A #fight=N part of a URL is used only "
        "when `fights` is empty.",
        examples=["aBcD1234eFgH5678", "https://www.warcraftlogs.com/reports/aBcD1234eFgH5678#fight=3"],
    )
    fights: list[int] | None = Field(
        default=None,
        max_length=300,
        description="WCL fight IDs to ingest; empty or missing ingests every encounter fight.",
    )


class JobState(StrEnum):
    """Where a job is. ``waiting_budget`` means it waits for Warcraft Logs points; the others are self-explanatory."""

    queued = "queued"
    running = "running"
    waiting_budget = "waiting_budget"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class JobErrorCode(StrEnum):
    """Why a job failed or is waiting. The job's state says which: failed, or queued for a retry, or waiting."""

    wcl_not_signed_in = "wcl_not_signed_in"  # the user's Warcraft Logs sign-in is missing or no longer valid
    report_not_found = "report_not_found"  # missing, or private to someone else (WCL does not tell them apart)
    rate_limited = "rate_limited"  # Warcraft Logs rate limited the requests; see retry_after_s
    budget_wait = "budget_wait"  # waiting for Warcraft Logs points; see eta_s
    wcl_unreachable = "wcl_unreachable"  # Warcraft Logs could not be reached
    wcl_error = "wcl_error"  # any other answer from Warcraft Logs
    not_in_cache = "not_in_cache"  # an offline ingest asked for a page that was never fetched
    pulls_failed = "pulls_failed"  # the ingest finished, but some pulls failed (see the pulls)
    interrupted = "interrupted"  # the worker stopped before the job finished; start it again
    internal_error = "internal_error"


class JobFailure(BaseModel):
    """A typed error. Never carries a token, URL or connection string."""

    code: JobErrorCode
    message: str
    retry_after_s: float | None = Field(
        default=None, description="rate_limited: seconds until the queue retries the job, when it will"
    )
    eta_s: float | None = Field(default=None, description="budget_wait: estimated seconds until the job can run")


class PullStatus(StrEnum):
    fetching = "fetching"  # reading its events from Warcraft Logs (or the page cache)
    normalizing = "normalizing"  # writing its event table
    processing = "processing"  # annotating it and building its records
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class JobPull(BaseModel):
    """One pull of an ingest, as far as it has reported."""

    pull_key: str
    fight: int
    stage: str  # the last stage reported ("fetched <spec>", "normalizing", "records", "project")
    pct: float = Field(ge=0, le=100)
    status: PullStatus
    message: str | None = None  # why it failed


class Job(BaseModel):
    """An ingest job. Also the payload of the `job.state` and `job.done` stream events."""

    id: str
    report_code: str
    state: JobState
    stage: str | None
    pct: float = Field(ge=0, le=100)
    pulls: list[JobPull]  # by fight ID
    error: JobFailure | None = Field(
        default=None, description="Set when the job failed, is queued for a retry, or waits for points"
    )
    points_spent: float | None = None
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
