"""
tests/integration/hosted/test_watch_pg.py

Live report watches on real Postgres (migration 0007) with procrastinate's own schema: the row round-trips (the
fights map included), one active watch per report holds even for two writers, a stopped watch is never changed
again, and a worker runs a watch end to end: the first poll ingests the report's boss fights as the user, the next
poll waits in the queue for one interval, and stopping the watch cancels it.

Each test runs its coroutine on a selector event loop, which psycopg's async connections need on Windows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from helpers.jobs.synth_queue import synth_env
from helpers.jobs.watch import QUEUE_CONFIG, FakeResolver, StubWcl, fight
from procrastinate import PsycopgConnector

from khadbot.auth.pg import PostgresAuthStore
from khadbot.config import WatchConfig
from khadbot.db import PostgresReadModelProjector, migrate
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.status import JobState, JobStore, PostgresJobBackend
from khadbot.jobs.watch_store import PostgresWatchBackend, StopReason, WatchRecord, WatchState, WatchStore

CODE = "SYNTH01"


def run[T](coro: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(coro(), loop_factory=asyncio.SelectorEventLoop)  # type: ignore[arg-type]


@pytest.fixture
def user_id(pool) -> str:
    migrate(pool)
    return PostgresAuthStore(pool).upsert_user(4242, "Khadgar", datetime.now(UTC)).id


def record(user_id: str, watch_id: str = "w1", **kw) -> WatchRecord:
    now = datetime.now(UTC)
    base = dict(
        id=watch_id,
        report_code=CODE,
        user_id=user_id,
        started_at=now,
        expires_at=now + timedelta(hours=6),
        quiet_since=now,
        updated_at=now,
    )
    return WatchRecord(**{**base, **kw})


def test_a_watch_round_trips_with_its_fights(pool, user_id):
    store = WatchStore(PostgresWatchBackend(pool))

    async def go():
        stored, created = await store.start(record(user_id, fights={3: 160000, 7: 900000}, polls=2, points_spent=2.0))
        again = await store.get(stored.id)
        latest = await store.latest_for(CODE, user_id)
        return created, again, latest

    created, again, latest = run(go)
    assert created and again is not None and again == latest
    assert again.fights == {3: 160000, 7: 900000} and again.polls == 2 and again.points_spent == 2.0


def test_one_active_watch_per_report_even_for_two_writers(pool, user_id):
    store = WatchStore(PostgresWatchBackend(pool))

    async def go():
        return await asyncio.gather(*(store.start(record(user_id, f"w{i}")) for i in range(6)))

    results = run(go)
    assert sum(1 for _, created in results if created) == 1
    assert len({rec.id for rec, _ in results}) == 1
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM watches WHERE state = 'active'").fetchone()[0] == 1


def test_the_unique_index_is_the_last_line_of_defence(pool, user_id):
    from psycopg.errors import UniqueViolation

    backend = PostgresWatchBackend(pool)
    with backend.transaction(report=CODE) as tx:
        tx.insert(record(user_id, "a"))
    with pytest.raises(UniqueViolation), backend.transaction(report=CODE) as tx:
        tx.insert(record(user_id, "b"))


def test_a_stopped_watch_is_never_changed_again_and_the_report_can_be_watched_anew(pool, user_id):
    store = WatchStore(PostgresWatchBackend(pool))

    async def go():
        rec, _ = await store.start(record(user_id))
        stopped, applied = await store.update(
            rec.id, lambda r: r.model_copy(update={"state": WatchState.STOPPED, "stop_reason": StopReason.USER})
        )
        late, late_applied = await store.update(rec.id, lambda r: r.model_copy(update={"polls": 9}))
        fresh, created = await store.start(record(user_id, "w2"))
        return stopped, applied, late, late_applied, fresh, created

    stopped, applied, late, late_applied, fresh, created = run(go)
    assert applied and stopped is not None and stopped.stop_reason is StopReason.USER
    assert not late_applied and late is not None and late.polls == 0
    assert created and fresh.id == "w2"


def test_a_worker_runs_a_watch_end_to_end(pool, database_url, schema_name, user_id, tmp_path):
    env = synth_env(tmp_path)
    env.context.projector = PostgresReadModelProjector(env.raw, pool=lambda: pool)
    env.context.client = StubWcl([fight(1, start=100000, end=160000), fight(2, start=300000, end=380000)])  # type: ignore[assignment]
    env.context.resolver = FakeResolver()
    connector = PsycopgConnector(
        conninfo=database_url, min_size=1, max_size=6, kwargs={"options": f"-c search_path={schema_name}"}
    )
    queue = JobQueue(
        JobStore(PostgresJobBackend(pool)),
        connector,
        config=QUEUE_CONFIG,
        context_factory=lambda: env.context,
        watches=WatchStore(PostgresWatchBackend(pool)),
        watch_config=WatchConfig(poll_interval_s=180),
    )

    async def go():
        async with queue.open():
            rec, _ = await queue.watches.start(CODE, user_id)
            for _ in range(6):
                await queue.run_worker(
                    queues=["watch", "ingest", "process"],
                    wait=False,
                    listen_notify=False,
                    install_signal_handlers=False,
                )
            polled = await queue.watches.store.get(rec.id)
            stopped = await queue.watches.stop(CODE, user_id)
        return polled, stopped

    polled, stopped = run(go)
    assert polled is not None and polled.active and polled.polls == 1 and set(polled.fights) == {1, 2}
    assert stopped is not None and stopped.stop_reason is StopReason.USER
    with pool.connection() as conn:
        ingests = conn.execute("SELECT state, auth_kind, requested_by, spec FROM jobs WHERE kind = 'ingest'").fetchall()
        polls = conn.execute(
            "SELECT status, scheduled_at, queueing_lock, args FROM procrastinate_jobs "
            "WHERE task_name = 'khadbot.watch_report' ORDER BY id"
        ).fetchall()
        pulls = conn.execute("SELECT count(*) FROM pulls WHERE report_code = %s", (CODE,)).fetchone()[0]
    assert [(s, a, u) for s, a, u, _ in ingests] == [(JobState.DONE.value, "user", user_id)]
    assert ingests[0][3]["live"] is True and ingests[0][3]["fights"] == [1, 2]
    assert pulls == 2
    assert [p[0] for p in polls] == ["succeeded", "cancelled"]  # the first poll ran; the next was dropped by the stop
    assert polls[1][1] is not None and polls[1][2] == f"watch:{polled.id}" and polls[1][3] == {"watch_id": polled.id}
