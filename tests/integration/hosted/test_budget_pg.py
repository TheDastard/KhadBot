"""
tests/integration/hosted/test_budget_pg.py

The WCL budget on real Postgres (migration 0006): the state round-trips through the ``wcl_budget`` row, and two
workers, each with its own connection pool and ``BudgetManager``, draining one queue of ingest requests at the same
time never hold more than the usable bucket, never grant a job twice, and serve every job in the end.
"""

from __future__ import annotations

import asyncio
import random
from collections import deque

from helpers.jobs.budget import FakeReader, MovableClock, budget_config

from khadbot.db import migrate, open_pool
from khadbot.jobs import budget
from khadbot.jobs.budget import Ask
from khadbot.jobs.budget_store import BudgetManager, PostgresBudgetBackend, RateLimitReading


def test_the_state_round_trips_through_the_row(pool):
    migrate(pool)
    clock = MovableClock()
    manager = BudgetManager(PostgresBudgetBackend(pool), budget_config(), clock=clock, reader=FakeReader())

    async def go():
        await manager.sync()
        await manager.request(Ask(job_id="a", report_code="R", user_id=None, points=50, asked_at=clock()))
        await manager.settle("a", 31.5)
        return await manager.state()

    state = asyncio.run(go())
    assert state.bucket.limit_per_hour == 1000 and state.bucket.spent == 31.5
    assert state.reservations[0].actual == 31.5
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM wcl_budget").fetchone()[0] == 1


def test_guild_of_reads_the_reports_table(pool):
    migrate(pool)
    with pool.connection() as conn:
        conn.execute("INSERT INTO reports (code, guild_id) VALUES ('GUILDED', 77)")
    backend = PostgresBudgetBackend(pool)
    assert backend.guild_of("GUILDED") == "77" and backend.guild_of("NOPE") is None


def test_two_workers_draining_a_queue_never_over_reserve(pool, database_url, schema_name):
    migrate(pool)
    cfg = budget_config(user_share=1.0, guild_share=1.0)
    clock = MovableClock()
    reader = FakeReader(limit=300, spent=0, reset_in_s=3000)
    usable = 300 * (1 - cfg.headroom_share)
    rng = random.Random(11)
    queue = deque(
        Ask(job_id=f"j{i}", report_code=f"R{i}", points=rng.choice([18, 40, 90, 150]), asked_at=clock())
        for i in range(40)
    )
    pools = [
        open_pool(database_url, min_size=1, max_size=4, kwargs={"options": f"-c search_path={schema_name}"})
        for _ in range(2)
    ]
    managers = [BudgetManager(PostgresBudgetBackend(p), cfg, clock=clock, reader=reader) for p in pools]
    granted: list[str] = []
    duplicates: list[str] = []
    peak = 0.0

    async def worker(manager: BudgetManager) -> None:
        nonlocal peak
        while True:
            try:
                ask = queue.popleft()
            except IndexError:
                ask = None
            out = await (manager.request(ask) if ask is not None else manager.admit_waiting())
            peak = max(peak, budget.held(out.state) + out.state.bucket.spent)
            for job in out.granted:
                if job in granted:
                    duplicates.append(job)
                    continue
                granted.append(job)
                await asyncio.sleep(0.001)  # the "fetch"
                await manager.settle(job, 0)
            if ask is None and not out.state.waiting and budget.held(out.state) == 0:
                return
            if ask is None and not out.granted:
                await asyncio.sleep(0.005)

    async def go():
        await managers[0].sync()
        await asyncio.gather(*(worker(m) for m in managers))

    try:
        asyncio.run(go())
    finally:
        for p in pools:
            p.close()
    assert 0 < peak <= usable
    assert sorted(granted) == sorted(f"j{i}" for i in range(40))
    assert duplicates == []


def test_a_sync_moves_the_limit_without_a_deploy(pool):
    migrate(pool)
    reader = FakeReader(limit=3600)
    manager = BudgetManager(PostgresBudgetBackend(pool), budget_config(), reader=reader)

    async def go():
        await manager.sync()
        reader.reading = RateLimitReading(10800, 0, 3600)  # a Gold subscription: 3x
        await manager.sync()
        return await manager.state()

    assert asyncio.run(go()).bucket.limit_per_hour == 10800
