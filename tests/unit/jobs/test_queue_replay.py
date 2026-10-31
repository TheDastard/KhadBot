"""
tests/unit/jobs/test_queue_replay.py

Ingest and live watch through the queue (procrastinate's in-memory connector) with WCL answered by replay: a recorded
report gives the same events as its offline ingest; a low replay limit sends an ingest to ``waiting_budget`` and it
completes once the window resets; a live report's fights are each ingested once; wiring gives ingest the recorded
fetch shape only under replay. Nothing leaves the machine: every test runs under a socket guard.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from helpers.encounter.real_fixtures import REAL_REPORTS, ingest_fixture
from helpers.jobs.budget import MovableClock, budget_config
from helpers.jobs.watch import FakeResolver
from helpers.wcl.replay import (
    RECORDED_FILTERS,
    SYNTH_FILTERS,
    WCL_FIXTURES,
    guard_network,
    replay_client,
    replay_env,
    seconds,
)
from procrastinate import utils
from procrastinate.testing import InMemoryConnector

from khadbot import wiring
from khadbot.config import QueueConfig, WatchConfig, reset_config
from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.store import ParquetEventStore
from khadbot.jobs import IngestReport, JobContext
from khadbot.jobs.budget import Block
from khadbot.jobs.budget_store import BudgetManager, MemoryBudgetBackend, RateLimitReading
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.run import default_filter_kwargs
from khadbot.jobs.status import JobState, JobStore, MemoryJobBackend
from khadbot.jobs.watch_store import MemoryWatchBackend, WatchStore
from khadbot.wcl.replay import ReplayTransport

CONFIG = QueueConfig(max_retries=1, retry_backoff_s=0, progress_interval_s=0.01)
SMALLEST_REAL = "QkVa6GHRK8yfrTbw"  # one pull, about 10 MB of pages


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = guard_network(monkeypatch)
    yield
    assert attempts == []


def make_queue(context: JobContext, budget: BudgetManager | None = None, **kw) -> tuple[JobQueue, MemoryJobBackend]:
    jobs = MemoryJobBackend()
    queue = JobQueue(
        JobStore(jobs), InMemoryConnector(), config=CONFIG, context_factory=lambda: context, budget=budget, **kw
    )
    return queue, jobs


async def drain(queue: JobQueue, jobs: MemoryJobBackend, rounds: int = 12) -> None:
    for _ in range(rounds):
        async with queue.open():
            await queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)
        busy = [j for j in jobs.rows.values() if j.state in (JobState.QUEUED, JobState.RUNNING)]
        if not any(not j.awaiting_children for j in busy):
            return
    raise AssertionError("jobs are still queued or running")


async def offline_hashes(code: str, tmp_path, **filters) -> dict[str, str | None]:
    store = ParquetEventStore(tmp_path / "offline")
    if code == "SYNTH01":
        result = await ingest_report(code, store=store, cache=RawPageCache(WCL_FIXTURES), offline=True, **SYNTH_FILTERS)
    else:
        result = await ingest_fixture(code, store)
    assert not result.failed
    return {p.pull_key: store.events_hash(p.pull_key) for p in store.pulls(code)}


# ---------------------------------------------------------------------------
# Ingest through the queue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "SYNTH01",
        pytest.param(
            SMALLEST_REAL,
            marks=pytest.mark.skipif(SMALLEST_REAL not in REAL_REPORTS, reason="recorded fixtures need `git lfs pull`"),
        ),
    ],
)
async def test_a_replayed_ingest_through_the_queue_gives_the_offline_events(code, tmp_path):
    transport = ReplayTransport(WCL_FIXTURES)
    async with replay_client(transport) as client:
        env = replay_env(tmp_path / "env", client, synthetic=code == "SYNTH01")
        queue, jobs = make_queue(env.context)
        async with queue.open():
            job = (await queue.enqueue(IngestReport(code=code, fights=None if code == "SYNTH01" else [14]))).job
        await drain(queue, jobs)

    assert {r.kind.value: r.state for r in jobs.rows.values()}["ingest"] is JobState.DONE
    assert all(r.state is JobState.DONE for r in jobs.rows.values()), [r.error_message for r in jobs.rows.values()]
    replayed = {p.pull_key: env.events.events_hash(p.pull_key) for p in env.events.pulls(code)}
    assert replayed == await offline_hashes(code, tmp_path)
    assert jobs.rows[job.id].points_spent is not None and jobs.rows[job.id].points_spent > 0


async def test_an_unrecorded_report_fails_the_ingest_as_wcl_would(tmp_path):
    async with replay_client(ReplayTransport(WCL_FIXTURES)) as client:
        env = replay_env(tmp_path, client, synthetic=True)
        queue, jobs = make_queue(env.context)
        async with queue.open():
            job = (await queue.enqueue(IngestReport(code="NOSUCHREPORT1"))).job
        await drain(queue, jobs)
    row = jobs.rows[job.id]
    assert row.state is JobState.FAILED and row.error_code == "wcl_error"


async def test_an_unrecorded_page_fails_its_pull_by_name(tmp_path):
    async with replay_client(ReplayTransport(WCL_FIXTURES)) as client:
        env = replay_env(tmp_path, client, synthetic=True)
        env.context.filters = lambda: {**SYNTH_FILTERS, "defensive_spell_ids": [1]}  # not what was recorded
        queue, jobs = make_queue(env.context)
        async with queue.open():
            job = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]))).job
        await drain(queue, jobs)
    pull = jobs.rows[job.id].progress.pulls["SYNTH01:1"]
    assert pull.state == "failed" and pull.error is not None and "Buffs filter 69af7f96a3bf" in pull.error


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


async def test_a_low_replay_limit_sends_an_ingest_to_waiting_budget_and_it_completes_after_the_reset(tmp_path):
    clock = MovableClock()
    transport = ReplayTransport(WCL_FIXTURES, limit_per_hour=40, reset_s=600, clock=seconds(clock))
    async with replay_client(transport) as client:

        async def reader() -> RateLimitReading:
            return RateLimitReading.parse(await client.rate_limit())

        manager = BudgetManager(MemoryBudgetBackend(), budget_config(), clock=clock, reader=reader)
        env = replay_env(tmp_path, client, synthetic=True)
        queue, jobs = make_queue(env.context, budget=manager)
        await manager.sync()
        async with queue.open():
            first = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]))).job
        await drain(queue, jobs)
        async with queue.open():
            second = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[2]))).job
        await drain(queue, jobs)

        assert jobs.rows[first.id].state is JobState.DONE
        assert jobs.rows[second.id].state is JobState.WAITING_BUDGET  # 40 points, about 16 spent, 18 asked, 8 kept
        wait = await manager.wait_of(second.id)
        assert wait is not None and wait.reason is Block.APP_POINTS

        clock.advance(seconds=601)  # the replay window and the budget's both turn
        recheck = queue.app.connector.jobs[jobs.rows[second.id].queue_job_id]  # type: ignore[attr-defined]
        recheck["scheduled_at"] = utils.utcnow() - timedelta(seconds=1)
        await drain(queue, jobs)

    assert jobs.rows[second.id].state is JobState.DONE
    assert sorted(p.pull_key for p in env.events.pulls("SYNTH01")) == ["SYNTH01:1", "SYNTH01:2"]


# ---------------------------------------------------------------------------
# Live watch
# ---------------------------------------------------------------------------


async def test_a_live_report_s_fights_are_each_ingested_once(tmp_path):
    transport = ReplayTransport(WCL_FIXTURES, live_report="SYNTH01")
    async with replay_client(transport) as client:
        env = replay_env(tmp_path, client, synthetic=True)
        env.context.resolver = FakeResolver()
        queue, jobs = make_queue(
            env.context,
            watches=WatchStore(MemoryWatchBackend()),
            watch_config=WatchConfig(poll_interval_s=180, quiet_period_s=2700, max_length_s=21600),
        )
        async with queue.open():
            rec, _ = await queue.watches.start("SYNTH01", "u1")
            for _ in range(3):  # one recorded fight more per poll, then nothing new
                await queue.watches.poll(rec.id)
                await drain(queue, jobs)

    ingests = sorted((r for r in jobs.rows.values() if r.kind.value == "ingest"), key=lambda r: r.created_at)
    assert [IngestReport.model_validate(r.spec).fights for r in ingests] == [[1], [2]]
    assert all(r.state is JobState.DONE for r in jobs.rows.values())
    assert sorted(p.pull_key for p in env.events.pulls("SYNTH01")) == ["SYNTH01:1", "SYNTH01:2"]


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("KHADBOT_WCL_REPLAY", raising=False)
    reset_config()
    yield monkeypatch
    reset_config()


def test_ingest_uses_the_configured_fetch_shape_without_replay(clean_env):
    ctx = JobContext()
    assert ctx.batch is True and ctx.filters() == default_filter_kwargs()
    assert wiring.wcl_replay() is None and wiring.replay_fetch_filters() is None


def test_under_replay_ingest_asks_for_the_recorded_filters_per_pull(clean_env):
    clean_env.setenv("KHADBOT_WCL_REPLAY", str(WCL_FIXTURES))
    ctx = JobContext()
    assert ctx.batch is False and dict(ctx.filters()) == RECORDED_FILTERS
