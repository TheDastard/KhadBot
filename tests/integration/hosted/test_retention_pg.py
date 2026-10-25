"""
tests/integration/hosted/test_retention_pg.py

Backfill and the records cleanup against a real database: the selection query's filters, the references the cleanup
reads, and the whole chain on the queue. SYNTH01 is ingested and processed, its raw pages are deleted as the bucket's
lifecycle rule would, the records version is bumped, and a backfill rebuilds every pull from stored events: new
records and read-model rows, a second backfill queueing nothing while the first is still queued, no WCL call, and a
cleanup afterwards that removes the superseded records and keeps the ones the rows point at.

Each test runs its coroutine on a selector event loop, which psycopg's async connections need on Windows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from helpers.jobs.synth_queue import PULLS, StubWclClient, synth_env
from procrastinate import PsycopgConnector

from khadbot.config import QueueConfig, RetentionConfig
from khadbot.db import PostgresReadModelIndex, PostgresReadModelProjector, migrate, queries
from khadbot.db.read_models import project_pull
from khadbot.encounter import digest
from khadbot.encounter.records_store import RecordsStore
from khadbot.jobs import IngestReport
from khadbot.jobs.backfill import run_backfill
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.retention import CleanupSources, cleanup_records
from khadbot.jobs.status import JobStore, PostgresJobBackend
from khadbot.storage import LocalBlobStore

CONFIG = QueueConfig(max_retries=1, retry_backoff_s=0, progress_interval_s=0.05)


def run[T](coro: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(coro(), loop_factory=asyncio.SelectorEventLoop)  # type: ignore[arg-type]


class CountingClient(StubWclClient):
    def __init__(self) -> None:
        self.calls = 0

    async def rate_limit(self, *, user_token: str | None = None) -> dict:
        self.calls += 1
        return await super().rate_limit(user_token=user_token)


# ===========================================================================
# Queries
# ===========================================================================


@pytest.fixture
def projected(pool, synth_dataset):
    migrate(pool)
    for code in synth_dataset.ctx.data.store.reports():
        for p in synth_dataset.store.pulls(code):
            project_pull(pool, synth_dataset.ctx.data.records(p.pull_key), synth_dataset.ctx.raw.report_meta(code))
    return synth_dataset


def test_pull_keys_filter_by_encounter_difficulty_and_report_start(pool, projected):
    with pool.connection() as conn:
        everything = conn.execute("SELECT pull_key, encounter_id, difficulty FROM pulls ORDER BY pull_key").fetchall()
        start = conn.execute("SELECT max(start_time_ms) FROM reports").fetchone()[0]
        encounter, difficulty = everything[0][1], everything[0][2]
        same = sorted(k for k, e, _ in everything if e == encounter)

        assert sorted(queries.backfill_pull_keys(conn, encounter)) == same
        assert queries.backfill_pull_keys(conn, -1) == []
        if difficulty is not None:
            assert queries.backfill_pull_keys(conn, encounter, difficulty + 100) == []
            assert sorted(queries.backfill_pull_keys(conn, encounter, difficulty)) == sorted(
                k for k, e, d in everything if e == encounter and d == difficulty
            )
        if start is not None:
            assert sorted(queries.backfill_pull_keys(conn, encounter, since_ms=start)) == same
            assert queries.backfill_pull_keys(conn, encounter, since_ms=start + 1) == []


def test_pull_keys_come_in_pull_order_within_a_night(pool, projected):
    with pool.connection() as conn:
        keys = queries.backfill_pull_keys(conn, conn.execute("SELECT encounter_id FROM pulls LIMIT 1").fetchone()[0])
    fights = [int(k.rpartition(":")[2]) for k in keys if k.startswith(keys[0].partition(":")[0] + ":")]
    assert fights == sorted(fights)


def test_the_referenced_records_are_the_blobs_the_rows_point_at(pool, projected, tmp_path):
    # Every row names the blob the records store keeps that pull's records under, so the cleanup's "referenced" set
    # matches the store's keys exactly. The dataset holds records in memory only, so the keys come from the store's
    # own naming rather than from a listing.
    store = RecordsStore(LocalBlobStore(tmp_path))
    data = projected.ctx.data
    expected = {
        store.key(p, data.records(p.pull_key).digest.produced_from)
        for code in data.store.reports()
        for p in data.store.pulls(code)
    }
    with pool.connection() as conn:
        assert queries.referenced_records_keys(conn) == expected
    assert PostgresReadModelIndex(pool).records_keys() == expected


# ===========================================================================
# The whole chain
# ===========================================================================


@pytest.fixture
def env(pool, tmp_path):
    migrate(pool)
    env = synth_env(tmp_path)
    env.context.projector = PostgresReadModelProjector(env.raw, pool=lambda: pool)
    env.context.client = CountingClient()  # type: ignore[assignment]
    return env


@pytest.fixture
def make_queue(pool, database_url, schema_name, env):
    def make() -> JobQueue:
        connector = PsycopgConnector(
            conninfo=database_url, min_size=1, max_size=6, kwargs={"options": f"-c search_path={schema_name}"}
        )
        return JobQueue(
            JobStore(PostgresJobBackend(pool)),
            connector,
            config=CONFIG,
            context_factory=lambda: env.context,
            retention=RetentionConfig(records_min_age_days=0, cleanup_cron=""),
            cleanup=CleanupSources(blobs=lambda: env.records.blobs, index=lambda: PostgresReadModelIndex(pool)),
        )

    return make


def active_jobs(pool) -> int:
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


def test_a_backfill_rebuilds_pulls_whose_raw_pages_expired_and_spends_no_points(env, make_queue, pool, monkeypatch):
    queue = make_queue()
    client = env.context.client

    async def ingest() -> None:
        async with queue.open():
            await queue.enqueue(IngestReport(code="SYNTH01", offline=True))
            await drain(queue, pool)

    run(ingest)
    old = set(env.records.keys("SYNTH01"))
    with pool.connection() as conn:
        encounter = conn.execute("SELECT encounter_id FROM pulls WHERE report_code = 'SYNTH01' LIMIT 1").fetchone()[0]
        versions = {r[0] for r in conn.execute("SELECT records_version FROM pulls").fetchall()}
    assert len(old) == len(PULLS) and versions == {digest.RECORDS_VERSION}

    for key in env.raw.blobs.list("raw/"):  # the lifecycle rule has expired every raw page
        env.raw.blobs.delete(key)
    calls = client.calls  # type: ignore[attr-defined]
    monkeypatch.setattr(digest, "RECORDS_VERSION", digest.RECORDS_VERSION + 1)

    async def backfill():
        async with queue.open():
            first = await run_backfill(queue, PostgresReadModelIndex(pool), encounter_id=encounter)
            second = await run_backfill(queue, PostgresReadModelIndex(pool), encounter_id=encounter)
            await drain(queue, pool)
            return first, second

    first, second = run(backfill)
    assert sorted(first.enqueued) == PULLS
    assert second.enqueued == [] and sorted(second.already_queued) == PULLS
    assert client.calls == calls  # type: ignore[attr-defined]

    with pool.connection() as conn:
        rows = conn.execute("SELECT pull_key, records_version, records_key FROM pulls ORDER BY pull_key").fetchall()
        priorities = {r[0] for r in conn.execute("SELECT priority FROM procrastinate_jobs").fetchall()}
        states = {r[0] for r in conn.execute("SELECT state FROM jobs WHERE kind = 'process'").fetchall()}
    assert {r[1] for r in rows} == {digest.RECORDS_VERSION}
    assert {r[2] for r in rows} == set(env.records.keys("SYNTH01")) - old
    assert -10 in priorities and states == {"done"}

    # The superseded records are now referenced by nothing: the cleanup removes exactly those.
    result = cleanup_records(env.records.blobs, PostgresReadModelIndex(pool), min_age=timedelta(0))
    assert set(result.deleted) == old
    assert {r[2] for r in rows} == set(env.records.keys("SYNTH01"))


def test_the_cleanup_task_removes_unreferenced_records_through_the_queue(env, make_queue, pool):
    queue = make_queue()

    async def go():
        async with queue.open():
            await queue.enqueue(IngestReport(code="SYNTH01", offline=True))
            await drain(queue, pool)
            env.records.blobs.put("records/SYNTH01/99__orphan.json", b"{}")
            return await queue.run_cleanup()

    result = run(go)
    assert result is not None and result.deleted == ["records/SYNTH01/99__orphan.json"]
    assert len(env.records.keys("SYNTH01")) == len(PULLS)
