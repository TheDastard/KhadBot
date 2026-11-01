"""
api/schemas/jobs.py

Ingest jobs (hosted mode): ``POST /v1/reports``, ``GET /v1/jobs/{id}`` and the job event stream.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from khadbot.api.schemas.common import DifficultyName


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
    waiting = "waiting"  # the job has not started on it yet (queued, or waiting for Warcraft Logs points)
    fetching = "fetching"  # reading its events from Warcraft Logs (or the page cache)
    normalizing = "normalizing"  # writing its event table
    processing = "processing"  # annotating it and building its records
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class StepName(StrEnum):
    """The three steps a player sees while a pull opens: \"Download the fight\", \"Prepare the events\", \"Find what happened\"."""

    download = "download"
    prepare = "prepare"
    analyse = "analyse"


class StepState(StrEnum):
    waiting = "waiting"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"  # never ran: an earlier step failed, or the job was stopped


class JobStep(BaseModel):
    """One of the three steps of a pull."""

    name: StepName
    state: StepState
    pct: float = Field(ge=0, le=100)


class JobPull(BaseModel):
    """One pull of an ingest, as far as it has reported."""

    pull_key: str
    fight: int
    encounter_id: int | None = Field(default=None, description="Set once the job has read the report's fights")
    encounter_name: str | None = None
    difficulty: int | None = Field(default=None, description="WCL difficulty code")
    difficulty_name: DifficultyName | None = None
    steps: list[JobStep] = Field(description="download, prepare, analyse, in that order")
    stage: str  # for logs: the last stage reported ("fetched <spec>", "normalizing", "records", "project")
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
    points_spent: float | None = Field(default=None, description="Only for the job's own requester")
    cancel_requested: bool
    can_cancel: bool = Field(
        default=False,
        description="The caller started this job and it is still active. Anyone who can read the report may follow "
        "a job; only its requester may cancel it.",
    )
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class IngestResponse(BaseModel):
    """
    What `POST /v1/reports` answers. `ready` with no job: every pull asked for is already loaded, so open it.
    Otherwise `job` is the ingest to follow: a new one (202), or the queued or running one that already covers the
    request, whoever started it (200).
    """

    ready: bool
    job: Job | None = None
