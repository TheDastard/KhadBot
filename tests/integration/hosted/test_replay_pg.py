"""
tests/integration/hosted/test_replay_pg.py

Replayed WCL through the queue on real Postgres: every recorded report (SYNTH01, and the real ones when their Git LFS
pages are present) ingested by a worker with WCL answered by replay gives the same events as its offline ingest, with
nothing sent off the machine; and a replayed live report's fights are each ingested once by a watch.

Each test runs its coroutine on a selector event loop, which psycopg's async connections need on Windows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from helpers.encounter.real_fixtures import REAL_REPORTS, fixture_fights, ingest_fixture
from helpers.jobs.watch import FakeResolver
from helpers.wcl.replay import SYNTH_FILTERS, WCL_FIXTURES, guard_network, replay_client, replay_env
from procrastinate import PsycopgConnector

from khadbot.auth.pg import PostgresAuthStore
from khadbot.config import QueueConfig, WatchConfig
from khadbot.db import PostgresReadModelProjector, migrate
from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.store import ParquetEventStore
from khadbot.jobs import IngestReport
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.status import JobStore, PostgresJobBackend
from khadbot.jobs.watch_store import PostgresWatchBackend, WatchStore
from khadbot.wcl.replay import ReplayTransport

CONFIG = QueueConfig(max_retries=1, retry_backoff_s=0, progress_interval_s=0.05)


def run[T](coro: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(coro(), loop_factory=asyncio.SelectorEventLoop)  # type: ignore[arg-type]


@pytest.fixture
def no_network(monkeypatch):
    """Off-machine connections fail; the database is on loopback in the local stack and in CI."""
    attempts = guard_network(monkeypatch)
    yield
    assert attempts == []


def make_queue(pool, database_url, schema_name, context, **kw) -> JobQueue:
    connector = PsycopgConnector(
        conninfo=database_url, min_size=1, max_size=6, kwargs={"options": f"-c search_path={schema_name}"}
    )
    return JobQueue(JobStore(PostgresJobBackend(pool)), connector, config=CONFIG, context_factory=lambda: context, **kw)


def job_states(pool) -> list[tuple[str, str, str | None]]:
    with pool.connection() as conn:
        return conn.execute("SELECT kind, state, error_code FROM jobs ORDER BY kind, pull_key").fetchall()


async def drain(queue: JobQueue, pool, rounds: int = 12, queues: list[str] | None = None) -> None:
    for _ in range(rounds):
        await queue.run_worker(queues=queues, wait=False, listen_notify=False, install_signal_handlers=False)
        with pool.connection() as conn:
            active = conn.execute(
                "SELECT count(*) FROM jobs WHERE state IN ('queued', 'running') AND NOT awaiting_children"
            ).fetchone()[0]
        if not active:
            return
    raise AssertionError("jobs are still active after the worker ran")


@pytest.mark.parametrize("code", ["SYNTH01", *REAL_REPORTS])
def test_every_recorded_report_ingested_through_the_queue_matches_its_offline_ingest(
    code, pool, database_url, schema_name, tmp_path, no_network
):
    migrate(pool)
    synthetic = code == "SYNTH01"
    fights = None if synthetic else fixture_fights(code)

    async def go():
        async with replay_client(ReplayTransport(WCL_FIXTURES)) as client:
            env = replay_env(tmp_path / "env", client, synthetic=synthetic)
            env.context.projector = PostgresReadModelProjector(env.raw, pool=lambda: pool)
            queue = make_queue(pool, database_url, schema_name, env.context)
            async with queue.open():
                await queue.enqueue(IngestReport(code=code, fights=fights))
                await drain(queue, pool)
        return {p.pull_key: env.events.events_hash(p.pull_key) for p in env.events.pulls(code)}

    replayed = run(go)
    assert {state for _, state, _ in job_states(pool)} == {"done"}

    offline = ParquetEventStore(tmp_path / "offline")
    if synthetic:
        result = asyncio.run(
            ingest_report(code, store=offline, cache=RawPageCache(WCL_FIXTURES), offline=True, **SYNTH_FILTERS)
        )
    else:
        result = asyncio.run(ingest_fixture(code, offline))
    assert not result.failed
    assert replayed == {p.pull_key: offline.events_hash(p.pull_key) for p in offline.pulls(code)}


def test_a_replayed_live_report_is_watched_to_the_end(pool, database_url, schema_name, tmp_path, no_network):
    migrate(pool)
    user_id = PostgresAuthStore(pool).upsert_user(4242, "Khadgar", datetime.now(UTC)).id

    async def go():
        async with replay_client(ReplayTransport(WCL_FIXTURES, live_report="SYNTH01")) as client:
            env = replay_env(tmp_path / "env", client, synthetic=True)
            env.context.resolver = FakeResolver()
            queue = make_queue(
                pool,
                database_url,
                schema_name,
                env.context,
                watches=WatchStore(PostgresWatchBackend(pool)),
                watch_config=WatchConfig(poll_interval_s=180),
            )
            async with queue.open():
                rec, _ = await queue.watches.start("SYNTH01", user_id)
                for _ in range(3):
                    await queue.watches.poll(rec.id)
                    await drain(queue, pool, queues=["ingest", "process"])
                await queue.watches.stop("SYNTH01", user_id)
        return sorted(p.pull_key for p in env.events.pulls("SYNTH01"))

    assert run(go) == ["SYNTH01:1", "SYNTH01:2"]
    with pool.connection() as conn:
        fights = [
            r[0]["fights"] for r in conn.execute("SELECT spec FROM jobs WHERE kind = 'ingest' ORDER BY created_at")
        ]
    assert fights == [[1], [2]]
