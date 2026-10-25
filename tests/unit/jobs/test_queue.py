"""
tests/unit/jobs/test_queue.py

The queue end to end on procrastinate's in-memory connector and the in-memory ``jobs`` table: enqueue, locks, a
worker running SYNTH01's ingest and its process jobs, retries, typed failures and cancellation. Postgres runs the
same flows in tests/integration/hosted/test_queue_pg.py.
"""

from __future__ import annotations

import pytest
from helpers.jobs.synth_queue import PULLS, StubWclClient, synth_env
from procrastinate.testing import InMemoryConnector

from khadbot.config import QueueConfig
from khadbot.jobs import IngestReport, JobContext, ProcessPull, TransientJobError, UserAuth, UserAuthUnavailableError
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.status import JobState, JobStore, MemoryJobBackend

CONFIG = QueueConfig(max_retries=2, retry_backoff_s=0, progress_interval_s=0.01)


def make_queue(context_factory, config: QueueConfig = CONFIG) -> tuple[JobQueue, MemoryJobBackend, InMemoryConnector]:
    backend = MemoryJobBackend()
    connector = InMemoryConnector()
    return JobQueue(JobStore(backend), connector, config=config, context_factory=context_factory), backend, connector


async def drain(queue: JobQueue, backend: MemoryJobBackend, rounds: int = 10) -> None:
    """Run the worker until no job is queued or running (a retry or a freshly queued child needs another pass)."""
    for _ in range(rounds):
        async with queue.open():
            await queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)
        if not any(job.active and not job.awaiting_children for job in backend.rows.values()):
            return
    raise AssertionError("jobs are still active after the worker ran")


def queue_statuses(connector: InMemoryConnector) -> dict[int, str]:
    return {job_id: row["status"] for job_id, row in connector.jobs.items()}


@pytest.fixture
def env(tmp_path):
    return synth_env(tmp_path)


class TestEnqueue:
    async def test_an_ingest_gets_a_row_and_a_locked_queue_job(self, env):
        queue, backend, connector = make_queue(lambda: env.context)
        async with queue.open():
            enqueued = await queue.enqueue(IngestReport(code="SYNTH01", offline=True))

        assert enqueued.created
        job = backend.rows[enqueued.job.id]
        assert (job.state, job.kind.value, job.report_code, job.auth_kind) == (
            JobState.QUEUED,
            "ingest",
            "SYNTH01",
            "app",
        )
        assert job.queue_job_id is not None
        row = connector.jobs[job.queue_job_id]
        assert row["queue_name"] == "ingest"
        assert row["args"] == {"job_id": job.id}  # the payload is the row's ID and nothing else
        assert row["lock"] == "ingest:SYNTH01"
        assert row["queueing_lock"].startswith("ingest:SYNTH01:")

    async def test_a_second_ingest_request_returns_the_first(self, env):
        queue, backend, connector = make_queue(lambda: env.context)
        async with queue.open():
            first = await queue.enqueue(IngestReport(code="SYNTH01", offline=True))
            second = await queue.enqueue(IngestReport(code="SYNTH01", offline=True))
        assert (second.created, second.job.id) == (False, first.job.id)
        assert len(connector.jobs) == 1 and len(backend.rows) == 1

    async def test_a_user_request_records_who_asked_and_never_a_token(self, env):
        queue, backend, connector = make_queue(lambda: env.context)
        async with queue.open():
            enqueued = await queue.enqueue(IngestReport(code="SYNTH01", auth=UserAuth(user_id="u1")))
        job = backend.rows[enqueued.job.id]
        assert (job.requested_by, job.auth_kind) == ("u1", "user")
        assert job.spec["auth"] == {"kind": "user", "user_id": "u1"}

    async def test_process_jobs_lock_on_the_pull(self, env):
        queue, backend, connector = make_queue(lambda: env.context)
        async with queue.open():
            enqueued = await queue.enqueue(ProcessPull(pull_key="SYNTH01:1"))
        row = connector.jobs[backend.rows[enqueued.job.id].queue_job_id]
        assert (row["queue_name"], row["lock"], row["queueing_lock"]) == ("process", "process:SYNTH01:1", None)


class TestRunning:
    async def test_an_ingest_runs_its_process_jobs_and_every_pull_reaches_done(self, env):
        queue, backend, connector = make_queue(lambda: env.context)
        async with queue.open():
            ingest = (await queue.enqueue(IngestReport(code="SYNTH01", offline=True))).job
        await drain(queue, backend)

        job = backend.rows[ingest.id]
        assert job.state is JobState.DONE and job.attempts == 1 and job.error_code is None
        assert sorted(job.progress.pulls) == PULLS
        assert {p.state for p in job.progress.pulls.values()} == {"done"}
        children = [r for r in backend.rows.values() if r.parent_id == ingest.id]
        assert sorted(c.pull_key for c in children) == PULLS
        assert {c.state for c in children} == {JobState.DONE}
        assert sorted(env.events.pulls("SYNTH01"), key=lambda p: p.fight_id)
        assert len(env.records.keys("SYNTH01")) == len(PULLS)
        assert set(queue_statuses(connector).values()) == {"succeeded"}

    async def test_a_repeat_ingest_finds_everything_stored(self, env):
        queue, backend, _ = make_queue(lambda: env.context)
        async with queue.open():
            await queue.enqueue(IngestReport(code="SYNTH01", offline=True))
        await drain(queue, backend)
        before = set(env.records.keys("SYNTH01"))
        async with queue.open():
            again = (await queue.enqueue(IngestReport(code="SYNTH01", offline=True))).job
        await drain(queue, backend)
        assert backend.rows[again.id].state is JobState.DONE
        assert set(env.records.keys("SYNTH01")) == before

    async def test_a_review_of_a_pull_that_is_not_ingested_fails_with_a_typed_error(self, env):
        from khadbot.jobs import ReviewPull

        queue, backend, _ = make_queue(lambda: env.context)
        async with queue.open():
            review = (await queue.enqueue(ReviewPull(pull_key="SYNTH01:9"))).job
        await drain(queue, backend)
        job = backend.rows[review.id]
        assert (job.state, job.error_code, job.attempts) == (JobState.FAILED, "pull_not_ingested", 1)

    async def test_an_offline_ingest_with_missing_pages_fails_once(self, env):
        queue, backend, _ = make_queue(lambda: env.context)
        async with queue.open():
            ingest = (
                await queue.enqueue(IngestReport(code="SYNTH01", fights=[1], offline=True, refresh_meta=False))
            ).job
        env.context.filters = lambda: {}  # a filter the recorded pages do not have
        await drain(queue, backend)
        job = backend.rows[ingest.id]
        assert job.state is JobState.FAILED and job.error_code == "pulls_failed"
        assert job.progress.pulls["SYNTH01:1"].state == "failed"


class FlakyResolver:
    """Fails with ``error`` for the first ``failures`` calls, then resolves app auth."""

    def __init__(self, error: Exception, failures: int) -> None:
        self.error, self.failures, self.calls = error, failures, 0

    async def provider_for(self, auth):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return None


class TestFailures:
    def flaky(self, env, error, failures):
        resolver = FlakyResolver(error, failures)
        env.context.resolver = resolver
        return resolver

    async def test_a_transient_failure_is_retried_and_then_succeeds(self, env):
        resolver = self.flaky(env, TransientJobError("renew later"), failures=1)
        queue, backend, connector = make_queue(lambda: env.context)
        async with queue.open():
            ingest = (await queue.enqueue(IngestReport(code="SYNTH01", offline=True))).job
        await drain(queue, backend)
        job = backend.rows[ingest.id]
        assert job.state is JobState.DONE and job.attempts == 2 and job.error_code is None
        assert resolver.calls == 2

    async def test_retries_run_out(self, env):
        self.flaky(env, TransientJobError("renew later"), failures=99)
        queue, backend, connector = make_queue(lambda: env.context)
        async with queue.open():
            ingest = (await queue.enqueue(IngestReport(code="SYNTH01", offline=True))).job
        await drain(queue, backend)
        job = backend.rows[ingest.id]
        assert job.state is JobState.FAILED and job.error_code == "wcl_network"
        assert job.attempts == CONFIG.max_retries + 1  # the first run and each retry

    async def test_a_sign_in_failure_is_not_retried(self, env):
        resolver = self.flaky(env, UserAuthUnavailableError("u1 must sign in"), failures=99)
        queue, backend, _ = make_queue(lambda: env.context)
        async with queue.open():
            ingest = (await queue.enqueue(IngestReport(code="SYNTH01", auth=UserAuth(user_id="u1")))).job
        await drain(queue, backend)
        job = backend.rows[ingest.id]
        assert (job.state, job.error_code, job.attempts) == (JobState.FAILED, "wcl_sign_in_required", 1)
        assert "u1" not in (job.error_message or "")
        assert resolver.calls == 1

    async def test_an_unexpected_error_fails_once_with_a_generic_message(self, env):
        self.flaky(env, RuntimeError("detail that must not reach the user"), failures=99)
        queue, backend, _ = make_queue(lambda: env.context)
        async with queue.open():
            ingest = (await queue.enqueue(IngestReport(code="SYNTH01", offline=True))).job
        await drain(queue, backend)
        job = backend.rows[ingest.id]
        assert (job.state, job.error_code, job.attempts) == (JobState.FAILED, "internal_error", 1)
        assert "detail" not in (job.error_message or "")


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


class TestCancel:
    async def test_a_queued_ingest_is_dropped(self, env):
        queue, backend, connector = make_queue(lambda: env.context)
        async with queue.open():
            ingest = (await queue.enqueue(IngestReport(code="SYNTH01", offline=True))).job
            cancelled = await queue.cancel(ingest.id)
        assert cancelled is not None and cancelled.state is JobState.CANCELLED
        assert set(queue_statuses(connector).values()) == {"cancelled"}
        await drain(queue, backend)
        assert env.events.pulls("SYNTH01") == []  # nothing ran

    async def test_cancel_stops_a_running_ingest_and_a_new_request_resumes(self, env):
        queue, backend, connector = make_queue(lambda: env.context)
        async with queue.open():
            ingest = (await queue.enqueue(IngestReport(code="SYNTH01"))).job
        env.context.client = CancelOnFirstRateLimitRead(queue, ingest.id)  # type: ignore[assignment]
        await drain(queue, backend)

        stopped = backend.rows[ingest.id]
        assert stopped.state is JobState.CANCELLED and stopped.finished_at is not None
        assert [r for r in backend.rows.values() if r.parent_id == ingest.id] == []  # no process jobs were queued
        assert queue_statuses(connector)[stopped.queue_job_id] == "aborted"

        env.context.client = StubWclClient()  # type: ignore[assignment]
        async with queue.open():
            resumed = await queue.enqueue(IngestReport(code="SYNTH01"))
        assert resumed.created and resumed.job.id != ingest.id  # the cancelled job no longer answers
        await drain(queue, backend)
        assert backend.rows[resumed.job.id].state is JobState.DONE
        assert len(env.records.keys("SYNTH01")) == len(PULLS)

    async def test_cancel_of_an_unknown_job(self, env):
        queue, *_ = make_queue(lambda: env.context)
        async with queue.open():
            assert await queue.cancel("nope") is None


def test_the_context_factory_default_builds_a_job_context():
    queue, *_ = make_queue(None)  # type: ignore[arg-type]
    assert isinstance(queue._context_factory(), JobContext)
