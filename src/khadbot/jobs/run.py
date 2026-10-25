"""
jobs/run.py

The job functions: ``run_ingest_report``, ``run_process_pull`` and ``run_review_pull`` do the work a job spec
names, report progress through a ``Progress``, and can be run twice (the second run writes nothing new). The CLI
calls them directly; the queue calls them through ``run_job``.

Everything a job needs beyond its spec comes from a ``JobContext``: the stores, the WCL client and the token
resolver. A spec never holds a token; ``TokenResolver`` turns its ``auth`` into a WCL token provider when the job
runs: in hosted mode ``UserTokenResolver`` loads a signed-in user's own token; local mode resolves app auth only.

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

from khadbot import wiring
from khadbot.auth.errors import WclSignInRequiredError, WclUnavailableError
from khadbot.encounter.analyst import ModelMismatchError, review_pull
from khadbot.encounter.config_files import fetch_filter_inputs, load_all_encounters, load_cooldowns
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.findings import FindingsStore
from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.ingest.fetch import RawPageCache, TokenProvider
from khadbot.encounter.models import IngestResult, PullFindings
from khadbot.encounter.records_store import ReadModelProjector
from khadbot.encounter.store import EventStore
from khadbot.jobs.progress import NullProgress, Progress
from khadbot.jobs.specs import AppAuth, Auth, IngestReport, JobSpec, ProcessPull, ReviewPull, UserAuth
from khadbot.wcl.client import WarcraftLogsClient

__all__ = [
    "JobContext",
    "JobError",
    "LocalTokenResolver",
    "ModelMismatchError",
    "ProcessPullResult",
    "ReviewPullResult",
    "TokenResolver",
    "UserAuthUnavailableError",
    "UserTokenResolver",
    "UserTokenSource",
    "default_filter_kwargs",
    "default_token_resolver",
    "process_specs_for",
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


class UserTokenSource(Protocol):
    """Signed-in users' WCL access tokens (``auth.tokens.WclTokens``): loaded and refreshed when asked."""

    async def access_token(self, user_id: str, *, force_refresh: bool = False) -> str: ...


class UserTokenResolver:
    """
    Resolves ``UserAuth`` to the user's own WCL token, loaded (and refreshed if expired) on every request ingest
    makes, so a long job never runs on a stale token and the token never sits in the job's payload.

    Args:
        tokens: Builds the token source on first use. None from it means this deployment has no signed-in
            users, as in local mode. App auth never calls it.
    """

    def __init__(self, tokens: Callable[[], UserTokenSource | None]) -> None:
        self._factory = tokens
        self._source: UserTokenSource | None = None

    def _tokens(self, auth: UserAuth) -> UserTokenSource:
        if self._source is None:
            self._source = self._factory()
        if self._source is None:
            raise UserAuthUnavailableError(
                f"Cannot fetch as user '{auth.user_id}': local mode has no signed-in users. Use app auth."
            )
        return self._source

    async def provider_for(self, auth: Auth) -> TokenProvider | None:
        """
        None for app auth; for user auth, a provider that returns the user's current access token.

        Raises:
            UserAuthUnavailableError: The user has no usable WCL sign-in (never signed in, or it was revoked), or
                this deployment has no signed-in users. Raised before the job does any work.
            JobError: WCL could not renew the sign-in just now; the job can be retried.
        """
        if isinstance(auth, AppAuth):
            return None
        source = self._tokens(auth)
        user_id = auth.user_id
        await _user_token(source, user_id)  # fail before any work when the sign-in is unusable

        async def provider() -> str:
            return await _user_token(source, user_id)

        return provider


async def _user_token(source: UserTokenSource, user_id: str) -> str:
    try:
        return await source.access_token(user_id)
    except WclSignInRequiredError:
        raise UserAuthUnavailableError(
            f"User '{user_id}' has no usable Warcraft Logs sign-in; they need to sign in again."
        ) from None
    except WclUnavailableError:
        raise JobError("Warcraft Logs could not renew the user's sign-in just now; retry the job later.") from None


def default_token_resolver() -> TokenResolver:
    """The configured resolver: users' own tokens in hosted mode (built on the first ``UserAuth``), else app only."""
    return UserTokenResolver(wiring.user_tokens)


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
    store: EventStore | None = None  # where ingest writes; None is the wired event store
    cache: RawPageCache | None = None  # where ingest reads and writes raw pages; None is the wired cache
    findings: FindingsStore | None = None
    client: WarcraftLogsClient | None = None  # None: ingest opens one from the app credentials when it needs WCL
    filters: Callable[[], Mapping[str, Any]] = default_filter_kwargs
    resolver: TokenResolver = field(default_factory=lambda: default_token_resolver())
    projector: ReadModelProjector | None = field(default_factory=wiring.read_model_projector)

    def encounter_data(self) -> EncounterData:
        if self.data is None:
            self.data = wiring.encounter_data()
        return self.data

    def event_store(self) -> EventStore:
        if self.store is None:
            self.store = wiring.event_store()
        return self.store

    def raw_cache(self) -> RawPageCache:
        if self.cache is None:
            self.cache = wiring.raw_cache()
        return self.cache

    def findings_store(self) -> FindingsStore:
        if self.findings is None:
            self.findings = wiring.findings_store()
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
        store=ctx.event_store(),
        cache=ctx.raw_cache(),
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
