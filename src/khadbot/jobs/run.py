"""
jobs/run.py

The job functions: ``run_ingest_report``, ``run_process_pull`` and ``run_review_pull`` do the work a job spec
names, report progress through a ``Progress``, and can be run twice (the second run writes nothing new). The CLI
calls them directly; the queue calls them through ``run_job``.

Everything a job needs beyond its spec comes from a ``JobContext``: the stores, the WCL client and the token
resolver. A spec never holds a token; ``TokenResolver`` turns its ``auth`` into a WCL token provider when the job
runs. Local mode resolves app auth only.

Progress sequence per job (pull-level events carry the pull key; job-level events carry None):

    IngestReport   ("ingest", None, 0), then per pull what ``ingest_report`` reports ("fetched <spec>" ... then
                   "normalizing" 1.0), then ("ingest", None, 1)
    ProcessPull    ("records", pull, 0), ("records", pull, 1), then, with a read-model projector,
                   ("project", pull, 0), ("project", pull, 1)
    ReviewPull     ("review", pull, 0), ("review", pull, 1)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from khadbot.config import DatabaseConfig
from khadbot.encounter.analyst import ModelMismatchError, review_pull
from khadbot.encounter.config_files import fetch_filter_inputs, load_all_encounters, load_cooldowns
from khadbot.encounter.digest import PullRecords
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.findings import FindingsStore, default_findings_store
from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.ingest.fetch import RawPageCache, TokenProvider
from khadbot.encounter.models import IngestResult, PullFindings
from khadbot.encounter.store import EventStore
from khadbot.jobs.progress import NullProgress, Progress
from khadbot.jobs.specs import AppAuth, Auth, IngestReport, JobSpec, ProcessPull, ReviewPull
from khadbot.tools.warcraftlogs._client import WarcraftLogsClient

__all__ = [
    "JobContext",
    "JobError",
    "LocalTokenResolver",
    "ModelMismatchError",
    "PostgresReadModelProjector",
    "ProcessPullResult",
    "ReadModelProjector",
    "ReviewPullResult",
    "TokenResolver",
    "UserAuthUnavailableError",
    "default_filter_kwargs",
    "process_specs_for",
    "read_model_projector",
    "run_ingest_report",
    "run_job",
    "run_process_pull",
    "run_review_pull",
]

# ---------------------------------------------------------------------------
# Errors and results
# ---------------------------------------------------------------------------


class JobError(Exception):
    """A job could not run; the message is safe to show the user."""


class UserAuthUnavailableError(JobError):
    """A job asked for a signed-in user's WCL token and this deployment has no way to resolve one."""


class ProcessPullResult(BaseModel):
    pull_key: str
    records_written: bool  # False when the records were already stored (a repeat run)
    projected: bool  # the read models were projected (hosted mode with a projector)


class ReviewPullResult(BaseModel):
    pull_key: str
    findings: PullFindings
    cached: bool  # the findings came from the findings store: no model call was made
    partial: bool  # the analysis was partial or unstructured, so it was not cached


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


class TokenResolver(Protocol):
    """Turns a spec's ``auth`` into the ``TokenProvider`` ingest fetches with (None means the app token)."""

    async def provider_for(self, auth: Auth) -> TokenProvider | None: ...


class LocalTokenResolver:
    """Local mode: there are no signed-in users, so only app auth resolves."""

    async def provider_for(self, auth: Auth) -> TokenProvider | None:
        if isinstance(auth, AppAuth):
            return None
        raise UserAuthUnavailableError(
            f"Cannot fetch as user '{auth.user_id}': local mode has no signed-in users. Use app auth."
        )


# ---------------------------------------------------------------------------
# Read-model projection hook
# ---------------------------------------------------------------------------


class ReadModelProjector(Protocol):
    """Writes one pull's read-model rows (hosted mode). Must be idempotent: the same records twice change nothing."""

    async def project(self, records: PullRecords) -> None: ...


# (pool, records, report metadata) -> whether rows were written; ``khadbot.db.read_models.project_pull``.
ReadModelWriter = Callable[[Any, PullRecords, dict | None], bool]


class PostgresReadModelProjector:
    """
    Projects a pull's records into the Postgres read-model tables (``khadbot.db.read_models.project_pull``), with
    the report's cached metadata from ``raw``. Idempotent because the writer is: the same records twice write
    nothing. The pool is taken on first use, so building a projector opens no connection.
    """

    def __init__(
        self,
        raw: RawPageCache | None = None,
        *,
        pool: Callable[[], Any] | None = None,
        write: ReadModelWriter | None = None,
    ) -> None:
        self._raw = raw
        self._pool = pool
        self._write = write

    async def project(self, records: PullRecords) -> None:
        if self._raw is None:
            from khadbot.encounter.ingest.pipeline import default_cache

            self._raw = default_cache()
        if self._pool is None or self._write is None:
            from khadbot.db import get_pool, project_pull  # local mode never imports the database code

            self._pool = self._pool or get_pool
            self._write = self._write or project_pull
        raw, pool, write = self._raw, self._pool, self._write
        meta = await asyncio.to_thread(raw.report_meta, records.pull.report_code)
        await asyncio.to_thread(write, pool(), records, meta)


def read_model_projector() -> ReadModelProjector | None:
    """
    The projector ``ProcessPull`` calls after it stores a pull's records: the Postgres one in hosted mode, else
    None (local mode has no read-model tables).
    """
    if not DatabaseConfig().hosted:
        return None
    return PostgresReadModelProjector()


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def default_filter_kwargs() -> dict:
    """Fetch-filter inputs from the cooldown registries and every authored encounter file."""
    return fetch_filter_inputs(
        load_cooldowns("defensive"),
        load_cooldowns("offensive"),
        load_all_encounters(),
        utility=load_cooldowns("utility"),
    )


@dataclass
class JobContext:
    """
    What the job functions run against. Every field left None is built on first use from the configuration, so a
    caller names only what it replaces (tests: stores, client, filters; the worker: the resolver and projector).
    """

    data: EncounterData | None = None  # records, annotation cache and event store the analysis reads
    store: EventStore | None = None  # where ingest writes; None is the default event store
    cache: RawPageCache | None = None  # where ingest reads and writes raw pages; None is the default cache
    findings: FindingsStore | None = None
    client: WarcraftLogsClient | None = None  # None: ingest opens one from the app credentials when it needs WCL
    filters: Callable[[], Mapping[str, Any]] = default_filter_kwargs
    resolver: TokenResolver = field(default_factory=LocalTokenResolver)
    projector: ReadModelProjector | None = field(default_factory=read_model_projector)

    def encounter_data(self) -> EncounterData:
        if self.data is None:
            self.data = EncounterData.from_config()
        return self.data

    def findings_store(self) -> FindingsStore:
        if self.findings is None:
            self.findings = default_findings_store()
        return self.findings


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


async def run_ingest_report(
    spec: IngestReport, ctx: JobContext | None = None, progress: Progress | None = None
) -> IngestResult:
    """
    Ingest the report named by ``spec``. The result's ``pulls`` are the pulls written; a queue enqueues a
    ``ProcessPull`` for each (``process_specs_for``). Pulls whose stored data already matches are skipped, so a
    repeat run writes nothing. Raises ``UserAuthUnavailableError`` before any work when the token cannot be resolved.
    """
    ctx = ctx or JobContext()
    progress = progress or NullProgress()
    token_provider = await ctx.resolver.provider_for(spec.auth)
    progress.report("ingest", None, 0.0)
    result = await ingest_report(
        spec.code,
        spec.fights,
        store=ctx.store,
        cache=ctx.cache,
        client=ctx.client,
        offline=spec.offline,
        refresh_meta=spec.refresh_meta,
        progress=lambda pull, stage, pct: progress.report(stage, pull, pct),
        token_provider=token_provider,
        **ctx.filters(),
    )
    progress.report("ingest", None, 1.0)
    return result


def process_specs_for(result: IngestResult) -> list[ProcessPull]:
    """The ``ProcessPull`` specs to run after an ingest: one per pull it wrote."""
    return [ProcessPull(pull_key=key) for key in result.pulls]


async def run_process_pull(
    spec: ProcessPull, ctx: JobContext | None = None, progress: Progress | None = None
) -> ProcessPullResult:
    """
    Annotate the pull, build its records and store them; with a read-model projector, project its read models.

    A repeat run finds the annotation and the records stored and writes neither. Raises ``KeyError`` if the pull is
    not ingested.
    """
    ctx = ctx or JobContext()
    progress = progress or NullProgress()
    data = ctx.encounter_data()
    code = spec.pull_key.rpartition(":")[0]
    stored = _stored_records(data, code)
    progress.report("records", spec.pull_key, 0.0)
    records = await asyncio.to_thread(data.records, spec.pull_key)
    progress.report("records", spec.pull_key, 1.0)
    projected = False
    if ctx.projector is not None:
        progress.report("project", spec.pull_key, 0.0)
        await ctx.projector.project(records)
        progress.report("project", spec.pull_key, 1.0)
        projected = True
    return ProcessPullResult(
        pull_key=spec.pull_key, records_written=_stored_records(data, code) != stored, projected=projected
    )


def _stored_records(data: EncounterData, code: str) -> set[str]:
    return set(data.records_store.keys(code)) if data.records_store is not None else set()


async def run_review_pull(
    spec: ReviewPull, ctx: JobContext | None = None, progress: Progress | None = None
) -> ReviewPullResult:
    """
    Run the analyst's standard review of one pull and cache the findings, or return the cached ones (no model call,
    nothing written). Raises ``KeyError`` for a pull that is not ingested and ``ModelMismatchError`` when
    ``spec.model`` is not the configured analyst model.
    """
    ctx = ctx or JobContext()
    progress = progress or NullProgress()
    progress.report("review", spec.pull_key, 0.0)
    out = await review_pull(
        spec.pull_key,
        depth=spec.depth,
        store=ctx.encounter_data(),
        findings=ctx.findings_store(),
        expected_model_id=spec.model,
    )
    progress.report("review", spec.pull_key, 1.0)
    return ReviewPullResult(pull_key=spec.pull_key, findings=out.findings, cached=out.cached, partial=out.partial)


async def run_job(
    spec: JobSpec, ctx: JobContext | None = None, progress: Progress | None = None
) -> IngestResult | ProcessPullResult | ReviewPullResult:
    """Run the job function a spec names."""
    if isinstance(spec, IngestReport):
        return await run_ingest_report(spec, ctx, progress)
    if isinstance(spec, ProcessPull):
        return await run_process_pull(spec, ctx, progress)
    return await run_review_pull(spec, ctx, progress)
