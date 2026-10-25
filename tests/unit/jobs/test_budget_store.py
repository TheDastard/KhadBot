"""
tests/unit/jobs/test_budget_store.py

``BudgetManager`` over the in-memory backend: requests, settlement, syncing with ``rateLimitData`` (and carrying on
when it can't be read), a waiting job's place, release, the user view, and many concurrent requests from two managers
sharing one backend never holding more than the usable bucket. Postgres runs the race in
tests/integration/hosted/test_budget_pg.py.
"""

from __future__ import annotations

import asyncio
import random

from helpers.jobs.budget import FakeReader, MovableClock, budget_config

from khadbot.jobs import budget
from khadbot.jobs.budget import Ask, Block
from khadbot.jobs.budget_store import BudgetManager, MemoryBudgetBackend, RateLimitReading


def make(reader: FakeReader | None = None, **cfg) -> tuple[BudgetManager, MovableClock, MemoryBudgetBackend]:
    clock = MovableClock()
    backend = MemoryBudgetBackend(guilds={"GUILDED": "77"})
    return BudgetManager(backend, budget_config(**cfg), clock=clock, reader=reader), clock, backend


def ask(clock: MovableClock, job: str, points: float, **kw) -> Ask:
    return Ask(job_id=job, report_code=kw.pop("report", "R"), points=points, asked_at=clock(), **kw)


class TestManager:
    async def test_the_first_change_starts_from_the_documented_limit(self):
        manager, _, _ = make()
        assert (await manager.state()).bucket.limit_per_hour == 3600

    async def test_request_and_settle(self):
        manager, clock, backend = make()
        out = await manager.request(ask(clock, "a", 50, user_id="u"))
        assert out.granted == ("a",)
        await manager.settle("a", 42)
        state = backend.state
        assert state is not None and state.bucket.spent == 42 and budget.held(state) == 0

    async def test_sync_takes_wcls_count(self):
        reader = FakeReader(limit=10800, spent=120, reset_in_s=600)
        manager, clock, backend = make(reader)
        assert await manager.sync() and reader.reads == 1
        bucket = backend.state.bucket  # type: ignore[union-attr]
        assert (bucket.limit_per_hour, bucket.spent, bucket.synced_at) == (10800, 120, clock())

    async def test_a_failed_read_keeps_the_local_count(self, caplog):
        reader = FakeReader()
        manager, clock, backend = make(reader)
        await manager.request(ask(clock, "a", 50))
        await manager.settle("a", 50)
        reader.fail = True
        assert not await manager.sync()
        assert backend.state.bucket.spent == 50  # type: ignore[union-attr]
        assert "Could not read WCL's rate limit" in caplog.text

    async def test_no_reader_means_no_sync(self):
        manager, _, _ = make(None)
        assert not await manager.sync()

    async def test_wait_of_gives_place_and_eta_without_granting(self):
        manager, clock, _ = make(FakeReader(limit=100, spent=80))
        await manager.sync()
        await manager.request(ask(clock, "a", 10))
        await manager.request(ask(clock, "b", 10))
        wait = await manager.wait_of("b")
        assert wait is not None and wait.position == 2 and wait.reason is Block.APP_POINTS
        assert await manager.wait_of("nobody") is None

    async def test_release_drops_both_the_hold_and_the_place(self):
        manager, clock, backend = make(FakeReader(limit=100, spent=80))
        await manager.sync()
        await manager.request(ask(clock, "a", 10))
        await manager.release("a")
        assert backend.state.waiting == ()  # type: ignore[union-attr]

    async def test_admit_waiting_grants_once_points_free_up(self):
        reader = FakeReader(limit=100, spent=80)
        manager, clock, _ = make(reader)
        await manager.sync()
        await manager.request(ask(clock, "a", 10))
        reader.reading = RateLimitReading(100, 0, 1800)
        await manager.sync()
        assert (await manager.admit_waiting()).granted == ("a",)

    async def test_guild_of_reads_the_backend(self):
        manager, _, _ = make()
        assert await manager.guild_of("GUILDED") == "77" and await manager.guild_of("OTHER") is None

    async def test_view(self):
        manager, clock, _ = make()
        await manager.request(ask(clock, "a", 30, user_id="u"))
        view = await manager.view(user_id="u", user_token=False)
        assert view.user.used == 30 and view.user.limit == 360


class TestConcurrency:
    async def test_two_managers_never_hold_more_than_the_usable_bucket(self):
        reader = FakeReader(limit=500, spent=0)
        clock = MovableClock()
        backend = MemoryBudgetBackend()
        cfg = budget_config(user_share=1.0, guild_share=1.0)
        workers = [BudgetManager(backend, cfg, clock=clock, reader=reader) for _ in range(2)]
        await workers[0].sync()
        usable = 500 * (1 - cfg.headroom_share)
        peak = 0.0
        rng = random.Random(7)

        async def work(manager: BudgetManager, name: str) -> None:
            nonlocal peak
            for i in range(30):
                job = f"{name}-{i}"
                out = await manager.request(ask(clock, job, rng.choice([18, 40, 90, 150]), report=job))
                state = backend.state
                assert state is not None
                peak = max(peak, budget.held(state) + state.bucket.spent)
                for granted in out.granted:
                    await asyncio.sleep(0)
                    await manager.settle(granted, 0)  # spend nothing, so the bucket never fills

        await asyncio.gather(work(workers[0], "w0"), work(workers[1], "w1"))
        assert 0 < peak <= usable
