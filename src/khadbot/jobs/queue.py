"""
jobs/queue.py

The hosted job queue: a procrastinate app on the same Postgres, with a task per job kind on the ``ingest``,
``process`` and ``review`` queues. A task wraps the job function in ``jobs/run.py``; it adds what the CLI does not
need: the ``jobs`` row (``status``), progress and cancellation (``reporting``), typed failures and retries
(``failures``).

Rules the queue keeps:

- A job payload is the ``jobs`` row's ID. The spec, with a user ID and never a token, is in the row.
- One ingest runs per report. An ingest request returns the queued or running ingest that already covers it; a
  procrastinate lock on the report code also stops two from running at once, and a queueing lock on the report and
  the request stops two identical ones from waiting.
- An ingest enqueues a ``ProcessPull`` for every pull it wrote or found unchanged, and finishes with them.
- Only WCL rate limits, network errors and a sign-in that could not be renewed are retried, with backoff. Anything
  else fails the job once with a typed code.
- A backfill's process jobs are queued at a lower priority than every other job (``enqueue_backfill``), once per pull.
- A live report watch (``jobs/watch.py``) polls on the ``watch`` queue: one task run is one poll, and it queues the
  next one. The ingests it queues carry ``live``, which lets them use the budget's headroom.
- The records cleanup (``jobs/retention.py``) runs on the ``process`` queue on a schedule any worker registers.
- Cancelling a queued job drops it; cancelling a running one stops it at the next page boundary.
- With a WCL budget (``jobs/budget_store.py``), an online ingest reserves its estimated points when a worker picks it
  up and settles to its measured spend when it ends. One that can't reserve goes to ``waiting_budget`` and is queued
  again when points free up, or at its next recheck time.
- An ingest's measured WCL points are written to the usage ledger (``jobs/usage.py``) when it settles, the same figure
  the budget settles to. Every job logs with its job, user and report IDs as context.

This module needs the ``hosted`` dependency group; nothing in local mode imports it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import procrastinate
from procrastinate import exceptions as procrastinate_exceptions
from procrastinate.retry import RetryStrategy
from pydantic import TypeAdapter

from khadbot.config import DatabaseConfig, QueueConfig, RetentionConfig, WatchConfig
from khadbot.encounter.ingest.normalize import parse_report_meta
from khadbot.encounter.models import IngestResult
from khadbot.jobs import budget as budget_policy
from khadbot.jobs.backfill import BACKFILL_PRIORITY
from khadbot.jobs.budget import Admission, Ask
from khadbot.jobs.failures import ErrorCode, JobCancelled, classify
from khadbot.jobs.reporting import CancelFlag, CancellingRawCache, ProgressWriter
from khadbot.jobs.retention import CleanupResult, CleanupSources, cleanup_records
from khadbot.jobs.run import JobContext, TransientJobError, default_token_resolver, run_job
from khadbot.jobs.specs import IngestReport, JobSpec, ProcessPull, UserAuth
from khadbot.jobs.status import JobKind, JobRecord, JobState, JobStore, NewJob, PostgresJobBackend
from khadbot.jobs.usage import UsageLedger, record_wcl_points
from khadbot.jobs.watch import WatchService
from khadbot.jobs.watch_store import MemoryWatchBackend, PostgresWatchBackend, WatchRecord, WatchStore
from khadbot.logging_setup import log_context

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from khadbot.jobs.budget_store import BudgetManager

logger = logging.getLogger(__name__)

_SPEC: TypeAdapter[JobSpec] = TypeAdapter(JobSpec)

QUEUE_NAMES = {JobKind.INGEST: "ingest", JobKind.PROCESS: "process", JobKind.REVIEW: "review"}
CLEANUP_TASK = "khadbot.cleanup_records"
WATCH_QUEUE = "watch"
WATCH_TASK = "khadbot.watch_report"
TASK_NAMES = {
    JobKind.INGEST: "khadbot.ingest_report",
    JobKind.PROCESS: "khadbot.process_pull",
    JobKind.REVIEW: "khadbot.review_pull",
}

# ---------------------------------------------------------------------------
# Describing a spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Enqueued:
    """The job an enqueue returned. ``created`` is False when an ingest already queued or running was returned."""

    job: JobRecord
    created: bool


def _describe(spec: JobSpec) -> tuple[JobKind, str, str | None]:
    """The job's kind, its report code and (process and review) its pull key."""
    if isinstance(spec, IngestReport):
        return JobKind.INGEST, spec.code, None
    kind = JobKind.PROCESS if isinstance(spec, ProcessPull) else JobKind.REVIEW
    return kind, spec.pull_key.rpartition(":")[0], spec.pull_key


def _locks(spec: JobSpec) -> tuple[str, str | None]:
    """(lock, queueing lock): jobs with one lock never run together; one queueing lock never waits twice."""
    if isinstance(spec, IngestReport):
        digest = hashlib.sha256(spec.model_dump_json(exclude={"auth"}).encode()).hexdigest()[:12]
        return f"ingest:{spec.code}", f"ingest:{spec.code}:{digest}"
    if isinstance(spec, ProcessPull):
        return f"process:{spec.pull_key}", None
    return f"review:{spec.pull_key}", None


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


class JobQueue:
    """
    The enqueue and cancel API, and the tasks a worker runs.

    Args:
        store: The ``jobs`` table.
        connector: procrastinate's connector (``PsycopgConnector`` in hosted mode, ``InMemoryConnector`` in tests).
        config: Retry and progress settings.
        context_factory: Builds the ``JobContext`` one job runs against. The default builds it from the configuration;
            tests pass a factory that returns fixture stores and a stub WCL client.
        budget: The WCL budget online ingests reserve points from; None runs every job without one.
        watches: The ``watches`` table; None keeps watches in memory (unit tests).
        watch_config: Poll interval, quiet period and maximum length of a live report watch.
        usage: The usage ledger an ingest's measured WCL points are written to; None writes none.
    """

    def __init__(
        self,
        store: JobStore,
        connector: Any,
        *,
        config: QueueConfig | None = None,
        context_factory: Callable[[], JobContext] | None = None,
        retention: RetentionConfig | None = None,
        cleanup: CleanupSources | None = None,
        budget: BudgetManager | None = None,
        watches: WatchStore | None = None,
        watch_config: WatchConfig | None = None,
        usage: UsageLedger | None = None,
    ) -> None:
        self.store = store
        self.budget = budget
        self.usage = usage
        self.config = config or QueueConfig()
        self._context_factory = context_factory or JobContext
        self.app = procrastinate.App(connector=connector)
        retry = RetryStrategy(
            max_attempts=self.config.max_retries,
            exponential_wait=self.config.retry_backoff_s,
            retry_exceptions=[TransientJobError],
        )
        for kind in JobKind:
            self._register(kind, retry)
        self._register_cleanup(retention or RetentionConfig(), cleanup or CleanupSources())
        self.watches = self._register_watch(watches or WatchStore(MemoryWatchBackend()), watch_config or WatchConfig())

    def _register(self, kind: JobKind, retry: RetryStrategy) -> None:
        async def run(context: procrastinate.JobContext, job_id: str) -> None:
            await self._execute(context, job_id)

        self.app.task(name=TASK_NAMES[kind], queue=QUEUE_NAMES[kind], pass_context=True, retry=retry)(run)

    def _register_cleanup(self, retention: RetentionConfig, sources: CleanupSources) -> None:
        self._retention = retention
        self._cleanup_sources = sources

        async def run(timestamp: int) -> None:
            await self.run_cleanup()

        task = self.app.task(name=CLEANUP_TASK, queue=QUEUE_NAMES[JobKind.PROCESS], queueing_lock=CLEANUP_TASK)(run)
        if retention.cleanup_cron:
            self.app.periodic(cron=retention.cleanup_cron)(task)

    def _register_watch(self, store: WatchStore, config: WatchConfig) -> WatchService:
        """The live report watch: its task (one poll per run) and the service the API starts and stops watches with."""
        service = WatchService(
            store,
            enqueue=lambda spec, user_id: self.enqueue(spec, requested_by=user_id),
            schedule=self._schedule_poll,
            cancel_poll=self._cancel_poll,
            config=config,
            context_factory=self._context_factory,
            budget=self.budget,
            wake=self._wake,
        )

        async def run(watch_id: str) -> None:
            await service.poll(watch_id)

        self.app.task(name=WATCH_TASK, queue=WATCH_QUEUE)(run)
        return service

    async def _schedule_poll(self, watch: WatchRecord, at: datetime | None) -> int:
        """Queue the watch's next poll at ``at`` (None: now). One poll waits per watch; one report polls at a time."""
        deferrer = self.app.tasks[WATCH_TASK].configure(
            lock=f"watch:{watch.report_code}", queueing_lock=f"watch:{watch.id}", schedule_at=at
        )
        try:
            return int(await deferrer.defer_async(watch_id=watch.id))
        except procrastinate_exceptions.AlreadyEnqueued:
            # A poll that did not come from the queue (or a lost reply) left one waiting: this one supersedes it.
            if watch.queue_job_id is not None:
                await self._cancel_poll(watch.queue_job_id)
            return int(await deferrer.defer_async(watch_id=watch.id))

    async def _cancel_poll(self, queue_job_id: int) -> None:
        await self.app.job_manager.cancel_job_by_id_async(queue_job_id)

    # -- enqueue and cancel ---------------------------------------------------

    async def enqueue(
        self, spec: JobSpec, *, requested_by: str | None = None, parent: JobRecord | None = None
    ) -> Enqueued:
        """
        Queue a job. An ingest that an ingest already queued or running covers returns that one (``created`` False).
        ``requested_by`` defaults to the spec's user, else the parent's.
        """
        kind, report, pull_key = _describe(spec)
        if isinstance(spec, IngestReport):
            auth_kind = spec.auth.kind
            if requested_by is None and isinstance(spec.auth, UserAuth):
                requested_by = spec.auth.user_id
        else:
            auth_kind = parent.auth_kind if parent else "app"
            requested_by = requested_by or (parent.requested_by if parent else None)
        new = NewJob(
            id=uuid.uuid4().hex,
            kind=kind,
            report_code=report,
            pull_key=pull_key,
            parent_id=parent.id if parent else None,
            requested_by=requested_by,
            auth_kind=auth_kind,
            spec=spec.model_dump(mode="json"),
        )
        if kind is JobKind.INGEST:
            job, created = await self.store.claim_ingest(new)
            if not created:
                return Enqueued(job, False)
        else:
            job = await self.store.create(new)
        lock, queueing_lock = _locks(spec)
        deferrer = self.app.tasks[TASK_NAMES[kind]].configure(lock=lock, queueing_lock=queueing_lock)
        try:
            queue_job_id = await deferrer.defer_async(job_id=job.id)
        except procrastinate_exceptions.AlreadyEnqueued:
            await self.store.finish(
                job.id, JobState.CANCELLED, error_code="already_queued", error_message="An identical job is queued."
            )
            active = await self.store.active_ingests(report)
            if active:
                return Enqueued(active[0], False)
            raise
        except Exception:
            await self.store.finish(
                job.id, JobState.FAILED, error_code=ErrorCode.INTERNAL, error_message="The job could not be queued."
            )
            raise
        await self.store.set_queue_job_id(job.id, queue_job_id)
        return Enqueued(job, True)

    async def enqueue_backfill(self, pull_key: str) -> Enqueued:
        """
        Queue a ``ProcessPull`` for a backfill at ``BACKFILL_PRIORITY``, app auth. A pull with a process job already
        queued or running returns that job (``created`` False), so queueing a backfill twice queues each pull once.
        """
        spec = ProcessPull(pull_key=pull_key)
        _, report, _ = _describe(spec)
        for active in await self.store.active_process(report):
            if active.pull_key == pull_key:
                return Enqueued(active, False)
        job = await self.store.create(
            NewJob(
                id=uuid.uuid4().hex,
                kind=JobKind.PROCESS,
                report_code=report,
                pull_key=pull_key,
                auth_kind="app",
                spec=spec.model_dump(mode="json"),
            )
        )
        lock, _ = _locks(spec)
        deferrer = self.app.tasks[TASK_NAMES[JobKind.PROCESS]].configure(
            lock=lock, queueing_lock=f"backfill:{pull_key}", priority=BACKFILL_PRIORITY
        )
        try:
            queue_job_id = await deferrer.defer_async(job_id=job.id)
        except procrastinate_exceptions.AlreadyEnqueued:  # another backfill queued it between the check and here
            gone = await self.store.finish(
                job.id, JobState.CANCELLED, error_code="already_queued", error_message="An identical job is queued."
            )
            existing = [a for a in await self.store.active_process(report) if a.pull_key == pull_key]
            return Enqueued(existing[0] if existing else (gone or job), False)
        except Exception:
            await self.store.finish(
                job.id, JobState.FAILED, error_code=ErrorCode.INTERNAL, error_message="The job could not be queued."
            )
            raise
        await self.store.set_queue_job_id(job.id, queue_job_id)
        return Enqueued(job, True)

    async def run_cleanup(self, *, dry_run: bool = False) -> CleanupResult | None:
        """
        Remove records no read-model row references and that are older than ``KHADBOT_RECORDS_MIN_AGE_DAYS``. None
        when this deployment has no read models to check against (nothing is removed without them).
        """
        index = self._cleanup_sources.index()
        if index is None:
            logger.info("Records cleanup skipped: no read-model index in this deployment")
            return None
        blobs = self._cleanup_sources.blobs()
        min_age = timedelta(days=self._retention.records_min_age_days)
        return await asyncio.to_thread(cleanup_records, blobs, index, min_age=min_age, dry_run=dry_run)

    async def cancel(self, job_id: str) -> JobRecord | None:
        """
        Cancel a job and the process jobs under it. Queued ones are dropped at once; a running one stops at its next
        page boundary, with written pulls kept (the raw page cache resumes). None if there is no such job.
        """
        job, dropped = await self.store.request_cancel(job_id)
        for gone in dropped:
            if gone.queue_job_id is not None:
                await self.app.job_manager.cancel_job_by_id_async(gone.queue_job_id)
        return job

    # -- worker ---------------------------------------------------------------

    def open(self) -> Any:
        """Open the queue's connection pool: ``async with queue.open():``."""
        return self.app.open_async()

    async def run_worker(
        self,
        *,
        queues: list[str] | None = None,
        concurrency: int | None = None,
        wait: bool = True,
        listen_notify: bool = True,
        install_signal_handlers: bool = True,
    ) -> None:
        """Run jobs from ``queues`` (default ``QueueConfig.queues``) until stopped, or until idle when ``wait`` is off."""
        await self.app.run_worker_async(
            queues=queues or list(self.config.queues),
            concurrency=concurrency or self.config.concurrency,
            wait=wait,
            listen_notify=listen_notify,
            install_signal_handlers=install_signal_handlers,
            **({"shutdown_graceful_timeout": self.config.shutdown_timeout_s} if self.config.shutdown_timeout_s else {}),
            name="khadbot",
        )

    # -- the task body --------------------------------------------------------

    def _context(self, flag: CancelFlag) -> JobContext:
        ctx = self._context_factory()
        return replace(ctx, cache=CancellingRawCache(ctx.raw_cache(), flag))

    async def _execute(self, context: procrastinate.JobContext, job_id: str) -> None:
        row = await self.store.get(job_id)
        with log_context(
            job_id=job_id, user_id=row.requested_by if row else None, report_code=row.report_code if row else None
        ):
            await self._execute_job(context, job_id)

    async def _execute_job(self, context: procrastinate.JobContext, job_id: str) -> None:
        reserved = await self._reserve_budget(job_id)
        if reserved is None:
            return  # waiting for WCL points: queued again when they free up
        job = await self.store.begin(job_id)
        if job is None:
            logger.info("Job %s is gone, finished or cancelled; not running it", job_id)
            if reserved:
                await self._release_budget(job_id)
            return
        spec = _SPEC.validate_python(job.spec)
        flag = CancelFlag()
        writer = ProgressWriter(self.store, job_id, flag, self.config.progress_interval_s)
        result: object = None
        try:
            async with writer:
                result = await run_job(spec, self._context(flag), writer)
                if isinstance(result, IngestResult):
                    await self._queue_processing(job, result)
            if isinstance(result, IngestResult):  # finish once the last progress is written
                await self.store.finish(
                    job_id,
                    JobState.DONE,
                    points_spent=result.points_spent,
                    failed_pulls=result.failed,
                    awaiting_children=True,
                )
            else:
                await self.store.finish(job_id, JobState.DONE)
        except JobCancelled:
            await self.store.finish(job_id, JobState.CANCELLED)
            raise procrastinate_exceptions.JobAborted from None
        except asyncio.CancelledError:
            # The worker is stopping and ran out of patience. The job's written data stays; ask for it again.
            await asyncio.shield(
                self.store.finish(
                    job_id,
                    JobState.FAILED,
                    error_code=ErrorCode.INTERRUPTED,
                    error_message="The worker stopped before the job finished; request it again.",
                )
            )
            raise
        except Exception as exc:
            failure = classify(exc)
            if failure.retryable:
                transient = TransientJobError(failure.message)
                if context.task.get_retry_exception(transient, context.job) is not None:
                    await self.store.requeue(job_id, failure.code, failure.message)
                    raise transient from exc
            await self.store.finish(job_id, JobState.FAILED, error_code=failure.code, error_message=failure.message)
            raise
        finally:
            spent = result.points_spent if isinstance(result, IngestResult) else None
            if isinstance(result, IngestResult):
                await asyncio.shield(self._record_usage(job, spent))
            if reserved:
                await asyncio.shield(self._settle_budget(job_id, spent))

    async def _record_usage(self, job: JobRecord, points: float | None) -> None:
        """Write the ingest's measured WCL points to the ledger: the figure the budget settles to, at the same point."""
        if self.usage is None:
            return
        try:
            await record_wcl_points(
                self.usage, points, job_id=job.id, user_id=job.requested_by, report_code=job.report_code
            )
        except Exception:  # noqa: BLE001 - the ledger must never change how the job ended
            logger.warning("Could not write the usage ledger for job %s", job.id, exc_info=True)

    async def _queue_processing(self, job: JobRecord, result: IngestResult) -> None:
        """
        Queue a ProcessPull per pull. Unchanged pulls are queued too: a cancelled earlier run may have written them
        without queueing them, and processing a processed pull writes nothing.
        """
        for key in [*result.pulls, *result.skipped]:
            await self.enqueue(ProcessPull(pull_key=key), parent=job)

    # -- the WCL budget (job pick-up path) -------------------------------------

    async def _reserve_budget(self, job_id: str) -> bool | None:
        """
        Before a job starts: reserve its WCL points. True when it holds a reservation, False when it needs none
        (no budget, not an online ingest, or no longer startable), None when it must wait: it is then in
        ``waiting_budget`` with a wake-up queued for its recheck time.
        """
        if self.budget is None:
            return False
        job = await self.store.get(job_id)
        if job is None or job.kind is not JobKind.INGEST or job.state not in (JobState.QUEUED, JobState.WAITING_BUDGET):
            return False
        spec = IngestReport.model_validate(job.spec)
        if spec.offline:
            return False  # reads the raw page cache only: no points
        admission = await self.budget.request(await self._ask(job, spec))
        await self._wake(admission, skip=job_id)
        if job_id in admission.granted:
            if job.state is JobState.WAITING_BUDGET and not await self.store.resume_from_budget(job_id):
                await self._release_budget(job_id)
                return False
            return True
        wait = admission.waits.get(job_id)
        if not await self.store.wait_for_budget(job_id):
            await self._release_budget(job_id)  # cancelled meanwhile
            return False
        if wait is not None:
            logger.info("Job %s waits for WCL points (%s), place %d", job_id, wait.reason.value, wait.position)
            await self._defer_again(job_id, schedule_at=wait.recheck_at)
        return None

    async def _ask(self, job: JobRecord, spec: IngestReport) -> Ask:
        """The budget request for an ingest: its estimate, who asked, the report's guild and whose token it uses."""
        assert self.budget is not None
        cfg = self.budget.config
        pulls = 0 if spec.refresh_meta else self._pull_count(spec)
        return Ask(
            job_id=job.id,
            report_code=job.report_code,
            user_id=job.requested_by,
            guild_id=await self.budget.guild_of(job.report_code),
            user_token=isinstance(spec.auth, UserAuth),
            points=budget_policy.estimate_points(pulls, cfg),
            live=spec.live,
            asked_at=job.created_at,
        )

    def _pull_count(self, spec: IngestReport) -> int:
        """The pulls an ingest fetches: its fights, else the boss fights in cached metadata, else a raid night."""
        assert self.budget is not None
        if spec.fights is not None:
            return len(spec.fights)
        try:
            meta = self._context_factory().raw_cache().report_meta(spec.code)
            if meta is not None:
                return len(parse_report_meta(spec.code, meta)[0])
        except Exception:  # noqa: BLE001 - an estimate must never stop a job; fall back to the default
            logger.warning("Could not count the pulls of %s from cached metadata", spec.code, exc_info=True)
        return self.budget.config.default_pulls

    async def _settle_budget(self, job_id: str, spent: float | None) -> None:
        """After a job: charge its measured spend, take WCL's count, and queue the waiting jobs that now fit."""
        assert self.budget is not None
        try:
            await self.budget.settle(job_id, spent)
            await self.budget.sync()
            await self._wake(await self.budget.admit_waiting())
        except Exception:  # noqa: BLE001 - settling must never change how the job ended
            logger.warning("Could not settle the WCL budget for job %s", job_id, exc_info=True)

    async def _release_budget(self, job_id: str) -> None:
        assert self.budget is not None
        await self.budget.release(job_id)
        await self._wake(await self.budget.admit_waiting())

    async def _wake(self, admission: Admission, *, skip: str | None = None) -> None:
        """Queue again each waiting job the budget just granted; release the grant of one that was cancelled."""
        assert self.budget is not None
        for job_id in admission.granted:
            if job_id == skip:
                continue
            if not await self.store.resume_from_budget(job_id):
                await self.budget.release(job_id)
                continue
            await self._defer_again(job_id)

    async def _defer_again(self, job_id: str, *, schedule_at: datetime | None = None) -> None:
        """
        Queue a new run of a job that already has its row: now, or at ``schedule_at``. It keeps the report's lock
        but takes no queueing lock, so it can sit next to an earlier wake-up, which is cancelled.
        """
        job = await self.store.get(job_id)
        if job is None:
            return
        lock, _ = _locks(_SPEC.validate_python(job.spec))
        deferrer = self.app.tasks[TASK_NAMES[job.kind]].configure(lock=lock, schedule_at=schedule_at)
        queue_job_id = await deferrer.defer_async(job_id=job_id)
        await self.store.set_queue_job_id(job_id, queue_job_id)
        if job.queue_job_id is not None and job.queue_job_id != queue_job_id:
            try:
                await self.app.job_manager.cancel_job_by_id_async(job.queue_job_id)
            except Exception:  # noqa: BLE001 - an earlier wake-up that already ran or is running is harmless
                logger.debug("Could not cancel the earlier queue job of %s", job_id, exc_info=True)


# ---------------------------------------------------------------------------
# Hosted wiring
# ---------------------------------------------------------------------------


def build_queue(
    *,
    pool: ConnectionPool | None = None,
    database_url: str | None = None,
    config: QueueConfig | None = None,
    context_factory: Callable[[], JobContext] | None = None,
    retention: RetentionConfig | None = None,
    cleanup: CleanupSources | None = None,
    budget: BudgetManager | None = None,
    watch_config: WatchConfig | None = None,
    usage: UsageLedger | None = None,
) -> JobQueue:
    """
    The queue on the configured Postgres. The ``jobs`` table uses the process-wide pool; procrastinate keeps its own
    async pool on the same ``DATABASE_URL`` (psycopg's sync pool cannot serve its async connector).
    """
    from khadbot.db import get_pool
    from khadbot.jobs.usage import PostgresUsageLedger

    cfg = config or QueueConfig()
    url = database_url or DatabaseConfig().database_url
    connector = procrastinate.PsycopgConnector(conninfo=url, min_size=1, max_size=cfg.concurrency + 4)
    shared_pool = pool or get_pool()
    store = JobStore(PostgresJobBackend(shared_pool))
    return JobQueue(
        store,
        connector,
        config=cfg,
        context_factory=context_factory,
        retention=retention,
        cleanup=cleanup,
        budget=budget,
        watches=WatchStore(PostgresWatchBackend(pool or get_pool())),
        watch_config=watch_config,
        usage=usage or PostgresUsageLedger(shared_pool),
    )


async def serve_worker(queues: list[str] | None = None, concurrency: int | None = None) -> None:
    """Run a worker on the configured stores until it is stopped (SIGINT or SIGTERM). The ``worker`` subcommand."""
    from khadbot.jobs.budget_store import build_budget_manager
    from khadbot.jobs.health import heartbeat

    resolver = default_token_resolver()  # one for the process: it builds the sign-in service on the first user job
    budget = build_budget_manager()
    queue = build_queue(context_factory=lambda: JobContext(resolver=resolver), budget=budget)
    await budget.sync()  # take WCL's count before the first job reserves
    async with heartbeat(), queue.open():  # the liveness file `main.py worker --check` reads
        await queue.run_worker(queues=queues, concurrency=concurrency)


__all__ = ["Enqueued", "JobQueue", "build_queue", "serve_worker"]
