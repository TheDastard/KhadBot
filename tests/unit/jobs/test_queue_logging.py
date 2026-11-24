"""
tests/unit/jobs/test_queue_logging.py

The worker's job lifecycle lines on procrastinate's in-memory connector: a job keeps the request ID it was queued
under, its process jobs inherit it, and every run writes one ``job.start`` and exactly one ``job.finish`` inside the
job's log context, with the outcome, error code and timing. The outcome agrees with the row the run left: an ingest
whose pulls all failed logs ``failed``, and one waiting for its process jobs logs ``awaiting_children`` and then one
``job.settled`` with the row's final state. procrastinate's own failure line loses its traceback.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from helpers.jobs.synth_queue import StubWclClient, synth_env
from procrastinate.testing import InMemoryConnector

from khadbot.config import QueueConfig
from khadbot.jobs import IngestReport, ReviewPull, TransientJobError
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.status import JobState, JobStore, MemoryJobBackend
from khadbot.logging_setup import current_log_context, log_context

CONFIG = QueueConfig(max_retries=2, retry_backoff_s=0, progress_interval_s=0.01)
REQUEST_ID = "abcd1234efgh"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class Line:
    record: logging.LogRecord
    context: dict[str, str]

    def __getattr__(self, name: str):
        return getattr(self.record, name)


class _Capture(logging.Handler):
    """Keeps each record with the log context it was written in (the formatter reads the same context)."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[Line] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(Line(record, current_log_context()))

    def events(self, event: str) -> list[Line]:
        return [line for line in self.lines if getattr(line.record, "event", None) == event]


@pytest.fixture
def captured() -> Iterator[_Capture]:
    handler = _Capture()
    loggers = [
        logging.getLogger("khadbot.jobs.queue"),
        logging.getLogger("khadbot.jobs.status"),
        logging.getLogger("procrastinate.worker"),
    ]
    saved = [lg.level for lg in loggers]
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)
    yield handler
    for lg, level in zip(loggers, saved, strict=True):
        lg.removeHandler(handler)
        lg.setLevel(level)


def make_queue(context_factory) -> tuple[JobQueue, MemoryJobBackend]:
    backend = MemoryJobBackend()
    return JobQueue(JobStore(backend), InMemoryConnector(), config=CONFIG, context_factory=context_factory), backend


async def drain(queue: JobQueue, backend: MemoryJobBackend, rounds: int = 10) -> None:
    for _ in range(rounds):
        async with queue.open():
            await queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)
        if not any(job.active and not job.awaiting_children for job in backend.rows.values()):
            return
    raise AssertionError("jobs are still active after the worker ran")


class FlakyResolver:
    """Fails with ``error`` for the first ``failures`` calls, then resolves app auth."""

    def __init__(self, error: Exception, failures: int) -> None:
        self.error, self.failures, self.calls = error, failures, 0

    async def provider_for(self, auth):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return None


@pytest.fixture
def env(tmp_path):
    return synth_env(tmp_path)


async def queue_ingest(queue: JobQueue, **spec) -> str:
    async with queue.open():
        with log_context(request_id=REQUEST_ID):  # what the API's request middleware sets
            return (await queue.enqueue(IngestReport(code="SYNTH01", offline=True, **spec))).job.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRequestId:
    async def test_an_ingest_and_its_process_jobs_keep_the_request_id(self, env):
        queue, backend = make_queue(lambda: env.context)
        ingest_id = await queue_ingest(queue)
        await drain(queue, backend)
        children = [r for r in backend.rows.values() if r.parent_id == ingest_id]
        assert children
        assert {r.request_id for r in backend.rows.values()} == {REQUEST_ID}


class TestLifecycleLines:
    async def test_every_run_logs_one_start_and_one_finish_in_the_jobs_context(self, env, captured):
        queue, backend = make_queue(lambda: env.context)
        ingest_id = await queue_ingest(queue)
        await drain(queue, backend)

        starts, finishes = captured.events("job.start"), captured.events("job.finish")
        assert sorted(line.context["job_id"] for line in starts) == sorted(backend.rows)
        assert sorted(line.context["job_id"] for line in finishes) == sorted(backend.rows)
        for line in [*starts, *finishes]:
            assert line.context["request_id"] == REQUEST_ID
            assert line.context["report_code"] == "SYNTH01"
            assert line.attempt == 1

        start = next(line for line in starts if line.context["job_id"] == ingest_id)
        assert (start.levelno, start.job_kind, start.queue) == (logging.INFO, "ingest", "ingest")
        finish = next(line for line in finishes if line.context["job_id"] == ingest_id)
        assert (finish.levelno, finish.outcome, finish.job_kind, finish.queue) == (
            logging.INFO,
            "awaiting_children",  # its process jobs were still queued when its run ended
            "ingest",
            "ingest",
        )
        assert finish.error_code is None and finish.exc_info is None
        assert isinstance(finish.duration_ms, int) and finish.duration_ms >= 0
        assert finish.points == backend.rows[ingest_id].points_spent
        assert {line.outcome for line in finishes if line.context["job_id"] != ingest_id} == {"done"}
        assert {line.job_kind for line in finishes if line.context["job_id"] != ingest_id} == {"process"}

        (settled,) = captured.events("job.settled")
        assert backend.rows[ingest_id].state is JobState.DONE
        assert (settled.levelno, settled.outcome, settled.error_code, settled.job_kind) == (
            logging.INFO,
            "done",
            None,
            "ingest",
        )
        assert settled.context == {"request_id": REQUEST_ID, "job_id": ingest_id, "report_code": "SYNTH01"}

    async def test_an_ingest_whose_pulls_all_failed_logs_the_failure_its_row_ends_with(self, env, captured):
        queue, backend = make_queue(lambda: env.context)
        async with queue.open():
            with log_context(request_id=REQUEST_ID):
                ingest_id = (await queue.enqueue(IngestReport(code="SYNTH01", offline=True, refresh_meta=False))).job.id
        env.context.filters = lambda: {}  # a filter the recorded pages do not have: every pull fails to fetch
        await drain(queue, backend)

        row = backend.rows[ingest_id]
        assert (row.state, row.error_code) == (JobState.FAILED, "pulls_failed")
        assert row.progress.pulls and {p.state for p in row.progress.pulls.values()} == {"failed"}
        (finish,) = captured.events("job.finish")
        assert (finish.outcome, finish.error_code, finish.levelno) == ("failed", "pulls_failed", logging.ERROR)
        assert finish.exc_info is None and finish.context["job_id"] == ingest_id
        assert captured.events("job.settled") == []  # the run's own job.finish already says how it ended

    async def test_a_pull_failure_settled_after_the_run_logs_job_settled_once(self, captured):
        from helpers.jobs.store_contract import ingest, process

        store = JobStore(MemoryJobBackend())
        with log_context(request_id=REQUEST_ID):
            await store.create(ingest("a", "SYNTH01"))
            await store.begin("a")
            await store.create(process("p0", "a", "SYNTH01:1"))
            waiting = await store.finish("a", JobState.DONE, awaiting_children=True, failed_pulls={"SYNTH01:2": "boom"})
        assert waiting is not None and waiting.state is JobState.RUNNING  # its job.finish says awaiting_children
        assert captured.events("job.settled") == []

        with log_context(request_id=REQUEST_ID, job_id="p0", report_code="SYNTH01"):  # the process job's run
            await store.begin("p0")
            await store.finish("p0", JobState.DONE)

        row = await store.get("a")
        assert row is not None and (row.state, row.error_code) == (JobState.FAILED, "pulls_failed")
        (settled,) = captured.events("job.settled")
        assert (settled.outcome, settled.error_code, settled.levelno) == ("failed", "pulls_failed", logging.ERROR)
        assert settled.context == {"request_id": REQUEST_ID, "job_id": "a", "report_code": "SYNTH01"}

    async def test_a_failure_logs_one_error_finish_with_its_code_and_no_procrastinate_traceback(self, env, captured):
        env.context.resolver = FlakyResolver(RuntimeError("boom"), failures=99)
        queue, backend = make_queue(lambda: env.context)
        ingest_id = await queue_ingest(queue)
        await drain(queue, backend)

        finishes = captured.events("job.finish")
        assert len(finishes) == 1
        (finish,) = finishes
        assert (finish.levelno, finish.outcome, finish.error_code) == (logging.ERROR, "failed", "internal_error")
        assert finish.context["job_id"] == ingest_id and finish.context["request_id"] == REQUEST_ID
        assert finish.exc_info is not None and isinstance(finish.exc_info[1], RuntimeError)

        ended = [
            line for line in captured.lines if line.name == "procrastinate.worker" and "ended" in line.getMessage()
        ]
        assert ended and all(line.exc_info is None for line in ended)  # the traceback is logged once, above
        tracebacks = [line for line in captured.lines if line.exc_info]
        assert tracebacks == [finish]

    async def test_a_typed_failure_logs_its_code(self, env, captured):
        queue, backend = make_queue(lambda: env.context)
        async with queue.open():
            with log_context(request_id=REQUEST_ID):
                await queue.enqueue(ReviewPull(pull_key="SYNTH01:9"))
        await drain(queue, backend)
        (finish,) = captured.events("job.finish")
        assert (finish.outcome, finish.error_code, finish.job_kind, finish.queue) == (
            "failed",
            "pull_not_ingested",
            "review",
            "review",
        )

    async def test_a_retryable_failure_logs_retrying_then_the_next_attempt(self, env, captured):
        env.context.resolver = FlakyResolver(TransientJobError("renew later"), failures=1)
        queue, backend = make_queue(lambda: env.context)
        ingest_id = await queue_ingest(queue)
        await drain(queue, backend)

        mine = [line for line in captured.events("job.finish") if line.context["job_id"] == ingest_id]
        assert [(line.outcome, line.attempt) for line in mine] == [("retrying", 1), ("awaiting_children", 2)]
        retrying = mine[0]
        assert (retrying.levelno, retrying.error_code) == (logging.WARNING, "wcl_network")
        assert retrying.exc_info is None and retrying.context["request_id"] == REQUEST_ID
        starts = [line.attempt for line in captured.events("job.start") if line.context["job_id"] == ingest_id]
        assert starts == [1, 2]

    async def test_a_cancelled_run_logs_cancelled(self, env, captured):
        queue, backend = make_queue(lambda: env.context)
        async with queue.open():
            with log_context(request_id=REQUEST_ID):
                ingest_id = (await queue.enqueue(IngestReport(code="SYNTH01"))).job.id
        env.context.client = CancelOnFirstRateLimitRead(queue, ingest_id)  # type: ignore[assignment]
        await drain(queue, backend)

        (finish,) = captured.events("job.finish")
        assert (finish.levelno, finish.outcome, finish.error_code) == (logging.INFO, "cancelled", None)
        assert finish.context["request_id"] == REQUEST_ID and finish.exc_info is None

    async def test_a_job_gone_before_it_ran_logs_gone(self, env, captured):
        queue, backend = make_queue(lambda: env.context)
        ingest_id = await queue_ingest(queue)
        await queue.store.begin(ingest_id)  # another run took it: this one finds nothing to start
        async with queue.open():
            await queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)
        (finish,) = captured.events("job.finish")
        assert (finish.outcome, finish.levelno) == ("gone", logging.INFO)


class CancelOnFirstRateLimitRead(StubWclClient):
    """Asks for the job to be cancelled when ingest first reads the rate limit, then gives the writer time to see it."""

    def __init__(self, queue: JobQueue, job_id: str) -> None:
        self.queue, self.job_id, self.done = queue, job_id, False

    async def rate_limit(self, *, user_token: str | None = None) -> dict:
        if not self.done:
            self.done = True
            await self.queue.cancel(self.job_id)
            import asyncio

            await asyncio.sleep(0.2)
        return {"pointsSpentThisHour": 0.0}


async def test_a_job_waiting_for_wcl_points_logs_waiting_budget(env, captured):
    from helpers.jobs.budget import CountingWclClient, FakeReader, MovableClock, budget_config

    from khadbot.jobs.budget_store import BudgetManager, MemoryBudgetBackend

    env.context.client = CountingWclClient(15.0)  # type: ignore[assignment]
    reader = FakeReader(limit=1000, spent=800, reset_in_s=1800)  # spent down to the headroom
    manager = BudgetManager(MemoryBudgetBackend(), budget_config(), clock=MovableClock(), reader=reader)
    backend = MemoryJobBackend()
    queue = JobQueue(
        JobStore(backend), InMemoryConnector(), config=CONFIG, context_factory=lambda: env.context, budget=manager
    )
    await manager.sync()
    async with queue.open():
        with log_context(request_id=REQUEST_ID):
            job_id = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]))).job.id
        await queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)

    (finish,) = captured.events("job.finish")
    assert (finish.outcome, finish.levelno, finish.error_code) == ("waiting_budget", logging.INFO, None)
    assert finish.context == {"request_id": REQUEST_ID, "job_id": job_id, "report_code": "SYNTH01"}
