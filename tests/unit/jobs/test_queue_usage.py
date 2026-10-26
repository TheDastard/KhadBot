"""
tests/unit/jobs/test_queue_usage.py

An ingest job writes its measured WCL points to the usage ledger when it settles, the same figure the WCL budget
settles to; offline ingests and process jobs write nothing; a failing ledger never changes how the job ended.
"""

from __future__ import annotations

import pytest
from helpers.jobs.budget import CountingWclClient, FakeReader, MovableClock, budget_config
from helpers.jobs.synth_queue import synth_env
from procrastinate.testing import InMemoryConnector

from khadbot.config import QueueConfig
from khadbot.jobs import IngestReport
from khadbot.jobs.budget_store import BudgetManager, MemoryBudgetBackend
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.status import JobState, JobStore, MemoryJobBackend
from khadbot.jobs.usage import MemoryUsageLedger

CONFIG = QueueConfig(max_retries=1, retry_backoff_s=0, progress_interval_s=0.01)
STEP = 15.0


@pytest.fixture
def env(tmp_path):
    env = synth_env(tmp_path)
    env.context.client = CountingWclClient(STEP)  # type: ignore[assignment]
    return env


def make(env, *, usage, budget=None):
    jobs = MemoryJobBackend()
    queue = JobQueue(
        JobStore(jobs),
        InMemoryConnector(),
        config=CONFIG,
        context_factory=lambda: env.context,
        usage=usage,
        budget=budget,
    )
    return queue, jobs


async def drain(queue: JobQueue) -> None:
    async with queue.open():
        await queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)


async def test_an_online_ingest_writes_one_wcl_row_with_its_measured_points(env):
    ledger = MemoryUsageLedger()
    queue, jobs = make(env, usage=ledger)
    async with queue.open():
        job = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]), requested_by="u1")).job
    await drain(queue)

    assert jobs.rows[job.id].points_spent == STEP
    (row,) = ledger.entries  # the process jobs spent nothing and wrote nothing
    assert (row.kind.value, row.wcl_points, row.job_id, row.user_id, row.report_code) == (
        "wcl",
        STEP,
        job.id,
        "u1",
        "SYNTH01",
    )
    assert row.guild_id is None  # nothing fills reports.guild_id yet


async def test_the_ledger_and_the_budget_settle_to_the_same_figure(env):
    ledger = MemoryUsageLedger()
    manager = BudgetManager(
        MemoryBudgetBackend(),
        budget_config(),
        clock=MovableClock(),
        reader=FakeReader(limit=1000, spent=0, reset_in_s=1800),
    )
    queue, _ = make(env, usage=ledger, budget=manager)
    async with queue.open():
        await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]), requested_by="u1")
    await drain(queue)

    (reservation,) = (await manager.state()).reservations
    (row,) = ledger.entries
    assert row.wcl_points == reservation.actual == STEP


async def test_an_offline_ingest_spends_nothing_and_writes_nothing(env):
    ledger = MemoryUsageLedger()
    queue, _ = make(env, usage=ledger)
    async with queue.open():
        await queue.enqueue(IngestReport(code="SYNTH01", offline=True))
    await drain(queue)
    assert ledger.entries == []


async def test_a_failing_ledger_does_not_fail_the_job(env):
    class Broken(MemoryUsageLedger):
        def record(self, entry):
            raise RuntimeError("ledger down")

    queue, jobs = make(env, usage=Broken())
    async with queue.open():
        job = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]))).job
    await drain(queue)
    assert jobs.rows[job.id].state is not JobState.FAILED and jobs.rows[job.id].points_spent == STEP
