"""
tests/integration/hosted/test_queue_pg.py

The queue on real Postgres with procrastinate's own schema: SYNTH01 ingested offline through a worker (stub WCL
client, recorded pages), every pull reaching ``done`` with its records and read-model rows written; a second ingest
request answered by the first job and locked per report; cancel of a running ingest and the resume afterwards; a
listener receiving the job's updates in order; and the worker's own retries and failures.

Each test runs its coroutine on a selector event loop, which psycopg's async connections need on Windows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from helpers.jobs.synth_queue import PULLS, StubWclClient, synth_env
from procrastinate import PsycopgConnector

from khadbot.config import QueueConfig
from khadbot.db import PostgresReadModelProjector, migrate
from khadbot.jobs import IngestReport, UserAuth
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.status import JobState, JobStore, PostgresJobBackend

CONFIG = QueueConfig(max_retries=1, retry_backoff_s=0, progress_interval_s=0.05)


def run[T](coro: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(coro(), loop_factory=asyncio.SelectorEventLoop)  # type: ignore[arg-type]


@pytest.fixture
def env(pool, tmp_path):
    migrate(pool)
    env = synth_env(tmp_path)
    env.context.projector = PostgresReadModelProjector(env.raw, pool=lambda: pool)
    return env


@pytest.fixture
def make_queue(pool, database_url, schema_name, env):
    def make() -> JobQueue:
        connector = PsycopgConnector(
            conninfo=database_url, min_size=1, max_size=6, kwargs={"options": f"-c search_path={schema_name}"}
        )
        return JobQueue(
            JobStore(PostgresJobBackend(pool)), connector, config=CONFIG, context_factory=lambda: env.context
        )

    return make


def active_jobs(pool) -> int:
    """Jobs still queued or running that are not just waiting for their process jobs."""
    with pool.connection() as conn:
        return conn.execute(
            "SELECT count(*) FROM jobs WHERE state IN ('queued', 'running') AND NOT awaiting_children"
        ).fetchone()[0]


async def drain(queue: JobQueue, pool, rounds: int = 10) -> None:
    for _ in range(rounds):
        await queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)
        if not active_jobs(pool):
            return
    raise AssertionError("jobs are still active after the worker ran")


def read_model_pulls(pool) -> int:
    with pool.connection() as conn:
        return conn.execute("SELECT count(*) FROM pulls WHERE report_code = 'SYNTH01'").fetchone()[0]


def test_an_ingest_and_its_process_jobs_run_to_done(env, make_queue, pool):
    queue = make_queue()

    async def go():
        async with queue.open():
            ingest = (await queue.enqueue(IngestReport(code="SYNTH01", offline=True))).job
            await drain(queue, pool)
            return ingest.id

    ingest_id = run(go)
    with pool.connection() as conn:
        states = conn.execute("SELECT kind, state, error_code FROM jobs ORDER BY kind, pull_key").fetchall()
        progress = conn.execute("SELECT progress FROM jobs WHERE id = %s", (ingest_id,)).fetchone()[0]
        queue_statuses = {r[0] for r in conn.execute("SELECT status FROM procrastinate_jobs").fetchall()}
    assert states == [("ingest", "done", None)] + [("process", "done", None)] * len(PULLS)
    assert sorted(progress["pulls"]) == PULLS and {p["state"] for p in progress["pulls"].values()} == {"done"}
    assert queue_statuses == {"succeeded"}
    assert len(env.records.keys("SYNTH01")) == len(PULLS)
    assert read_model_pulls(pool) == len(PULLS)


def test_a_second_request_gets_the_first_job_and_the_report_is_locked(env, make_queue, pool):
    queue = make_queue()

    async def go():
        async with queue.open():
            first = await queue.enqueue(IngestReport(code="SYNTH01", offline=True))
            second = await queue.enqueue(IngestReport(code="SYNTH01", offline=True, fights=[1]))
            return first, second

    first, second = run(go)
    assert (first.created, second.created) == (True, False)
    assert second.job.id == first.job.id
    with pool.connection() as conn:
        rows = conn.execute("SELECT queue_name, lock, queueing_lock, args FROM procrastinate_jobs").fetchall()
    assert len(rows) == 1
    assert rows[0][:2] == ("ingest", "ingest:SYNTH01") and rows[0][2].startswith("ingest:SYNTH01:")
    assert rows[0][3] == {"job_id": first.job.id}


def test_the_queueing_lock_alone_refuses_a_second_waiting_ingest(env, make_queue, pool):
    """Behind the de-duplication, procrastinate's own lock holds even if the row check were bypassed."""
    from procrastinate import exceptions

    queue = make_queue()

    async def go():
        async with queue.open():
            task = queue.app.tasks["khadbot.ingest_report"]
            configured = task.configure(lock="ingest:SYNTH01", queueing_lock="ingest:SYNTH01:same")
            await configured.defer_async(job_id="x")
            with pytest.raises(exceptions.AlreadyEnqueued):
                await configured.defer_async(job_id="y")

    run(go)


def test_cancel_stops_a_running_ingest_and_a_new_request_resumes(env, make_queue, pool):
    queue = make_queue()

    class CancelsOnFirstRead(StubWclClient):
        def __init__(self, job_id: str) -> None:
            self.job_id, self.done = job_id, False

        async def rate_limit(self, *, user_token=None):
            if not self.done:
                self.done = True
                await queue.cancel(self.job_id)
                await asyncio.sleep(0.5)  # longer than the progress interval: the writer sees the flag
            return {"pointsSpentThisHour": 0.0}

    async def go():
        async with queue.open():
            ingest = (await queue.enqueue(IngestReport(code="SYNTH01"))).job
            env.context.client = CancelsOnFirstRead(ingest.id)  # type: ignore[assignment]
            await drain(queue, pool)
            stopped = await queue.store.get(ingest.id)
            env.context.client = StubWclClient()  # type: ignore[assignment]
            resumed = await queue.enqueue(IngestReport(code="SYNTH01"))
            await drain(queue, pool)
            return stopped, resumed, await queue.store.get(resumed.job.id)

    stopped, resumed, finished = run(go)
    assert stopped is not None and stopped.state is JobState.CANCELLED
    assert resumed.created and finished is not None and finished.state is JobState.DONE
    assert len(env.records.keys("SYNTH01")) == len(PULLS)
    with pool.connection() as conn:
        assert "aborted" in {r[0] for r in conn.execute("SELECT status FROM procrastinate_jobs").fetchall()}


def test_a_listener_sees_the_ingests_updates_in_order(env, make_queue, pool):
    queue = make_queue()

    async def go():
        async with queue.open(), queue.store.listen() as events:
            ingest = (await queue.enqueue(IngestReport(code="SYNTH01", offline=True))).job
            await drain(queue, pool)
            seen: list[str] = []
            while True:
                try:
                    seen.append(await asyncio.wait_for(anext(events), 1))
                except TimeoutError:
                    break
            final = await queue.store.get(ingest.id)
            return ingest.id, seen, final

    ingest_id, seen, final = run(go)
    assert seen[0] == ingest_id  # queued
    assert ingest_id in seen and final is not None and final.state is JobState.DONE
    assert seen.count(ingest_id) >= 3  # queued, running and its later changes
    assert seen[-1] == ingest_id  # the last change is the ingest settling after its process jobs


def test_a_failed_job_keeps_its_typed_error(env, make_queue, pool):
    queue = make_queue()

    class NoSignIn:
        async def provider_for(self, auth):
            from khadbot.jobs import UserAuthUnavailableError

            raise UserAuthUnavailableError("sign in")

    env.context.resolver = NoSignIn()  # type: ignore[assignment]
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO users (id, wcl_user_id, name, created_at, last_login_at) VALUES ('u1', 1, 'Test', now(), now())"
        )

    async def go():
        async with queue.open():
            job = (await queue.enqueue(IngestReport(code="SYNTH01", auth=UserAuth(user_id="u1")))).job
            await drain(queue, pool)
            return await queue.store.get(job.id)

    job = run(go)
    assert job is not None
    assert (job.state, job.error_code, job.attempts) == (JobState.FAILED, "wcl_sign_in_required", 1)
