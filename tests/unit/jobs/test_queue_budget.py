"""
tests/unit/jobs/test_queue_budget.py

The queue's job pick-up path with a WCL budget, on procrastinate's in-memory connector: an online ingest reserves
before it starts and settles to its measured spend; one over its user's allowance goes to ``waiting_budget`` with a
recheck queued for the window's reset and runs once the window turns; a cancelled waiting job is dropped; offline
ingests and process jobs reserve nothing.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from helpers.jobs.budget import CountingWclClient, FakeReader, MovableClock, budget_config
from helpers.jobs.synth_queue import synth_env
from procrastinate import utils
from procrastinate.testing import InMemoryConnector

from khadbot.config import QueueConfig
from khadbot.jobs import IngestReport
from khadbot.jobs.budget import Block
from khadbot.jobs.budget_store import BudgetManager, MemoryBudgetBackend
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.status import JobState, JobStore, MemoryJobBackend

CONFIG = QueueConfig(max_retries=1, retry_backoff_s=0, progress_interval_s=0.01)
STEP = 15.0  # what each ingest measures: the stub's counter moves this much between its two reads


@pytest.fixture
def env(tmp_path):
    env = synth_env(tmp_path)
    env.context.client = CountingWclClient(STEP)  # type: ignore[assignment]
    return env


@pytest.fixture
def clock():
    return MovableClock()


@pytest.fixture
def reader():
    return FakeReader(limit=1000, spent=0, reset_in_s=1800)


def make(env, clock, reader, **cfg):
    jobs = MemoryJobBackend()
    connector = InMemoryConnector()
    manager = BudgetManager(MemoryBudgetBackend(), budget_config(**cfg), clock=clock, reader=reader)
    queue = JobQueue(JobStore(jobs), connector, config=CONFIG, context_factory=lambda: env.context, budget=manager)
    return queue, jobs, connector, manager


async def drain(queue: JobQueue, jobs: MemoryJobBackend, rounds: int = 10) -> None:
    """Run the worker until nothing is queued or running (waiting jobs stay waiting)."""
    for _ in range(rounds):
        async with queue.open():
            await queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)
        busy = [j for j in jobs.rows.values() if j.state in (JobState.QUEUED, JobState.RUNNING)]
        if not any(not j.awaiting_children for j in busy):
            return
    raise AssertionError("jobs are still queued or running")


async def test_an_online_ingest_reserves_then_settles_to_its_measured_spend(env, clock, reader):
    queue, jobs, _, manager = make(env, clock, reader)
    async with queue.open():
        job = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]), requested_by="u")).job
    await drain(queue, jobs)

    assert jobs.rows[job.id].state is JobState.DONE
    state = await manager.state()
    (reservation,) = state.reservations  # the process jobs reserved nothing
    assert reservation.ask.job_id == job.id and reservation.ask.points == 18.0  # one pull fetched alone + 4
    assert reservation.ask.user_id == "u" and reservation.settled and reservation.actual == STEP
    assert reader.reads == 1  # the bucket took WCL's count after the job


async def test_a_job_over_its_users_allowance_waits_for_the_window_and_then_runs(env, clock, reader):
    queue, jobs, connector, manager = make(env, clock, reader, user_share=0.02)  # 20 points per user
    await manager.sync()
    async with queue.open():
        first = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]), requested_by="u")).job
    await drain(queue, jobs)
    async with queue.open():
        second = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[2]), requested_by="u")).job
    await drain(queue, jobs)

    assert jobs.rows[first.id].state is JobState.DONE
    waiting = jobs.rows[second.id]
    assert waiting.state is JobState.WAITING_BUDGET  # 15 used + 18 asked > 20
    wait = await manager.wait_of(second.id)
    assert wait is not None and (wait.position, wait.reason) == (1, Block.USER_ALLOWANCE)
    assert wait.eta == clock() + timedelta(seconds=1800)
    recheck = connector.jobs[waiting.queue_job_id]
    assert recheck["status"] == "todo" and recheck["scheduled_at"] == wait.recheck_at
    assert recheck["queueing_lock"] is None and recheck["lock"] == "ingest:SYNTH01"

    clock.advance(seconds=1801)  # the window turns; the recheck comes due
    recheck["scheduled_at"] = utils.utcnow() - timedelta(seconds=1)
    await drain(queue, jobs)
    assert jobs.rows[second.id].state is JobState.DONE
    assert (await manager.wait_of(second.id)) is None


async def test_a_waiting_job_is_cancelled_at_once(env, clock, reader):
    reader.reading = reader.reading.__class__(1000, 800, 1800)  # spent down to the headroom
    queue, jobs, connector, manager = make(env, clock, reader)
    await manager.sync()
    async with queue.open():
        job = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]))).job
    await drain(queue, jobs)
    waiting = jobs.rows[job.id]
    assert waiting.state is JobState.WAITING_BUDGET
    assert (await manager.wait_of(job.id)).reason is Block.APP_POINTS  # type: ignore[union-attr]

    async with queue.open():
        cancelled = await queue.cancel(job.id)
    assert cancelled is not None and cancelled.state is JobState.CANCELLED
    assert connector.jobs[waiting.queue_job_id]["status"] == "cancelled"


async def test_a_job_freed_by_another_settling_is_queued_again(env, clock, reader):
    queue, jobs, connector, manager = make(env, clock, reader, user_share=0.02)
    await manager.sync()
    async with queue.open():
        first = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]), requested_by="u")).job
    await drain(queue, jobs)
    async with queue.open():
        second = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[2]), requested_by="u")).job
    await drain(queue, jobs)
    assert jobs.rows[second.id].state is JobState.WAITING_BUDGET

    clock.advance(seconds=1801)  # the window turns before anything wakes it
    async with queue.open():
        other = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1, 2]), requested_by="v")).job
    await drain(queue, jobs)  # picking up the other job admits the waiting one too
    assert jobs.rows[other.id].state is JobState.DONE
    assert jobs.rows[second.id].state is JobState.DONE
    assert jobs.rows[first.id].state is JobState.DONE


async def test_offline_ingests_reserve_nothing(env, clock, reader):
    queue, jobs, _, manager = make(env, clock, reader)
    async with queue.open():
        job = (await queue.enqueue(IngestReport(code="SYNTH01", offline=True))).job
    await drain(queue, jobs)
    assert jobs.rows[job.id].state is JobState.DONE
    assert (await manager.state()).reservations == ()


async def test_a_whole_report_is_estimated_from_its_cached_metadata(env, clock, reader):
    queue, jobs, _, manager = make(env, clock, reader)
    async with queue.open():
        await queue.enqueue(IngestReport(code="SYNTH01"))
    await drain(queue, jobs)
    (reservation,) = (await manager.state()).reservations
    assert reservation.ask.points == pytest.approx(2 * 7.2 + 4)  # SYNTH01 has two boss pulls


async def test_without_cached_metadata_a_night_is_assumed(env, clock, reader):
    queue, _, _, manager = make(env, clock, reader)
    job = (await queue.store.claim_ingest(_new("NOCACHE")))[0]
    ask = await queue._ask(job, IngestReport(code="NOCACHE"))
    assert ask.points == pytest.approx(25 * 7.2 + 4)


def _new(code: str):
    from khadbot.jobs.status import JobKind, NewJob

    return NewJob(
        id="n1",
        kind=JobKind.INGEST,
        report_code=code,
        spec=IngestReport(code=code).model_dump(mode="json"),
    )


async def test_a_live_ingest_runs_in_the_headroom_an_ordinary_one_waits_for(env, clock, reader):
    reader.reading = reader.reading.__class__(1000, 800, 1800)  # spent down to the headroom
    queue, jobs, _, manager = make(env, clock, reader)
    await manager.sync()
    async with queue.open():
        ordinary = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]), requested_by="u")).job
        live = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[2], live=True), requested_by="v")).job
    await drain(queue, jobs)

    assert jobs.rows[live.id].state is JobState.DONE
    assert jobs.rows[ordinary.id].state is JobState.WAITING_BUDGET
    state = await manager.state()
    (reservation,) = [r for r in state.reservations if r.ask.job_id == live.id]
    assert reservation.ask.live
